import unittest

from rack.rack_config import RackConfigError, validate_rack_config
from rack.rack_lighting import (
    RIPPLE_STEPS,
    SELECTION_COLORS,
    build_locate_plan,
    build_preview_plan,
    clear_command,
    color_for_slot,
)
from scripts.seed_rack import seed_rack_config


class RackLightingTests(unittest.TestCase):
    def setUp(self):
        self.config = validate_rack_config(seed_rack_config())

    def test_first_frame_clears_the_string(self):
        plan = build_locate_plan(self.config, [{"item_id": "a", "bin_id": "bin-07"}])
        self.assertEqual(plan["frames"][0]["commands"], [{"op": "off"}])
        self.assertEqual(plan["frames"][0]["delay_ms"], 0)

    def test_final_frame_paints_every_selected_bin(self):
        plan = build_locate_plan(
            self.config,
            [{"item_id": "a", "bin_id": "bin-07"}, {"item_id": "b", "bin_id": "bin-12"}],
        )
        final = plan["frames"][-1]["commands"]
        self.assertEqual(sorted(command["index"] for command in final), [6, 11])
        self.assertTrue(all(command["op"] == "pixel" for command in final))

    def test_each_selection_gets_a_distinct_color(self):
        plan = build_locate_plan(
            self.config,
            [{"item_id": "a", "bin_id": "bin-07"}, {"item_id": "b", "bin_id": "bin-12"}],
        )
        colors = [entry["rgb"] for entry in plan["lit"]]
        self.assertEqual(colors[0], SELECTION_COLORS[0])
        self.assertEqual(colors[1], SELECTION_COLORS[1])
        self.assertNotEqual(colors[0], colors[1])

    def test_colors_wrap_after_the_palette_is_exhausted(self):
        self.assertEqual(color_for_slot(len(SELECTION_COLORS)), SELECTION_COLORS[0])

    def test_ripple_touches_neighbours_and_then_blacks_them(self):
        plan = build_locate_plan(self.config, [{"item_id": "a", "bin_id": "bin-06"}])
        ripple_frames = plan["frames"][1 : 1 + RIPPLE_STEPS]
        self.assertEqual(len(ripple_frames), RIPPLE_STEPS)
        lit_neighbours = {command["index"] for command in ripple_frames[0]["commands"]}
        self.assertEqual(lit_neighbours, {1, 4, 6, 9})
        self.assertTrue(all(command["rgb"] == [0, 0, 0] for command in ripple_frames[-1]["commands"]))
        self.assertTrue(all(frame["delay_ms"] > 0 for frame in ripple_frames))

    def test_ripple_never_overwrites_another_selected_bin(self):
        plan = build_locate_plan(
            self.config,
            [{"item_id": "a", "bin_id": "bin-06"}, {"item_id": "b", "bin_id": "bin-07"}],
        )
        ripple_indexes = {
            command["index"] for frame in plan["frames"][1 : 1 + RIPPLE_STEPS] for command in frame["commands"]
        }
        self.assertNotIn(6, ripple_indexes)
        self.assertNotIn(5, ripple_indexes)

    def test_two_items_in_one_bin_both_appear_lit(self):
        plan = build_locate_plan(
            self.config,
            [{"item_id": "a", "bin_id": "bin-07"}, {"item_id": "b", "bin_id": "bin-07"}],
        )
        self.assertEqual([entry["item_id"] for entry in plan["lit"]], ["a", "b"])
        self.assertEqual(plan["lit"][0]["rgb"], plan["lit"][1]["rgb"])
        self.assertEqual(len(plan["frames"][-1]["commands"]), 1)

    def test_unknown_bin_is_a_mapping_error(self):
        with self.assertRaises(RackConfigError):
            build_locate_plan(self.config, [{"item_id": "a", "bin_id": "bin-99"}])

    def test_empty_selection_only_clears(self):
        plan = build_locate_plan(self.config, [])
        self.assertEqual(plan["frames"], [{"delay_ms": 0, "commands": [{"op": "off"}]}])
        self.assertEqual(plan["lit"], [])

    def test_preview_plan_lights_exactly_one_bin_briefly(self):
        plan = build_preview_plan(self.config, "bin-03", ttl_seconds=5)
        self.assertEqual(plan["ttl_seconds"], 5)
        self.assertEqual(plan["frames"][-1]["commands"], [{"op": "pixel", "index": 2, "rgb": SELECTION_COLORS[0]}])

    def test_clear_command_is_the_firmware_off_op(self):
        self.assertEqual(clear_command(), {"op": "off"})
