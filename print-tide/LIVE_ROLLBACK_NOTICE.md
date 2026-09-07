# Live lighting rolled back — 2026-09-05 22:45 CDT

Adi reported that the live LED animations looked much worse than the earlier smooth renderer. Codex restored the exact original /home/edi/printer-led-mcp/server.py from server.py.pre-print-tide-20260905-214912 and restarted the existing service. The restored SHA256 is d0a24c0da94558592affd9c661a8c50dd0b2dd7d3743e020126c019b79136d1b.

The degraded live renderer was Codex's provisional build; Claude's replacement Python engine had not been activated. The experimental port 8772 now closes with the original service. Keep Claude's new work as a hardware-free preview for review. Do not reinstall or restart into the experimental renderer without Adi approving the preview and a bounded physical trial. Preserve the original renderer's smoothness and use this incident as an acceptance criterion. This notice supersedes earlier handoff language proposing automatic installation of the next build.

---

**Superseded 2026-09-05 23:50 CDT.** Print Tide was installed after this notice
(`deployment/install-receipt.json`). On 2026-09-06 Adi confirmed it is the main
renderer, not a preview. See README status box.
