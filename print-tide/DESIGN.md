# Design

## The three identities

Installers mismatch ropes because one word ("printer 3") is doing three jobs.
The studio separates them and never conflates them again:

| Identity | Owner | Changes when |
| --- | --- | --- |
| `printer1`…`printer7` | the host telemetry service | never |
| `node02`…`node08` | the rope controller | never (it is the hardware) |
| list position 0…6 | the physical wall | you rearrange the bays |

Swapping which printer drives a rope is a `printer` change. Rearranging the wall
is an order change. Reversing a rope is a per-rope flag. All three are
independent, which is why a mismatch can be fixed in the browser instead of on a
ladder.

## Three zones, composed independently

A rope is not one strip of meaning. Since 2026-09-14 it is a 30-position
*inactive foot* at the data-in wire (the bottom of the rope on this wall,
which Adi asked to have ignored), a 60-position *active region* above it, and
a fixed 10-position *accent cap* at the far end.

The foot is handled the same way as the cap: by construction rather than by
convention. `render_rope` renders 60 positions and does not know the foot
exists; `compose_rope` prepends 30 dark pixels and then runs `mask_inactive`
over the finished frame as the last word, so even a wrong-length status list
cannot light physical 0–29. Progress is remapped to the 60 (0/25/50/75/100 % =
0/15/30/45/60) rather than blacked out of a 90-position bar, so the percentage
still means what it says. The UI draws the foot as an unlit part of the object
with an `OFF 30%` label, because a strip that appears to start a third of the
way up would otherwise read as broken.

The cap is a fixture, not a state indicator. That is why it is composed by a
separate function (`render_accent`) with its own brightness, and joined to the
status frame by `compose_rope` only at the very end. There is no code path by
which a state, an event, a celebration, a ripple or Identify can write to
physical 90-99 or 0-29, because nothing in the status layer even knows those
positions exist — `render_rope` returns 60 pixels.

This also settles the direction question cleanly. `reverse` is applied inside
`compose_rope` to the status list alone, so the cap's physical location is
defined relative to the wire and cannot follow the bar around. The alternative —
reversing the whole 100-position frame and then patching the cap back — would
have worked but would have made "which end is the cap on?" a property of a
boolean rather than of the geometry.

Progress is recomputed over the 60 active positions rather than 100, so the
percentage means what it says; neither the foot nor the cap is silently part of
every bar.

## Layering

```
model.py      snapshot -> sanitized state      (no I/O, no time source)
layout.py     schema, validation, persistence  (no rendering)
renderer.py   state -> pixels                  (pure, deterministic)
transport.py  pixels -> firmware messages      (budget, diffing, encoding)
studio.py     clock, lifecycle, ONE writer
web.py        HTTP/UI
core.py       frozen façade for the host
```

The seam that matters is `renderer.py`. It is a pure function of
`(state, percent, t, position, settings, events)`. That single property buys:

* **No duplicate animation implementation.** The browser does not animate
  printer state; it plays frames the Python renderer produced.
* **A cheap smooth UI.** Because the renderer is pure, the server can compute a
  second of *future* frames on request. The browser fetches a run-length encoded
  filmstrip about once a second and plays it at 12 fps on its own clock. One
  request per second buys motion that a 1 Hz status poll could never show.
* **Testability.** Ripple ordering, direction, alarm floors and celebration
  bounds are ordinary assertions on returned lists.

## Designing animations for a 24-message-per-second rope

This is the central constraint and it shaped every pattern.

The firmware takes `fill`/`range`/`pixel`/`off` only. A frame therefore costs
roughly *the number of contiguous colour runs that changed*, not its beauty. A
naive full-strip moving gradient re-quantizes a dozen boundaries every frame and
consumes an entire rope's budget for one frame of motion.

So every state is composed as **a slow broad layer plus narrow fast sprites**:

* The broad layer is either static (idle's depth gradient, printing's filled
  region) or moves in coarse 10-pixel bands with a quantized amplitude, so
  quantization holds it still for many frames and it costs ~1–2 runs.
* The motion the eye actually follows is carried by narrow sprites: two water
  crests, one cyan sweep, a falling droplet, a splash. A sprite's falloff is
  snapped to three tiers, which is invisible at this brightness and thirds its
  run count.
* Uniform states are nearly free: paused and error breathe as a solid colour
  broken only by *static* markers, so a whole breathing frame is three or four
  messages.

Measured by `tests/test_renderer.py`: every state stays under 34 colour runs per
frame and under 16 changed runs between consecutive frames — inside the 8
messages/rope/tick allowance often enough to look continuous.

When the budget does bind, `plan_updates` sends the highest-contrast differences
first, so a limited rope looks *simplified* rather than torn, and every fourth
pass falls back to plain left-to-right order so a low-contrast region can never
be starved. Skipped runs stay dirty and are re-offered.

The accent cap is designed to the same rule. White and solid colour are static,
so after the first paint they cost nothing at all. Rainbow is a **uniform** hue
across the ten positions — one contiguous run, therefore one message — stepped
72 times over 24 seconds, and deliberately not tied to the wall speed slider so
its cost cannot be multiplied by a UI control. A per-position rainbow gradient
would be prettier in a screenshot and would cost ten messages per frame per
rope; it is not offered.

