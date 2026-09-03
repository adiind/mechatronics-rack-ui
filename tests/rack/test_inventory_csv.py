import unittest

from rack.inventory_csv import CSV_COLUMNS, diff_inventory, export_csv_text, parse_csv
from rack.inventory_store import InventoryError
from rack.rack_config import validate_rack_config
from scripts.seed_rack import seed_inventory, seed_rack_config

NOW = "2026-09-01T18:00:00+00:00"


def make_item(item_id="esp32-c6", name="XIAO ESP32-C6", locations=("rack-01/bin-07",), quantity=9):
    return {
        "item_id": item_id,
        "display_name": name,
        "category": "electronic_part",
        "quantity": quantity,
        "unit": "pcs",
        "availability": "available",
        "locations": list(locations),
        "last_verified_at": NOW,
        "notes": "",
    }


class InventoryCsvTests(unittest.TestCase):
    def setUp(self):
        self.config = validate_rack_config(seed_rack_config())
        self.inventory = seed_inventory()
        self.inventory["items"] = [make_item(locations=("rack-01/bin-07", "rack-01/bin-12"))]

    def test_export_has_the_documented_header(self):
        first_line = export_csv_text(self.inventory).splitlines()[0]
        self.assertEqual(first_line.split(","), CSV_COLUMNS)

    def test_multiple_locations_export_semicolon_separated(self):
        row = export_csv_text(self.inventory).splitlines()[1]
        self.assertIn("rack-01/bin-07;rack-01/bin-12", row)

    def test_round_trip_is_lossless(self):
        parsed = parse_csv(export_csv_text(self.inventory), self.config, existing=self.inventory)
        self.assertEqual(parsed["items"], self.inventory["items"])
        self.assertEqual(parsed["bins"], self.inventory["bins"])

    def test_blank_quantity_parses_as_none(self):
        text = export_csv_text(self.inventory).replace(",9,", ",,")
        parsed = parse_csv(text, self.config, existing=self.inventory)
        self.assertIsNone(parsed["items"][0]["quantity"])

    def test_bad_category_is_rejected_with_the_row_number(self):
        text = export_csv_text(self.inventory).replace("electronic_part", "sparkles")
        with self.assertRaises(InventoryError) as caught:
            parse_csv(text, self.config, existing=self.inventory)
        self.assertEqual(str(caught.exception), "row_2_invalid_category")

    def test_unknown_location_is_rejected(self):
        text = export_csv_text(self.inventory).replace("rack-01/bin-07", "rack-01/bin-99")
        with self.assertRaises(InventoryError):
            parse_csv(text, self.config, existing=self.inventory)

    def test_missing_column_is_rejected(self):
        with self.assertRaises(InventoryError) as caught:
            parse_csv("item_id,display_name\nx,y\n", self.config, existing=self.inventory)
        self.assertEqual(str(caught.exception), "invalid_csv_header")

    def test_diff_reports_added_changed_and_removed(self):
        incoming = {
            "version": 1,
            "bins": self.inventory["bins"],
            "items": [
                make_item(locations=("rack-01/bin-07",), quantity=7),
                make_item("solder-wire", "Solder wire", locations=()),
            ],
        }
        difference = diff_inventory(self.inventory, incoming)
        self.assertEqual(difference["added"], ["solder-wire"])
        self.assertEqual(difference["removed"], [])
        self.assertEqual(difference["changed"][0]["item_id"], "esp32-c6")
        self.assertIn("quantity", difference["changed"][0]["fields"])
        self.assertIn("locations", difference["changed"][0]["fields"])
