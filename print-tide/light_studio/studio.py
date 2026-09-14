"""The coordinator: one owner for all seven printer ropes.

Responsibilities kept deliberately separate from their neighbours:

    model.py      raw snapshot  -> sanitized display state
    renderer.py   display state -> pixels          (pure, shared with the browser)
    transport.py  pixels        -> firmware messages under a budget
    layout.py     mapping       -> validated, atomically persisted JSON
    studio.py     the clock, the lifecycle rules, and the single writer
    web.py        HTTP/UI

The studio never imports the host module, never opens a printer connection and
never talks to a broker itself: it is handed a snapshot callback, the initial
CAST_MAP, a publisher callback and a cast-enabled callback, and that is all.
"""
from __future__ import annotations

import copy
import re
import threading
import time
from collections import deque
from pathlib import Path

from . import transport as tp
from .layout import (Conflict, DEFAULTS, LayoutStore, atomic_write, merge_settings,
                     read_json, validate_accent, validate_settings)
from .model import STALE_AFTER, STATES, normalize, number

#: States whose bed still holds a part: collection (button or door) applies.
COLLECTABLE = ('finished', 'stopped')

#: Keys stripped from raw printer reports before they reach the browser. This
#: mirrors ``redact_report`` in the host (reference/server.py) as defence in
#: depth: the studio never trusts that the host already did it. A key is
#: dropped if any underscore-separated token is in the set or if it contains
#: one of the fragments.
_REDACT_TOKENS = frozenset({'sn', 'serial', 'access', 'code', 'passwd', 'password',
                            'passw', 'ssid', 'ip', 'mac', 'url', 'rtsp', 'ipcam',
                            'token', 'key', 'secret', 'uid', 'uuid', 'host', 'addr'})
_REDACT_FRAGMENTS = ('access', 'passw', 'rtsp', 'ipcam', 'serial', 'secret', 'token')


