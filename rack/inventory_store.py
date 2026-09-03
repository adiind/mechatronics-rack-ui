"""The versioned inventory record, its validation, updates, and audit trail."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from rack.storage import atomic_write_json
from rack.rack_config import bin_ids

# The generic five, plus the five the studio's own ordering sheet uses
# (EDI Materials to order - Mechatronics). Both are valid so an import from the
# sheet does not have to flatten its own taxonomy into ours.
CATEGORIES = {
    "material",
    "electronic_part",
    "tool",
    "machine",
    "consumable",
    "control_prototyping",
    "inputs_sensing",
    "actuators_outputs",
    "drivers_power_electronics",
    "mechanisms_build_hardware",
}
AVAILABILITIES = {"available", "checked_out", "depleted", "unknown", "maintenance"}
BIN_STATES = {"unknown", "empty"}
ITEM_KEYS = {
    "item_id",
    "display_name",
    "category",
    "quantity",
    "unit",
    "availability",
    "locations",
    "last_verified_at",
    "notes",
}
# Facts the ordering sheet carries that the rack itself does not need. Optional
# so a hand-written or CSV-round-tripped item stays valid without them.
OPTIONAL_ITEM_KEYS = {"vendor", "product_url", "unit_price_usd", "priority"}
PRIORITIES = {"Core", "Useful", "Optional"}


class InventoryError(ValueError):
    """A stable, user-facing inventory error."""


def _known_locations(config: dict) -> set[str]:
    return {f"{config['rack_id']}/{bin_id}" for bin_id in bin_ids(config)}


def validate_inventory(inventory: object, config: dict) -> dict:
    if not isinstance(inventory, dict) or set(inventory) != {"version", "items", "bins"}:
        raise InventoryError("invalid_inventory")
    if inventory["version"] != 1:
        raise InventoryError("unsupported_version")
    known = _known_locations(config)

    if not isinstance(inventory["items"], list):
        raise InventoryError("invalid_inventory")
    seen_items: set[str] = set()
    for item in inventory["items"]:
        if not isinstance(item, dict) or not ITEM_KEYS <= set(item):
            raise InventoryError("invalid_item")
        if set(item) - ITEM_KEYS - OPTIONAL_ITEM_KEYS:
            raise InventoryError("invalid_item")
        if "priority" in item and item["priority"] not in PRIORITIES:
            raise InventoryError("invalid_priority")
        if "unit_price_usd" in item and item["unit_price_usd"] is not None:
            if not isinstance(item["unit_price_usd"], (int, float)) or item["unit_price_usd"] < 0:
                raise InventoryError("invalid_unit_price")
        for key in ("vendor", "product_url"):
            if key in item and item[key] is not None and not isinstance(item[key], str):
                raise InventoryError("invalid_item")
        if not isinstance(item["item_id"], str) or not item["item_id"].strip():
            raise InventoryError("invalid_item")
        if item["item_id"] in seen_items:
            raise InventoryError("duplicate_item_id")
        seen_items.add(item["item_id"])
        if not isinstance(item["display_name"], str) or not item["display_name"].strip():
            raise InventoryError("invalid_item")
        if item["category"] not in CATEGORIES:
            raise InventoryError("invalid_category")
        if item["availability"] not in AVAILABILITIES:
            raise InventoryError("invalid_availability")
        if item["quantity"] is not None and (type(item["quantity"]) is not int or item["quantity"] < 0):
            raise InventoryError("invalid_quantity")
        if not isinstance(item["unit"], str) or not isinstance(item["notes"], str):
            raise InventoryError("invalid_item")
        if not isinstance(item["locations"], list) or len(set(item["locations"])) != len(item["locations"]):
            raise InventoryError("invalid_locations")
        for location in item["locations"]:
            if location not in known:
                raise InventoryError("unknown_location")
        if item["last_verified_at"] is not None and not isinstance(item["last_verified_at"], str):
            raise InventoryError("invalid_item")

    if not isinstance(inventory["bins"], dict) or set(inventory["bins"]) != known:
        raise InventoryError("bin_record_mismatch")
    for record in inventory["bins"].values():
        if not isinstance(record, dict) or set(record) != {"state", "last_verified_at"}:
            raise InventoryError("invalid_bin_record")
        if record["state"] not in BIN_STATES:
            raise InventoryError("invalid_bin_state")
        if record["last_verified_at"] is not None and not isinstance(record["last_verified_at"], str):
            raise InventoryError("invalid_bin_record")
    return inventory


def load_inventory(path: Path, config: dict) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError("unreadable_inventory") from exc
    return validate_inventory(raw, config)


def save_inventory(path: Path, inventory: dict) -> None:
    atomic_write_json(Path(path), inventory)


def append_audit(path: Path, entry: dict) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    return entry


def _find_item(inventory: dict, item_id: object) -> dict:
    for item in inventory["items"]:
        if item["item_id"] == item_id:
            return item
    raise InventoryError("unknown_item")


def _require_location(location: object, config: dict) -> str:
    if location not in _known_locations(config):
        raise InventoryError("unknown_location")
    return str(location)


def apply_update(inventory: dict, update: object, config: dict, *, actor: str, now: str) -> tuple[dict, dict]:
    if not isinstance(update, dict) or not isinstance(update.get("action"), str):
        raise InventoryError("unknown_action")
    action = update["action"]
    working = copy.deepcopy(inventory)
    before: dict = {}
    after: dict = {}

    if action == "assign":
        item = _find_item(working, update.get("item_id"))
        location = _require_location(update.get("location"), config)
        before = {"locations": list(item["locations"])}
        if location not in item["locations"]:
            item["locations"].append(location)
        item["last_verified_at"] = now
        working["bins"][location] = {"state": "unknown", "last_verified_at": now}
        after = {"locations": list(item["locations"])}
    elif action == "move":
        item = _find_item(working, update.get("item_id"))
        source = _require_location(update.get("from_location"), config)
        target = _require_location(update.get("to_location"), config)
        if source not in item["locations"]:
            raise InventoryError("item_not_at_location")
        before = {"locations": list(item["locations"])}
        item["locations"] = [target if entry == source else entry for entry in item["locations"]]
        item["last_verified_at"] = now
        working["bins"][source] = {"state": "empty", "last_verified_at": now}
        after = {"locations": list(item["locations"])}
    elif action == "clear":
        location = _require_location(update.get("location"), config)
        before = {"items_at_location": [item["item_id"] for item in working["items"] if location in item["locations"]]}
        for item in working["items"]:
            if location in item["locations"]:
                item["locations"] = [entry for entry in item["locations"] if entry != location]
                item["last_verified_at"] = now
        working["bins"][location] = {"state": "empty", "last_verified_at": now}
        after = {"items_at_location": []}
    elif action == "mark_unknown":
        location = _require_location(update.get("location"), config)
        before = {"bin": dict(working["bins"][location])}
        working["bins"][location] = {"state": "unknown", "last_verified_at": None}
        after = {"bin": dict(working["bins"][location])}
    elif action == "set_availability":
        item = _find_item(working, update.get("item_id"))
        if update.get("availability") not in AVAILABILITIES:
            raise InventoryError("invalid_availability")
        before = {"availability": item["availability"]}
        item["availability"] = update["availability"]
        item["last_verified_at"] = now
        after = {"availability": item["availability"]}
    elif action == "set_quantity":
        item = _find_item(working, update.get("item_id"))
        quantity = update.get("quantity")
        if quantity is not None and (type(quantity) is not int or quantity < 0):
            raise InventoryError("invalid_quantity")
        before = {"quantity": item["quantity"]}
        item["quantity"] = quantity
        item["last_verified_at"] = now
        after = {"quantity": item["quantity"]}
    elif action == "upsert_item":
        candidate = update.get("item")
        if not isinstance(candidate, dict):
            raise InventoryError("invalid_item")
        candidate = dict(candidate)
        candidate.setdefault("locations", [])
        candidate.setdefault("last_verified_at", now)
        candidate.setdefault("notes", "")
        existing = next((item for item in working["items"] if item["item_id"] == candidate.get("item_id")), None)
        before = {"item": copy.deepcopy(existing)}
        if existing is None:
            working["items"].append(candidate)
        else:
            existing.update(candidate)
        after = {"item": copy.deepcopy(_find_item(working, candidate.get("item_id")))}
    else:
        raise InventoryError("unknown_action")

    validate_inventory(working, config)
    entry = {
        "at": now,
        "actor": actor,
        "action": action,
        "target": update.get("item_id") or update.get("location") or update.get("to_location"),
        "before": before,
        "after": after,
    }
    return working, entry


def bin_occupancy(inventory: dict, config: dict) -> dict[str, dict]:
    occupancy: dict[str, dict] = {}
    for bin_id in bin_ids(config):
        location = f"{config['rack_id']}/{bin_id}"
        record = inventory["bins"][location]
        items = [
            {
                "item_id": item["item_id"],
                "display_name": item["display_name"],
                "category": item["category"],
                "availability": item["availability"],
                "quantity": item["quantity"],
                "unit": item["unit"],
                "notes": item["notes"],
                "last_verified_at": item["last_verified_at"],
                "vendor": item.get("vendor"),
                "product_url": item.get("product_url"),
                "unit_price_usd": item.get("unit_price_usd"),
                "priority": item.get("priority"),
            }
            for item in inventory["items"]
            if location in item["locations"]
        ]
        occupancy[bin_id] = {
            "bin_id": bin_id,
            "location": location,
            "state": "occupied" if items else record["state"],
            "last_verified_at": record["last_verified_at"],
            "items": items,
        }
    return occupancy
