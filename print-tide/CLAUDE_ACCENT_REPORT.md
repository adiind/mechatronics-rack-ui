# Claude accent report — independent far-end cap

**Author:** Claude (Pi), 2026-09-05, after `LIVE_ROLLBACK_NOTICE.md`.
**Request:** *"about 10 led from the end of the led - opposite of where wire
connects - those should just be pure white all the time, actually not just white
give me option to select a color / turn on rainbow mode for those led, and then
rest can behave the way we were planning."*

Written vs. executed: I **wrote** all code and docs below and **executed** only
`python3 -m unittest discover -s tests -v`. No browser, no broker, no printer, no
firmware, no service restart, no host edit.

---

## Deployment status — read this first

**This is a preview build. It is not driving the wall, and a restart will not
change that.**

On 2026-09-05 at 22:45 CDT Codex restored the original
`/home/edi/printer-led-mcp/server.py` from
`server.py.pre-print-tide-20260905-214912` and restarted the service, after Adi
reported the live animations looked much worse than the earlier smooth renderer.
The live host no longer imports `light_studio`; port 8772 is closed.

Corrections I made to my own earlier documentation, which was written before the
rollback and had become wrong:

* `CLAUDE_BUILD_REPORT.md` said **"a service restart is sufficient"**. That is
  now false and is marked as superseded, with the original text kept in a
  collapsed block for the record.
* `README.md`'s Tailscale URL and "Rollback" section have been replaced with a
  status banner and a "Current deployment state, and what a trial would involve"
  section.
* `AGENTS.md` and `DESIGN.md` now carry the preview-only status.

`deployment/install-receipt.json` still reads `"status": "installed"`, but Codex
recorded the reversal alongside it in `deployment/rollback-receipt.json`
(22:45:09 CDT, restored SHA256 `d0a24c0d…`), so the pair reads correctly in
sequence. I left both untouched as history. The only residual risk is someone
reading the install receipt on its own.

The degraded renderer Adi saw was Codex's provisional build, not mine — but that
does not make mine approved. **My renderer's smoothness on real ropes is
unverified.** Matching the original renderer's smoothness is the acceptance
criterion, and only eyes on the wall can settle it.

---

## What I built

### The zone split

A rope is 100 *addressable positions* (WS2811 modules, not dies):

```
physical 0 .......................... 89 | 90 ............ 99
^ data-in wire                           |                 ^ far end
<------------ status region (90) ------->|<-- accent cap -->
```

The structural decision that makes the guarantee hold: **`render_rope` now
returns 90 pixels and has no concept of positions 90-99.** The cap is rendered
by a separate `render_accent`, and the two are joined only at the end by
`compose_rope`. There is no code path by which a state, an event, a ripple, a
celebration or Identify can write into the cap, because the status layer cannot
address it. That is enforced structurally rather than by a rule someone has to
remember.

`reverse` is applied inside `compose_rope` to the status list alone, so the
cap's location is a property of the geometry rather than of a boolean. It cannot
follow the bar to the wire end.

Progress is recomputed across 90 positions: 0/50/100% is 0/45/90. The cap is not
silently 10% of every bar.

### Accent modes

Per rope, persisted with the layout:

| Mode | Behaviour |
| --- | --- |
| **Pure white** (default) | R=G=B, no tint, no breathing, no state influence |
| **Solid colour** | any RGB from a native colour picker |
| **Rainbow** | uniform hue across the ten positions, stepped in time |

Plus an independent **cap brightness** (0-100% of the same physical ceiling) and
an **Apply to all ropes** action in two places: inside each bay card, and in the
lab's wall-wide cap panel.

### Two judgement calls worth flagging

**1. Cap brightness is independent of the wall brightness slider, but quiet mode
still dims it (to 45%).** Dimming the animation should not dim a fixture that is
meant to be always-on — but "quiet hours" with seventy white LEDs at full output
would not be quiet. If Adi wants the caps fully exempt from quiet mode, that is
one constant (`ACCENT_QUIET_SCALE` in `renderer.py`).

