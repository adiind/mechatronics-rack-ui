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
python3 scripts/seed_rack.py --data-dir data
RACK_OPERATOR_TOKEN="choose-a-local-token" python3 -m rack.rack_server --port 8770
```

Open `http://127.0.0.1:8770`. Search and inventory viewing are open locally;
editing requires the operator token. The seed command creates an empty 24-bin
example under `data/rack/`. That directory is intentionally ignored so an
operator's inventory and audit history stay local.

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
