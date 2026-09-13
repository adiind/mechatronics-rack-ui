# Print Tide — printer light studio

A Pi-hosted mapping UI and coordinated animation renderer for the seven printer
ropes in the EDI printer bay.

> ## Status: LIVE — this is the main renderer for the wall
>
> Installed into `/home/edi/printer-led-mcp/server.py` on 2026-09-05 at 23:50 CDT
> (`deployment/install-receipt.json`, source SHA 799e04c2…). It supersedes the
> earlier 22:45 rollback recorded in `LIVE_ROLLBACK_NOTICE.md`. The physical
> ropes are driven by this renderer; the original `NodeAnimator` is only a
> startup fallback. UI: http://100.65.17.33:8772/ (Tailscale).
>
> Confirmed by Adi on 2026-09-06: Print Tide is the main thing, not a preview.
> Edits to `light_studio/` take effect on `systemctl --user restart printer-led-mcp.service`.
>
> Brightness: `BRIGHT_CAP` is 252 (effectively full) since 2026-09-06, and all
> seven nodes were reflashed the same day with `color_correct: [80%,80%,80%]`
> (was 55%). Firmware source of truth is now `firmware/` on this Pi; see
> `firmware/README.md` for the flash procedure. `reference/ledwall-node02.yaml`
> is the historical 55% config.

It does three things:

1. **Maps** printers to LED ropes so you can fix a mismatch visually instead of
   re-plugging controllers.
2. **Animates** all seven ropes from one shared clock, with a distinct look per
   printer state and gentle ripples across the wall.
3. **Tells the truth** about what it knows: freshness, whether a completion was
   actually observed, and whether a light command was merely queued.

---

## Use it

### Map the wall

1. Open **Map the wall → Walk the wall**. Stand at the leftmost physical bay.
   Press **Identify this rope**: that rope pulses cyan/white for five seconds
   and then returns to its real state on its own, even if you close the browser.
   * If the rope that lit up is the one in front of you, press **Yes, it's here**.
   * If not, press **Not this one · try another** and Identify again.
   Repeat down the wall. When you finish, the bay order in the studio matches
   the physical order, which is what ripple direction uses.
2. To change which *printer* drives a rope, drag a printer name onto another
   bay, or use the **Driven by** menu (works on a phone, no dragging needed).
3. Tick **Reverse status direction** if the bar fills the wrong way round;
   progress then runs the other way inside the 90-position status region. The
   far-end cap does not move — it is defined relative to the wire.
4. Click a printer name to rename it. Names are display-only; telemetry
   identity (`printer1`…) never changes. Open **Far-end cap** to set that rope's
   accent colour, rainbow or brightness.
5. Nothing you do here touches the wall until **Save & apply**. Until then the
   bar at the bottom lists exactly what is pending, and changed bays are marked
   **DRAFT**. **Discard draft** reverts; **Undo last save** restores the layout
   before the last save.

### Watch it

Each bay shows the live pattern its printer's rope is playing right now, plus
plain text: state, percentage, ETA, layer, nozzle/bed, and how long ago the last
message arrived. Unknown is shown as unknown — never as `0` and never as
"available".

When a print you were watching finishes, that bay celebrates briefly, one ripple
crosses the wall, and the rope settles to steady green. Press **Mark collected**
after you clear the bed; the rope returns to blue idle. That is *local display
bookkeeping only* — no command is ever sent to a printer. A new observed print
clears the mark automatically.

### Animation lab

Set a **different simulated state per bay**, or pick a scenario, and watch the
whole wall respond. This is a simulation: it cannot publish, and it cannot alter
telemetry. The wall settings panel here (brightness, flow speed, ripples,
quarter marks, reduced motion, quiet mode) and the far-end cap panel both edit
the *same draft* as the map, so what you preview is what Save & apply will send.
The preview runs through the same Python compositor as the hardware path,
including your draft's cap settings and rope directions — but a browser canvas
can only show you the *intent*, never how smooth the physical rope will look.

---

## Two zones per rope

Each rope is 100 *addressable positions* (WS2811 modules — not 100 individual
dies), split into two independently composed zones:

```
physical 0 .......................... 89 | 90 ............ 99
^ data-in wire                           |                 ^ far end
<------------ status region (90) ------->|<-- accent cap -->
```

* **Status region — 90 positions.** All printer state and animation. Progress is
  measured across these 90 alone: 0/50/100% is 0/45/90 positions. **The cap is
  not part of the percentage.**
