"""Pure, deterministic frame rendering for one 100-position rope.

Every frame anywhere in this project comes from this module: the hardware
writer, the live browser filmstrip and the animation lab all call it. There is
deliberately no second animation implementation in JavaScript, so what the
browser plays is what the wall was asked to show.

Two zones per rope
------------------
A rope is 100 *addressable positions* (WS2811 modules), not 100 dies::

    physical 0 .......................... 89 | 90 ............ 99
    ^ data-in wire                           |                 ^ far end
    <------------ status region (90) ------->|<-- accent (10) ->

* The **status region** carries all printer state and animation. Progress maps
  to these 90 positions alone: 0/50/100% is 0/45/90 of them. The cap is not part
  of the percentage.
* The **accent** is a fixed fixture at the far end, opposite the wire: solid
  white by default, or a chosen colour, or a slow rainbow. Nothing in the status
  layer may write to it -- not events, not ripples, not errors, not Identify.

``reverse`` flips the direction of the *status region only*. The accent's
physical location is defined relative to the wire and never moves.

Transport-aware composition
---------------------------
The firmware only accepts ``fill``/``range``/``pixel``/``off``, so a frame is
expensive to transmit in proportion to *how many contiguous colour runs
changed*, not how pretty it is. Every state here is therefore built as:

    a slow, broad base   (quantization holds it still for many frames -> ~free)
  + a few narrow sprites (crests, droplets, sweep, markers -> a handful of runs)

That is why the water reads as moving at 3-5 physical updates per second on a
budget that a naive full-strip gradient would blow through in one frame. See
DESIGN.md for the measured run counts.

All returned channel values are integers, quantized to :data:`QUANT` and capped
at :data:`BRIGHT_CAP`, which is the current physical ceiling for this wall.
"""
from __future__ import annotations

import math

from .layout import DEFAULTS, merge_accent, merge_settings

#: Addressable positions on one rope, and how they are split between the zones.
PIXELS = 100
ACCENT_POSITIONS = 10
STATUS_POSITIONS = PIXELS - ACCENT_POSITIONS
#: First physical index of the accent cap, counting from the data-in wire.
ACCENT_START = STATUS_POSITIONS

#: Effective wall brightness: the highest value any channel is driven to,
#: out of 255. 252 is the largest multiple of QUANT, i.e. effectively full
#: output, matching the original NodeAnimator which sent 255-valued colours.
#: The node firmware applies its own brightness ceiling on top (a firmware
#: setting, deliberately not touched from here), so a cap below 255 here
#: stacked with that ceiling and left the wall dim (2026-09-06, Adi). Use the
#: UI brightness slider to dim; it is a share of this cap.
BRIGHT_CAP = 252
#: Channel quantization step. Bigger = fewer colour runs = cheaper transport.
QUANT = 6

#: Quiet mode still dims the accent, or "quiet hours" would leave seventy white
#: LEDs at full output. It is floored so the cap stays clearly lit and white.
ACCENT_QUIET_SCALE = 0.45

#: Rainbow is a *uniform* hue across the ten positions, stepped in time. One
#: contiguous range message per step, and the step rate is fixed rather than
#: following the wall's speed slider, so the cost stays predictable:
#: 72 steps / 24 s = 3 messages per second per rope with rainbow enabled.
RAINBOW_PERIOD = 24.0
RAINBOW_STEPS = 72

#: Droplet timing, taken from the original NodeAnimator: one pixel per
#: DROP_STEP (0.06 s) = ~16.7 positions/second, a 0.12 s splash, and
#: DROP_INTERVAL (1.8 s) of quiet between drops.
DROP_SPEED = 1.0 / 0.06
SPLASH_SECONDS = 0.12
DROP_GAP = 1.8
MIN_DROP_CYCLE = 2.0

#: Alarms must survive quiet mode and a low brightness slider.
ALARM_FLOOR = 0.38
IDENTIFY_FLOOR = 0.60

IDENTIFY_SECONDS = 5.0
IDENTIFY_PULSES = 3
#: A genuine completion plays a smooth rainbow wash for this long, then the
#: rope settles to the resting cyan (Adi, 2026-09-06). Uniform hue per tick =
#: one range message per tick, so it is smooth on a 24 msg/s rope.
CELEBRATE_SECONDS = 12.0
RAINBOW_CYCLES = 2            # full hue turns during the celebration
RAINBOW_FADE = 1.5            # seconds of cross-fade into cyan at the end

