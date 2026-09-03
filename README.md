# Mechatronics Rack UI

A small, local-first web application for finding parts in a physical rack or
drawer cabinet. It keeps a versioned JSON inventory with an append-only audit
log, presents a searchable accessible browser UI, and produces time-bounded
locator plans through a server-side transport boundary.

The default configuration is deliberately **dry run**: no broker credentials
are included, and no MQTT command leaves the process. It is suitable for
running as a local inventory UI or as a reference implementation for an
authenticated hardware integration.

## What is included

- Rack/drawer geometry validation and LED-index mapping.
- Search, inventory updates, audit history, and CSV import/export.
- Local HTTP API and vanilla HTML/CSS/JavaScript UI.
- A bounded locator-plan builder. Browser code never publishes MQTT.
- Standard-library unit and HTTP API tests.

## Quick start

Requires Python 3.10 or newer. No third-party dependency is needed for the
default dry-run server.

```sh
PYTHONPATH=. python3 scripts/seed_rack.py --data-dir data
RACK_OPERATOR_TOKEN="choose-a-local-token" python3 -m rack.rack_server --port 8770
```

Open `http://127.0.0.1:8770`. Search and inventory viewing are open locally;
editing requires the operator token. The seed command creates an empty 24-bin
example under `data/rack/`. That directory is intentionally ignored so an
operator's inventory and audit history stay local.

## Rack geometry and color order

`scripts/seed_rack.py` writes the real `rack-01` layout: 7 rows x 6 columns
of bins numbered row-major from the top-left, with the LED string wired
**serpentine starting at the bottom-left** (physical row 0 runs left to right,
the row above runs right to left, and so on). The physical Uline rack has 4
rows of 6; the extra rows are positions the string grows into. Each config
may also carry `"color_order": "RGB" | "GRB"` (default RGB). Locate plans are
built in intent RGB and swapped to wire order only at publish time, so the UI
always shows true colors while a GRB WS2811 string receives the bytes it needs.

## Ask-the-rack chat (Gemini)

The page includes a chat panel. A visitor types a plain-language request
("where is the stepper driver?", "what can I build a line follower with?");
the server sends the **complete verified inventory** plus the question to
Gemini, asks for a structured answer, and then re-validates every returned
`item_id` against the inventory record. Only verified items are shown, and
only verified items with a mapped bin can light the rack. The browser never
sees the key and never talks to Google directly.

Configuration (any one of these):

```sh
# 1. an ignored env file (default location)
mkdir -p ~/.config/mechatronics-rack-ui && chmod 700 ~/.config/mechatronics-rack-ui
printf 'GEMINI_API_KEY=...\n' > ~/.config/mechatronics-rack-ui/gemini.env && chmod 600 ~/.config/mechatronics-rack-ui/gemini.env
# 2. or --gemini-env /path/to/file   3. or GEMINI_API_KEY in the environment
python3 -m rack.rack_server --gemini-model gemini-3.6-flash   # --no-chat disables the panel
```

`POST /api/chat` takes `{"message": str, "history": [{role, text}], "light": bool}`
and returns the reply, the validated `matches` with their bin locations, and
what was `lit`. `GET /api/health` reports whether chat is enabled. Without a
key the panel shows "assistant not configured" and everything else works.

`scripts/import_pilot_csv.py` converts the earlier pilot CSV
(`pixel,id,part,category,priority,qty,tags,url`) into the inventory record;
pixel *N* becomes `bin-(N+1)`, and pixels past the mapped bin count import as
stocked-but-unplaced items.

## Tests

```sh
python3 -m unittest discover -s tests/rack -v
```

Some API tests start an ephemeral loopback server. They do not contact a
broker or hardware.

## Hardware integrations

The code can accept a separately created, local MQTT client configuration only
when `--client-config` is explicitly supplied. This public repository contains
no credentials, endpoints, firmware, controller configuration, deployment
scripts, or live-hardware instructions. Integrators are responsible for their
own authenticated broker, safety checks, and physical verification.

## Public-repository scope

This repository intentionally excludes real inventory records and audit logs,
broker/OTA credentials, private handoffs, Raspberry Pi deployment material,
firmware, screenshots, generated artifacts, and unrelated application code.
No license is granted by this repository unless its owner adds one.