* **Accent cap — 10 positions**, at the end opposite the wire. Solid neutral
  white by default; per rope you can pick any solid colour, switch to rainbow,
  and set its own brightness. It never shows printer state. Errors, pauses,
  celebrations, ripples and Identify all stay inside the status region.

**Reversing a rope flips the status region only.** The cap's location is defined
relative to the wire, so it can never move to the wire end. The card under each
bay says "fills away from the wire" or "fills toward the wire", and the preview
draws physical position 0 at the bottom with `WIRE` and `CAP` end labels and a
dashed line at the zone boundary.

### Editing the caps

* **Per rope**: open *Far-end cap* on a bay card in Map the wall. Mode
  (Pure white / Solid colour / Rainbow), a colour picker, and cap brightness.
* **All ropes at once**: either *Apply this cap to all ropes* inside a card, or
  the *Far-end caps · all ropes* panel in the Animation lab.
* Caps are part of the single draft: they change the wall only on **Save &
  apply**, are listed in the pending-change summary, and are covered by Undo.
* The cap belongs to the **rope**, not the printer. Swapping which printer drives
  a rope leaves its cap alone; reordering bays moves the cap with its rope,
  because the rope itself moved.

### Cap brightness, quiet mode and the budget

Cap brightness is independent of the wall brightness slider — dimming the
animation does not dim the caps, which is the point of "always on". Quiet mode
*does* dim them, to 45%, because quiet hours with seventy white LEDs at full
output would not be quiet. Both stay under the same physical channel ceiling.

Rainbow is a **uniform hue across the ten positions, stepped in time**: 72 steps
over 24 seconds, so it costs at most one message every third of a second per
rope, and the step rate does not follow the wall speed slider. A per-position
rainbow gradient would cost ten messages per frame and is deliberately not
offered. White and solid-colour caps are static and cost nothing after their
first paint, apart from the ordinary 12-second resync.

## What the colours mean

Every colour on the ropes is a shade of pink (2026-09-13); states are told apart by lightness, saturation and motion.

| State | Look | Trigger |
| --- | --- | --- |
| Available | solid resting mid rose, no motion | `IDLE`/`READY`, fresh |
| Preparing | light-pink sweep rising through a dark pink body | `PREPARE` only — never inferred from temperature |
| Printing | full pink theme: pastel-pink filled region, remainder in six bands of pink darkening from hot pink at the waterline towards the top, near-white droplets and splashes, across the 90-position region | `RUNNING` |
| Paused | light pink breathing with steady near-white marks at both ends | `PAUSE` |
| Error | deep fuchsia breathing, the most saturated pink on the wall (almost no green) | `print_error` set (any gcode state) |
| Stopped early | steady deep plum until the door opens or **Mark collected** | `FAILED` with `print_error` cleared (cancel, or a dismissed failure) |
| Collect | 12 s smooth pink wash (one hue per tick, sweeping magenta-pink to rose and back) cross-fading into the lightest near-white pink, held until the door opens or **Mark collected** | observed completion (wash), or retained `FINISH` (collect pink only) |
| Offline | dim mauve double-thump heartbeat | link down, no timestamp, stale >120 s, or a future timestamp |
| Unknown | pale greyish-pink dashes | connected, but the reported state is not one we recognise |

Error, pause, offline and unknown are never overwritten by cross-wall ripples,
and error/pause keep a visibility floor even in quiet mode at low brightness.

## What it deliberately does not claim

* **A retained `FINISH` is not a new completion and not an empty bed.** The
  studio distinguishes *finished (seen)* — a transition it actually watched —
  from *retained FINISH · completion not observed*. An initial `FINISH`, a
  `FINISH` revealed by a reconnect, and a process restart never celebrate.
* **A queued MQTT publish is not a receipt.** The nodes acknowledge on
  `ledwall/nodeNN/ack`, which this process does not subscribe to. Delivery is
  reported as `node receipt unverified`, always. Identify is a *physical* check:
  believe your eyes, not the UI.
* **A timestamp proves a message arrived**, not that any particular field
  changed in it.
* Job names are not job IDs. Collection acknowledgement is keyed to the retained
  job name, which is the best identifier this telemetry offers.
* No model assignment, filament/AMS data or error cause is inferred.

---

## Structure and editing

| File | What lives there |
| --- | --- |
| `light_studio/model.py` | raw snapshot → sanitized state; freshness rules |
| `light_studio/layout.py` | mapping schema, validation, atomic persistence, undo |
| `light_studio/renderer.py` | **pure** frame rendering; the only animation code |
| `light_studio/transport.py` | frame diffing, firmware payloads, message budgets |
| `light_studio/studio.py` | the coordinator: one writer, clock, lifecycle rules |
| `light_studio/web.py` | loopback/tailnet UI server |
| `light_studio/core.py` | frozen façade the installed host file imports |
| `light_studio/static/` | `index.html`, `style.css`, `app.js` — no dependencies |
| `integrate.py` | regenerates `server.staged.py` from the untouched reference |
| `deployment/install.py` | backup, guarded install, health check, rollback |

