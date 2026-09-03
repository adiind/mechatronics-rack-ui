#!/usr/bin/env python3
"""Write a valid empty rack config and inventory record."""

from __future__ import annotations

import argparse
from pathlib import Path

from rack.storage import atomic_write_json

BIN_COUNT = 24


def seed_rack_config() -> dict:
    return {
        "version": 1,
        "rack_id": "rack-01",
        "display_name": "Mechatronics rack 01",
        "endpoint": "mechatronics-rack-01",
        "topic_prefix": "ledwall/node01",
        "pixel_count": 24,
        "rows": 6,
        "columns": 4,
        "origin": "top-left",
        "bins": [{"bin_id": f"bin-{index + 1:02d}", "led_index": index} for index in range(BIN_COUNT)],
    }


def seed_inventory() -> dict:
    return {
        "version": 1,
        "items": [],
        "bins": {
            f"rack-01/bin-{index + 1:02d}": {"state": "unknown", "last_verified_at": None}
            for index in range(BIN_COUNT)
        },
    }


def write_seed_rack(data_dir: Path) -> tuple[Path, Path]:
    rack_dir = Path(data_dir) / "rack"
    config_path = rack_dir / "rack-01.json"
    inventory_path = rack_dir / "inventory.json"
    atomic_write_json(config_path, seed_rack_config())
    atomic_write_json(inventory_path, seed_inventory())
    (rack_dir / "audit.jsonl").touch()
    return config_path, inventory_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    args = parser.parse_args()
    config_path, inventory_path = write_seed_rack(args.data_dir)
    print(f"wrote {config_path} and {inventory_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
