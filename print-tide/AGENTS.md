# Print Tide

Read README.md and DESIGN.md. Preserve `reference/` source copies, runtime
`data/` and deployment backups/receipts. Runtime is integrated into the existing
`printer-led-mcp` user service; never run a second publisher against the same
ropes. Restrict hardware writes to node02–node08, never node01. Credentials stay
in the existing host service and must never appear in output. Browser previews
must remain free of hardware writes. Model assignments, error causes and
physical bed clearance are not inferred.

**Deployment status: PREVIEW ONLY.** The live host was rolled back to the
original `server.py` on 2026-09-05 (see `LIVE_ROLLBACK_NOTICE.md`). Nothing in
this directory drives the physical lights, port 8772 is closed, and a service
restart activates nothing. Do not install or restart into this renderer without
Adi approving the preview and a bounded physical trial that compares smoothness
against the original renderer.

House rules for this project:

- `renderer.py` is pure and is the *only* animation implementation. Do not add a
  JavaScript one; the browser plays server-rendered filmstrips.
- A rope is two zones: 90 status positions from the data-in wire, then a fixed
  10-position accent cap at physical 90-99. `render_rope` returns 90 pixels and
  literally cannot address the cap; only `compose_rope` joins them. `reverse`
  flips the status region alone and must never move the cap.
- New animation must be composed as slow-broad + narrow-fast so it stays inside
  the transport budget. `tests/test_renderer.py` enforces the run-count limits.
- Anything that would let a restart, a reconnect or a retained `FINISH` produce a
  celebration is a bug, not a feature.
- `light_studio/core.py` is a frozen façade: the installed host file imports
  `Studio` and `serve` from it and calls `Studio(...)` with five positionals.
  Changing that means restaging and reinstalling the host file, not just a
  restart. `tests/test_integration_contract.py` guards it.

## Codex
- 2026-09-05: Built Print Tide after the Pi Claude dispatch hit its session limit;
  implemented seven-bay mapping, Identify, saved layouts, direction/order
  controls, eight animation states and shared ripples; validation evidence and
  live installation receipt accompany the build.

## Claude
- 2026-09-05: Took ownership of the implementation per Adi's handoff and did a
  substantive product pass over Codex's provisional build. Split the monolithic
  `core.py` into `model`/`layout`/`renderer`/`transport`/`studio` with `core.py`
  kept as a frozen façade so the installed host integration needs only a
  restart. Rewrote the renderer around the real transport constraint (slow broad
  layer + narrow tiered sprites) which replaced the 2 Hz 13-pixel bands with
  eight distinct, continuous, budget-aware states including a bounded completion
  celebration and a per-rope identify sweep. Added a run-length-encoded filmstrip
  API so the browser plays real server-rendered frames at 12 fps instead of a
  1 Hz snapshot. Made the animation lab per-bay, unified wall settings into the
  single draft (removing a save path that silently applied pending mapping
  edits), added a physical "walk the wall" flow, freshness reasons, layer counts
  and observed-versus-retained completion. Fixed an HTTP keep-alive body-drain
  bug I introduced with HTTP/1.1, and a false "someone else saved" toast after
  the user's own save. Tests: 22 → 188, all passing here; browser behaviour is
  statically checked, not executed. See CLAUDE_BUILD_REPORT.md.
- 2026-09-05 (later): Added Adi's independent ten-position far-end accent cap.
  A rope is now 90 status positions plus a fixed cap at physical 90-99, opposite
  the wire, with per-rope white/colour/rainbow, its own brightness and an
  apply-to-all action; progress recomputed across 90; `reverse` flips the status
  region only. Schema 3 migrates schema-1/2 layouts without losing mapping,
  names, direction, settings or undo. Also fixed two issues Codex flagged: the
  droplet's fixed 2 s cycle never let a drop land below ~40% progress, and
  `observed_complete` could show an old completion age against a different
  retained job. Corrected the now-stale deployment claims in README and
  CLAUDE_BUILD_REPORT after the live rollback. Tests: 192 → 253, all passing
  here; both bug fixes verified by reverting them and watching the new tests
  fail. Browser and hardware behaviour remain unverified by me. See
  CLAUDE_ACCENT_REPORT.md.