**Editing the UI**: change `static/*` and reload the browser. Static files are
read from disk per request, so no restart is needed.
**Editing Python**: the loaded module only changes on a service restart, which
Codex coordinates.

Live state lives in `data/`: `layout.json` (current + previous, for undo),
`collected.json`, `lifecycle.json`. All are written atomically and contain no
credentials.

## Hardware limits, honestly

* Only `node02`–`node08`, 100 addressable positions each (90 status + 10 cap).
  `node01` is the unrelated rack/wall pilot and is rejected by pattern in the
  mapping, in Identify and in the initial-map check.
* The rope's own smoothness is the acceptance test. The previous provisional
  renderer was rolled back for looking worse than the original; nothing here is
  known to look better on hardware until someone watches it.
* The firmware accepts only `fill`, `range`, `pixel` and `off`. There is no bulk
  frame, no firmware effect and no timestamp, so the studio sends *differences*.
* Budget: **24 messages/second/rope, 140/second aggregate**, burst 40/80, at most
  8 messages per rope per 125 ms tick. These are conservative choices in
  `transport.py`, not a measured firmware ceiling — lower them if the wall ever
  looks like it is dropping updates.
* Consequence: the coordinator renders at 8 Hz but a rope receives roughly
  **3–5 visually meaningful updates per second**. The animations are composed to
  suit that: a near-static base plus narrow moving sprites, so the budget is
  spent on the parts of the frame the eye follows. When the budget binds, the
  highest-contrast differences go first and the rest are re-offered next tick.
* Synchronization is **coordinated from the Pi, not frame-locked in hardware.**
  Seven independent ESP32s over campus Wi-Fi will not be sample-accurate.
* Channel values are capped at 252 (`BRIGHT_CAP`) before the node's own 80%
  `color_correct` (flashed 2026-09-06); the brightness slider is a share of that
  ceiling, not a way past it. Going brighter means reflashing from `firmware/`. Browser
  colours are amplified for screen legibility and are not a photometric match.
* Every rope is fully repainted at least every 12 seconds, after a publish
  failure, after re-enabling casting, and after any layout save.

## Test and demo without hardware

From this directory:

```sh
python3 -m unittest discover -s tests -v
python3 -m light_studio --demo --port 8872 --data /tmp/print-tide-demo
```

The demo binds loopback only, is constructed with **no publisher at all**, and
labels itself DEMO throughout. Its synthetic fleet covers every state and runs a
full print → finish → idle → prepare cycle every 140 seconds, so the celebration
and the wall ripple can be reviewed without waiting on a real print.

`python3 integrate.py` regenerates `server.staged.py`; it neither installs nor
runs anything. `deployment/install.py` verifies the live source hash, backs it
up, installs atomically, restarts the existing user unit, health-checks the UI
and rolls back on failure.

## Current deployment state

**The live host is this build** (installed 2026-09-05 23:50 CDT, see the status
box at the top). `printer-led-mcp.service` imports `light_studio` and serves the
UI on port 8772 bound to the Tailscale IP. A service restart picks up edits to
`light_studio/`. Roll back at any time by restoring a
`server.py.pre-print-tide-*` backup and restarting the unit.

If the studio fails to initialize at startup, the host file falls back to the
original animator *before any new writer has published anything*, so a bad
layout file degrades to the old behaviour rather than to a dark wall.

Channel-value history: the first live build capped channels at 140 and then 168,
stacked with the firmware's 55% `color_correct`, which is why the wall looked
dim compared with the original renderer. Fixed 2026-09-06 (cap 252).

## Known limitations

* A print that starts and finishes entirely while this process is down cannot be
  reconstructed, and will not celebrate.
* Rope health (since 2026-09-06): the host feeds controller acks and the
  retained `ledwall/+/status` topics into `Studio.observe_node`. Each bay shows
  online/offline and whether acks are keeping up with sends; the footer sums it
  as "receipt confirmed N/7". Identify reports when the controller answered. A
  controller that comes back online is repainted automatically (it boots dark).
* Quiet mode is a manual toggle, not a schedule.
* There is no per-state colour editor, print queue or printer control, and none
  is planned here — this is lighting and display work.
