import json
import tempfile
import unittest
from pathlib import Path

from rack.mqtt_transport import (
    RecordingTransport,
    TransportError,
    ack_topic,
    command_topic,
    load_client_config,
    status_topic,
)
from rack.rack_config import validate_rack_config
from scripts.seed_rack import seed_rack_config


class TransportTopicTests(unittest.TestCase):
    def setUp(self):
        self.config = validate_rack_config(seed_rack_config())

    def test_topics_derive_from_the_configured_prefix(self):
        self.assertEqual(command_topic(self.config), "ledwall/node01/set")
        self.assertEqual(status_topic(self.config), "ledwall/node01/status")
        self.assertEqual(ack_topic(self.config), "ledwall/node01/ack")


class RecordingTransportTests(unittest.TestCase):
    def test_publish_records_topic_and_payload(self):
        transport = RecordingTransport()
        transport.publish("ledwall/node01/set", {"op": "off"})
        self.assertEqual(transport.published, [("ledwall/node01/set", {"op": "off"})])

    def test_availability_starts_unknown_and_is_settable(self):
        transport = RecordingTransport()
        self.assertEqual(transport.availability(), "unknown")
        transport.set_availability("online")
        self.assertEqual(transport.availability(), "online")

    def test_failing_transport_raises_a_stable_error(self):
        transport = RecordingTransport(fail=True)
        with self.assertRaises(TransportError) as caught:
            transport.publish("ledwall/node01/set", {"op": "off"})
        self.assertEqual(str(caught.exception), "publish_failed")


class ClientConfigTests(unittest.TestCase):
    def test_valid_client_config_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.json"
            path.write_text(
                json.dumps({"host": "127.0.0.1", "port": 1883, "username": "rack", "password": "secret"}),
                encoding="utf-8",
            )
            config = load_client_config(path)
            self.assertEqual(config["host"], "127.0.0.1")
            self.assertEqual(config["port"], 1883)

    def test_missing_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.json"
            path.write_text(json.dumps({"host": "127.0.0.1", "port": 1883}), encoding="utf-8")
            with self.assertRaises(TransportError) as caught:
                load_client_config(path)
            self.assertEqual(str(caught.exception), "invalid_client_config")

    def test_error_text_never_contains_the_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "client.json"
            path.write_text(
                json.dumps({"host": "", "port": 1883, "username": "rack", "password": "hunter2"}),
                encoding="utf-8",
            )
            with self.assertRaises(TransportError) as caught:
                load_client_config(path)
            self.assertNotIn("hunter2", str(caught.exception))