### The smoothness rule (added 2026-09-06)

A rope gets `NODE_RATE` (24) messages/second and the coordinator ticks at 8 Hz,
so **a scene may change at most ~3 colour runs per tick on average**. Exceed it
and the planner applies only part of each frame; the rope tears into a patchwork
that reads as "random colours" (seen live on the error scene, 31 msg/s, and
measured at 77 msg/s for the old banded idle water). Every body that breathes or
tints must therefore be a *single run*, markers sit at the rope ends so they do
not split the body, sprites snap to whole pixels, and ripples are a uniform wash
per rope whose wall-scale motion comes from the per-bay delay alone.
`tests/test_renderer.py::test_every_state_stays_under_the_sustained_message_budget`
enforces it (mean ≤ 2.25 changed runs/tick over 160 ticks, max ≤ 8).

## Truth rules

The wall is only useful if it is trustworthy, so several attractive features are
deliberately refused:

* **Celebrations require a witnessed transition.** Both observations fresh, no
  gap in our own observation, and not a row restored from disk. A restart cannot
  celebrate, however plausible the story is. `lifecycle.json` therefore exists to
  *suppress*, never to permit.
* **"Finished (seen)" is bound to the job it watched.** `observed_complete`
  records the job name alongside the time, and is retired by a changed job name,
  by any interval we did not observe, and by a restart — it is not persisted at
  all, because a restart is by definition an unwatched interval. The annotation
  is deliberately fragile: losing a "(seen)" label costs nothing, while showing
  an old completion time against a different retained job is a lie. Job names
  are not unique IDs, so equality of names narrows the claim but never
  establishes it.
* **Freshness is an input, never inferred.** Link state, timestamp presence,
  staleness and clock skew are four distinct named reasons, all of which produce
  `offline` with no percentage, ETA or temperature.
* **Unknown is a state.** An unrecognised `gcode_state` renders as grey dashes,
  not as available.
* **Enqueue is not delivery.** Identify reports "queued to the broker; confirm by
  watching the rope".

## Alternatives considered

* **A separate lighting process.** Rejected: two processes writing the same
  ropes needs a lease protocol, and the telemetry already lives in the host. One
  writer inside the existing service is simpler and safer.
* **Firmware-native effects.** Smoother and frame-locked, but it needs a
  coordinated reflash of seven boards on a MAC-gated network. Out of scope here.
* **A JavaScript renderer.** Would give free 60 fps in the browser and a
  permanent, unfixable disagreement with the wall. Rejected outright.
* **Sending 700 pixel messages per frame.** Would work for about a second.

---

# Roadmap

## Built now

> Built and tested hardware-free. **None of it is driving the physical wall**:
> the live host was rolled back to the original renderer on 2026-09-05 and this
> package is a preview pending Adi's review and a bounded physical trial.

- Seven-bay mapping with drag swap, menu swap, bay reordering, per-rope
  direction, editable display names, draft/live separation with a pending-change
  summary, Save & apply, Discard, Undo, Restore original.
- Independent 10-position far-end accent cap per rope: white / solid colour /
  rainbow, own brightness, apply-to-all, persisted with the layout and migrated
  from older schemas.
- Guided "walk the wall" installation flow driven by physical Identify.
- Bounded five-second Identify that restores itself server-side.
- Eight distinct state animations on a shared clock, cross-wall idle wave and
  event ripples with distance-based propagation delay.
- Per-bay animation lab with scenarios; preview cannot publish.
- Collection acknowledgement, cleared by a new observed job.
- Atomic, versioned persistence with revision conflict detection.
- Budgeted single-writer transport with priority scheduling, fairness passes and
  periodic resync.

## Next, roughly in value order

0. **A side-by-side smoothness trial against the original renderer.** This is
   the gating item, not a feature. The provisional build was rolled back for
   looking worse on hardware; until someone watches this one on the wall, its
   headline claim is unproven.
1. ~~**Rope health from the retained `ledwall/+/status` topics.**~~ Done
   2026-09-06: host subscribes to `ledwall/+/status` and `ledwall/+/ack`, feeds
   `Studio.observe_node`; per-bay receipt words, footer summary, Identify reports
   a controller answer, offline→online forces a repaint. Tests in
   `tests/test_health.py`.
2. **Cool-down indicator after a print.** Bed temperature is already in the
   snapshot. Show it falling on the collect signal, with a user-set threshold.
   Must not claim a part is safe to touch.
3. **Find my print.** A QR or Discord link that opens the studio focused on one
   bay with Identify ready. Reuses existing aliases and authorization.
4. **Pickup reminders and claims.** A light local claim/collected workflow. A
   claimed printer is never started, paused or controlled.
5. **Scheduled quiet hours.** Time-based dimming that preserves alarm floors.
   Manual quiet mode already exists.
6. **Richer fault context.** Preserve raw error/HMS codes and AMS data in the
   collector *before* displaying filament runout or material readiness.
   `has_error` alone identifies no cause and must not be dressed up as one.
7. **Firmware-native wave engine.** Timestamped effects and hardware-level
   restore for genuinely frame-locked motion — a separate, separately tested
   firmware project.

Existing cameras and timelapse tooling can be linked from a bay later; reuse
those caches and permissions rather than opening seven more streams for this UI.