#: Ripple propagation between adjacent physical bays, and its lifetime.
RIPPLE_DELAY = 0.22
RIPPLE_SECONDS = 0.80
#: States whose meaning must never be washed out by a cross-wall ripple.
RIPPLE_STATES = frozenset({'idle', 'preparing', 'printing', 'finished'})

# ---------------------------------------------------------------- colour maths

def _lerp(a, b, k):
    return a + (b - a) * k


def _mix(c1, c2, k):
    if k <= 0:
        return c1
    if k >= 1:
        return c2
    return (_lerp(c1[0], c2[0], k), _lerp(c1[1], c2[1], k), _lerp(c1[2], c2[2], k))


def _scale(c, k):
    return (c[0] * k, c[1] * k, c[2] * k)


def _bump(distance, radius):
    """Cheap smooth falloff in [0,1]; no exp() in the per-pixel path."""
    if distance >= radius:
        return 0.0
    k = 1.0 - distance / radius
    return k * k


def _sprite(pix, centre, radius, colour, strength, steps=None):
    """Blend a narrow sprite into the frame. Narrow == cheap to transmit.

    ``steps`` snaps the falloff into that many tiers. A tiered glint costs a
    third of the colour runs of a smooth one and, on a 100-LED rope at this
    brightness, is indistinguishable to the eye.
    """
    n = len(pix)
    low = max(0, int(math.floor(centre - radius)))
    high = min(n - 1, int(math.ceil(centre + radius)))
    for i in range(low, high + 1):
        k = _bump(abs(i - centre), radius)
        if steps:
            k = round(k * steps) / steps
        k *= strength
        if k > 0:
            pix[i] = _mix(pix[i], colour, min(1.0, k))


def _block(pix, start, count, colour):
    n = len(pix)
    for i in range(max(0, start), min(n, start + count)):
        pix[i] = colour


# ------------------------------------------------------------------- palettes

DEEP = (0, 26, 80)         # unlit water (idle only; printing uses COL_REST)
MID = (0, 80, 150)
TEAL = (0, 150, 170)
CREST = (110, 255, 255)
PREP = (0, 232, 255)
FILL_LOW = (0, 118, 68)
FILL_HIGH = (0, 196, 124)
WATERLINE = (150, 255, 214)
DROPLET = (120, 220, 255)
SPLASH = (225, 255, 240)

# The original NodeAnimator's printing palette, reproduced exactly. These are
# the colours Adi's wall has always used for a running print: green water
# rising through a red remainder, with a green drop falling into it.
COL_PRINT = (0, 255, 60)     # printed portion / the water
COL_REST = (255, 0, 0)       # the not-yet-printed remainder
COL_DROP = (0, 255, 60)      # raindrop, same green as the water
#: The original splashed the identical green onto the waterline, which is
#: invisible against the water. Keep it green, but brighten it so the landing
#: actually reads as a splash.
COL_SPLASH = (150, 255, 170)
AMBER = (255, 122, 8)
AMBER_MARK = (255, 205, 120)
ANGRY = (255, 38, 0)
ANGRY_MARK = (255, 235, 220)
GREEN = (0, 198, 84)
GREEN_BRIGHT = (170, 255, 200)
SLATE = (120, 100, 215)
GREY = (150, 150, 160)
#: The resting look: nothing to say, so plain cyan. Used for 'idle' and for
#: 'finished' once the rainbow is over. Static, so it costs nothing to hold.
CYAN = (0, 220, 240)


# --------------------------------------------------------------- state scenes

#: Width of the slow swell's coarse bands. A per-pixel slow gradient would
#: re-quantize a dozen boundaries every frame and eat the whole message budget;
#: banding it means the swell costs one or two runs per frame instead.
SWELL_BAND = 10
SWELL_STEPS = 5


def _idle(n, T, wall):
    """The default: solid resting cyan. No motion, so no transport cost.

    Replaced the subdued water on 2026-09-06 at Adi's request: the "nothing"
    state is just cyan, and a finished print settles into the same look after
    its rainbow.
    """
    return [CYAN for _ in range(n)]


def _preparing(n, T, wall):
    """Cyan rising sweep. Only ever shown when telemetry actually says PREPARE.

    Uniform body plus one flat sweep block: two runs change per tick.
    """
    breath = 0.5 + 0.5 * math.sin(T * 0.8 + wall)
    body = _mix((0, 12, 26), (0, 46, 66), 0.25 + 0.35 * breath)
    pix = [body for _ in range(n)]
    span = n + 30
    centre = math.floor(((T * 8.0 + wall * 4.0) % span) - 15)
    _block(pix, centre - 3, 7, PREP)
    return pix


