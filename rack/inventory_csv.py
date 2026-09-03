"""CSV is the portable import/export format; JSON stays the runtime record."""

from __future__ import annotations

import csv
import io

from rack.inventory_store import InventoryError, validate_inventory

CSV_COLUMNS = [
    "item_id",
    "display_name",
    "category",
    "quantity",
    "unit",
    "availability",
    "locations",
    "last_verified_at",
    "notes",
]


def export_csv_text(inventory: dict) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for item in sorted(inventory["items"], key=lambda entry: entry["item_id"]):
        writer.writerow(
            {
                **{
                    key: item[key]
                    for key in CSV_COLUMNS
                    if key not in {"locations", "quantity", "last_verified_at"}
                },
                "quantity": "" if item["quantity"] is None else item["quantity"],
                "locations": ";".join(sorted(item["locations"])),
                "last_verified_at": item["last_verified_at"] or "",
            }
        )
    return buffer.getvalue()


def parse_csv(text: str, config: dict, *, existing: dict) -> dict:
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != CSV_COLUMNS:
        raise InventoryError("invalid_csv_header")

    items = []
    for offset, row in enumerate(reader):
        line = offset + 2
        quantity_text = (row["quantity"] or "").strip()
        try:
            quantity = int(quantity_text) if quantity_text else None
        except ValueError:
            raise InventoryError(f"row_{line}_invalid_quantity") from None
        item = {
            "item_id": (row["item_id"] or "").strip(),
            "display_name": (row["display_name"] or "").strip(),
            "category": (row["category"] or "").strip(),
            "quantity": quantity,
            "unit": (row["unit"] or "").strip(),
            "availability": (row["availability"] or "").strip(),
            "locations": [part for part in (row["locations"] or "").split(";") if part.strip()],
            "last_verified_at": (row["last_verified_at"] or "").strip() or None,
            "notes": row["notes"] or "",
        }
        # The CSV carries the nine operator-editable columns only. Anything the
        # sheet import attached (price, vendor, link, priority) is preserved from
        # the current record rather than wiped by a round trip.
        previous = next((entry for entry in existing["items"] if entry["item_id"] == item["item_id"]), None)
        if previous:
            for key in ("vendor", "product_url", "unit_price_usd", "priority"):
                if key in previous:
                    item[key] = previous[key]
        try:
            validate_inventory({"version": 1, "items": [item], "bins": existing["bins"]}, config)
        except InventoryError as exc:
            raise InventoryError(f"row_{line}_{exc}") from None
        items.append(item)

    incoming = {"version": 1, "items": items, "bins": existing["bins"]}
    return validate_inventory(incoming, config)


def diff_inventory(current: dict, incoming: dict) -> dict:
    current_by_id = {item["item_id"]: item for item in current["items"]}
    incoming_by_id = {item["item_id"]: item for item in incoming["items"]}
    added = sorted(set(incoming_by_id) - set(current_by_id))
    removed = sorted(set(current_by_id) - set(incoming_by_id))
    changed = []
    for item_id in sorted(set(current_by_id) & set(incoming_by_id)):
        fields = [
            key
            for key in CSV_COLUMNS
            if key != "item_id" and current_by_id[item_id][key] != incoming_by_id[item_id][key]
        ]
        if fields:
            changed.append({"item_id": item_id, "fields": fields})
    return {"added": added, "changed": changed, "removed": removed}
