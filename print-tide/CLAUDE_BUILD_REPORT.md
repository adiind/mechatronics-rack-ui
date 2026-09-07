# Claude build report — Print Tide product pass

**Author:** Claude (Pi), 2026-09-05. **Baseline:** the provisional Print Tide
built and installed by Codex earlier the same day. **Scope:** the substantive
product pass requested in `CLAUDE_LED_OWNERSHIP_HANDOFF.md`.

Written vs. executed: I **wrote** all code and docs below and **executed** only
`python3 -m unittest discover -s tests -v`, the one command I was permitted to
run. I did not open a browser, restart a service, publish to a broker, or touch
anything outside this workspace. Browser behaviour is *statically* checked, not
executed — see "What I could not verify".

---

## Headline

| | Codex baseline | After this pass |
| --- | --- | --- |
| Renderer | 13-pixel bands, ~2 Hz, one 100-px loop | 8 states composed as slow-broad + narrow-tiered sprites, 8 Hz, budget-aware |
| Browser motion | one frame per 1 Hz status poll | run-length encoded filmstrip, 12 fps playback of real server frames |
| Animation lab | one state forced on all seven bays | per-bay state and progress, plus four scenarios |
| Wall settings | separate save path that silently applied pending mapping edits | one draft, one Save & apply, itemised pending-change summary |
| Architecture | one 257-line `core.py` | `model`/`layout`/`renderer`/`transport`/`studio`/`web` + frozen `core` façade |
| Completion honesty | observed vs. not, in memory | plus `observed_complete`; restart provably cannot celebrate |
| Tests | 22 | 192 |

> Superseded in the follow-up accent pass: `observed_complete` is no longer
> persisted at all, and is now bound to the job name it watched. See
> `CLAUDE_ACCENT_REPORT.md`.

**Host integration: ~~restart only~~ — see the correction in "Deployment".**
This build was never activated on hardware, and the live host has since been
rolled back to the original renderer. I deliberately kept `light_studio.core`'s
export names and `Studio`'s five positional parameters unchanged, so
`server.staged.py` and `integrate.py` are untouched and
`tests/test_integration_contract.py` proves the staged file still binds cleanly
against the package — but that staged file is **not installed**, and a restart
activates nothing.

---

## Design decisions

### 1. The transport budget is the design constraint, so animations are built for it

The firmware takes only `fill`/`range`/`pixel`/`off`. A frame therefore costs
roughly *the number of contiguous colour runs that changed*. The baseline's
smooth full-strip gradients change almost every run every frame, which is why it
could only afford 2 Hz and coarse bands.

Every state is now composed as **a slow broad layer plus narrow fast sprites**:

- Broad layers are static (idle's depth gradient, printing's filled region) or
  move in coarse 10-pixel bands with a quantized amplitude, so quantization
  holds them still for many frames — one or two runs per frame.
- Motion the eye follows is carried by narrow sprites whose falloff is snapped
  to three tiers. Tiering is invisible at 140/255 on a WS2811 rope and roughly
  thirds the run count.
- Paused and error breathe as a *solid* colour broken only by static markers, so
  a whole breathing frame is three or four messages.

`tests/test_renderer.py` pins this down: ≤34 colour runs per frame and ≤16
changed runs between consecutive frames, for every state. I found and fixed two
violations this way — idle (19, then 17) and preparing (18) — by banding idle's
swell and tiering both states' sprites.

Budgets in `transport.py` are **24 msg/s/rope, 140/s aggregate**, burst 40/80,
≤8 per rope per 125 ms tick. These are conservative engineering choices, not a
measured firmware ceiling, and they are stated as such in README and DESIGN.
Practical result: 8 Hz rendering, **roughly 3–5 visually meaningful updates per
second per rope**. When the budget binds, `plan_updates` sends the
highest-contrast differences first so a limited rope looks simplified rather
than torn, and every fourth pass ignores priority and walks left-to-right so a
low-contrast region cannot starve.

### 2. One renderer, and the browser plays its frames

The renderer is a pure function of `(state, percent, t, position, settings,
events)`. That lets the server compute a second of *future* frames on request.
The browser fetches a run-length-encoded filmstrip about once a second and plays
it at 12 fps on its own clock. This gives smooth motion without a second
animation implementation in JavaScript, and without an 8 Hz status poll.
`tests/test_studio.py` asserts a decoded film frame equals the frame the
coordinator is publishing.

### 3. Draft and live are now unambiguous

