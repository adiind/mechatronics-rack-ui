"""Output transport: frame diffing, message budgets and wire encoding.

The node firmware accepts only ``fill``, ``range``, ``pixel`` and ``off`` on
``ledwall/nodeNN/set``. There is no bulk-frame command, no firmware effect and
no timestamp, so a "frame" is really a small set of contiguous repaints and the
only way to animate seven ropes politely is to send *differences*.

Everything in this module is pure or trivially stateful, so the budget rules can
be tested against a fake publisher with no broker anywhere near them.
"""
from __future__ import annotations

# --- chosen budgets ---------------------------------------------------------
# These are conservative engineering choices for this wall, not a measured
# firmware ceiling. ~24 small JSON messages/second/node is well under what an
# ESP32-C6 parses comfortably, and the aggregate keeps the shared hidden-SSID
# campus link quiet. Lower them if the wall ever looks like it is dropping
# updates; nothing else in the studio needs to change.
NODE_RATE = 24.0            # sustained messages per second, per rope
NODE_BURST = 40.0           # catch-up allowance, per rope
AGGREGATE_RATE = 140.0      # sustained messages per second, all seven ropes
AGGREGATE_BURST = 80.0
MAX_MSGS_PER_NODE_TICK = 8  # keeps one busy rope from eating a whole tick

#: Every Nth planning pass ignores priority and works left to right, so a
#: low-contrast region can never be starved by livelier neighbours.
FAIRNESS_PERIOD = 4


class TokenBucket:
    """Classic bucket. ``clock`` is injected so tests use a fake one."""

    def __init__(self, rate, burst, now=0.0):
        self.rate = float(rate)
        self.burst = float(burst)
        self.tokens = float(burst)
        self.stamp = float(now)

    def refill(self, now):
        elapsed = max(0.0, float(now) - self.stamp)
        self.stamp = float(now)
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate)

    def take(self, count):
        count = min(count, int(self.tokens))
        if count > 0:
            self.tokens -= count
        return max(0, count)


def diff_runs(applied, target):
    """Maximal constant-colour runs of ``target`` that contain a changed pixel.

    Runs are kept whole even when a few pixels inside already match: repainting
    an already-correct pixel is free, splitting a run costs another message.
    """
    n = len(target)
    runs = []
    i = 0
    while i < n:
        colour = target[i]
        j = i + 1
        while j < n and target[j] == colour:
            j += 1
        for k in range(i, j):
            if applied[k] != colour:
                runs.append((i, j - i, colour))
                break
        i = j
    return runs


def _delta(applied, start, count, colour):
    worst = 0
    for i in range(start, start + count):
        old = applied[i]
        if old is None:
            return 255
        for a, b in zip(old, colour):
            d = a - b
            if d < 0:
                d = -d
            if d > worst:
                worst = d
    return worst


def plan_updates(applied, target, limit, *, prioritize=True):
    """Choose at most ``limit`` runs to repaint.

    When the budget cannot cover the whole frame we send the most *visible*
    differences first, so a budget-limited rope looks deliberately simplified
    rather than torn. Skipped runs stay dirty and are re-offered next tick.
    """
    runs = diff_runs(applied, target)
    limit = max(0, int(limit))
    if limit >= len(runs):
        return runs
    if prioritize:
        scored = sorted(
            runs,
            key=lambda r: (-_delta(applied, r[0], r[1], r[2]) * min(r[1], 20), r[0]),
        )
        chosen = scored[:limit]
        return sorted(chosen, key=lambda r: r[0])
    return runs[:limit]


def payload_for(start, count, colour, pixels):
    """Build one firmware payload, rejecting anything the node would refuse."""
    start, count = int(start), int(count)
    rgb = [int(c) for c in colour]
    if len(rgb) != 3 or any(not 0 <= c <= 255 for c in rgb):
        raise ValueError('Pixel colour out of range')
    if count < 1 or start < 0 or start + count > pixels:
        raise ValueError('Pixel range outside the rope')
    if start == 0 and count == pixels:
        return {'op': 'off'} if rgb == [0, 0, 0] else {'op': 'fill', 'rgb': rgb}
    if count == 1:
        return {'op': 'pixel', 'index': start, 'rgb': rgb}
    return {'op': 'range', 'start': start, 'count': count, 'rgb': rgb}


def apply_run(applied, start, count, colour):
    applied[start:start + count] = [colour] * count


# ------------------------------------------------------------ browser filmstrip

def encode_film(rope_frames):
    """Run-length encode frames against a shared palette.

    A raw filmstrip of seven ropes x twelve frames is 25k numbers; run-length
    encoding against a shared palette takes it to roughly a tenth of that, which
    is what makes smooth browser playback affordable over Tailscale.
    """
    palette = {}
    encoded = []
    for frames in rope_frames:
        rope = []
        for frame in frames:
            runs = []
            n = len(frame)
            i = 0
            while i < n:
                colour = frame[i]
                j = i + 1
                while j < n and frame[j] == colour:
                    j += 1
                key = (colour[0], colour[1], colour[2])
                index = palette.get(key)
                if index is None:
                    index = palette[key] = len(palette)
                runs.append([j - i, index])
                i = j
            rope.append(runs)
        encoded.append(rope)
    return [list(key) for key in palette], encoded