def droplet_timing(travel, speed=DROP_SPEED):
    """``(fall_seconds, cycle_seconds)`` for a droplet crossing ``travel``.

    The cycle must always outlast the fall plus the splash. A fixed two-second
    cycle looked fine at high progress and silently broke at low progress: with
    a 90-position region an empty bucket needs ~3 s of falling, so the droplet
    used to reset in mid-air and never land.
    """
    fall = max(0.0, travel) / speed
    return fall, max(MIN_DROP_CYCLE, fall + SPLASH_SECONDS + DROP_GAP)


def _printing(n, T, percent, marks, still=False):
    """The original 'bucket filling with rain', reproduced.

    Behavioural reference is ``NodeAnimator`` in reference/server.py: the
    printed portion is solid green, the remainder solid red, and while the print
    runs a green drop falls from the top into the water with a brief splash.
    0% is entirely red, 100% entirely green.

    Solid zones are also the cheapest thing this transport can carry -- the base
    is two ``range`` messages and a moving drop is two ``pixel`` messages, which
    is why the original never came close to its message budget.

    ``percent is None`` means the printer says RUNNING but has not told us how
    far along it is. That must not be drawn as 0%, which would be a full red
    bar, so it gets an explicit travelling green marker instead.
    """
    if percent is None:
        pix = [_scale(COL_PRINT, 0.28) for _ in range(n)]
        span = n + 30
        centre = ((T * 30.0) % span) - 15
        _sprite(pix, centre, 6.0, COL_PRINT, 1.0, steps=2)
        return pix

    fill = max(0, min(n, int(round(percent / 100.0 * n))))
    pix = [COL_PRINT if i < fill else COL_REST for i in range(n)]

    # One drop per cycle, falling from the top down into the water. It is only
    # drawn above the waterline, so it never eats into the green fill.
    travel = (n - 1) - fill
    if travel > 2 and not still:
        fall, cycle = droplet_timing(travel)
        phase = T % cycle
        if phase < fall:
            drop = int(round((n - 1) - phase * DROP_SPEED))
            if fill <= drop < n:
                _block(pix, drop, 1, COL_DROP)
        elif phase < fall + SPLASH_SECONDS and fill:
            _block(pix, max(0, fill - 2), 2, COL_SPLASH)
    return pix


def _paused(n, T):
    """Amber breathing plus a stable marker that does not breathe."""
    breath = 0.45 + 0.35 * (0.5 + 0.5 * math.sin(T * 1.2))
    pix = [_scale(AMBER, breath) for _ in range(n)]
    _block(pix, 0, 4, AMBER_MARK)               # steady amber marks at both ends;
    _block(pix, n - 4, 4, AMBER_MARK)           # the body stays a single run
    return pix


def _error(n, T):
    """Orange-red breathing: deeper and slower than pause, never a strobe.

    One uniform run plus two steady end marks. The earlier five 'hot ticks'
    split the body into five runs that all changed every tick, which needed
    ~31 messages/second and tore the rope into random-looking patches.
    """
    breath = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(T * 1.6))
    pix = [_scale(_mix(ANGRY, (255, 92, 12), 0.35 * breath), breath) for _ in range(n)]
    _block(pix, 0, 3, ANGRY_MARK)
    _block(pix, n - 3, 3, ANGRY_MARK)
    return pix


def _finished(n, T, since_complete):
    """Smooth rainbow wash on a genuine completion, then the resting cyan.

    The whole rope carries one hue that turns through the spectrum: one run per
    tick, so it stays smooth under the message budget where a spatial rainbow
    would tear. Over the last RAINBOW_FADE seconds it cross-fades into CYAN so
    the hand-off to the resting state has no visible step.
    """
    if since_complete is not None and 0 <= since_complete < CELEBRATE_SECONDS:
        hue = (since_complete / CELEBRATE_SECONDS) * RAINBOW_CYCLES
        hue = math.floor(hue * RAINBOW_STEPS) / RAINBOW_STEPS
        colour = _hue_rgb(hue)
        remaining = CELEBRATE_SECONDS - since_complete
        if remaining < RAINBOW_FADE:
            colour = _mix(colour, CYAN, 1.0 - remaining / RAINBOW_FADE)
        return [colour for _ in range(n)]
    return [CYAN for _ in range(n)]


