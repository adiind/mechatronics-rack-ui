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

# Everything on the ropes is a shade of pink (Adi, 2026-09-13: "REPLACE EVERY
# COLOR WITH A SHADE OF PINK" ... "I DONT WANT WHITE, JUST SHADES OF PINK").
# No near-white or pastel either: every colour keeps green at or below 60% of
# red and blue at or above 40% of red, so it reads as pink and nothing else
# (see is_pink). States are told apart by lightness, saturation and motion
# instead of hue: the printed water is a bright hot pink, the remainder bands
# darken towards the top, ready-to-collect is the lightest pink and static,
# idle is a static dusty rose, paused breathes a light pink, error breathes the
# deepest most saturated fuchsia, stopped-early is a static deep plum, offline
# a dim mauve heartbeat, unknown washed-out pink dashes.

#: The lightest pink allowed anywhere on the wall.
LIGHT_PINK = (255, 150, 205)

# The printing palette: bright hot-pink water, SHADE_BANDS bands of deeper
# pink darkening step by step towards the top, a light-pink drop that reads
# against every band. Banded rather than a smooth gradient so the base stays
# a handful of runs; the darkest shade is kept well clear of the "reads as
# off" floor.
COL_PRINT = (255, 95, 175)   # printed portion / the water: bright hot pink
COL_REST = (235, 55, 150)    # remainder at the waterline: deep pink
COL_DEEP_PINK = (90, 25, 60) # remainder at the top: darkest pink
SHADE_BANDS = 6
COL_DROP = LIGHT_PINK        # raindrop: the lightest pink, visible on every band
COL_SPLASH = (255, 135, 200)
#: Preparing: a dark pink body with a light-pink sweep rising through it.
PREP_LOW = (48, 14, 34)
PREP_HIGH = (104, 30, 72)
PREP = LIGHT_PINK
#: Paused: breathing light pink with steady deep-pink marks at both ends.
AMBER = (255, 125, 195)
AMBER_MARK = (230, 30, 130)
#: Error: the deepest, most saturated pink on the wall, breathing. Almost no
#: green so it is the only "hot" colour; nothing healthy comes near it.
ANGRY = (255, 15, 110)
ANGRY_MARK = (255, 95, 175)
#: Ready to collect: the lightest pink, static, held until the door opens or
#: the bay is marked collected.
GREEN = LIGHT_PINK
GREEN_BRIGHT = LIGHT_PINK
#: Stopped early: static deep plum pink, dimmer at both ends.
MAGENTA = (165, 45, 105)
#: Offline heartbeat: dim mauve pink.
SLATE = (150, 70, 125)
#: Unknown: washed-out pink dashes.
GREY = (170, 95, 140)
#: The resting look for 'idle': a static dusty rose.
CYAN = (190, 70, 130)
#: Identify: a hot pink body pulsing with a light-pink core.
IDENT_BODY = (255, 95, 175)
IDENT_CORE = LIGHT_PINK
#: Ripple tints: the light pink for a completion, a deep rose for anything
#: else. Both sit far enough from the water to survive quantization at the
#: default brightness, so a ripple is always visible on a printing rope.
RIPPLE_COMPLETE = LIGHT_PINK
RIPPLE_OTHER = (200, 40, 130)
#: The "rainbow" accent and the completion wash stay inside the pink hues:
#: from magenta (hue 5/6, where red and blue are level) to a rose that still
#: keeps blue well above 40% of red, swept back and forth so there is no
#: jump, and softened towards the water pink so it reads as pink, not neon.
PINK_HUE_LOW = 0.84
PINK_HUE_HIGH = 0.93
PINK_HUE_SOFTEN = 0.30


# --------------------------------------------------------------- state scenes

#: Width of the slow swell's coarse bands. A per-pixel slow gradient would
#: re-quantize a dozen boundaries every frame and eat the whole message budget;
#: banding it means the swell costs one or two runs per frame instead.
SWELL_BAND = 10
SWELL_STEPS = 5


def _idle(n, T, wall):
    """The default: a solid resting rose. No motion, so no transport cost.

    The "nothing" state is one static colour (Adi, 2026-09-06), pink like
    everything else since 2026-09-13.
    """
    return [CYAN for _ in range(n)]


def _preparing(n, T, wall):
    """Light-pink sweep rising through a dark pink body. Only ever shown when
    telemetry actually says PREPARE.

    Uniform body plus one flat sweep block: two runs change per tick.
    """
    breath = 0.5 + 0.5 * math.sin(T * 0.8 + wall)
    body = _mix(PREP_LOW, PREP_HIGH, 0.25 + 0.35 * breath)
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


def shade(band):
    """Colour of pink band ``band`` (0 = at the waterline, SHADE_BANDS-1 = top)."""
    band = max(0, min(SHADE_BANDS - 1, band))
    return _mix(COL_REST, COL_DEEP_PINK, band / (SHADE_BANDS - 1))


