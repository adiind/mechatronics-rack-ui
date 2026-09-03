import unittest

from rack.rack_config import RackConfigError, led_index_for, neighbor_bins, validate_rack_config
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
        expected = sorted(led_index_for(self.config, bin_id) for bin_id in ("bin-07", "bin-12"))
        self.assertEqual(sorted(command["index"] for command in final), expected)
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
        # bin-08 sits at row 1, column 1 of the 6-wide grid: neighbours are bin-02, bin-07, bin-09, bin-14.
        plan = build_locate_plan(self.config, [{"item_id": "a", "bin_id": "bin-08"}])
        ripple_frames = plan["frames"][1 : 1 + RIPPLE_STEPS]
        self.assertEqual(len(ripple_frames), RIPPLE_STEPS)
        lit_neighbours = {command["index"] for command in ripple_frames[0]["commands"]}
        self.assertEqual(sorted(neighbor_bins(self.config, "bin-08")), ["bin-02", "bin-07", "bin-09", "bin-14"])
        self.assertEqual(lit_neighbours, {led_index_for(self.config, b) for b in ("bin-02", "bin-07", "bin-09", "bin-14")})
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
        self.assertEqual(
            plan["frames"][-1]["commands"],
            [{"op": "pixel", "index": led_index_for(self.config, "bin-03"), "rgb": SELECTION_COLORS[0]}],
        )

    def test_clear_command_is_the_firmware_off_op(self):
        self.assertEqual(clear_command(), {"op": "off"})


class WireOrderTests(unittest.TestCase):
    def test_grb_swaps_red_and_green_only_on_the_wire(self):
        from rack.rack_lighting import to_wire_rgb, wire_command

        self.assertEqual(to_wire_rgb([255, 10, 20], "GRB"), [10, 255, 20])
        self.assertEqual(to_wire_rgb([255, 10, 20], "RGB"), [255, 10, 20])
        self.assertEqual(wire_command({"op": "off"}, "GRB"), {"op": "off"})
        self.assertEqual(wire_command({"op": "pixel", "index": 3, "rgb": [1, 2, 3]}, "GRB"), {"op": "pixel", "index": 3, "rgb": [2, 1, 3]})


class SeedGeometryTests(unittest.TestCase):
    def test_seed_is_serpentine_from_bottom_left(self):
        from scripts.seed_rack import COLUMNS, ROWS, seed_rack_config

        config = seed_rack_config()
        index = {entry["bin_id"]: entry["led_index"] for entry in config["bins"]}
        bottom_left = f"bin-{(ROWS - 1) * COLUMNS + 1:02d}"
        bottom_right = f"bin-{ROWS * COLUMNS:02d}"
        self.assertEqual(index[bottom_left], 0)  # string starts bottom-left
        self.assertEqual(index[bottom_right], COLUMNS - 1)  # bottom row runs left to right
        above_bottom_right = f"bin-{(ROWS - 2) * COLUMNS + COLUMNS:02d}"
        self.assertEqual(index[above_bottom_right], COLUMNS)  # next row starts on the right
        self.assertEqual(sorted(index.values()), list(range(ROWS * COLUMNS)))
        self.assertEqual(config["color_order"], "GRB")