def _offline(n, T):
    """Dim slate/purple double-thump heartbeat. Never shows a stale percentage."""
    cycle = 2.4
    phase = T % cycle
    thump = max(_bump(abs(phase - 0.10), 0.22), 0.62 * _bump(abs(phase - 0.44), 0.20))
    # Rest level, not darkness: a powered-off printer used to sit at ~9% of
    # output, which reads as a broken strip rather than a resting one.
    level = 0.45 + 0.55 * thump
    pix = [_scale(SLATE, level) for _ in range(n)]
    _block(pix, n // 2 - 1, 2, _scale(SLATE, min(1.0, 0.65 + 0.35 * level)))
    return pix


def _unknown(n, T):
    """Neutral grey dashes: state absent, and explicitly not 'available'."""
    pulse = 0.62 + 0.20 * (0.5 + 0.5 * math.sin(T * 0.45))
    pix = []
    for i in range(n):
        on = (i % 12) < 4
        pix.append(_scale(GREY, pulse if on else 0.24))
    return pix


def _identify(n, age):
    """Three bounded cyan/white pulses with a sweeping core. Unmistakable."""
    per = IDENTIFY_SECONDS / IDENTIFY_PULSES
    phase = (age % per) / per
    env = math.sin(phase * math.pi) ** 2 if phase < 0.62 else 0.0
    pix = [_scale((0, 210, 235), 0.10 + 0.90 * env) for _ in range(n)]
    if env > 0.05:
        _sprite(pix, phase / 0.62 * (n - 1), 4.0, (255, 255, 255), 1.0)
    return pix


SCENES = {
    'idle': lambda n, T, wall, ctx: _idle(n, T, wall),
    'preparing': lambda n, T, wall, ctx: _preparing(n, T, wall),
    'printing': lambda n, T, wall, ctx: _printing(n, T, ctx['percent'], ctx['marks'],
                                                 ctx['still']),
    'paused': lambda n, T, wall, ctx: _paused(n, T),
    'error': lambda n, T, wall, ctx: _error(n, T),
    'finished': lambda n, T, wall, ctx: _finished(n, T, ctx['since_complete']),
    'offline': lambda n, T, wall, ctx: _offline(n, T),
    'unknown': lambda n, T, wall, ctx: _unknown(n, T),
}


# ---------------------------------------------------------------- composition

def _apply_ripples(pix, events, t, position, state):
    """One brief spatial pulse per genuine event, delayed by physical distance.

    Blended, never substituted: the printing waterline and the filled/remaining
    contrast survive a ripple passing through.
    """
    n = len(pix)
    strength_cap = 0.24 if state == 'printing' else 0.34
    for event in events:
        delay = abs(position - event.get('position', 0)) * RIPPLE_DELAY
        age = t - event.get('at', 0.0) - delay
        if not 0.0 <= age < RIPPLE_SECONDS:
            continue
        envelope = math.sin(age / RIPPLE_SECONDS * math.pi) ** 2
        tint = (40, 235, 155) if event.get('kind') == 'complete' else (60, 155, 255)
        # Uniform per rope: the wall-scale motion comes from the per-bay delay,
        # and a uniform tint keeps the rope's run count unchanged while it passes.
        # A spatial crest re-coloured every pixel and tore under the budget.
        k = envelope * 0.8 * strength_cap
        if k > 0.004:
            for i in range(n):
                pix[i] = _mix(pix[i], tint, k)


def _finalize(pix, opts, state):
    """Brightness, quiet dimming, hardware cap and quantization."""
    k = max(0.0, min(100.0, float(opts['brightness']))) / 100.0
    if state == 'identify':
        k = max(k, IDENTIFY_FLOOR)              # a verification action stays visible
    else:
        if opts['quiet']:
            k *= 0.20
        if state in ('error', 'paused'):
            k = max(k, ALARM_FLOOR)             # alarms survive dimming
    k *= BRIGHT_CAP / 255.0
    out = []
    for colour in pix:
        row = []
        for value in colour:
            v = value * k
            if v <= 0.0:
                row.append(0)
                continue
            v = int(round(v / QUANT)) * QUANT
            # Step down to a whole quantization step rather than clamping to a
            # cap that may not be a multiple of one.
            row.append(v if v <= BRIGHT_CAP else (BRIGHT_CAP // QUANT) * QUANT)
        out.append(row)
    return out


def render_rope(state, percent=None, t=0.0, position=0, settings=None, events=(),
                *, pixels=STATUS_POSITIONS, since_complete=None, identify_age=None):
    """Render the *status region* as ``pixels`` ``[r, g, b]`` rows.

    This is the 90-position zone only; the accent cap is composed separately by
    :func:`compose_rope`. Progress therefore maps to these positions alone.

    Pure: identical arguments always give an identical frame, which is what lets
    the browser play a filmstrip of future frames and still match the wall.
    """
    if state not in SCENES:
        state = 'unknown'
    n = int(pixels)
    if n < 1:
        raise ValueError('A rope needs at least one pixel')
    opts = merge_settings(settings)

    if identify_age is not None:
        if not 0.0 <= identify_age < IDENTIFY_SECONDS:
            identify_age = min(max(identify_age, 0.0), IDENTIFY_SECONDS - 1e-6)
        return _finalize(_identify(n, identify_age), opts, 'identify')

    still = bool(opts['reduced_motion'])
    T = 0.0 if still else t * float(opts['speed'])
    wall = position * 0.85
    ctx = {
        'percent': percent,
        'marks': bool(opts['waterline_marks']),
        'since_complete': None if still else since_complete,
        'still': still,
    }
    pix = SCENES[state](n, T, wall, ctx)
    if opts['ripples'] and not still and state in RIPPLE_STATES and events:
        _apply_ripples(pix, events, t, position, state)
    return _finalize(pix, opts, state)


# Backwards-compatible positional alias used by the original core API.
def render(state, percent, t, position, settings=None, events=()):
    return render_rope(state, percent, t, position, settings, events)


def blank(pixels=PIXELS):
    return [[0, 0, 0] for _ in range(pixels)]


# ------------------------------------------------------------- accent cap

def _hue_rgb(hue):
    """Fully saturated, full-value RGB for ``hue`` in [0, 1). No colorsys import."""
    sector = (hue % 1.0) * 6.0
    index = int(sector)
    rise = sector - index
    fall = 1.0 - rise
    table = [(1.0, rise, 0.0), (fall, 1.0, 0.0), (0.0, 1.0, rise),
             (0.0, fall, 1.0), (rise, 0.0, 1.0), (1.0, 0.0, fall)]
    r, g, b = table[index % 6]
    return (r * 255.0, g * 255.0, b * 255.0)


def _finalize_accent(pix, level):
    """Cap and quantize the accent.

    Deliberately separate from the status finalizer: no alarm floor, no wall
    brightness slider and no state tinting reach the cap. Equal input channels
    stay exactly equal, which is what makes 'white' neutral white.
    """
    level = max(0.0, min(1.0, level)) * (BRIGHT_CAP / 255.0)
    out = []
    for colour in pix:
        row = []
        for value in colour:
            v = value * level
            if v <= 0.0:
                row.append(0)
                continue
            v = int(round(v / QUANT)) * QUANT
            # Step down to a whole quantization step rather than clamping to a
            # cap that may not be a multiple of one.
            row.append(v if v <= BRIGHT_CAP else (BRIGHT_CAP // QUANT) * QUANT)
        out.append(row)
    return out


def render_accent(accent=None, t=0.0, position=0, *, positions=ACCENT_POSITIONS,
                  quiet=False, still=False):
    """Render the fixed far-end cap. Never depends on printer state.

    ``still`` (reduced motion) freezes the rainbow; white and colour caps are
    static already, so they cost nothing after their first paint.
    """
    cfg = merge_accent(accent)
    level = max(0.0, min(100.0, float(cfg['brightness']))) / 100.0
    if quiet:
        level *= ACCENT_QUIET_SCALE

    mode = cfg['mode']
    if mode == 'rainbow':
        # Stepped so a slow drift costs a bounded number of messages, and offset
        # per bay so the seven caps read as one rainbow across the wall.
        turns = 0.0 if still else t / RAINBOW_PERIOD
        hue = turns + position / 7.0
        hue = math.floor(hue * RAINBOW_STEPS) / RAINBOW_STEPS
        base = _hue_rgb(hue)
    elif mode == 'color':
        base = tuple(float(c) for c in cfg['color'])
    else:
        base = (255.0, 255.0, 255.0)
    return _finalize_accent([base] * int(positions), level)


def compose_rope(status, accent, reverse=False):
    """Join the two zones into one physical frame.

    ``status`` is in *logical* order (index 0 = where progress starts).
    ``reverse`` flips it inside its own 90 positions; the accent is appended at
    physical 90-99 either way, because its location is fixed relative to the
    wire, not to the direction the bar happens to fill.
    """
    body = list(reversed(status)) if reverse else list(status)
    return [list(pixel) for pixel in body] + [list(pixel) for pixel in accent]