def redact_report(value):
    """Recursively drop sensitive-looking keys from a Bambu report payload."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            name = str(key)
            low = name.lower()
            if any(fragment in low for fragment in _REDACT_FRAGMENTS) or \
               any(token in _REDACT_TOKENS for token in low.split('_')):
                continue
            out[name] = redact_report(item)
        return out
    if isinstance(value, list):
        return [redact_report(item) for item in value]
    return value
from .renderer import (ACCENT_POSITIONS, CELEBRATE_SECONDS, IDENTIFY_SECONDS,
                       PIXELS, STATUS_POSITIONS, compose_rope, render_accent,
                       render_rope)
from .themes import describe as describe_themes

#: Rope controllers this studio may ever address. node01 is the unrelated
#: rack/wall pilot and is excluded by pattern, not by convention.
NODE_PATTERN = re.compile(r'^node(?:0[2-9]|[1-9][0-9])$')

TICK_HZ = 8.0
#: Full repaint interval per rope. Heals a node power-cycle or a broker gap --
#: and, more often, ordinary QoS 0 packet loss. Publishes are fire-and-forget
#: and a pixel is marked painted once the *broker* accepts it, so a lost packet
#: leaves a stale pixel that the diff will never resend on its own: it believes
#: that pixel is already correct. Measured loss on this wall is ~7%, and a
#: printing rope's falling drop moves ~8 times a second, so an erase is lost
#: every few seconds and the drop appears to hang in mid-air.
#: A full printing repaint is only ~4 messages, so resyncing often is cheap
#: (~20 msg/s across seven ropes) and bounds any stale pixel to this interval.
RESYNC_SECONDS = 2.0
#: Rope health. The firmware acks every accepted command on ledwall/nodeNN/ack
#: and the broker retains online/offline on ledwall/nodeNN/status. The host
#: feeds both into :meth:`Studio.observe_node`; the studio never subscribes
#: itself. A rope is "confirmed" when an ack has arrived this recently after
#: a send, and "silent" when sends have gone unanswered for this long.
ACK_FRESH_SECONDS = 3.0
SILENT_SECONDS = 6.0
#: A tick gap longer than this breaks transition continuity: we did not observe
#: the interval, so we must not claim to know what happened during it.
CONTINUITY_GAP = STALE_AFTER
EVENT_MEMORY = 16
EVENT_LIFETIME = 4.0
#: Two events of the same kind for the same printer inside this window are the
#: same event as far as the wall is concerned.
EVENT_DEDUPE = 6.0

MAX_FILM_FRAMES = 24
MAX_FILM_FPS = 20


def _check_initial(initial):
    if not isinstance(initial, dict) or not initial:
        raise ValueError('No printer to rope mapping was supplied')
    if len(initial) > 8:
        raise ValueError('This studio coordinates at most eight ropes')
    nodes = set()
    for name, target in initial.items():
        if not isinstance(name, str) or not name:
            raise ValueError('Printer names must be non-empty strings')
        if not isinstance(target, dict):
            raise ValueError('Each mapping entry must be an object')
        node = target.get('node')
        if not isinstance(node, str) or not NODE_PATTERN.match(node):
            raise ValueError('Only node02-node08 style rope controllers are allowed')
        if node in nodes:
            raise ValueError('Two printers are mapped to the same rope')
        if target.get('pixels') != PIXELS:
            raise ValueError(f'Every rope on this wall must be {PIXELS} pixels')
        nodes.add(node)
    return copy.deepcopy(initial)


class Studio:
    """Single-writer lighting coordinator plus the data the UI reads.

    Constructor signature is intentionally stable: the staged host integration
    calls ``Studio(dir, CAST_MAP, snapshots, publish, enabled)`` positionally.
    """

    def __init__(self, root, initial, snapshots, publish, enabled,
                 *, clock=time.time, demo=False, pixels=PIXELS, raw_reports=None):
        self.lock = threading.RLock()
        # Optional: the host's recent raw (already redacted) MQTT reports per
        # printer, for the telemetry log page. Read-only; never influences lights.
        self.raw_reports = raw_reports if callable(raw_reports) else (lambda: {})
        self.initial = _check_initial(initial)
        self.pixels = pixels
        # Zone split is fixed for this wall: 90 status positions from the wire,
        # then the 10-position accent cap at the far end.
        self.accent_positions = min(ACCENT_POSITIONS, pixels)
        self.status_positions = pixels - self.accent_positions
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.snapshots = snapshots
        self.publish = publish
        self.enabled = enabled
        self.clock = clock
        self.demo = bool(demo)

        # Persistence first: a corrupt layout must fail before any writer runs.
        self.store = LayoutStore(self.root, self.initial)
        self.ack_path = self.root / 'collected.json'
        self.doors = {}                  # last door_open per printer (None = unknown)
        self.lifecycle_path = self.root / 'lifecycle.json'
        self.acks = self._load_acks()
        self.prior = self._load_lifecycle()

        self.nodes = sorted(target['node'] for target in self.initial.values())
        now = self.clock()
        self.phase = 0.0                    # animation clock, immune to NTP steps
        self._wall = now
        self.status = {}
        self.frames = {}
        self.events = []
        self.celebrations = {}
        self.ident = None
        self.last_error = None
        self.running = False
        self.thread = None

        self.applied = {node: [None] * pixels for node in self.nodes}
        self.resync_due = {
            node: now + RESYNC_SECONDS * index / max(1, len(self.nodes))
            for index, node in enumerate(self.nodes)
        }
        self.node_buckets = {node: tp.TokenBucket(tp.NODE_RATE, tp.NODE_BURST, now)
                             for node in self.nodes}
        self.aggregate = tp.TokenBucket(tp.AGGREGATE_RATE, tp.AGGREGATE_BURST, now)
        self.rotation = 0
        self.pass_number = 0
        self.was_enabled = False
        self.sent_window = deque()
        self.counters = {'sent': 0, 'failed': 0, 'skipped_budget': 0}
        # Delivery evidence per rope; stays 'unverified' until the host wires
        # observe_node, so a host without the subscription never overclaims.
        self.receipts_wired = False
        self.health = {node: self._blank_health() for node in self.nodes}

    # ------------------------------------------------------------- persistence

    def _load_acks(self):
        data = read_json(self.ack_path, {})
        if not isinstance(data, dict):
            raise ValueError('Saved collection record is not an object')
        return {k: v for k, v in data.items()
                if isinstance(k, str) and (v is None or isinstance(v, str))}

    def _load_lifecycle(self):
        """Rehydrate the previous run's bookkeeping.

        Restored rows are flagged and can only ever *suppress* a transition. We
        did not observe the restart interval, so per the wall's rules a process
        restart can never produce a celebration, however plausible the story is.

        ``observed_complete`` is deliberately **not** restored, and no longer
        persisted at all. It asserts "we watched this exact job finish", and a
        restart is by definition an interval we did not watch. Any key left in an
        older file is ignored rather than trusted.
        """
        data = read_json(self.lifecycle_path, {})
        prior = {}
        if isinstance(data, dict):
            rows = data.get('printers')
            if isinstance(rows, dict):
                for name, row in rows.items():
                    if not isinstance(row, dict) or name not in self.initial:
                        continue
                    if row.get('state') not in STATES or number(row.get('wall')) is None:
                        continue
                    prior[name] = {'state': row['state'], 'fresh': row.get('fresh') is True,
                                   'at': float(row['wall']), 'restored': True}
        self.observed_complete = {}
        return prior

    def _persist_lifecycle(self):
        """Diagnostic record of what we last saw, and when.

        Used on the next start only to mark rows as restored (belt and braces
        alongside an empty in-memory history), never to justify an event.
        """
        atomic_write(self.lifecycle_path, {
            'version': 2,
            'printers': {name: {'state': row['state'], 'fresh': row['fresh'],
                                'wall': round(row['at'], 3)}
                         for name, row in self.prior.items()},
        })

    # ------------------------------------------------------------------ layout

    def config(self):
        with self.lock:
            return self.store.snapshot()

    def save(self, config):
        with self.lock:
            saved = self.store.save(config)
            self._resync_all()          # mapping/settings changed: repaint cleanly
            return copy.deepcopy(saved)

    def undo(self, revision):
        with self.lock:
            saved = self.store.undo(revision)
            self._resync_all()
            return copy.deepcopy(saved)

    def reset(self, revision):
        with self.lock:
            saved = self.store.reset(revision)
            self._resync_all()
            return copy.deepcopy(saved)

    def mapping(self):
        """Current printer -> rope map, for list_printers/get_print_status.

        Read live on every MCP call, so a save in the browser is reflected
        immediately without restarting telemetry.
        """
        with self.lock:
            return {slot['printer']: {'node': slot['node'], 'pixels': self.pixels}
                    for slot in self.store.current['slots']}

    def _resync_all(self):
        for node in self.nodes:
            self.applied[node] = [None] * self.pixels

    # ------------------------------------------------------------ rope health

    @staticmethod
    def _blank_health():
        return {'status': 'unknown', 'status_at': None, 'last_send': None,
                'last_ack': None, 'acks_ok': 0, 'acks_error': 0, 'last_error': None}

    def observe_node(self, node, kind, payload, now=None):
        """Feed one broker message about a rope controller.

        ``kind`` is ``'status'`` (payload ``'online'``/``'offline'``, retained by
        the broker) or ``'ack'`` (payload the firmware's ``{"ok": bool, ...}``
        JSON, already decoded). Messages for ropes this wall does not drive,
        including the stale ``node004``-style ghosts on the broker, are ignored.
        Returns True when the message was attributed to a rope.
        """
        with self.lock:
            health = self.health.get(node)
            if health is None:
                return False
            self.receipts_wired = True
            if now is None:
                now = self.clock()
            if kind == 'status':
                status = str(payload).strip().lower()
                if status not in ('online', 'offline'):
                    return False
                came_back = status == 'online' and health['status'] != 'online'
                health['status'] = status
                health['status_at'] = now
                if came_back:
                    # restore_mode is ALWAYS_OFF: a rebooted controller is dark
                    # until repainted, so forget what we believed it showed.
                    self.applied[node] = [None] * self.pixels
                return True
            if kind == 'ack':
                if not isinstance(payload, dict):
                    return False
                if payload.get('ok') is True:
                    health['acks_ok'] += 1
                    health['last_ack'] = now
                    if health['status'] != 'online':
                        # An ack is stronger evidence than a stale retained status.
                        health['status'] = 'online'
                        health['status_at'] = now
                    if self.ident and self.ident['node'] == node:
                        self.ident['acks'] = self.ident.get('acks', 0) + 1
                else:
                    health['acks_error'] += 1
                    error = payload.get('error')
                    health['last_error'] = str(error)[:40] if error else 'rejected'
                return True
            return False

    def _receipt(self, node, now):
        """One word of delivery evidence for a rope."""
        if not self.receipts_wired:
            return 'unverified'
        h = self.health[node]
        if h['status'] == 'offline':
            return 'offline'
        if h['last_send'] is None:
            return 'idle'
        if h['last_ack'] is not None and (
                h['last_ack'] >= h['last_send'] - ACK_FRESH_SECONDS
                or now - h['last_ack'] <= ACK_FRESH_SECONDS):
            return 'confirmed'
        if now - h['last_send'] > SILENT_SECONDS or (
                h['last_ack'] is None and now - h['last_send'] > ACK_FRESH_SECONDS):
            return 'silent'
        return 'pending'

    def _ropes_view(self, now):
        out = {}
        for node in self.nodes:
            h = self.health[node]
            out[node] = {
                'status': h['status'],
                'receipt': self._receipt(node, now),
                'ack_age': None if h['last_ack'] is None else round(now - h['last_ack'], 1),
                'acks_ok': h['acks_ok'],
                'acks_error': h['acks_error'],
                'last_error': h['last_error'],
            }
        return out

    def _receipt_summary(self, ropes):
        if not self.receipts_wired:
            return 'unverified'
        counts = {}
        for rope in ropes.values():
            counts[rope['receipt']] = counts.get(rope['receipt'], 0) + 1
        total = len(ropes)
        parts = [f"confirmed {counts.get('confirmed', 0)}/{total}"]
        for word in ('offline', 'silent', 'pending'):
            if counts.get(word):
                parts.append(f"{counts[word]} {word}")
        return ' · '.join(parts)

    # ---------------------------------------------------------------- actions

    def identify(self, node):
        """Flash one rope for a bounded five seconds, then restore its state.

        Server-side and time-bounded on purpose: closing the browser mid-flash
        must not leave a rope stuck in the identify pattern.
        """
        with self.lock:
            if node not in self.applied:
                raise ValueError('Unknown printer rope')
            if not self._cast_on():
                raise ValueError('Lighting output is currently paused by the controller')
            now = self.clock()
            self.ident = {'node': node, 'phase': self.phase, 'until': now + IDENTIFY_SECONDS,
                          'acks': 0}
            return {
                'node': node,
                'seconds': IDENTIFY_SECONDS,
                # Deliberately not "confirmed": the firmware acknowledges on a
                # topic this process does not subscribe to.
                'delivery': 'queued to the broker; confirm by watching the rope',
            }

    def collected(self, printer):
        """Local display bookkeeping only. Never sends anything to a printer."""
        with self.lock:
            state = self.status.get(printer)
            if not state or not state['fresh'] or state['state'] not in COLLECTABLE:
                raise ValueError('Only a live, finished or stopped printer can be marked collected')
            self.acks[printer] = state['job']
            atomic_write(self.ack_path, self.acks)
            return {'ok': True, 'printer': printer}

    # ------------------------------------------------------------ observation

    def _cast_on(self):
        try:
            return bool(self.enabled())
        except Exception:
            return False

    def _advance(self, now):
        """Advance the animation clock by a sane delta.

        Wall-clock jumps (NTP steps, suspend) must not teleport the water or
        resurrect expired ripples, so the animation runs on its own accumulator.
        """
        delta = now - self._wall
        self._wall = now
        if not 0.0 <= delta <= 2.0:
            delta = 1.0 / TICK_HZ
        self.phase += delta
        return delta

    def _observe(self, rows, now):
        by_name = {}
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get('name'), str):
                    by_name[row['name']] = row

        slots = self.store.current['slots']
        acks_changed = False
        lifecycle_changed = False
        for position, slot in enumerate(slots):
            name = slot['printer']
            state = normalize(by_name.get(name, {}), now)
            previous = self.prior.get(name)
            observed = state['state']

            # A completion is only real if we watched the whole transition: both
            # sides fresh, no gap in our own observation, and not a row restored
            # from disk across a restart we did not witness.
            continuous = (previous is not None
                          and not previous.get('restored')
                          and 0 <= now - previous['at'] <= CONTINUITY_GAP
                          and previous['fresh'] and state['fresh'])
            if continuous:
                if observed == 'finished' and previous['state'] in ('printing', 'preparing', 'paused'):
                    self._add_event(now, position, 'complete', name)
                    # Bind the claim to the job name we saw finish. Names are not
                    # unique IDs, so this only ever narrows the claim.
                    self.observed_complete[name] = {'at': now, 'job': state['job']}
                elif observed in ('printing', 'preparing') and previous['state'] in ('idle', 'finished', 'stopped', 'unknown'):
                    self._add_event(now, position, 'start', name)
            else:
                # We did not watch this interval. Anything could have started and
                # finished inside it, so an earlier "we saw it finish" no longer
                # applies to whatever is being reported now.
                self.observed_complete.pop(name, None)

            if previous is None or previous.get('restored') \
                    or previous['state'] != observed or previous['fresh'] != state['fresh']:
                lifecycle_changed = True
            self.prior[name] = {'state': observed, 'fresh': state['fresh'], 'at': now,
                                'restored': False}

            # A genuinely active job means the bed is in use again.
            if observed in ('printing', 'preparing'):
                if name in self.acks:
                    del self.acks[name]
                    acks_changed = True
                self.celebrations.pop(name, None)
                self.observed_complete.pop(name, None)

            # The door is the verification layer: opening (or closing) it on a
            # finished or stopped bay means someone was there and the bed is
            # being cleared, so it counts as collection without a button press.
            door = state['door_open'] if state['fresh'] else None
            door_before = self.doors.get(name)
            if door is not None and door_before is not None and door != door_before \
                    and observed in COLLECTABLE and state['fresh'] \
                    and self.acks.get(name) != state['job']:
                self.acks[name] = state['job']
                acks_changed = True
            self.doors[name] = door

            if observed in COLLECTABLE and name in self.acks and self.acks[name] == state['job']:
                state['state'] = 'idle'
                state['collected'] = True

            # "(seen)" must mean this job, watched from start to finish, with no
            # gap since. A different job name invalidates the claim outright.
            witnessed = self.observed_complete.get(name)
            if witnessed is not None and witnessed['job'] != state['job']:
                self.observed_complete.pop(name, None)
                witnessed = None
            state['completion_observed'] = observed == 'finished' and witnessed is not None
            state['completed_ago'] = (round(max(0.0, now - witnessed['at']))
                                      if state['completion_observed'] else None)
            self.status[name] = state

        if acks_changed:
            atomic_write(self.ack_path, self.acks)
        if lifecycle_changed:
            self._persist_lifecycle()

    def _add_event(self, now, position, kind, printer):
        for event in self.events:
            if event['printer'] == printer and event['kind'] == kind \
                    and self.phase - event['at'] < EVENT_DEDUPE:
                return
        self.events.append({'at': self.phase, 'wall': now, 'position': position,
                            'kind': kind, 'printer': printer})
        del self.events[:-EVENT_MEMORY]
        if kind == 'complete':
            self.celebrations[printer] = self.phase

    def _expire(self, now):
        self.events = [e for e in self.events if 0 <= self.phase - e['at'] < EVENT_LIFETIME]
        self.celebrations = {p: at for p, at in self.celebrations.items()
                             if 0 <= self.phase - at < CELEBRATE_SECONDS * 4}
        if self.ident and now >= self.ident['until']:
            # Bounded even if the browser vanished: the rope returns to whatever
            # its printer is actually doing, repainted from scratch.
            self.applied[self.ident['node']] = [None] * self.pixels
            self.ident = None

    # ------------------------------------------------------------- rendering

    def _scene(self):
        """A plain snapshot of everything the renderer needs.

        Taken under the lock and then rendered *without* it, so a browser asking
        for a filmstrip can never stall the writer thread mid-tick.
        """
        return {
            'settings': dict(self.store.current['settings']),
            'slots': copy.deepcopy(self.store.current['slots']),
            'events': copy.deepcopy(self.events),
            'status': copy.deepcopy(self.status),
            'celebrations': dict(self.celebrations),
            'ident': dict(self.ident) if self.ident else None,
        }

    def _compose(self, scene, position, t):
        """One *physical* rope frame at animation time ``t``.

        Status region and accent cap are rendered independently and joined by
        :func:`compose_rope`, so no state, event, celebration or Identify can
        reach into physical positions 90-99. Pure with respect to ``scene``.
        """
        slot = scene['slots'][position]
        settings = scene['settings']
        row = scene['status'].get(slot['printer'])

        if row is None:
            body = render_rope('unknown', None, t, position, settings,
                               pixels=self.status_positions)
        else:
            identify_age = None
            ident = scene['ident']
            if ident and ident['node'] == slot['node']:
                age = t - ident['phase']
                identify_age = age if 0 <= age < IDENTIFY_SECONDS else None
            started = scene['celebrations'].get(slot['printer'])
            body = render_rope(
                row['state'], row['percent'], t, position, settings, scene['events'],
                pixels=self.status_positions,
                since_complete=None if started is None else t - started,
                identify_age=identify_age,
                speed=row.get('speed'),
            )

        cap = render_accent(slot.get('accent'), t, position,
                            positions=self.accent_positions,
                            quiet=bool(settings.get('quiet')),
                            still=bool(settings.get('reduced_motion')),
                            theme=settings.get('theme'))
        return compose_rope(body, cap, slot['reverse'])

    def _render_all(self):
        scene = self._scene()
        for position, slot in enumerate(scene['slots']):
            self.frames[slot['node']] = self._compose(scene, position, self.phase)

    # -------------------------------------------------------------- publishing

    def _publish_frames(self, now):
        if not self._cast_on() or self.publish is None:
            # cast_progress(false) suspends output promptly; re-enabling forces a
            # full repaint because we no longer know what the ropes are showing.
            if self.was_enabled:
                self._resync_all()
            self.was_enabled = False
            return
        if not self.was_enabled:
            self._resync_all()
        self.was_enabled = True

        self.aggregate.refill(now)
        self.pass_number += 1
        prioritize = self.pass_number % tp.FAIRNESS_PERIOD != 0

        slots = self.store.current['slots']
        order = slots[self.rotation:] + slots[:self.rotation]
        self.rotation = (self.rotation + 1) % max(1, len(slots))

        sent_now = 0
        for slot in order:
            node = slot['node']
            # Frames are already in physical order: _compose applied `reverse`
            # to the status region only, and put the accent at 90-99.
            target = self.frames.get(node)
            if target is None:
                continue

            # A backwards clock step must not park the resync deadline in the
            # far future and leave a rope un-healed.
            if now >= self.resync_due[node] or self.resync_due[node] > now + RESYNC_SECONDS:
                self.applied[node] = [None] * self.pixels
                self.resync_due[node] = now + RESYNC_SECONDS

            bucket = self.node_buckets[node]
            bucket.refill(now)
            allowance = min(tp.MAX_MSGS_PER_NODE_TICK,
                            int(bucket.tokens), int(self.aggregate.tokens))
            if allowance <= 0:
                continue

            applied = self.applied[node]
            runs = tp.plan_updates(applied, target, allowance, prioritize=prioritize)
            pending = len(tp.diff_runs(applied, target))
            if pending > len(runs):
                self.counters['skipped_budget'] += pending - len(runs)

            for start, count, colour in runs:
                try:
                    payload = tp.payload_for(start, count, colour, self.pixels)
                except ValueError:
                    continue
                bucket.take(1)
                self.aggregate.take(1)
                try:
                    ok = self.publish(node, payload) is True
                except Exception:
                    ok = False
                if not ok:
                    # Enqueue failed: forget what we believed the rope shows so
                    # the next healthy tick repaints it completely.
                    self.counters['failed'] += 1
                    self.applied[node] = [None] * self.pixels
                    break
                tp.apply_run(applied, start, count, colour)
                self.counters['sent'] += 1
                self.health[node]['last_send'] = now
                sent_now += 1

        self.sent_window.append((now, sent_now))
        while self.sent_window and now - self.sent_window[0][0] > 5.0:
            self.sent_window.popleft()

    def _rate(self, now):
        if not self.sent_window:
            return 0.0
        span = max(1.0, now - self.sent_window[0][0])
        return round(sum(count for _, count in self.sent_window) / span, 1)

    # -------------------------------------------------------------------- tick

    def tick(self):
        rows = self.snapshots()
        with self.lock:
            now = self.clock()
            self._advance(now)
            self._observe(rows, now)
            self._expire(now)
            self._render_all()
            self._publish_frames(now)

    def start(self):
        if self.running:
            raise RuntimeError('This studio is already running')
        self.running = True

        def loop():
            interval = 1.0 / TICK_HZ
            while self.running:
                began = time.monotonic()
                try:
                    self.tick()
                    self.last_error = None
                except Exception:
                    # Token-free: nothing from the host env may reach the browser.
                    self.last_error = 'Lighting update failed; check the Pi service log'
                time.sleep(max(0.02, interval - (time.monotonic() - began)))

        self.thread = threading.Thread(target=loop, daemon=True, name='print-tide')
        self.thread.start()

    def stop(self):
        self.running = False
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self.thread = None

    # ------------------------------------------------------------------- views

    def view(self):
        """Everything the browser is allowed to know. No frames: see film()."""
        with self.lock:
            now = self.clock()
            config = self.store.snapshot()
            ident = None
            if self.ident:
                ident = {'node': self.ident['node'],
                         'remaining': round(max(0.0, self.ident['until'] - now), 1),
                         # True once the controller acked a command during the flash.
                         'acked': self.ident.get('acks', 0) > 0}
            ropes = self._ropes_view(now)
            return {
                'demo': self.demo,
                'config': config,
                'printers': copy.deepcopy(self.status),
                'identify': ident,
                'cast_enabled': self._cast_on(),
                'can_undo': self.store.can_undo,
                'last_error': self.last_error,
                'phase': round(self.phase, 3),
                'at': now,
                'pixels': self.pixels,
                'status_positions': self.status_positions,
                'accent_positions': self.accent_positions,
                'accent_start': self.status_positions,
                'nodes': list(self.nodes),
                'themes': describe_themes(),
                'ropes': ropes,
                'events': [{'kind': e['kind'], 'printer': e['printer'],
                            'position': e['position'], 'age': round(self.phase - e['at'], 2)}
                           for e in self.events],
                'transport': {
                    'sent': self.counters['sent'],
                    'failed': self.counters['failed'],
                    'skipped_budget': self.counters['skipped_budget'],
                    'rate_per_second': self._rate(now),
                    'node_rate_ceiling': tp.NODE_RATE,
                    'aggregate_ceiling': tp.AGGREGATE_RATE,
                    # 'unverified' until the host feeds observe_node; then a
                    # count of ropes whose acks are keeping up with sends.
                    'node_receipt': self._receipt_summary(ropes),
                },
            }

    def _film(self, rope_specs, frames, fps, start):
        step = 1.0 / fps
        ropes = []
        for spec in rope_specs:
            series = []
            for index in range(frames):
                t = start + index * step
                series.append(spec(t))
            ropes.append(series)
        palette, encoded = tp.encode_film(ropes)
        return {'t0': round(start, 4), 'dt': round(step, 6), 'fps': fps,
                'frames': frames, 'pixels': self.pixels,
                'status_positions': self.status_positions,
                'accent_positions': self.accent_positions,
                'accent_start': self.status_positions,
                'palette': palette, 'ropes': encoded}

    @staticmethod
    def _film_bounds(frames, fps):
        frames = number(frames)
        fps = number(fps)
        if frames is None or fps is None:
            raise ValueError('Frame count and rate must be numbers')
        frames = max(1, min(MAX_FILM_FRAMES, int(frames)))
        fps = max(1, min(MAX_FILM_FPS, int(fps)))
        return frames, fps

    def live_film(self, frames=10, fps=10):
        """A short filmstrip of what the wall is being asked to show.

        The renderer is pure, so the browser can play near-future frames on its
        own clock and still be showing exactly the frames the coordinator sends.
        """
        frames, fps = self._film_bounds(frames, fps)
        with self.lock:
            scene = self._scene()
            start = self.phase
        specs = [(lambda t, position=position: self._compose(scene, position, t))
                 for position in range(len(scene['slots']))]
        film = self._film(specs, frames, fps, start)
        film['bays'] = [{'node': s['node'], 'printer': s['printer'],
                         'reverse': s['reverse'], 'accent': s['accent']}
                        for s in scene['slots']]
        return film

    def sim_film(self, bays, frames=10, fps=10, start=0.0, settings=None,
                 ripples=True, accents=None, reverses=None):
        """Explicitly simulated frames for the animation lab. Never published.

        ``bays`` is one ``{state, percent}`` per physical bay, so a mixed wall
        (one error, one printing, one idle) can be previewed as it would look.
        ``accents`` and ``reverses`` let the lab preview the *draft* cap settings
        and direction through the same compositor the hardware path uses.
        """
        frames, fps = self._film_bounds(frames, fps)
        start = number(start)
        if start is None:
            raise ValueError('Preview time must be a number')
        opts = validate_settings(settings if settings is not None else {})
        with self.lock:
            slots = copy.deepcopy(self.store.current['slots'])
        count = len(slots)
        if not isinstance(bays, list) or len(bays) != count:
            raise ValueError(f'Supply exactly {count} simulated bays')

        if accents is None:
            caps = [slot['accent'] for slot in slots]
        else:
            if not isinstance(accents, list) or len(accents) != count:
                raise ValueError(f'Supply exactly {count} accent settings')
            caps = [validate_accent(accent) for accent in accents]

        if reverses is None:
            flips = [slot['reverse'] for slot in slots]
        else:
            if not isinstance(reverses, list) or len(reverses) != count:
                raise ValueError(f'Supply exactly {count} direction flags')
            if any(type(flag) is not bool for flag in reverses):
                raise ValueError('Rope direction must be true or false')
            flips = list(reverses)

        parsed = []
        for bay in bays:
            if not isinstance(bay, dict):
                raise ValueError('Each simulated bay must be an object')
            state = bay.get('state')
            if state not in STATES:
                raise ValueError('Unknown simulated state')
            percent = bay.get('percent')
            if percent is not None:
                percent = number(percent)
                if percent is None or not 0 <= percent <= 100:
                    raise ValueError('Simulated progress must be 0-100')
            parsed.append((state, percent))

        events = []
        if ripples and opts['ripples']:
            # A repeating demonstration ripple so the lab can show propagation.
            cycle = 5.0
            base = (start // cycle) * cycle
            for at in (base, base + cycle):
                events.append({'at': at, 'position': 0, 'kind': 'complete',
                               'printer': None})

        specs = []
        for position, (state, percent) in enumerate(parsed):
            def spec(t, state=state, percent=percent, position=position):
                since = None
                if state == 'finished':
                    since = t % 9.0          # loops celebration -> steady green
                body = render_rope(state, percent, t, position, opts, events,
                                   pixels=self.status_positions, since_complete=since)
                # Same compositor as the hardware path: the lab cannot show an
                # accent the wall would not produce.
                cap = render_accent(caps[position], t, position,
                                    positions=self.accent_positions,
                                    quiet=opts['quiet'], still=opts['reduced_motion'],
                                    theme=opts.get('theme'))
                return compose_rope(body, cap, flips[position])
            specs.append(spec)
        film = self._film(specs, frames, fps, start)
        film['simulated'] = True
        film['bays'] = [{'node': slot['node'], 'printer': slot['printer'],
                         'reverse': flips[index], 'accent': caps[index]}
                        for index, slot in enumerate(slots)]
        return film

    # ------------------------------------------------------------ telemetry log

    def logs(self, printer=None, since=None, limit=200, raw=False):
        """What the printers actually said, as a change log per printer.

        Consecutive raw reports are diffed server-side so the page only has to
        show ``changed`` (field -> [old, new]); identical consecutive reports are
        folded into the previous entry's ``repeats``. ``since`` (epoch seconds)
        returns only newer entries for incremental polling. Bambu sends a full
        ~100-key report about once a second per printer, so the full payload is
        only included when ``raw`` is true (the page asks for one entry at a
        time when a row is expanded); otherwise each entry carries ``keys``, the
        number of fields in that report. Every report is passed through
        :func:`redact_report` again here, so even a host that forgot to redact
        cannot leak a serial, address or URL to the browser. Read-only: nothing
        here touches the lights.
        """
        try:
            limit = max(1, min(400, int(limit)))
        except (TypeError, ValueError):
            raise ValueError('Log limit must be a number')
        if since is not None:
            since = number(since)
            if since is None:
                raise ValueError('Log cursor must be a number')
        reports = self.raw_reports() or {}
        if not isinstance(reports, dict):
            reports = {}
        with self.lock:
            slots = self.store.current['slots']
            status = copy.deepcopy(self.status)
            now = self.clock()
        labels = {slot['printer']: slot['label'] for slot in slots}
        positions = {slot['printer']: index for index, slot in enumerate(slots)}
        names = [slot['printer'] for slot in slots]
        names += sorted(name for name in reports if name not in positions)
        out = []
        for name in names:
            if printer is not None and name != printer:
                continue
            series = reports.get(name) or []
            entries = []
            prev = None
            for item in series:
                try:
                    at, report = item
                    at = float(at)
                except (TypeError, ValueError):
                    continue
                if not isinstance(report, dict):
                    continue
                report = redact_report(report)
                if prev is None:
                    changed = {key: [None, value] for key, value in sorted(report.items())}
                else:
                    changed = {}
                    for key in sorted(set(prev) | set(report)):
                        if prev.get(key) != report.get(key):
                            changed[key] = [prev.get(key), report.get(key)]
                first = prev is None
                prev = report
                if not changed:
                    if entries:
                        entries[-1]['repeats'] += 1
                        entries[-1]['last_at'] = round(at, 3)
                    continue
                entry = {'at': round(at, 3), 'last_at': round(at, 3),
                         'first': first, 'changed': changed, 'keys': len(report),
                         'repeats': 0}
                if raw:
                    entry['raw'] = report
                entries.append(entry)
            if since is not None:
                entries = [entry for entry in entries if entry['at'] > since]
            out.append({
                'printer': name,
                'label': labels.get(name, name),
                'position': positions.get(name),
                'status': status.get(name),
                'reports_kept': len(series),
                'entries': entries[-limit:],
            })
        return {'at': now, 'printers': out}

    def preview(self, state, percent, t, settings=None):
        """Legacy single-state preview: one frame per bay. Read-only."""
        if state not in STATES:
            raise ValueError('Unknown preview state')
        if number(percent) is None or not 0 <= percent <= 100:
            raise ValueError('Preview progress must be 0-100')
        with self.lock:
            count = len(self.store.current['slots'])
            opts = settings if settings is not None else self.store.current['settings']
        bays = [{'state': state, 'percent': percent} for _ in range(count)]
        film = self.sim_film(bays, frames=1, fps=1, start=t, settings=opts)
        palette = film['palette']
        out = []
        for rope in film['ropes']:
            frame = []
            for count_, index in rope[0]:
                frame.extend([list(palette[index])] * count_)
            out.append(frame)
        return out


__all__ = ['Studio', 'Conflict', 'DEFAULTS', 'STATES', 'normalize', 'render_rope',
           'merge_settings', 'atomic_write']