The baseline had two save paths; "Save & apply wall settings" silently applied
unsaved mapping edits too. Now there is **one draft** (mapping *and* settings)
and **one Save & apply**. Changed bays wear a `DRAFT` badge, and the sticky save
bar itemises what is pending ("2 printer assignments, 1 rope direction, wall
settings"). The lab previews the draft settings, so what you see is what saving
will send.

### 4. Restart can never celebrate

`lifecycle.json` now persists last-observed state per printer, but restored rows
are flagged and can only ever *suppress* a transition — never permit one. The
brief is explicit that a process restart must not celebrate, and it now provably
cannot, even when the story is plausible. The genuinely useful half is
`observed_complete`, which lets a bay say **"finished 12 min ago (seen)"** versus
**"retained FINISH · completion not observed"**.

---

## Implemented changes

**Architecture.** Split `core.py` into `model.py` (state interpretation),
`layout.py` (schema/validation/atomic persistence/undo), `renderer.py` (pure
rendering), `transport.py` (diffing, payloads, budgets, wire encoding),
`studio.py` (coordinator) and `web.py` (HTTP). `core.py` is now a frozen façade.

**Renderer.** Eight distinct states, all continuous:
idle water (static depth gradient, banded swell, two tiered crests);
preparing (rising cyan sweep with a wake, only on `PREPARE`);
printing (gradient fill, bright 2-px meniscus, quarter marks in the remainder for
legibility, falling droplet, splash, and an explicit *indeterminate shuttle* when
`RUNNING` has no percentage — not 0%);
paused (amber breath + three static blocks);
error (deeper orange-red breath + five hot ticks, minimum 58% amplitude, no strobe);
finished (2.8 s expanding celebration, then steady green with end caps);
offline (double-thump slate/purple heartbeat);
unknown (grey dashes, visibly not "available").
Ripples blend rather than substitute, are capped lower during printing so the
waterline survives, and are suppressed for error/paused/offline/unknown.
Alarms keep a 38% brightness floor through quiet mode; identify keeps 60%.

**Coordinator.** 8 Hz tick; an animation phase accumulator that clamps deltas so
an NTP step cannot teleport the water or resurrect ripples; staggered per-rope
resync; per-rope and aggregate token buckets; rotation plus fairness passes;
identify made a five-second server-side window with a sweeping cyan/white pulse
that restores only its own rope; event de-duplication and bounding; scene
snapshotting so film requests render *outside* the writer lock.

**State semantics.** Four named non-fresh reasons (`link_down`, `no_telemetry`,
`stale`, `clock_skew`) surfaced as plain words; `layer`/`total_layer` surfaced
(they were already in the host snapshot and unused); ETA sanity-clamped;
timestamps accept epoch, naive Pi-local and offset-aware forms; job names
control-stripped and length-bounded.

**HTTP.** `GET /api/film` (read, no token) for the live filmstrip; `POST
/api/preview` now takes per-bay simulated states; reads and writes strictly
separated by verb; `Referrer-Policy` added; CSP tightened by removing
`'unsafe-inline'` for styles (the markup now has no inline styles).

**UI.** Rewritten `index.html`/`app.js`/`style.css`: 12 fps canvas playback with
bloom, per-bay lab controls and scenarios, guided "walk the wall" flow driven by
physical Identify, inline rename, DRAFT badges, pending-change summary, richer
per-bay status, honest transport line (`msg/s of ceiling · sent · failed · node
receipt unverified`), sticky save bar, phone layout down to two columns.

**Demo mode.** The synthetic fleet now covers every state simultaneously and
runs a full print → finish → idle → prepare cycle every 140 seconds, so the
celebration and wall ripple are reviewable without a real print.

### Bugs I found and fixed in my own work during review

- HTTP/1.1 keep-alive: early rejections (403/415) returned without reading the
  request body, so the next request on the same socket would be parsed as
  garbage. Bodies are now consumed before authorization; oversized bodies close
  the connection. Regression test included.
- A user's own save triggered a false "another window saved a different layout"
  toast.
- `live_film` held the coordinator lock while rendering 84 frames, stalling the
  writer on every browser poll.
- A "Quiet night" scenario produced eight bays for a seven-bay wall.
- Full DOM rebuild on every 1 Hz poll, which would steal focus from the rename
  field and reset a slider mid-drag.
- A backwards clock step could park a rope's resync deadline in the far future.

---

## Test evidence

```
$ python3 -m unittest discover -s tests -v
Ran 192 tests in 10.6s
OK
```

Executed on this Pi, in this workspace, at the end of the pass. All tests are
hardware-free: fake clock, fake publisher, temporary directories, loopback
sockets only. No test imports the host module, opens a broker connection or
touches `/home/edi/printer-led-mcp`.

| Module | Tests | Covers |
| --- | --- | --- |
| `test_core.py` | 3 | frozen façade: exported names, five-positional constructor, mapping readable with no writer |
| `test_model.py` | 17 | number/bool/NaN rejection, three timestamp forms, four staleness reasons, 120 s boundary, error priority, unknown-never-available, hot-nozzle-is-not-preparing, host/serial/code never survive |
| `test_layout.py` | 21 | one-to-one mapping, node01 rejection, duplicates, slot shape/types, revision types, settings ranges and unknown keys, schema-1 migration, save/reload/conflict/undo/reset, corrupt file, NaN refusal, failed save leaves file intact |
| `test_renderer.py` | 34 | bounds/quantization/cap for every state × percent × time, determinism, eight-state distinctness, waterline monotonicity, 0% ≠ unknown%, reduced motion freeze, quiet-mode alarm floor, ripple distance ordering and expiry, ripple never touches error/pause/offline/unknown, ripple does not erase progress, celebration bounds, identify override/clamping, **run-count budgets** |
| `test_transport.py` | 16 | diff runs, run merging, firmware payload shapes *and key counts* (the node YAML rejects wrong key counts), out-of-range refusal, bucket burst/refill, plan limits, priority ordering, fairness pass, skipped runs stay dirty, RLE round-trip |
| `test_studio.py` | 48 | bad initial maps, corrupt layout stops startup before publishing, initial/reconnect/gap/**restart** never celebrate, real completion exactly once, event dedupe/bounds/expiry, clock-jump immunity, collection rules and restart survival, identify bounds/restore/refusals, cast disable stops output and re-enable repaints, allowlist + firmware legality of every message, aggregate budget, no rope starves, failed publish never counted as delivered, raising publisher survives, periodic resync, reverse flips the physical end, mapping visible to MCP immediately, film matches renderer, film built without the lock, simulation never publishes |
| `test_http.py` | 19 | bind policy (0.0.0.0/LAN refused, tailnet allowed), Origin+CSRF both required, host header, GET/POST separation, content type, size limit, malformed JSON, keep-alive body drain, path routing incl. traversal attempts, no host/serial/code/path in `/api/state`, security headers, node01 over HTTP, revision conflict, invalid layout leaves saved layout intact, preview read-only, film readable |
| `test_ui_assets.py` | 20 | JS bracket balance, strict mode, **no `innerHTML`/`eval`/`document.write`**, every `$('id')` exists in the HTML, client calls only endpoints the server serves, lab never posts a write, no inline script/style the CSP would block, legend names every state in words, all client-created classes are styled, no external font/image requests, phone breakpoint, `browser_check.py` parses |
| `test_demo.py` | 5 | demo covers every state, produces a genuine observed completion, has no publisher, carries no plausible real identifiers |
| `test_integration_contract.py` | 9 | staged host file parses, starts exactly one writer with the animator only in the fallback branch, `Studio` called with five positionals that bind against the live signature, `serve` uses `resolve_bind_host("auto")` and 8772, MCP tools read the live map, **every other original line survives verbatim**, no credential names in the glue, reference copies intact |

---

## Exact files touched

**Rewritten**
`light_studio/core.py`, `light_studio/web.py`, `light_studio/__main__.py`,
`light_studio/__init__.py`, `light_studio/static/index.html`,
`light_studio/static/app.js`, `light_studio/static/style.css`,
`tests/test_core.py`, `tests/test_http.py`, `tests/browser_check.py`,
`README.md`, `DESIGN.md`

**Added**
`light_studio/model.py`, `light_studio/layout.py`, `light_studio/renderer.py`,
`light_studio/transport.py`, `light_studio/studio.py`,
`tests/test_model.py`, `tests/test_layout.py`, `tests/test_renderer.py`,
`tests/test_transport.py`, `tests/test_studio.py`, `tests/test_ui_assets.py`,
`tests/test_demo.py`, `tests/test_integration_contract.py`,
`CLAUDE_BUILD_REPORT.md`

**Appended to** `AGENTS.md` (my `## Claude` section; `## Codex` left intact)

**Deliberately untouched**
`reference/` (all four files), `integrate.py`, `server.staged.py`,
`deployment/install.py`, `deployment/install-receipt.json`, `data/`,
`preflight-data/`, `CLAUDE_LED_OWNERSHIP_HANDOFF.md`, and everything outside
this workspace. No file was deleted.

---

## Deployment

> ### CORRECTION — this section is superseded (2026-09-05 22:45 CDT)
>
> **Everything below about "a restart is sufficient" is now wrong.** After this
> report was written, Adi reported that the live animations looked much worse
> than the original renderer, and Codex restored the original
> `/home/edi/printer-led-mcp/server.py` from
> `server.py.pre-print-tide-20260905-214912` and restarted the service. See
> `LIVE_ROLLBACK_NOTICE.md`.
>
> Current reality:
>
> * The live host is the **original** file. It does not import `light_studio`.
> * **Restarting the service activates nothing here.** The restart-only claim
>   assumed the staged file was installed; it no longer is.
> * Port 8772 is closed. Demo mode on loopback is the only way to view the UI.
> * The degraded renderer Adi saw was Codex's provisional build, not this one —
>   but that does not make this one approved. Its smoothness on hardware is
>   still unverified, and matching the original's smoothness is the acceptance
>   criterion for any future trial.
> * `deployment/install-receipt.json` still says `"installed"`; it predates the
>   rollback, which is recorded in `deployment/rollback-receipt.json`.
>
> Current deployment guidance lives in README.md ("Current deployment state, and
> what a trial would involve") and in `CLAUDE_ACCENT_REPORT.md`.

<details>
<summary>Original (now inaccurate) deployment text, kept for the record</summary>

**A service restart is sufficient. The staged host source does not need
replacing.** `server.staged.py` still matches the installed file, and the
interface it uses is contract-tested.

At restart the studio will:

- read the existing `data/` (empty at last inspection ⇒ defaults from `CAST_MAP`);
- if a `layout.json` written by the baseline *does* exist, migrate it: schema 1
  is accepted and upgraded to 2, and the new `waterline_marks` setting is filled
  from defaults. `collected.json` is unchanged in format.
- create `lifecycle.json` on the first tick.

If initialization raises (corrupt layout, bad `CAST_MAP`), the host's `except`
branch starts the original `NodeAnimator` before any new writer has published —
that ordering is preserved and contract-tested.

</details>

The still-accurate part: `light_studio.core` keeps its export names and
`Studio`'s five positional parameters, so `server.staged.py` and `integrate.py`
did not need changing then and have not needed changing for the accent work
either. That contract remains guarded by `tests/test_integration_contract.py`.

**Rollback** is unchanged: restore the dated backup named in
`deployment/install-receipt.json` and restart the user unit.

---

## What I could not verify, and what I would check first

I could not run a browser, so **no claim here about visual behaviour is
executed evidence.** `tests/browser_check.py` is rewritten for the new UI but was
not run by me. Suggested order for Codex:

1. `python3 -m light_studio --demo --port 8872 --data /tmp/print-tide-demo`, then
   `python3 tests/browser_check.py`. The demo's 140 s cycle exercises a real
   observed completion, celebration and ripple.
2. **Watch the physical wall for a minute after restart.** My run-count budgets
   are enforced in tests, but "does 3–5 updates/second read as smooth water" is a
   judgement only eyes on the rope can make. If it looks choppy, raise
   `NODE_RATE`/`AGGREGATE_RATE` in `transport.py`; if the nodes look overloaded,
   lower them. Both are single constants.
3. Confirm `cast_progress(false)` blanks... **it does not blank — it freezes.**
   Output stops promptly and the rope holds its last frame, matching the original
   `NodeAnimator`'s behaviour. Re-enabling forces a full repaint. If Adi wants
   disable to go dark instead, that is a one-line change and a deliberate
   behaviour choice I did not make unilaterally.
4. Check `list_printers` `cast_target` after saving a swap in the browser — it
   should change with no telemetry restart.

Other honest limits, all documented in README:

- Node receipts remain **unverified**. The nodes ack on `ledwall/nodeNN/ack` and
  retain status on `ledwall/+/status`, but this process subscribes to neither.
  Identify is a physical check; the UI never claims delivery. This is the single
  highest-value next feature and heads the DESIGN roadmap.
- Synchronization is coordinated from the Pi, not frame-locked in hardware.
- A print that starts and finishes entirely while the service is down cannot be
  reconstructed and will not celebrate.
- Job names are not job IDs; collection acknowledgement is keyed to the retained
  job name, the best identifier this telemetry offers.
- Physical positions and printer *model* assignments remain unverified by me —
  the walk-the-wall flow exists precisely because only a person at the wall can
  establish them.
- Browser colours are amplified ×1.8 for screen legibility and are not a
  photometric match to the rope.
