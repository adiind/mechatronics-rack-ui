import json
import tempfile
import unittest
from pathlib import Path

from rack.inventory_store import save_inventory
from rack.mqtt_transport import RecordingTransport, command_topic
from rack.rack_config import validate_rack_config
from rack.rack_service import RackService
from scripts.seed_rack import seed_inventory, seed_rack_config

NOW = "2026-09-01T18:00:00+00:00"


class FakeClock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def make_item(item_id, name, locations=()):
    return {
        "item_id": item_id,
        "display_name": name,
        "category": "electronic_part",
        "quantity": 4,
        "unit": "pcs",
        "availability": "available",
        "locations": list(locations),
        "last_verified_at": NOW,
        "notes": "",
    }


class RackServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.config = validate_rack_config(seed_rack_config())
        inventory = seed_inventory()
        inventory["items"] = [
            make_item("esp32-c6", "XIAO ESP32-C6", locations=("rack-01/bin-07", "rack-01/bin-12")),
            make_item("m3-screws", "M3 screws"),
        ]
        self.inventory_path = self.data_dir / "inventory.json"
        self.audit_path = self.data_dir / "audit.jsonl"
        save_inventory(self.inventory_path, inventory)
        self.clock = FakeClock()
        self.transport = RecordingTransport()
        self.service = RackService(
            self.config,
            self.inventory_path,
            self.audit_path,
            self.transport,
            clock=self.clock,
            timestamp=lambda: NOW,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def published_ops(self):
        return [payload for topic, payload in self.transport.published if topic == command_topic(self.config)]

    def test_locate_publishes_every_frame_and_reports_lit_bins(self):
        result = self.service.locate(["esp32-c6"])
        self.assertEqual([entry["bin_id"] for entry in result["lit"]], ["bin-07", "bin-12"])
        self.assertEqual(self.published_ops()[0], {"op": "off"})
        self.assertEqual(self.published_ops()[-1]["op"], "pixel")

    def test_unmapped_item_is_reported_not_lit(self):
        result = self.service.locate(["m3-screws"])
        self.assertEqual(result["unmapped"], ["m3-screws"])
        self.assertEqual(result["lit"], [])

    def test_unknown_item_is_reported_without_raising(self):
        result = self.service.locate(["ghost"])
        self.assertEqual(result["unknown_items"], ["ghost"])

    def test_highlight_expires_and_clears_once(self):
        self.service.locate(["esp32-c6"], ttl_seconds=10)
        before = len(self.published_ops())
        self.clock.advance(11)
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.published_ops()[before:], [{"op": "off"}])
        self.assertIsNone(self.service.snapshot()["highlight"])

    def test_new_locate_replaces_the_previous_highlight(self):
        first = self.service.locate(["esp32-c6"])
        second = self.service.locate(["esp32-c6"])
        self.assertNotEqual(first["session_id"], second["session_id"])
        self.assertEqual(self.service.snapshot()["highlight"]["session_id"], second["session_id"])

    def test_snapshot_exposes_bin_states_and_availability(self):
        snapshot = self.service.snapshot()
        self.assertEqual(snapshot["rack"]["rack_id"], "rack-01")
        self.assertEqual(snapshot["bins"]["bin-07"]["state"], "occupied")
        self.assertEqual(snapshot["bins"]["bin-01"]["state"], "unknown")
        self.assertEqual(snapshot["endpoint_availability"], "unknown")

    def test_update_persists_and_writes_audit(self):
        self.service.update_inventory(
            {"action": "assign", "item_id": "m3-screws", "location": "rack-01/bin-05"},
            actor="adi",
        )
        stored = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        item = next(entry for entry in stored["items"] if entry["item_id"] == "m3-screws")
        self.assertEqual(item["locations"], ["rack-01/bin-05"])
        audit_line = json.loads(self.audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertEqual(audit_line["action"], "assign")
        self.assertEqual(audit_line["actor"], "adi")

    def test_preview_lights_one_bin_with_a_short_ttl(self):
        result = self.service.preview_bin("bin-03")
        self.assertEqual(result["lit"][0]["bin_id"], "bin-03")
        self.assertLessEqual(result["expires_in"], 5)

    def test_export_csv_round_trips_through_import(self):
        text = self.service.export_csv()
        self.assertIn("esp32-c6", text)
        preview = self.service.import_inventory(text, actor="adi", apply=False)
        self.assertEqual(preview["changed"], [])
        self.assertEqual(preview["added"], [])
        self.assertEqual(preview["removed"], [])
        self.assertFalse(preview["applied"])
