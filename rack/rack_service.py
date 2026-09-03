"""The rack coordinator: one owner for inventory state and rack lighting."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from rack.inventory_csv import diff_inventory, export_csv_text, parse_csv
from rack.inventory_store import apply_update, append_audit, bin_occupancy, load_inventory, save_inventory
from rack.mqtt_transport import command_topic
from rack.rack_config import color_order
from rack.rack_lighting import DEFAULT_TTL_SECONDS, build_locate_plan, build_preview_plan, clear_command, wire_command
from rack.rack_search import search_items

PREVIEW_TTL_SECONDS = 5


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RackService:
    def __init__(
        self,
        config: dict,
        inventory_path: Path,
        audit_path: Path,
        transport,
        *,
        clock=time.monotonic,
        timestamp=_utc_now,
    ):
        self._config = config
        self._inventory_path = Path(inventory_path)
        self._audit_path = Path(audit_path)
        self._transport = transport
        self._clock = clock
        self._timestamp = timestamp
        self._lock = threading.RLock()
        self._highlight: dict | None = None
        self._inventory = load_inventory(self._inventory_path, config)

    def _publish_plan(self, plan: dict) -> None:
        topic = command_topic(self._config)
        order = color_order(self._config)
        for frame in plan["frames"]:
            # Frames are milliseconds apart; the ripple is a courtesy, not a
            # correctness requirement, so a slow broker must never block a
            # request. Send them back to back and let the string catch up.
            for command in frame["commands"]:
                self._transport.publish(topic, wire_command(command, order))

    def _start_highlight(self, plan: dict, kind: str) -> dict:
        session_id = uuid.uuid4().hex
        self._publish_plan(plan)
        self._highlight = {
            "session_id": session_id,
            "kind": kind,
            "lit": plan["lit"],
            "expires_at": self._clock() + plan["ttl_seconds"],
            "started_at": self._timestamp(),
        }
        return self._highlight

    def tick(self) -> None:
        with self._lock:
            if self._highlight and self._clock() >= self._highlight["expires_at"]:
                self._highlight = None
                self._transport.publish(command_topic(self._config), clear_command())

    def clear_highlight(self) -> dict:
        with self._lock:
            self._highlight = None
            self._transport.publish(command_topic(self._config), clear_command())
            return {"cleared": True}

    def search(self, query: str = "", category: str | None = None, availability: str | None = None) -> list[dict]:
        with self._lock:
            return search_items(
                self._inventory, self._config, query=query, category=category, availability=availability
            )

    def locate(self, item_ids: list[str], *, ttl_seconds: int | None = None) -> dict:
        with self._lock:
            self.tick()
            known = {item["item_id"]: item for item in self._inventory["items"]}
            selections: list[dict] = []
            unmapped: list[str] = []
            unknown_items: list[str] = []
            for item_id in item_ids:
                item = known.get(item_id)
                if item is None:
                    unknown_items.append(item_id)
                    continue
                if not item["locations"]:
                    unmapped.append(item_id)
                    continue
                for location in sorted(item["locations"]):
                    selections.append({"item_id": item_id, "bin_id": location.partition("/")[2]})
            plan = build_locate_plan(self._config, selections, ttl_seconds=ttl_seconds or DEFAULT_TTL_SECONDS)
            highlight = self._start_highlight(plan, "locate") if selections else None
            return {
                "session_id": highlight["session_id"] if highlight else None,
                "expires_in": plan["ttl_seconds"] if highlight else 0,
                "lit": plan["lit"],
                "unmapped": unmapped,
                "unknown_items": unknown_items,
            }

    def preview_bin(self, bin_id: str) -> dict:
        with self._lock:
            plan = build_preview_plan(self._config, bin_id, ttl_seconds=PREVIEW_TTL_SECONDS)
            highlight = self._start_highlight(plan, "preview")
            return {
                "session_id": highlight["session_id"],
                "expires_in": PREVIEW_TTL_SECONDS,
                "lit": plan["lit"],
            }

    def inventory_snapshot(self) -> dict:
        """A copy of the verified inventory record for read-only consumers such as chat."""
        with self._lock:
            return json.loads(json.dumps(self._inventory))

    def snapshot(self) -> dict:
        with self._lock:
            self.tick()
            highlight = None
            if self._highlight:
                highlight = {
                    "session_id": self._highlight["session_id"],
                    "kind": self._highlight["kind"],
                    "lit": self._highlight["lit"],
                    "expires_in": max(0, round(self._highlight["expires_at"] - self._clock())),
                }
            return {
                "rack": {
                    "rack_id": self._config["rack_id"],
                    "display_name": self._config["display_name"],
                    "endpoint": self._config["endpoint"],
                    "rows": self._config["rows"],
                    "columns": self._config["columns"],
                    "origin": self._config["origin"],
                    "unit_style": self._config.get("unit_style", "bin_rack"),
                },
                "bins": bin_occupancy(self._inventory, self._config),
                "endpoint_availability": self._transport.availability(),
                "highlight": highlight,
                "generated_at": self._timestamp(),
            }

    def update_inventory(self, update: dict, *, actor: str) -> dict:
        with self._lock:
            updated, entry = apply_update(self._inventory, update, self._config, actor=actor, now=self._timestamp())
            save_inventory(self._inventory_path, updated)
            append_audit(self._audit_path, entry)
            self._inventory = updated
            return {"audit": entry, "snapshot": self.snapshot()}

    def export_csv(self) -> str:
        with self._lock:
            return export_csv_text(self._inventory)

    def import_inventory(self, text: str, *, actor: str, apply: bool) -> dict:
        with self._lock:
            incoming = parse_csv(text, self._config, existing=self._inventory)
            difference = diff_inventory(self._inventory, incoming)
            if not apply:
                return {**difference, "applied": False}
            save_inventory(self._inventory_path, incoming)
            entry = {
                "at": self._timestamp(),
                "actor": actor,
                "action": "csv_import",
                "target": None,
                "before": {"item_count": len(self._inventory["items"])},
                "after": {"item_count": len(incoming["items"])},
            }
            append_audit(self._audit_path, entry)
            self._inventory = incoming
            return {**difference, "applied": True, "audit": entry}

    def audit_tail(self, limit: int = 20) -> list[dict]:
        if not self._audit_path.exists():
            return []
        lines = self._audit_path.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(line) for line in lines[-limit:] if line.strip()][::-1]
