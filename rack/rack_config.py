"""Rack geometry: bin identity, LED index, and grid position."""

from __future__ import annotations

import json
from pathlib import Path

ORIGINS = {"top-left", "bottom-left"}
UNIT_STYLES = {"bin_rack", "drawer_cabinet"}
REQUIRED_KEYS = {
    "version",
    "rack_id",
    "display_name",
    "endpoint",
    "topic_prefix",
    "pixel_count",
    "rows",
    "columns",
    "origin",
    "bins",
}
# Optional because the 24-bin rack config predates them.
OPTIONAL_KEYS = {"unit_style", "color_order"}
COLOR_ORDERS = {"RGB", "GRB"}


class RackConfigError(ValueError):
    """A stable, user-facing rack configuration error."""


def validate_rack_config(config: object) -> dict:
    if not isinstance(config, dict) or not REQUIRED_KEYS <= set(config):
        raise RackConfigError("invalid_rack_config")
    if set(config) - REQUIRED_KEYS - OPTIONAL_KEYS:
        raise RackConfigError("invalid_rack_config")
    if config.get("unit_style", "bin_rack") not in UNIT_STYLES:
        raise RackConfigError("invalid_unit_style")
    if config.get("color_order", "RGB") not in COLOR_ORDERS:
        raise RackConfigError("invalid_color_order")
    if config["version"] != 1:
        raise RackConfigError("unsupported_version")
    for key in ("rack_id", "display_name", "endpoint", "topic_prefix"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise RackConfigError("invalid_rack_config")
    for key in ("pixel_count", "rows", "columns"):
        if type(config[key]) is not int or config[key] < 1:
            raise RackConfigError("invalid_rack_config")
    if config["origin"] not in ORIGINS:
        raise RackConfigError("invalid_origin")
    bins = config["bins"]
    if not isinstance(bins, list) or not bins:
        raise RackConfigError("invalid_rack_config")
    if config["rows"] * config["columns"] != len(bins):
        raise RackConfigError("grid_bin_count_mismatch")

    seen_bins: set[str] = set()
    seen_indexes: set[int] = set()
    for entry in bins:
        if not isinstance(entry, dict) or set(entry) != {"bin_id", "led_index"}:
            raise RackConfigError("invalid_bin_entry")
        bin_id = entry["bin_id"]
        led_index = entry["led_index"]
        if not isinstance(bin_id, str) or not bin_id.strip():
            raise RackConfigError("invalid_bin_entry")
        if type(led_index) is not int:
            raise RackConfigError("invalid_bin_entry")
        if bin_id in seen_bins:
            raise RackConfigError("duplicate_bin_id")
        if not 0 <= led_index < config["pixel_count"]:
            raise RackConfigError("led_index_out_of_range")
        if led_index in seen_indexes:
            raise RackConfigError("duplicate_led_index")
        seen_bins.add(bin_id)
        seen_indexes.add(led_index)
    return config


def load_rack_config(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RackConfigError("unreadable_rack_config") from exc
    return validate_rack_config(raw)


def color_order(config: dict) -> str:
    """Byte order the string expects on the wire. Plans stay in intent RGB; the swap happens at publish."""
    return config.get("color_order", "RGB")


def bin_ids(config: dict) -> list[str]:
    return [entry["bin_id"] for entry in config["bins"]]


def _entry(config: dict, bin_id: str) -> dict:
    for entry in config["bins"]:
        if entry["bin_id"] == bin_id:
            return entry
    raise RackConfigError("unknown_bin")


def led_index_for(config: dict, bin_id: str) -> int:
    return _entry(config, bin_id)["led_index"]


def grid_position(config: dict, bin_id: str) -> tuple[int, int]:
    position = bin_ids(config).index(_entry(config, bin_id)["bin_id"])
    return divmod(position, config["columns"])


def neighbor_bins(config: dict, bin_id: str) -> list[str]:
    row, column = grid_position(config, bin_id)
    identifiers = bin_ids(config)
    neighbors = []
    for delta_row, delta_column in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        next_row = row + delta_row
        next_column = column + delta_column
        if 0 <= next_row < config["rows"] and 0 <= next_column < config["columns"]:
            neighbors.append(identifiers[next_row * config["columns"] + next_column])
    return neighbors
