"""Transport: diffing, firmware payload legality, budgets, fairness, encoding."""
import unittest

from light_studio import transport as tp
from light_studio.renderer import render_rope


def frame(colour, pixels=100):
    return [list(colour) for _ in range(pixels)]


class DiffTests(unittest.TestCase):
    def test_an_unknown_rope_is_one_run_when_the_target_is_uniform(self):
        runs = tp.diff_runs([None] * 100, frame((6, 6, 6)))
        self.assertEqual(runs, [(0, 100, [6, 6, 6])])

    def test_no_change_means_no_messages(self):
        target = frame((6, 6, 6))
        self.assertEqual(tp.diff_runs([list(p) for p in target], target), [])

    def test_only_the_changed_runs_are_emitted(self):
        applied = [list(p) for p in frame((0, 0, 0))]
        target = frame((0, 0, 0))
        target[40] = [12, 0, 0]
        target[41] = [12, 0, 0]
        self.assertEqual(tp.diff_runs(applied, target), [(40, 2, [12, 0, 0])])

    def test_a_constant_run_is_not_split_by_pixels_that_already_match(self):
        applied = [list(p) for p in frame((0, 0, 0))]
        target = frame((0, 0, 0))
        for i in range(30, 40):
            target[i] = [6, 6, 6]
        applied[35] = [6, 6, 6]                 # already correct in the middle
        self.assertEqual(tp.diff_runs(applied, target), [(30, 10, [6, 6, 6])])


class PayloadTests(unittest.TestCase):
    def test_full_strip_uses_fill_and_full_black_uses_off(self):
        self.assertEqual(tp.payload_for(0, 100, [6, 12, 18], 100),
                         {'op': 'fill', 'rgb': [6, 12, 18]})
        self.assertEqual(tp.payload_for(0, 100, [0, 0, 0], 100), {'op': 'off'})

    def test_single_pixel_uses_pixel_and_the_rest_use_range(self):
        self.assertEqual(tp.payload_for(7, 1, [1, 2, 3], 100),
                         {'op': 'pixel', 'index': 7, 'rgb': [1, 2, 3]})
        self.assertEqual(tp.payload_for(7, 4, [1, 2, 3], 100),
                         {'op': 'range', 'start': 7, 'count': 4, 'rgb': [1, 2, 3]})

    def test_payload_shapes_match_the_firmware_key_count_rules(self):
        """The node YAML rejects a payload whose key count is wrong."""
        expected = {'off': 1, 'fill': 2, 'pixel': 3, 'range': 4}
        for payload in (tp.payload_for(0, 100, [0, 0, 0], 100),
                        tp.payload_for(0, 100, [9, 9, 9], 100),
                        tp.payload_for(3, 1, [9, 9, 9], 100),
                        tp.payload_for(3, 9, [9, 9, 9], 100)):
            self.assertEqual(len(payload), expected[payload['op']], payload)

    def test_out_of_range_pixels_and_colours_are_refused(self):
        for args in [(0, 0, [1, 1, 1]), (-1, 5, [1, 1, 1]), (98, 5, [1, 1, 1]),
                     (0, 101, [1, 1, 1]), (0, 5, [1, 1, 256]), (0, 5, [1, -1, 1]),
                     (0, 5, [1, 1])]:
            with self.assertRaises(ValueError, msg=args):
                tp.payload_for(args[0], args[1], args[2], 100)


class BudgetTests(unittest.TestCase):
    def test_a_bucket_never_exceeds_its_burst_or_goes_negative(self):
        bucket = tp.TokenBucket(10, 20, now=0.0)
        bucket.refill(1000.0)
        self.assertEqual(bucket.tokens, 20)
        self.assertEqual(bucket.take(50), 20)
        self.assertEqual(bucket.take(1), 0)
        self.assertGreaterEqual(bucket.tokens, 0)

    def test_a_bucket_refills_at_its_rate(self):
        bucket = tp.TokenBucket(10, 20, now=0.0)
        bucket.take(20)
        bucket.refill(1.0)
        self.assertAlmostEqual(bucket.tokens, 10.0)

    def test_planning_never_returns_more_than_the_limit(self):
        applied = [None] * 100
        target = render_rope('idle', None, 3.0, 0, {'brightness': 100})
        for limit in (0, 1, 3, 8, 500):
            self.assertLessEqual(len(tp.plan_updates(applied, target, limit)), limit)

    def test_a_limited_plan_sends_the_most_visible_differences_first(self):
        applied = [[0, 0, 0] for _ in range(100)]
        target = [[0, 0, 0] for _ in range(100)]
        for i in range(10, 20):
            target[i] = [6, 0, 0]               # barely visible
        for i in range(60, 70):
            target[i] = [138, 138, 138]         # unmistakable
        chosen = tp.plan_updates(applied, target, 1)
        self.assertEqual(chosen, [(60, 10, [138, 138, 138])])

    def test_the_fairness_pass_walks_left_to_right_instead(self):
        applied = [[0, 0, 0] for _ in range(100)]
        target = [[0, 0, 0] for _ in range(100)]
        for i in range(10, 20):
            target[i] = [6, 0, 0]
        for i in range(60, 70):
            target[i] = [138, 138, 138]
        chosen = tp.plan_updates(applied, target, 1, prioritize=False)
        self.assertEqual(chosen, [(10, 10, [6, 0, 0])])

    def test_a_skipped_run_stays_dirty_and_is_offered_again(self):
        applied = [[0, 0, 0] for _ in range(100)]
        target = [[0, 0, 0] for _ in range(100)]
        for i in range(10, 20):
            target[i] = [6, 0, 0]
        for i in range(60, 70):
            target[i] = [138, 138, 138]
        first = tp.plan_updates(applied, target, 1)
        tp.apply_run(applied, *first[0])
        self.assertEqual(tp.plan_updates(applied, target, 5), [(10, 10, [6, 0, 0])])


class EncodingTests(unittest.TestCase):
    def test_run_length_encoding_round_trips_exactly(self):
        frames = [[render_rope(state, 55, t, 0) for t in (0.0, 0.5)]
                  for state in ('idle', 'printing', 'error')]
        palette, encoded = tp.encode_film(frames)
        for rope_in, rope_out in zip(frames, encoded):
            for original, runs in zip(rope_in, rope_out):
                rebuilt = []
                for count, index in runs:
                    rebuilt.extend([list(palette[index])] * count)
                self.assertEqual(rebuilt, original)

    def test_encoding_is_much_smaller_than_the_raw_pixels(self):
        frames = [[render_rope('idle', None, t / 10, i) for t in range(12)]
                  for i in range(7)]
        _, encoded = tp.encode_film(frames)
        runs = sum(len(f) for rope in encoded for f in rope)
        self.assertLess(runs * 2, 7 * 12 * 100 * 3 / 4)


if __name__ == '__main__':
    unittest.main()
