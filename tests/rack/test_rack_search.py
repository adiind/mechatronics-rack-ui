import unittest

from rack.rack_config import validate_rack_config
from rack.rack_search import search_items
from scripts.seed_rack import seed_inventory, seed_rack_config

NOW = "2026-09-01T18:00:00+00:00"


def make_item(item_id, name, category="electronic_part", availability="available", locations=(), notes=""):
    return {
        "item_id": item_id,
        "display_name": name,
        "category": category,
        "quantity": 4,
        "unit": "pcs",
        "availability": availability,
        "locations": list(locations),
        "last_verified_at": NOW,
        "notes": notes,
    }


class RackSearchTests(unittest.TestCase):
    def setUp(self):
        self.config = validate_rack_config(seed_rack_config())
        self.inventory = seed_inventory()
        self.inventory["items"] = [
            make_item("esp32-c6", "XIAO ESP32-C6", locations=("rack-01/bin-07", "rack-01/bin-12")),
            make_item("solder-wire", "Solder wire", category="consumable", locations=("rack-01/bin-03",)),
            make_item("m3-screws", "M3 screws", category="material", availability="depleted"),
            make_item("caliper", "Digital caliper", category="tool", locations=("rack-01/bin-24",), notes="esp32 drawer"),
        ]

    def test_empty_query_returns_everything_alphabetically(self):
        results = search_items(self.inventory, self.config)
        self.assertEqual(
            [result["display_name"] for result in results],
            ["Digital caliper", "M3 screws", "Solder wire", "XIAO ESP32-C6"],
        )

    def test_multi_location_item_reports_every_location(self):
        result = search_items(self.inventory, self.config, query="esp32")[0]
        self.assertEqual(result["item_id"], "esp32-c6")
        self.assertEqual(result["location_count"], 2)
        self.assertEqual([entry["bin_id"] for entry in result["locations"]], ["bin-07", "bin-12"])
        self.assertEqual([entry["led_index"] for entry in result["locations"]], [35, 30])

    def test_exact_item_id_ranks_above_a_note_match(self):
        results = search_items(self.inventory, self.config, query="esp32-c6")
        self.assertEqual(results[0]["item_id"], "esp32-c6")

    def test_note_text_is_searchable(self):
        results = search_items(self.inventory, self.config, query="drawer")
        self.assertEqual([result["item_id"] for result in results], ["caliper"])

    def test_category_and_availability_filters_combine(self):
        results = search_items(self.inventory, self.config, category="material", availability="depleted")
        self.assertEqual([result["item_id"] for result in results], ["m3-screws"])

    def test_unmapped_item_is_flagged_and_has_no_locations(self):
        result = search_items(self.inventory, self.config, query="M3")[0]
        self.assertFalse(result["mapped"])
        self.assertEqual(result["locations"], [])
        self.assertEqual(result["location_count"], 0)

    def test_search_is_case_insensitive(self):
        self.assertEqual(len(search_items(self.inventory, self.config, query="SOLDER")), 1)