**2. Rainbow is uniform, not a spatial gradient.** The handoff offered either; I
took the low-cost default. Ten positions × a moving gradient is ten messages per
frame per rope. A uniform hue is one contiguous run, therefore **one message**,
and I stepped it (72 steps over 24 s ⇒ at most 3 messages/second/rope) and
deliberately did *not* tie it to the wall speed slider, so no UI control can
multiply its cost. Static caps cost nothing after their first paint beyond the
ordinary 12-second resync. With all seven ropes on rainbow the caps add roughly
21 messages/second against the 140/second aggregate budget.

I will not claim this looks smooth on hardware. It is a stepped hue drift by
design, and a browser canvas cannot tell you how a WS2811 rope reads.

### Schema 3 and migration

`accent` is optional on input and always present on output, so a schema-1 or
schema-2 file migrates by gaining default white caps and keeps its mapping,
labels, direction, settings, revision and undo history. Validation rejects
unknown modes, unknown keys, wrong channel counts, out-of-range channels, and —
specifically — `True` as a colour channel and `NaN` as a brightness.

The cap belongs to the **rope**, not the printer: swapping which printer drives
a rope leaves the cap alone, while reordering bays moves the cap with its rope,
because the rope itself moved.

### UI

Bay cards gain a collapsible *Far-end cap* panel whose summary always states the
current mode without opening. Both canvases draw a dashed zone boundary and
`WIRE` / `CAP` end labels, with physical position 0 always at the bottom —
the one orientation we actually know. The card line reads "90 status + 10 cap ·
fills away from the wire" (or "toward"), described relative to the wire rather
than to "top"/"bottom", which we cannot know. Cap edits flow through the single
draft, appear in the pending-change summary ("2 far-end caps"), and are covered
by Save & apply, Discard and Undo. The lab previews **draft** caps and
directions through the same Python compositor the hardware path uses.

---

## The two issues from Codex's read-through

**1. The droplet never landed at low progress — confirmed and fixed.**
`_printing` used a fixed 2-second cycle while the fall took `travel / 30`
seconds. With a 90-position region an empty bucket needs ~2.97 s of falling, so
below roughly 40% progress the droplet reset in mid-air and no splash ever
happened. `droplet_timing` now derives the cycle from the fall
(`max(2.0, fall + splash + gap)`), and a drop at 0% now lands on the floor
rather than being suppressed by an `and fill` guard.

**2. `observed_complete` could be shown against the wrong job — confirmed and
fixed.** It stored only a timestamp, so a completion witnessed for job A could
survive a disconnected interval and be displayed as the age of a later retained
FINISH for job B. It now stores `{at, job}` and is retired by a changed job name,
by *any* interval we did not observe, and by a restart. I also **stopped
persisting it entirely** — a restart is by definition an unwatched interval, so a
persisted value could never be trusted, and keeping the field would have been
false comfort. `lifecycle.json` is now version 2 and holds only the diagnostic
last-seen record; an older file containing `observed_complete` is ignored, which
is covered by a test.

The annotation is deliberately fragile. Losing a "(seen)" label costs nothing;
showing an old completion time against a different job is a lie.

---

## Test evidence

```
$ python3 -m unittest discover -s tests -v
Ran 253 tests in 12.8s
OK
```

Executed on this Pi at the end of the pass. All hardware-free: fake clock, fake
publisher, temp directories, loopback sockets. 192 → 253 tests.

**Both bug fixes were verified by reverting them and confirming the new tests
fail**, rather than by assuming:

* `droplet_timing` forced back to a fixed 2 s cycle →
  `test_the_droplet_cycle_always_outlasts_the_fall_and_the_splash` fails
  (`2.0 not >= 2.027`) and
  `test_a_droplet_reaches_the_waterline_and_splashes_at_every_progress` fails
  with *"no splash reached the waterline at 0%"*.
* Job-association check disabled →
  `test_an_observed_completion_is_bound_to_the_job_it_watched` fails.

Both fixes were then restored and the suite is green.

