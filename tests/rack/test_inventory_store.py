import json
import tempfile
import unittest
from pathlib import Path

from rack.inventory_store import (
    InventoryError,
    append_audit,
    apply_update,
    bin_occupancy,
    load_inventory,
    save_inventory,
    validate_inventory,
)
from rack.rack_config import validate_rack_config
from scripts.seed_rack import seed_inventory, seed_rack_config

NOW = "2026-09-01T18:00:00+00:00"


def item(item_id="esp32-c6", locations=("rack-01/bin-07",)):
    return {
        "item_id": item_id,
        "display_name": "XIAO ESP32-C6",
        "category": "electronic_part",
        "quantity": 9,
        "unit": "pcs",
        "availability": "available",
        "locations": list(locations),
        "last_verified_at": NOW,
        "notes": "",
    }


class InventoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.config = validate_rack_config(seed_rack_config())
        self.inventory = seed_inventory()

    def test_seed_inventory_is_valid(self):
        self.assertEqual(validate_inventory(self.inventory, self.config), self.inventory)

    def test_unknown_category_is_rejected(self):
        bad = item()
        bad["category"] = "sparkles"
        self.inventory["items"].append(bad)
        with self.assertRaises(InventoryError) as caught:
            validate_inventory(self.inventory, self.config)
        self.assertEqual(str(caught.exception), "invalid_category")

    def test_location_outside_the_rack_is_rejected(self):
        self.inventory["items"].append(item(locations=("rack-01/bin-99",)))
        with self.assertRaises(InventoryError) as caught:
            validate_inventory(self.inventory, self.config)
        self.assertEqual(str(caught.exception), "unknown_location")

    def test_assign_records_location_and_audit_entry(self):
        self.inventory["items"].append(item(locations=()))
        updated, entry = apply_update(
            self.inventory,
            {"action": "assign", "item_id": "esp32-c6", "location": "rack-01/bin-07"},
            self.config,
            actor="adi",
            now=NOW,
        )
        self.assertEqual(updated["items"][0]["locations"], ["rack-01/bin-07"])
        self.assertEqual(updated["items"][0]["last_verified_at"], NOW)
        self.assertEqual(entry["action"], "assign")
        self.assertEqual(entry["actor"], "adi")
        self.assertEqual(entry["before"]["locations"], [])
        self.assertEqual(entry["after"]["locations"], ["rack-01/bin-07"])

    def test_apply_update_does_not_mutate_the_input(self):
        self.inventory["items"].append(item(locations=()))
        apply_update(
            self.inventory,
            {"action": "assign", "item_id": "esp32-c6", "location": "rack-01/bin-07"},
            self.config,
            actor="adi",
            now=NOW,
        )
        self.assertEqual(self.inventory["items"][0]["locations"], [])

    def test_move_keeps_other_locations(self):
        self.inventory["items"].append(item(locations=("rack-01/bin-07", "rack-01/bin-12")))
        updated, entry = apply_update(
            self.inventory,
            {
                "action": "move",
                "item_id": "esp32-c6",
                "from_location": "rack-01/bin-07",
                "to_location": "rack-01/bin-03",
            },
            self.config,
            actor="adi",
            now=NOW,
        )
        self.assertEqual(sorted(updated["items"][0]["locations"]), ["rack-01/bin-03", "rack-01/bin-12"])
        self.assertEqual(entry["action"], "move")

    def test_clear_marks_the_bin_empty_and_unassigns_items(self):
        self.inventory["items"].append(item())
        updated, _ = apply_update(
            self.inventory,
            {"action": "clear", "location": "rack-01/bin-07"},
            self.config,
            actor="adi",
            now=NOW,
        )
        self.assertEqual(updated["items"][0]["locations"], [])
        self.assertEqual(updated["bins"]["rack-01/bin-07"]["state"], "empty")
        self.assertEqual(updated["bins"]["rack-01/bin-07"]["last_verified_at"], NOW)

    def test_mark_unknown_clears_verification(self):
        updated, _ = apply_update(
            self.inventory,
            {"action": "mark_unknown", "location": "rack-01/bin-02"},
            self.config,
            actor="adi",
            now=NOW,
        )
        self.assertEqual(updated["bins"]["rack-01/bin-02"]["state"], "unknown")
        self.assertIsNone(updated["bins"]["rack-01/bin-02"]["last_verified_at"])

    def test_occupancy_separates_occupied_empty_and_unknown(self):
        self.inventory["items"].append(item())
        self.inventory["bins"]["rack-01/bin-01"] = {"state": "empty", "last_verified_at": NOW}
        occupancy = bin_occupancy(self.inventory, self.config)
        self.assertEqual(occupancy["bin-07"]["state"], "occupied")
        self.assertEqual(occupancy["bin-07"]["items"][0]["item_id"], "esp32-c6")
        self.assertEqual(occupancy["bin-01"]["state"], "empty")
        self.assertEqual(occupancy["bin-02"]["state"], "unknown")

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(InventoryError) as caught:
            apply_update(self.inventory, {"action": "burn"}, self.config, actor="adi", now=NOW)
        self.assertEqual(str(caught.exception), "unknown_action")

    def test_save_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            self.inventory["items"].append(item())
            save_inventory(path, self.inventory)
            self.assertEqual(load_inventory(path, self.config)["items"][0]["item_id"], "esp32-c6")

    def test_audit_appends_one_json_line_per_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            append_audit(path, {"at": NOW, "actor": "adi", "action": "clear"})
            append_audit(path, {"at": NOW, "actor": "adi", "action": "assign"})
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["action"], "assign")
