import json
import tempfile
import unittest
from pathlib import Path

from rack.rack_config import (
    RackConfigError,
    bin_ids,
    grid_position,
    led_index_for,
    load_rack_config,
    neighbor_bins,
    validate_rack_config,
)
from scripts.seed_rack import write_seed_rack


def seed_config():
    return {
        "version": 1,
        "rack_id": "rack-01",
        "display_name": "Mechatronics rack 01",
        "endpoint": "mechatronics-rack-01",
        "topic_prefix": "ledwall/node01",
        "pixel_count": 24,
        "rows": 6,
        "columns": 4,
        "origin": "top-left",
        "bins": [{"bin_id": f"bin-{index + 1:02d}", "led_index": index} for index in range(24)],
    }


class RackConfigTests(unittest.TestCase):
    def test_valid_config_round_trips(self):
        config = validate_rack_config(seed_config())
        self.assertEqual(len(bin_ids(config)), 24)
        self.assertEqual(bin_ids(config)[0], "bin-01")
        self.assertEqual(led_index_for(config, "bin-07"), 6)

    def test_grid_position_is_row_major_zero_based(self):
        config = validate_rack_config(seed_config())
        self.assertEqual(grid_position(config, "bin-01"), (0, 0))
        self.assertEqual(grid_position(config, "bin-05"), (1, 0))
        self.assertEqual(grid_position(config, "bin-24"), (5, 3))

    def test_neighbors_stay_inside_the_grid(self):
        config = validate_rack_config(seed_config())
        self.assertEqual(sorted(neighbor_bins(config, "bin-01")), ["bin-02", "bin-05"])
        self.assertEqual(sorted(neighbor_bins(config, "bin-06")), ["bin-02", "bin-05", "bin-07", "bin-10"])

    def test_duplicate_led_index_is_rejected(self):
        config = seed_config()
        config["bins"][3]["led_index"] = 0
        with self.assertRaises(RackConfigError) as caught:
            validate_rack_config(config)
        self.assertEqual(str(caught.exception), "duplicate_led_index")

    def test_led_index_outside_pixel_count_is_rejected(self):
        config = seed_config()
        config["bins"][0]["led_index"] = 24
        with self.assertRaises(RackConfigError) as caught:
            validate_rack_config(config)
        self.assertEqual(str(caught.exception), "led_index_out_of_range")

    def test_bin_count_must_match_grid(self):
        config = seed_config()
        config["rows"] = 5
        with self.assertRaises(RackConfigError) as caught:
            validate_rack_config(config)
        self.assertEqual(str(caught.exception), "grid_bin_count_mismatch")

    def test_unknown_bin_lookup_is_a_mapping_error(self):
        config = validate_rack_config(seed_config())
        with self.assertRaises(RackConfigError) as caught:
            led_index_for(config, "bin-99")
        self.assertEqual(str(caught.exception), "unknown_bin")

    def test_seed_writes_a_loadable_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path, inventory_path = write_seed_rack(Path(tmp))
            config = load_rack_config(config_path)
            self.assertEqual(config["rack_id"], "rack-01")
            self.assertEqual(config["topic_prefix"], "ledwall/node01")
            self.assertEqual(config["pixel_count"], 24)
            self.assertEqual(len(config["bins"]), 24)
            inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
            self.assertEqual(inventory["items"], [])
            self.assertEqual(len(inventory["bins"]), 24)
            self.assertEqual(inventory["bins"]["rack-01/bin-01"]["state"], "unknown")
