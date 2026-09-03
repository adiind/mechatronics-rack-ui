#!/usr/bin/env python3
"""Convert the pilot rack-mcp inventory.csv (pixel,id,part,category,priority,qty,tags,url)
into the versioned inventory record this app stores.

Pixel N maps to led_index N, i.e. bin-(N+1) in the seed config. Pixels beyond the
mapped bin count are kept as stocked-but-unplaced items with no location.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

from rack.inventory_store import save_inventory, validate_inventory
from rack.rack_config import load_rack_config

CATEGORY_SLUGS = {
    "Control & prototyping": "control_prototyping",
    "Inputs & sensing": "inputs_sensing",
    "Actuators & outputs": "actuators_outputs",
    "Drivers, power & electronics": "drivers_power_electronics",
    "Mechanisms & build hardware": "mechanisms_build_hardware",
}


def convert(rows: list[dict], config: dict, *, verified_at: str | None) -> dict:
    bin_by_index = {entry["led_index"]: entry["bin_id"] for entry in config["bins"]}
    items = []
    bins = {f"{config['rack_id']}/{entry['bin_id']}": {"state": "unknown", "last_verified_at": None} for entry in config["bins"]}
    for row in rows:
        pixel = int(row["pixel"])
        bin_id = bin_by_index.get(pixel)
        quantity = int(row["qty"]) if (row.get("qty") or "").strip() else None
        item = {
            "item_id": row["id"].strip(),
            "display_name": row["part"].strip(),
            "category": CATEGORY_SLUGS.get(row["category"].strip(), "electronic_part"),
            "quantity": quantity,
            "unit": "pcs",
            "availability": "available" if quantity is None or quantity > 0 else "depleted",
            "locations": [f"{config['rack_id']}/{bin_id}"] if bin_id else [],
            "last_verified_at": verified_at if bin_id else None,
            "notes": (row.get("tags") or "").strip(),
        }
        if (row.get("priority") or "").strip():
            item["priority"] = row["priority"].strip()
        if (row.get("url") or "").strip():
            item["product_url"] = row["url"].strip()
        items.append(item)
    return validate_inventory({"version": 1, "items": items, "bins": bins}, config)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "rack")
    parser.add_argument("--mark-verified", action="store_true", help="stamp mapped items as verified now")
    args = parser.parse_args()
    config = load_rack_config(args.data_dir / "rack-01.json")
    with args.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    verified_at = datetime.now(timezone.utc).isoformat() if args.mark_verified else None
    inventory = convert(rows, config, verified_at=verified_at)
    save_inventory(args.data_dir / "inventory.json", inventory)
    placed = sum(1 for item in inventory["items"] if item["locations"])
    print(f"wrote {len(inventory['items'])} items ({placed} in mapped bins, {len(inventory['items']) - placed} unplaced) to {args.data_dir / 'inventory.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
