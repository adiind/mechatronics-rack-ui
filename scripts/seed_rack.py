#!/usr/bin/env python3
"""Write a valid empty rack config and inventory record for rack-01.

Geometry (decisions 2026-09-03): 7 rows x 6 columns of bins numbered row-major
from the top-left; the LED string is wired serpentine starting bottom-left; the
WS2811 modules take GRB byte order. The physical Uline rack has 4 rows of 6, the
extra rows hold parts the string will grow into.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rack.storage import atomic_write_json

ROWS = 7
COLUMNS = 6
BIN_COUNT = ROWS * COLUMNS


def serpentine_led_index(row: int, column: int, *, rows: int = ROWS, columns: int = COLUMNS) -> int:
    """LED index for a grid position when the string starts bottom-left and snakes upward.

    Physical row 0 is the bottom row and runs left to right; each row above
    reverses direction. `row` is counted from the top (bin-01 is top-left).
    """
    physical_row = rows - 1 - row
    offset = column if physical_row % 2 == 0 else columns - 1 - column
    return physical_row * columns + offset


def seed_rack_config() -> dict:
    return {
        "version": 1,
        "rack_id": "rack-01",
        "display_name": "Mechatronics rack 01",
        "endpoint": "mechatronics-rack-01",
        "topic_prefix": "ledwall/node01",
        "pixel_count": BIN_COUNT,
        "rows": ROWS,
        "columns": COLUMNS,
        "origin": "top-left",
        "color_order": "GRB",
        "bins": [
            {"bin_id": f"bin-{row * COLUMNS + column + 1:02d}", "led_index": serpentine_led_index(row, column)}
            for row in range(ROWS)
            for column in range(COLUMNS)
        ],
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