def _shades(pix, fill):
    """Paint the remainder above ``fill`` as SHADE_BANDS equal bands of pink.

    Band boundaries are relative to the waterline so the shade nearest the water
    is always the hot pink and the far end is always the darkest, whatever the
    percentage. They only move when the percent does, so between updates the
    whole base is free on the wire.
    """
    n = len(pix)
    rest = n - fill
    if rest <= 0:
        return
    for i in range(fill, n):
        band = min(SHADE_BANDS - 1, (i - fill) * SHADE_BANDS // rest)
        pix[i] = shade(band)


def _printing(n, T, percent, marks, still=False):
    """The original 'bucket filling with rain', reproduced.

    Behavioural reference is ``NodeAnimator`` in reference/server.py: the
    printed portion is solid hot pink, the remainder deep pink at the
    waterline stepping through SHADE_BANDS progressively darker pinks towards
    the top, and while the print runs a light-pink drop falls from the top into
    the water with a brief splash. 0% is entirely shaded pink, 100% entirely
    hot pink. Red is reserved for errors so a healthy print can never be mistaken
    for one.

    Solid zones are the cheapest thing this transport can carry -- the base is
    one ``range`` for the water plus one per band, all of which hold still
    between percent changes, and a moving drop is two ``pixel`` messages.

    ``percent is None`` means the printer says RUNNING but has not told us how
    far along it is. That must not be drawn as 0%, which would be a full blue
    bar, so it gets an explicit travelling light-pink marker instead.
    """
    if percent is None:
        pix = [_scale(COL_PRINT, 0.28) for _ in range(n)]
        span = n + 30
        centre = ((T * 30.0) % span) - 15
        _sprite(pix, centre, 6.0, COL_PRINT, 1.0, steps=2)
        return pix

    fill = max(0, min(n, int(round(percent / 100.0 * n))))
    pix = [COL_PRINT for _ in range(n)]
    _shades(pix, fill)

    # One drop per cycle, falling from the top down into the water. It is only
    # drawn above the waterline, so it never eats into the water.
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
    """Light-pink breathing plus stable deep-pink marks that do not breathe."""
    breath = 0.45 + 0.35 * (0.5 + 0.5 * math.sin(T * 1.2))
    pix = [_scale(AMBER, breath) for _ in range(n)]
    _block(pix, 0, 4, AMBER_MARK)               # steady amber marks at both ends;
    _block(pix, n - 4, 4, AMBER_MARK)           # the body stays a single run
    return pix


def _error(n, T):
    """Deep fuchsia breathing: deeper and faster than pause, never a strobe.

    The most saturated pink on the wall with almost no green, so it cannot be
    confused with the light breathing pause or with anything a healthy print
    shows (hot-pink fill, softer pink remainder).

    One uniform run plus two steady end marks. The earlier five 'hot ticks'
    split the body into five runs that all changed every tick, which needed
    ~31 messages/second and tore the rope into random-looking patches.
    """
    breath = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(T * 1.6))
    pix = [_scale(ANGRY, breath) for _ in range(n)]
    _block(pix, 0, 3, ANGRY_MARK)
    _block(pix, n - 3, 3, ANGRY_MARK)
    return pix


def _finished(n, T, since_complete):
    """Smooth pink wash on a genuine completion, then the steady collect pink.

    The whole rope carries one hue that sweeps back and forth through the pink
    range: one run per tick, so it stays smooth under the message budget where
    a spatial gradient would tear. Over the last RAINBOW_FADE seconds it
    cross-fades into the light collect pink so the hand-off has no visible
    step. That holds until the door is opened or the bay is marked collected,
    at which point the studio reports the bay as idle.
    """
    if since_complete is not None and 0 <= since_complete < CELEBRATE_SECONDS:
        hue = (since_complete / CELEBRATE_SECONDS) * RAINBOW_CYCLES
        hue = math.floor(hue * RAINBOW_STEPS) / RAINBOW_STEPS
        colour = _pink_hue_rgb(hue)
        remaining = CELEBRATE_SECONDS - since_complete
        if remaining < RAINBOW_FADE:
            colour = _mix(colour, GREEN, 1.0 - remaining / RAINBOW_FADE)
        return [colour for _ in range(n)]
    return [GREEN for _ in range(n)]


def _stopped(n, T):
    """Steady deep plum: the print stopped early and the bed still needs clearing.

    Bambu keeps ``gcode_state=FAILED`` after a cancel or a dismissed failure
    until the next job starts. This is that window. One static run, so it costs
    nothing to hold; darker than idle, lighter than nothing else static, and it
    never breathes, so it is not an error, a pause or a completion.
    """
    pix = [MAGENTA for _ in range(n)]
    _block(pix, 0, 3, _scale(MAGENTA, 0.45))
    _block(pix, n - 3, 3, _scale(MAGENTA, 0.45))
    return pix


def _offline(n, T):
    """Dim mauve double-thump heartbeat. Never shows a stale percentage."""
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
    """Pale greyish-pink dashes: state absent, and explicitly not 'available'."""
    pulse = 0.62 + 0.20 * (0.5 + 0.5 * math.sin(T * 0.45))
    pix = []
    for i in range(n):
        on = (i % 12) < 4
        pix.append(_scale(GREY, pulse if on else 0.24))
    return pix


