"""Translate selected bins into timed frames of firmware pixel commands.

The ESPHome firmware understands four ops only: off, fill, pixel, range
(see ledwall/ledwall_command.py). It has no animation of its own, so any
proximity cue has to be sent as discrete frames by the coordinator.
"""

from __future__ import annotations

from rack.rack_config import led_index_for, neighbor_bins

SELECTION_COLORS: list[list[int]] = [
    [0, 200, 60],
    [0, 120, 255],
    [255, 150, 0],
    [200, 0, 220],
    [0, 200, 200],
    [255, 60, 60],
]
RIPPLE_STEPS = 3
RIPPLE_INTERVAL_MS = 180
DEFAULT_TTL_SECONDS = 45
BLACK = [0, 0, 0]


def color_for_slot(slot: int) -> list[int]:
    return list(SELECTION_COLORS[slot % len(SELECTION_COLORS)])


def clear_command() -> dict:
    return {"op": "off"}


def _pixel(index: int, rgb: list[int]) -> dict:
    return {"op": "pixel", "index": index, "rgb": list(rgb)}


def _dim(rgb: list[int], factor: float) -> list[int]:
    return [max(0, min(255, round(component * factor))) for component in rgb]


def build_locate_plan(config: dict, selections: list[dict], *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict:
    frames: list[dict] = [{"delay_ms": 0, "commands": [clear_command()]}]
    lit: list[dict] = []
    color_by_bin: dict[str, list[int]] = {}

    for selection in selections:
        bin_id = selection["bin_id"]
        led_index = led_index_for(config, bin_id)
        if bin_id not in color_by_bin:
            color_by_bin[bin_id] = color_for_slot(len(color_by_bin))
        lit.append(
            {
                "item_id": selection.get("item_id"),
                "bin_id": bin_id,
                "led_index": led_index,
                "rgb": list(color_by_bin[bin_id]),
            }
        )

    if not color_by_bin:
        return {"ttl_seconds": ttl_seconds, "frames": frames, "lit": [], "clear": clear_command()}

    selected_indexes = {led_index_for(config, bin_id) for bin_id in color_by_bin}
    for step in range(RIPPLE_STEPS):
        commands = []
        is_last_step = step == RIPPLE_STEPS - 1
        for bin_id, rgb in color_by_bin.items():
            shade = BLACK if is_last_step else _dim(rgb, 0.25)
            for neighbor in neighbor_bins(config, bin_id):
                index = led_index_for(config, neighbor)
                if index in selected_indexes:
                    continue
                commands.append(_pixel(index, shade))
        if commands:
            frames.append({"delay_ms": RIPPLE_INTERVAL_MS, "commands": commands})

    frames.append(
        {
            "delay_ms": RIPPLE_INTERVAL_MS,
            "commands": [_pixel(led_index_for(config, bin_id), rgb) for bin_id, rgb in color_by_bin.items()],
        }
    )
    return {"ttl_seconds": ttl_seconds, "frames": frames, "lit": lit, "clear": clear_command()}


def build_preview_plan(config: dict, bin_id: str, *, ttl_seconds: int = 5) -> dict:
    led_index = led_index_for(config, bin_id)
    return {
        "ttl_seconds": ttl_seconds,
        "frames": [
            {"delay_ms": 0, "commands": [clear_command()]},
            {"delay_ms": 0, "commands": [_pixel(led_index, color_for_slot(0))]},
        ],
        "lit": [{"item_id": None, "bin_id": bin_id, "led_index": led_index, "rgb": color_for_slot(0)}],
        "clear": clear_command(),
    }