| Module | Tests | Accent-relevant coverage added |
| --- | --- | --- |
| `test_accent.py` | 47 | zone geometry; compose puts the cap at the far end **in both directions**; white channels exactly equal at every brightness; colour reproduced and capped; brightness 0 dark; ceiling/quantization for every mode; cap brightness independent of the wall slider; quiet dims but keeps it lit; rainbow moves, is uniform across the ten positions, is stepped inside the budget, covers the hue circle, is offset per bay, freezes under reduced motion; static caps provably static; mode/colour/brightness/unknown-key validation incl. bool and NaN channels; schema-1 and schema-2 migration preserving mapping/labels/direction/settings/revision **and undo**; every printer state and Identify preserving the cap; celebrations and ripples never reaching it; a dark wall still lighting it; progress mapping to 0/45/90; per-rope vs all-rope caps; swapping printers not moving a cap; static caps costing only their resync; rainbow costing measurably more but under 4.5 msg/s/rope; no message addressing a position outside the rope; `view`/film carrying the zone split and per-bay caps; lab previewing draft caps and directions without saving; malformed accent/direction input rejected |
| `test_renderer.py` | 36 | droplet cycle outlasts fall+splash for every travel; a splash actually reaches the waterline at 0/3/12/25/50/90% |
| `test_studio.py` | 53 | reverse flips the status region only and leaves the cap identical; completion bound to its job; disconnected interval retires it; restart never reports observed; not persisted; legacy file ignored |
| `test_ui_assets.py` | 27 | zone explained in words; cap controls offer white/colour/rainbow/apply-to-all; client reads the boundary from server data rather than hard-coding 90; wire/cap labels; lab sends `accents`/`reverses`; no regex in colour conversion |

---

## Files touched

**Changed:** `light_studio/layout.py` (schema 3, accent validation),
`light_studio/renderer.py` (zone constants, `render_accent`, `compose_rope`,
`droplet_timing`, 90-position default), `light_studio/studio.py` (zone
composition, publish path no longer reverses, `observed_complete` rework, zone
fields on `view`/film, `sim_film` accents/reverses), `light_studio/web.py`
(preview accepts `accents`/`reverses`), `light_studio/core.py` (façade exports),
`light_studio/static/{index.html,app.js,style.css}`, `tests/test_renderer.py`,
`tests/test_studio.py`, `tests/test_ui_assets.py`, `tests/browser_check.py`,
`README.md`, `DESIGN.md`, `AGENTS.md` (my section only),
`CLAUDE_BUILD_REPORT.md` (deployment correction only).

**Added:** `tests/test_accent.py`, `CLAUDE_ACCENT_REPORT.md`.

**Untouched:** `reference/`, `integrate.py`, `server.staged.py`, `deployment/`,
`data/`, `preflight-data/`, `LIVE_ROLLBACK_NOTICE.md`, both handoffs, and
everything outside this workspace. No file deleted. No host, service, broker,
printer or firmware was touched.

The host interface is unchanged: `light_studio.core` still exports `Studio` with
five positional parameters, so `integrate.py` and `server.staged.py` needed no
edits and `tests/test_integration_contract.py` still passes.

---

## What I could not verify

* **Nothing here has been seen on hardware or in a browser.** UI behaviour is
  checked statically (bracket balance, every `$('id')` exists, no markup
  injection, all created classes styled, endpoints match the server); that is
  not the same as running it.
* **Smoothness is unproven.** This is the point the rollback turned on. My run
  budgets are enforced by tests, but "does this look better than the original"
  cannot be answered from here.
* **The assumption that physical position 0 is the data-in wire** comes from the
  handoff and from how WS2811 chains address. If a rope is wired the other way,
  its cap will appear at the wire end and the fix is per rope, not global —
  worth checking on one rope before trusting all seven.
* **Which ten modules are "the last ten"** is a physical fact I cannot confirm;
  Identify plus the `CAP` label in the preview is how to check it.
* Rainbow is a stepped hue drift, not a continuous fade, and I have not seen it
  on a rope.

### Suggested order for Codex

1. `python3 -m light_studio --demo --port 8872 --data /tmp/print-tide-demo`,
   then `python3 tests/browser_check.py` (updated for the cap workflow:
   per-rope colour, apply-to-all, wall-wide rainbow, save, undo, and the
   direction text now reading "fills toward/away from the wire").
2. Show Adi the demo — especially the zone boundary and whether the cap is where
   he expects relative to the wire.
3. Only with his approval: a bounded physical trial on **one** rope if possible,
   comparing smoothness against the original renderer before considering the
   other six.
