# HANDOFF — for Claude Code on this Pi (edi)

Read `SPEC.md` in this directory first. It is the contract. This file is the
as-built reality of this Pi plus the ESP32 flashing procedure. Written
2026-08-25 from the Mac that flashed the first nodes; fleet state corrected
later the same day.

## Ground rules

- `printer-led-mcp.service` (user unit) is LIVE and drives real printer bars.
  Back up before touching `~/printer-led-mcp/server.py` or `printer-led.env`.
- Spec §9 open decisions (rack-01 identity, printer-state authority, store
  format, the unidentified Amazon item) belong to Adi. Ask; don't assume.
- Never print or commit secrets. `~/printer-led-mcp/printer-led.env` contains
  credentials (read var names, not values). Same for any `secrets.yaml`.

## This Pi, as built (2026-08-25)

| Thing | Where / what |
| --- | --- |
| MQTT broker | `mosquitto.service`, port 1883, LAN IP `10.106.4.15` (DHCP — can change; check `ip -4 -br addr show wlan0`) |
| Printer→LED service | `~/printer-led-mcp/` — `server.py`, user unit `printer-led-mcp.service`. Bambu printer MQTT in (LAN mode, per-printer IPs), node MQTT out |
| Printer→node mapping | `CAST_MAP` in `~/printer-led-mcp/printer-led.env`: printer1→node03, printer2→node04, printer3→node02, printer4→node05 … printer7→node08, all 100 px |
| LED wall pilot | `~/ledwall-mcp/` (20-px wall, node01) + `ledwall-button.service` |
| Rack pilot | `~/rack-mcp/` with `inventory.csv` |
| WLED emulator | `wled-sim.service` — fake strip for testing without hardware |
| Lighting coordinator (STAGED) | `~/lighting-platform/coordinator/` — §5.3 intent coordinator + simnode + tests + draft intent firmware. Built & sim-verified 2026-08-25, NOT installed; see its README.md for the cutover runbook |
| Bambu telemetry contract | `reference/bambu-cloud-mqtt-HANDOUT.md` — verified topics, auth, schema (338 fields). Cloud contract is REFERENCE; the live service talks LAN mode |

Node MQTT contract today (firmware side): each node subscribes
`ledwall/nodeNN/set`, acks on `ledwall/nodeNN/ack`, retains availability on
`ledwall/nodeNN/status` (`online`/`offline` via birth/will). The authoritative
payload contract is the `on_message` lambda in each node's YAML (see below).
Spec §3/§7 renames endpoints to `printer-01`… and `lighting/...` topics — that
is a deliberate migration, not a find-and-replace.

## ESP32 node fleet, as of 2026-08-25

Hardware per node: Seeed Studio XIAO ESP32-C6 on a Seeed LED driver board.
9 sets purchased; firmware is ESPHome (esp-idf framework), board
`seeed_xiao_esp32c6`.

| Node | State |
| --- | --- |
| node01 | 20-px LED wall pilot (different role, leave alone) |
| node02–08 | ALL flashed at 100 px — confirmed by Adi 2026-08-25; any earlier "not flashed" note is stale. node03 MAC `10:bd:a3:9d:a4:c8` is on the NU portal |

Live online/offline state: read the retained `ledwall/+/status` topics on the
local broker (it requires auth — credentials are on this Pi in the ledwall
setup; use them locally, never print them). mDNS/`.local` resolution is
blocked on this network; do not use it to judge node liveness.

## How to flash a XIAO ESP32-C6 node

All 8 current boards are already flashed — this section is for the 9th spare,
future rack controllers, or re-flashing.

ESPHome configs live on Adi's Mac: `/Users/adi/Documents/Bambu/ledwall/esphome/`
(`ledwall-node01..08.yaml` + `secrets.yaml`, esphome 2026.8.0 in
`/Users/adi/Documents/Bambu/ledwall/.venv`). Flashing has so far been done from
the Mac. To flash from this Pi instead, ask Adi to copy that `esphome/`
directory over (it contains `secrets.yaml`, so he copies it, not you), then:

1. `python3 -m venv ~/esphome-venv && ~/esphome-venv/bin/pip install esphome`
   (match the Mac: 2026.8.0).
2. Plug the XIAO into this Pi via USB-C → it appears as `/dev/ttyACM0`.
   Permission denied → `sudo usermod -aG dialout edi`, re-login.
3. First flash must be USB:
   `~/esphome-venv/bin/esphome run esphome/ledwall-nodeNN.yaml --device /dev/ttyACM0`
   First compile downloads the esp-idf toolchain — expect 10–20 min on the Pi;
   later builds are minutes.
4. Board won't enter flash mode → hold BOOT, tap RESET, release BOOT, flash,
   then tap RESET to run.
5. Copy the Wi-Fi MAC from the boot log and register it on the NU device
   portal — the network is MAC-gated; an unregistered board never joins.
6. After the first USB flash, reflash over the air with the same command minus
   `--device` (OTA password is in `secrets.yaml`).
7. Verify: `mosquitto_sub -h localhost -t 'ledwall/+/status' -v` (with broker
   auth) shows the node `online`, then publish a test frame to
   `ledwall/nodeNN/set` and watch the ack.

Hard-won YAML gotchas (already encoded in the node YAMLs — keep them when
writing new configs):

- **RF switch**: on the XIAO ESP32-C6, GPIO14 powers the RF switch (LOW = on)
  and GPIO3 selects the antenna (LOW = onboard). Both must be driven LOW at
  boot priority 900, before Wi-Fi starts, or the radio comes up on a
  disconnected antenna connector and nothing works.
- **Hidden SSID**: the campus SSID is hidden; `fast_connect: true` and
  `power_save_mode: none` are required or the board loops on
  "Probe Request Unsuccessful" and drops MQTT on keepalive.
- Nodes need the broker's **LAN** address (`10.106.4.15`), not localhost, not a
  Tailscale IP.
- `mqtt.reboot_timeout: 0s` — nodes must not reboot-loop when the broker is
  briefly down (spec §8 requires boot-dark, stay-alive behavior).

## Environment gotchas

- The only shell path to this Pi is Tailscale SSH (`edi@100.65.17.33`). It
  periodically demands browser re-auth: the ssh hangs printing a
  `login.tailscale.com` URL that Adi must open. LAN sshd rejects keys.
- This Pi's LAN IP is DHCP-assigned; re-check before hardcoding anywhere.

## Suggested first moves (maps to SPEC §10)

1. Read `SPEC.md` fully, then confirm the §9 open decisions with Adi.
2. Inventory what exists here vs. what the spec wants (namespaces, endpoint
   names, coordinator vs. current `server.py`).
3. Build the normalized printer-state layer against
   `reference/bambu-cloud-mqtt-HANDOUT.md`; test precedence with `wled-sim`.
4. One printer rope + one rack controller end to end before replicating.
