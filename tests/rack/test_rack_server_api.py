import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from rack.inventory_store import save_inventory
from rack.mqtt_transport import RecordingTransport
from rack.rack_config import validate_rack_config
from rack.rack_server import create_rack_server
from rack.rack_service import RackService
from scripts.seed_rack import seed_inventory, seed_rack_config

NOW = "2026-09-01T18:00:00+00:00"
TOKEN = "test-operator-token"


class RackServerApiTests(unittest.TestCase):
    def setUp(self):
        os.environ["RACK_OPERATOR_TOKEN"] = TOKEN
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        config = validate_rack_config(seed_rack_config())
        inventory = seed_inventory()
        inventory["items"] = [
            {
                "item_id": "esp32-c6",
                "display_name": "XIAO ESP32-C6",
                "category": "electronic_part",
                "quantity": 9,
                "unit": "pcs",
                "availability": "available",
                "locations": ["rack-01/bin-07"],
                "last_verified_at": NOW,
                "notes": "",
            }
        ]
        save_inventory(data_dir / "inventory.json", inventory)
        self.transport = RecordingTransport()
        service = RackService(
            config,
            data_dir / "inventory.json",
            data_dir / "audit.jsonl",
            self.transport,
            timestamp=lambda: NOW,
        )
        static_root = Path(__file__).resolve().parents[2] / "rack" / "static"
        self.server = create_rack_server("127.0.0.1", 0, service, static_root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.tmp.cleanup()
        os.environ.pop("RACK_OPERATOR_TOKEN", None)

    def request(self, path, method="GET", payload=None, token=None, raw=False):
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["X-Rack-Operator"] = token
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                text = response.read().decode("utf-8")
                return response.status, text if raw else json.loads(text)
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8")
            return error.code, text if raw else json.loads(text)

    def test_rack_snapshot_is_public(self):
        status, payload = self.request("/api/rack")
        self.assertEqual(status, 200)
        self.assertEqual(payload["rack"]["rack_id"], "rack-01")
        self.assertEqual(len(payload["bins"]), 42)

    def test_search_returns_results_and_count(self):
        status, payload = self.request("/api/search?q=esp32")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["results"][0]["location_count"], 1)

    def test_locate_is_public_and_lights_the_bin(self):
        status, payload = self.request("/api/locate", "POST", {"item_ids": ["esp32-c6"]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["lit"][0]["bin_id"], "bin-07")
        self.assertTrue(self.transport.published)

    def test_locate_rejects_a_non_list_payload(self):
        status, payload = self.request("/api/locate", "POST", {"item_ids": "esp32-c6"})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_update_without_a_token_is_rejected(self):
        status, payload = self.request(
            "/api/inventory/update", "POST", {"action": "clear", "location": "rack-01/bin-07"}
        )
        self.assertEqual(status, 401)
        self.assertEqual(payload["error"], "operator_token_invalid")

    def test_update_with_the_token_succeeds(self):
        status, payload = self.request(
            "/api/inventory/update",
            "POST",
            {"action": "clear", "location": "rack-01/bin-07"},
            token=TOKEN,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["snapshot"]["bins"]["bin-07"]["state"], "empty")
        self.assertEqual(payload["audit"]["action"], "clear")

    def test_unknown_bin_update_returns_a_visible_mapping_error(self):
        status, payload = self.request(
            "/api/inventory/update",
            "POST",
            {"action": "clear", "location": "rack-01/bin-99"},
            token=TOKEN,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "unknown_location")

    def test_preview_requires_the_token(self):
        self.assertEqual(self.request("/api/preview", "POST", {"bin_id": "bin-03"})[0], 401)
        status, payload = self.request("/api/preview", "POST", {"bin_id": "bin-03"}, token=TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(payload["lit"][0]["bin_id"], "bin-03")

    def test_csv_export_is_operator_only_and_returns_csv(self):
        self.assertEqual(self.request("/api/inventory/export.csv")[0], 401)
        status, text = self.request("/api/inventory/export.csv", token=TOKEN, raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(text.startswith("item_id,display_name"))

    def test_csv_import_dry_run_does_not_apply(self):
        _, text = self.request("/api/inventory/export.csv", token=TOKEN, raw=True)
        status, payload = self.request(
            "/api/inventory/import", "POST", {"csv": text.replace(",9,", ",4,"), "apply": False}, token=TOKEN
        )
        self.assertEqual(status, 200)
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["changed"][0]["item_id"], "esp32-c6")
        _, snapshot = self.request("/api/rack")
        self.assertEqual(snapshot["bins"]["bin-07"]["items"][0]["quantity"], 9)

    def test_missing_token_env_reports_not_configured(self):
        os.environ.pop("RACK_OPERATOR_TOKEN")
        status, payload = self.request("/api/preview", "POST", {"bin_id": "bin-03"}, token=TOKEN)
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "operator_token_not_configured")

    def test_static_page_is_served_at_root(self):
        status, text = self.request("/", raw=True)
        self.assertEqual(status, 200)
        self.assertIn("Mechatronics rack", text)

    def test_path_traversal_is_refused(self):
        self.assertEqual(self.request("/../server.py", raw=True)[0], 403)

    def test_health_reports_endpoint_availability(self):
        status, payload = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["endpoint_availability"], "unknown")
