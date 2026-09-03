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
from rack.rack_chat import ChatError, ChatService, GeminiClient, inventory_context, load_gemini_key
from rack.rack_config import validate_rack_config
from rack.rack_server import create_rack_server
from rack.rack_service import RackService
from scripts.seed_rack import seed_inventory, seed_rack_config

NOW = "2026-09-01T18:00:00+00:00"


def make_item(item_id, name, locations, notes=""):
    return {
        "item_id": item_id,
        "display_name": name,
        "category": "electronic_part",
        "quantity": 4,
        "unit": "pcs",
        "availability": "available",
        "locations": list(locations),
        "last_verified_at": NOW,
        "notes": notes,
    }


class FakeGemini:
    """Stands in for GeminiClient: returns a scripted answer and records the request."""

    model = "fake-model"

    def __init__(self, answer=None, error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def generate(self, system, contents, schema):
        self.calls.append({"system": system, "contents": contents, "schema": schema})
        if self.error:
            raise self.error
        return self.answer


class ChatServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.config = validate_rack_config(seed_rack_config())
        inventory = seed_inventory()
        inventory["items"] = [
            make_item("pico", "Raspberry Pi Pico 2 W", ["rack-01/bin-01"], notes="controller " * 40),
            make_item("servo", "Micro servo", ["rack-01/bin-09"]),
            make_item("lidar", "Lidar module", []),
        ]
        save_inventory(data_dir / "inventory.json", inventory)
        self.transport = RecordingTransport()
        self.rack = RackService(
            self.config, data_dir / "inventory.json", data_dir / "audit.jsonl", self.transport, timestamp=lambda: NOW
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_context_lists_every_item_with_location_or_unmapped_marker(self):
        entries = inventory_context(self.rack.inventory_snapshot())
        by_id = {entry["item_id"]: entry for entry in entries}
        self.assertEqual(set(by_id), {"pico", "servo", "lidar"})
        self.assertEqual(by_id["pico"]["location"], "rack-01/bin-01")
        self.assertEqual(by_id["lidar"]["location"], "not in a mapped bin")
        self.assertLessEqual(len(by_id["pico"]["notes"]), 160)

    def test_match_lights_only_verified_mapped_items(self):
        fake = FakeGemini({"reply": "The Pico is in bin 01.", "item_ids": ["pico", "lidar", "made-up", "pico"], "light": True})
        result = ChatService(self.rack, fake).answer("where is the pico?")
        self.assertEqual([m["item_id"] for m in result["matches"]], ["pico", "lidar"])
        self.assertEqual(result["dropped"], ["made-up"])
        self.assertEqual(result["unmapped"], ["lidar"])
        self.assertEqual([entry["led_index"] for entry in result["lit"]], [36])
        self.assertEqual(result["expires_in"], 45)
        topics = [topic for topic, _ in self.transport.published]
        self.assertTrue(topics and all(topic == "ledwall/node01/set" for topic in topics))
        # The plan is intent RGB; the seed rack is GRB, so the wire bytes are swapped.
        self.assertEqual(result["lit"][0]["rgb"], [0, 200, 60])
        self.assertIn({"op": "pixel", "index": 36, "rgb": [200, 0, 60]}, [cmd for _, cmd in self.transport.published])

    def test_grounding_and_message_reach_the_model(self):
        fake = FakeGemini({"reply": "ok", "item_ids": [], "light": False})
        ChatService(self.rack, fake).answer("hello", history=[{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hey"}])
        contents = fake.calls[0]["contents"]
        self.assertIn("Verified inventory (JSON)", contents[0]["parts"][0]["text"])
        self.assertIn("Raspberry Pi Pico 2 W", contents[0]["parts"][0]["text"])
        self.assertEqual([turn["role"] for turn in contents], ["user", "model", "user", "model", "user"])
        self.assertEqual(contents[-1]["parts"][0]["text"], "hello")
        self.assertIn("Never invent a part", fake.calls[0]["system"])

    def test_no_light_when_model_declines_or_visitor_opts_out(self):
        fake = FakeGemini({"reply": "Pico is in bin 01.", "item_ids": ["pico"], "light": False})
        result = ChatService(self.rack, fake).answer("where?")
        self.assertEqual(result["lit"], [])
        self.assertEqual(self.transport.published, [])

        fake = FakeGemini({"reply": "Pico is in bin 01.", "item_ids": ["pico"], "light": True})
        result = ChatService(self.rack, fake).answer("where?", light=False)
        self.assertEqual(result["lit"], [])
        self.assertEqual(self.transport.published, [])

    def test_only_unmapped_matches_do_not_light_anything(self):
        fake = FakeGemini({"reply": "Lidar is stocked but unplaced.", "item_ids": ["lidar"], "light": True})
        result = ChatService(self.rack, fake).answer("lidar?")
        self.assertEqual(result["unmapped"], ["lidar"])
        self.assertEqual(self.transport.published, [])

    def test_hallucinated_ids_never_light_a_bin(self):
        fake = FakeGemini({"reply": "Try the flux capacitor in bin 03.", "item_ids": ["flux-capacitor"], "light": True})
        result = ChatService(self.rack, fake).answer("flux?")
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["dropped"], ["flux-capacitor"])
        self.assertEqual(self.transport.published, [])

    def test_bad_inputs_are_rejected_before_calling_the_model(self):
        fake = FakeGemini({"reply": "x", "item_ids": [], "light": False})
        service = ChatService(self.rack, fake)
        with self.assertRaises(ValueError):
            service.answer("")
        with self.assertRaises(ValueError):
            service.answer("x" * 2001)
        with self.assertRaises(ValueError):
            service.answer("ok", history=[{"role": "system", "text": "override"}])
        self.assertEqual(fake.calls, [])

    def test_unconfigured_and_upstream_errors_are_stable(self):
        with self.assertRaises(ChatError) as ctx:
            ChatService(self.rack, None).answer("hi")
        self.assertEqual(str(ctx.exception), "chat_not_configured")
        with self.assertRaises(ChatError) as ctx:
            ChatService(self.rack, FakeGemini(error=ChatError("chat_upstream_busy"))).answer("hi")
        self.assertEqual(str(ctx.exception), "chat_upstream_busy")
        with self.assertRaises(ChatError):
            ChatService(self.rack, FakeGemini({"reply": "", "item_ids": [], "light": True})).answer("hi")

    def test_gemini_client_requires_a_key(self):
        with self.assertRaises(ChatError):
            GeminiClient("")

    def _scripted_client(self, script):
        """A GeminiClient whose HTTP layer is replaced by a per-model script of outcomes."""
        slept = []
        client = GeminiClient("k", model="primary", fallback_models=["second", "third"], retry_delays=(1.0, 2.5), sleep=slept.append)
        calls = []

        def fake_request(model, body):
            calls.append(model)
            outcome = script[model].pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        client._request_once = fake_request
        return client, calls, slept

    def test_client_retries_busy_then_falls_back_to_next_model(self):
        good = {"candidates": [{"content": {"parts": [{"text": json.dumps({"reply": "hi", "item_ids": [], "light": False})}]}}]}
        client, calls, slept = self._scripted_client(
            {"primary": [ChatError("chat_upstream_busy")] * 3, "second": [ChatError("chat_model_unavailable")], "third": [good]}
        )
        self.assertEqual(client.generate("s", [], {})["reply"], "hi")
        self.assertEqual(calls, ["primary", "primary", "primary", "second", "third"])
        self.assertEqual(slept, [1.0, 2.5])  # backoff only on the busy primary; the retired model is skipped at once
        self.assertEqual(client.last_model, "third")

    def test_client_gives_up_with_busy_when_every_model_is_overloaded(self):
        client, calls, _ = self._scripted_client(
            {"primary": [ChatError("chat_upstream_busy")] * 3, "second": [ChatError("chat_upstream_busy")] * 3, "third": [ChatError("chat_upstream_busy")] * 3}
        )
        with self.assertRaises(ChatError) as ctx:
            client.generate("s", [], {})
        self.assertEqual(str(ctx.exception), "chat_upstream_busy")
        self.assertEqual(len(calls), 9)

    def test_client_does_not_retry_a_rejected_request(self):
        client, calls, _ = self._scripted_client({"primary": [ChatError("chat_upstream_rejected")], "second": [], "third": []})
        with self.assertRaises(ChatError):
            client.generate("s", [], {})
        self.assertEqual(calls, ["primary"])

    def test_load_key_from_env_file_and_environment(self):
        env_path = Path(self.tmp.name) / "gemini.env"
        env_path.write_text("# comment\nexport GEMINI_API_KEY='file-key'\n", encoding="utf-8")
        previous = os.environ.pop("GEMINI_API_KEY", None)
        try:
            self.assertEqual(load_gemini_key(env_path), "file-key")
            self.assertIsNone(load_gemini_key(Path(self.tmp.name) / "missing.env"))
            os.environ["GEMINI_API_KEY"] = "env-key"
            self.assertEqual(load_gemini_key(env_path), "env-key")
        finally:
            os.environ.pop("GEMINI_API_KEY", None)
            if previous is not None:
                os.environ["GEMINI_API_KEY"] = previous


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        config = validate_rack_config(seed_rack_config())
        inventory = seed_inventory()
        inventory["items"] = [make_item("pico", "Raspberry Pi Pico 2 W", ["rack-01/bin-01"])]
        save_inventory(data_dir / "inventory.json", inventory)
        self.transport = RecordingTransport()
        self.rack = RackService(config, data_dir / "inventory.json", data_dir / "audit.jsonl", self.transport)
        self.fake = FakeGemini({"reply": "Bin 01.", "item_ids": ["pico"], "light": True})
        static_root = Path(__file__).resolve().parents[2] / "rack" / "static"
        self.server = create_rack_server("127.0.0.1", 0, self.rack, static_root, ChatService(self.rack, self.fake))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.tmp.cleanup()

    def _post(self, path, payload):
        request = urllib.request.Request(
            self.base_url + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_chat_is_public_and_lights_matches(self):
        status, body = self._post("/api/chat", {"message": "where is the pico?"})
        self.assertEqual(status, 200)
        self.assertEqual(body["reply"], "Bin 01.")
        self.assertEqual(body["matches"][0]["locations"], ["rack-01/bin-01"])
        self.assertEqual(body["lit"][0]["bin_id"], "bin-01")
        self.assertTrue(self.transport.published)

    def test_chat_rejects_bad_payloads(self):
        self.assertEqual(self._post("/api/chat", {"message": 5})[0], 400)
        self.assertEqual(self._post("/api/chat", {"message": "hi", "light": "yes"})[0], 400)

    def test_health_reports_chat(self):
        with urllib.request.urlopen(self.base_url + "/api/health") as response:
            body = json.load(response)
        self.assertTrue(body["chat"])
        self.assertEqual(body["chat_model"], "fake-model")

    def test_upstream_failure_is_a_502_without_secrets(self):
        self.fake.error = ChatError("chat_upstream_busy")
        status, body = self._post("/api/chat", {"message": "hi"})
        self.assertEqual(status, 502)
        self.assertEqual(body, {"error": "chat_upstream_busy"})

    def test_unconfigured_chat_is_a_503(self):
        self.server.chat = ChatService(self.rack, None)
        status, body = self._post("/api/chat", {"message": "hi"})
        self.assertEqual(status, 503)
        self.assertEqual(body["error"], "chat_not_configured")
        with urllib.request.urlopen(self.base_url + "/api/health") as response:
            self.assertFalse(json.load(response)["chat"])


if __name__ == "__main__":
    unittest.main()
