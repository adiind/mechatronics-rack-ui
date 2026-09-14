"""State interpretation: freshness, priority, unknown-is-not-zero, sanitization."""
import datetime as dt
import json
import unittest

from light_studio.model import STALE_AFTER, epoch, freshness, normalize, number
from test_core import NOW, report


class NumberTests(unittest.TestCase):
    def test_bools_nan_and_strings_are_not_numbers(self):
        for value in (True, False, float('nan'), float('inf'), '42', None, [1]):
            self.assertIsNone(number(value), value)
        self.assertEqual(number(0), 0.0)
        self.assertEqual(number(3.5), 3.5)


class TimestampTests(unittest.TestCase):
    def test_host_local_naive_string_is_read_in_pi_local_time(self):
        moment = dt.datetime.fromtimestamp(NOW).replace(microsecond=0)
        self.assertAlmostEqual(epoch(moment.strftime('%Y-%m-%d %H:%M:%S')),
                               moment.timestamp(), places=3)

    def test_offset_aware_and_epoch_forms_are_accepted(self):
        self.assertEqual(epoch('1970-01-01T00:01:00Z'), 60.0)
        self.assertEqual(epoch(1234.5), 1234.5)

    def test_garbage_timestamps_are_none_not_exceptions(self):
        for value in ('', 'yesterday', None, [], {}, float('nan'), '2026-13-45'):
            self.assertIsNone(epoch(value), value)


class FreshnessTests(unittest.TestCase):
    def test_every_way_of_being_stale_is_named_and_not_fresh(self):
        cases = {
            'link_down': {'mqtt_connected': False},
            'no_telemetry': {'updated': None},
            'stale': {'updated': NOW - STALE_AFTER - 1},
            'clock_skew': {'updated': NOW + 3600},
        }
        for reason, change in cases.items():
            row = report() | change
            self.assertEqual(freshness(row, NOW)[0], reason)
            state = normalize(row, NOW)
            self.assertEqual(state['state'], 'offline')
            self.assertFalse(state['fresh'])
            # No stale percentage, ETA or temperature may survive.
            for field in ('percent', 'remaining_min', 'nozzle', 'bed', 'layer'):
                self.assertIsNone(state[field], (reason, field))

    def test_boundary_of_the_120_second_window(self):
        self.assertTrue(normalize(report(updated=NOW - STALE_AFTER + 1), NOW)['fresh'])
        self.assertFalse(normalize(report(updated=NOW - STALE_AFTER - 1), NOW)['fresh'])

    def test_slightly_ahead_source_clock_is_tolerated_and_reported_as_zero(self):
        state = normalize(report(updated=NOW + 2), NOW)
        self.assertTrue(state['fresh'])
        self.assertEqual(state['age'], 0.0)


class StateTests(unittest.TestCase):
    def test_error_outranks_every_other_state(self):
        for raw in ('RUNNING', 'PAUSE', 'FINISH', 'IDLE', 'PREPARE'):
            self.assertEqual(normalize(report(raw, has_error=True), NOW)['state'], 'error')

    def test_raw_states_map_without_inventing_stages(self):
        expected = {'RUNNING': 'printing', 'PREPARE': 'preparing', 'PAUSE': 'paused',
                    'FINISH': 'finished', 'IDLE': 'idle', 'FAILED': 'stopped'}
        for raw, state in expected.items():
            self.assertEqual(normalize(report(raw), NOW)['state'], state)

    def test_speed_profile_is_named_and_bounded(self):
        # Bambu spd_lvl 1..4 -> Silent/Standard/Sport/Ludicrous; spd_mag is the
        # feed-rate percentage. Anything else, or stale telemetry, is None.
        for level, name in ((1, 'silent'), (2, 'standard'), (3, 'sport'), (4, 'ludicrous')):
            row = normalize(report('RUNNING', speed_level=level, speed_percent=166), NOW)
            self.assertEqual(row['speed'], name)
            self.assertEqual(row['speed_percent'], 166)
        for bad in (0, 5, 'fast', None, True, 2.5):
            self.assertIsNone(normalize(report('RUNNING', speed_level=bad), NOW)['speed'])
        self.assertIsNone(normalize(report('RUNNING', speed_percent=0), NOW)['speed_percent'])
        self.assertIsNone(normalize(report('RUNNING', speed_percent=9000), NOW)['speed_percent'])
        stale = normalize(report('RUNNING', speed_level=4, updated='2000-01-01 00:00:00'), NOW)
        self.assertIsNone(stale['speed'])

    def test_failed_is_red_only_while_the_error_code_is_set(self):
        # Bambu keeps gcode_state=FAILED until the next print starts; dismissing
        # the error on the printer clears print_error, and the rope must follow:
        # 'stopped' (magenta) until the bed is cleared, never red forever.
        self.assertEqual(normalize(report('FAILED', has_error=True), NOW)['state'], 'error')
        self.assertEqual(normalize(report('FAILED', has_error=False), NOW)['state'], 'stopped')

    def test_door_state_passes_through_only_as_a_boolean(self):
        row = report('FINISH'); row['door_open'] = True
        self.assertIs(normalize(row, NOW)['door_open'], True)
        row['door_open'] = 'yes'
        self.assertIsNone(normalize(row, NOW)['door_open'])
        self.assertIsNone(normalize(report('FINISH'), NOW)['door_open'])

    def test_absent_or_unrecognized_state_is_unknown_never_available(self):
        for raw in (None, '', 'WOBBLE', 42, {'a': 1}):
            self.assertEqual(normalize(report(raw), NOW)['state'], 'unknown')

    def test_hot_nozzle_alone_never_implies_preparing(self):
        self.assertEqual(normalize(report('IDLE', nozzle=245.0), NOW)['state'], 'idle')

    def test_percent_zero_is_kept_but_junk_becomes_unknown(self):
        self.assertEqual(normalize(report(percent=0), NOW)['percent'], 0.0)
        for value in (None, True, float('nan'), 'garbage', [50]):
            self.assertIsNone(normalize(report(percent=value), NOW)['percent'], value)

    def test_percent_and_eta_are_clamped_to_sane_ranges(self):
        self.assertEqual(normalize(report(percent=140), NOW)['percent'], 100.0)
        self.assertEqual(normalize(report(percent=-5), NOW)['percent'], 0.0)
        self.assertIsNone(normalize(report(remaining_min=-3), NOW)['remaining_min'])

    def test_layer_numbers_survive_only_as_clean_integers(self):
        self.assertEqual(normalize(report(layer=42), NOW)['layer'], 42)
        self.assertIsNone(normalize(report(layer='42nd'), NOW)['layer'])
        self.assertIsNone(normalize(report(layer=True), NOW)['layer'])


class SanitizationTests(unittest.TestCase):
    def test_host_serial_and_code_never_survive_normalization(self):
        blob = json.dumps(normalize(report(), NOW))
        for secret in ('SECRETSERIAL', 'SECRETCODE', '10.0.0.9'):
            self.assertNotIn(secret, blob)

    def test_job_names_are_length_bounded_and_control_stripped(self):
        state = normalize(report(job='a\x00b\nc' + 'x' * 500), NOW)
        self.assertNotIn('\x00', state['job'])
        self.assertNotIn('\n', state['job'])
        self.assertLessEqual(len(state['job']), 180)

    def test_non_dict_rows_do_not_explode(self):
        self.assertEqual(normalize(None, NOW)['state'], 'offline')


if __name__ == '__main__':
    unittest.main()