def _identify(n, age):
    """Three bounded pink pulses with a sweeping light-pink core. Unmistakable."""
    per = IDENTIFY_SECONDS / IDENTIFY_PULSES
    phase = (age % per) / per
    env = math.sin(phase * math.pi) ** 2 if phase < 0.62 else 0.0
    pix = [_scale(IDENT_BODY, 0.10 + 0.90 * env) for _ in range(n)]
    if env > 0.05:
        _sprite(pix, phase / 0.62 * (n - 1), 4.0, IDENT_CORE, 1.0)
    return pix


SCENES = {
    'idle': lambda n, T, wall, ctx: _idle(n, T, wall),
    'preparing': lambda n, T, wall, ctx: _preparing(n, T, wall),
    'printing': lambda n, T, wall, ctx: _printing(n, T, ctx['percent'], ctx['marks'],
                                                 ctx['still']),
    'paused': lambda n, T, wall, ctx: _paused(n, T),
    'error': lambda n, T, wall, ctx: _error(n, T),
    'finished': lambda n, T, wall, ctx: _finished(n, T, ctx['since_complete']),
    'stopped': lambda n, T, wall, ctx: _stopped(n, T),
    'offline': lambda n, T, wall, ctx: _offline(n, T),
    'unknown': lambda n, T, wall, ctx: _unknown(n, T),
}


# ---------------------------------------------------------------- composition

#: Green-to-red ratios bounding the water: the deeper shades above it sit
#: below the floor, the light-pink drop, splash and collect pink above the
#: ceiling. Ratios, not levels, so the test holds at any brightness.
WATER_RATIO_FLOOR = (COL_PRINT[1] / COL_PRINT[0] + COL_REST[1] / COL_REST[0]) / 2
WATER_RATIO_CEILING = (COL_PRINT[1] / COL_PRINT[0] + COL_DROP[1] / COL_DROP[0]) / 2


def is_water(pixel):
    """True for a pixel of the printed water, false for any shade above it."""
    r, g, _ = pixel
    return r > 0 and WATER_RATIO_FLOOR <= g / r <= WATER_RATIO_CEILING


#: Bounds that make a colour "a shade of pink and nothing else": green no more
#: than this fraction of red (above it the colour goes pastel, then white) and
#: blue no less than this fraction of red (below it the colour goes red).
PINK_MAX_GREEN = 0.60
PINK_MIN_BLUE = 0.40


def is_pink(pixel):
    """A shade of pink: red-led, blue over green, and neither whitish nor red.

    Rejects white, pastel and grey (too much green), red and orange (too little
    blue), yellow, green, cyan, blue and violet (not red-led with blue over
    green).
    """
    r, g, b = pixel
    return (r > 0 and r >= b > g
            and g <= r * PINK_MAX_GREEN and b >= r * PINK_MIN_BLUE)


def _water_span(pix):
    """Length of the leading water run of a frame."""
    span = 0
    for pixel in pix:
        if not is_water(pixel):
            break
        span += 1
    return span


def _apply_ripples(pix, events, t, position, state):
    """One brief spatial pulse per genuine event, delayed by physical distance.

    Blended, never substituted: the printing waterline and the filled/remaining
    contrast survive a ripple passing through.
    """
    n = len(pix)
    strength_cap = 0.30 if state == 'printing' else 0.34
    for event in events:
        delay = abs(position - event.get('position', 0)) * RIPPLE_DELAY
        age = t - event.get('at', 0.0) - delay
        if not 0.0 <= age < RIPPLE_SECONDS:
            continue
        envelope = math.sin(age / RIPPLE_SECONDS * math.pi) ** 2
        tint = RIPPLE_COMPLETE if event.get('kind') == 'complete' else RIPPLE_OTHER
        # Uniform per rope: the wall-scale motion comes from the per-bay delay,
        # and a uniform tint keeps the rope's run count unchanged while it passes.
        # A spatial crest re-coloured every pixel and tore under the budget.
        k = envelope * 0.8 * strength_cap
        if k > 0.004:
            # On a printing rope only the water takes the tint: re-tinting
            # every pink band as well would change SHADE_BANDS+1 runs per tick for
            # the whole ripple and tear the rope under the budget.
            span = _water_span(pix) if state == 'printing' else n
            for i in range(span):
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


def _pink_hue_rgb(cycle):
    """Fully saturated RGB for a point in the pink sweep.

    ``cycle`` in [0, 1) is one full back-and-forth through the pink hues: a
    cosine sweep from PINK_HUE_LOW up to PINK_HUE_HIGH and back, so the colour
    never jumps when the cycle wraps.
    """
    k = 0.5 - 0.5 * math.cos((cycle % 1.0) * 2.0 * math.pi)
    pure = _hue_rgb(PINK_HUE_LOW + (PINK_HUE_HIGH - PINK_HUE_LOW) * k)
    return _mix(pure, COL_PRINT, PINK_HUE_SOFTEN)


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
        base = _pink_hue_rgb(hue)
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
