"""Publishing side of the rack coordinator.

paho is imported lazily so the pure modules and their tests run without it.
The broker credential is read from an ignored JSON file, never a CLI flag,
and never appears in an error string.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

AVAILABILITY_STATES = {"online", "offline", "unknown"}


class TransportError(RuntimeError):
    """A stable, user-facing transport error. Never carries credential text."""


def command_topic(rack_config: dict) -> str:
    return f"{rack_config['topic_prefix']}/set"


def status_topic(rack_config: dict) -> str:
    return f"{rack_config['topic_prefix']}/status"


def ack_topic(rack_config: dict) -> str:
    return f"{rack_config['topic_prefix']}/ack"


def load_client_config(path: Path) -> dict:
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportError("invalid_client_config") from exc
    if not isinstance(config, dict) or set(config) != {"host", "port", "username", "password"}:
        raise TransportError("invalid_client_config")
    if not all(isinstance(config[key], str) and config[key] for key in ("host", "username", "password")):
        raise TransportError("invalid_client_config")
    if type(config["port"]) is not int or not 1 <= config["port"] <= 65535:
        raise TransportError("invalid_client_config")
    return config


class RecordingTransport:
    """In-memory transport used by tests and by a dry run."""

    def __init__(self, *, fail: bool = False):
        self.published: list[tuple[str, dict]] = []
        self.last_ack: dict | None = None
        self._fail = fail
        self._availability = "unknown"

    def publish(self, topic: str, payload: dict) -> None:
        if self._fail:
            raise TransportError("publish_failed")
        self.published.append((topic, payload))

    def availability(self) -> str:
        return self._availability

    def set_availability(self, state: str) -> None:
        if state not in AVAILABILITY_STATES:
            raise TransportError("invalid_availability_state")
        self._availability = state

    def close(self) -> None:
        return None


class MqttTransport:
    """Live transport against the Pi broker."""

    def __init__(self, client_config: dict, rack_config: dict, *, client_id: str = "rack-coordinator"):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise TransportError("paho_mqtt_not_installed") from exc

        self._lock = threading.Lock()
        self._availability = "unknown"
        self.last_ack: dict | None = None
        self._rack_config = rack_config
        # ESPHome 2026.8.0 pins paho 1.6.1, whose constructor predates
        # CallbackAPIVersion. Keep the same call shape as ledwall_command.py.
        self._client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
        self._client.username_pw_set(client_config["username"], client_config["password"])
        self._client.on_message = self._on_message
        try:
            self._client.connect(client_config["host"], client_config["port"], keepalive=30)
        except OSError as exc:
            raise TransportError("mqtt_connect_failed") from exc
        self._client.subscribe([(status_topic(rack_config), 0), (ack_topic(rack_config), 0)])
        self._client.loop_start()

    def _on_message(self, _client, _userdata, message) -> None:
        payload = message.payload.decode("utf-8", errors="replace")
        with self._lock:
            if message.topic == status_topic(self._rack_config):
                self._availability = payload if payload in AVAILABILITY_STATES else "unknown"
            elif message.topic == ack_topic(self._rack_config):
                try:
                    self.last_ack = json.loads(payload)
                except json.JSONDecodeError:
                    self.last_ack = {"ok": False, "error": "unparseable_ack"}

    def publish(self, topic: str, payload: dict) -> None:
        info = self._client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=0, retain=False)
        info.wait_for_publish(timeout=5.0)
        if not info.is_published():
            raise TransportError("publish_failed")

    def availability(self) -> str:
        with self._lock:
            return self._availability

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
