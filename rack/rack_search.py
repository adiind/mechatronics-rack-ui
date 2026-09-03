"""Pure search over the verified inventory record."""

from __future__ import annotations

from rack.rack_config import led_index_for


def _rank(item: dict, needle: str) -> int:
    """Name matches beat note matches: searching "esp32" should not surface a
    caliper whose note mentions the esp32 drawer above the ESP32 itself."""
    if not needle:
        return 9
    if item["item_id"].lower() == needle:
        return 0
    if item["display_name"].lower().startswith(needle):
        return 1
    if needle in item["item_id"].lower() or needle in item["display_name"].lower():
        return 2
    return 3


def _matches(item: dict, needle: str) -> bool:
    if not needle:
        return True
    haystack = " ".join((item["item_id"], item["display_name"], item["category"], item["notes"])).lower()
    return needle in haystack


def _locations(item: dict, config: dict) -> list[dict]:
    entries = []
    for location in sorted(item["locations"]):
        rack_id, _, bin_id = location.partition("/")
        entries.append(
            {
                "location": location,
                "rack_id": rack_id,
                "bin_id": bin_id,
                "led_index": led_index_for(config, bin_id),
            }
        )
    return entries


def search_items(
    inventory: dict,
    config: dict,
    *,
    query: str = "",
    category: str | None = None,
    availability: str | None = None,
) -> list[dict]:
    needle = (query or "").strip().lower()
    selected = [
        item
        for item in inventory["items"]
        if _matches(item, needle)
        and (category is None or item["category"] == category)
        and (availability is None or item["availability"] == availability)
    ]
    selected.sort(key=lambda item: (_rank(item, needle), item["display_name"].lower()))
    results = []
    for item in selected:
        locations = _locations(item, config)
        results.append(
            {
                "item_id": item["item_id"],
                "display_name": item["display_name"],
                "category": item["category"],
                "availability": item["availability"],
                "quantity": item["quantity"],
                "unit": item["unit"],
                "last_verified_at": item["last_verified_at"],
                "notes": item["notes"],
                "priority": item.get("priority"),
                "unit_price_usd": item.get("unit_price_usd"),
                "locations": locations,
                "location_count": len(locations),
                "mapped": bool(locations),
            }
        )
    return results
