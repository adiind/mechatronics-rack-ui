"""LOG-TRUTH: a telemetry row's wall reading is historical, never today's status.

The /logs page promises "the state the wall derives from that report". The
reading therefore comes from the retained reports up to and including that
one (gcode_state and print_error carried forward inside the buffer), is
computed on the server, and is independent of the printer's current status.
An old progress row must not turn red because an error arrived later.
"""
import copy
import tempfile
import unittest
from pathlib import Path

from light_studio.studio import Studio
from test_core import MAP, NOW, report

T0 = 1_760_000_000.0


def full(**fields):
    base = {'gcode_state': 'RUNNING', 'mc_percent': 10, 'layer_num': 3, 'print_error': 0,
            'spd_lvl': 2, 'nozzle_temper': 220.0}
    base.update(fields)
    return base


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rows = [report('RUNNING', name='printer1', percent=10)]
        self.reports = {'printer1': []}
        self.studio = Studio(Path(self.tmp.name), MAP, lambda: self.rows, None,
                             lambda: True, clock=lambda: NOW, demo=True,
                             raw_reports=lambda: self.reports)
        self.studio.tick()

    def entries(self, **kw):
        logs = self.studio.logs(printer='printer1', **kw)
        return logs['printers'][0]['entries']

    def readings(self, **kw):
        return [(e['wall']['state'], e['wall']['basis'], e['wall']['speed']) for e in self.entries(**kw)]

    def test_printing_progress_error_clear_paused_stopped_read_historically(self):
        self.reports['printer1'] = [
            (T0 + 0, full()),                                       # RUNNING stated
            (T0 + 1, {'mc_percent': 11, 'nozzle_temper': 220.4}),  # progress only: carried
            (T0 + 2, {'print_error': 50348044}),                    # error arrives
            (T0 + 3, {'mc_percent': 12}),                           # still in error, carried
            (T0 + 4, {'print_error': 0}),                           # error cleared -> RUNNING again
            (T0 + 5, {'gcode_state': 'PAUSE'}),
            (T0 + 6, {'gcode_state': 'FAILED', 'print_error': 0}),
            (T0 + 7, {'mc_percent': 12, 'spd_lvl': 4}),             # stopped, speed changes
        ]
        self.assertEqual(self.readings(), [
            ('printing', 'report', 'standard'),
            ('printing', 'carried', 'standard'),
            ('error', 'report', 'standard'),
            ('error', 'carried', 'standard'),
            ('printing', 'carried', 'standard'),   # state RUNNING carried, error cleared by this report
            ('paused', 'report', 'standard'),
            ('stopped', 'report', 'standard'),
            ('stopped', 'carried', 'ludicrous'),
        ])

    def test_a_later_error_never_rewrites_older_rows(self):
        self.reports['printer1'] = [(T0, full()), (T0 + 1, {'mc_percent': 11})]
        before = self.readings()
        self.assertEqual([r[0] for r in before], ['printing', 'printing'])
        # The printer now reports an error: current status changes ...
        self.rows = [report('RUNNING', name='printer1', percent=11, has_error=True)]
        self.studio.tick()
        self.assertEqual(self.studio.logs(printer='printer1')['printers'][0]['status']['state'], 'error')
        # ... and the old rows do not.
        self.assertEqual(self.readings(), before)
        # Nor does an error *report* rewrite the rows before it.
        self.reports['printer1'].append((T0 + 2, {'print_error': 1234}))
        self.assertEqual(self.readings()[:2], before)
        self.assertEqual(self.readings()[2][0], 'error')

    def test_speed_is_never_inferred_from_todays_status(self):
        self.reports['printer1'] = [(T0, {'gcode_state': 'RUNNING', 'mc_percent': 5})]
        self.rows = [report('RUNNING', name='printer1', percent=5, speed_level=4, speed_percent=166)]
        self.studio.tick()
        self.assertEqual(self.studio.logs(printer='printer1')['printers'][0]['status']['speed'], 'ludicrous')
        self.assertEqual(self.readings(), [('printing', 'report', None)])

    def test_a_buffer_without_a_state_field_is_unavailable_not_guessed(self):
        self.reports['printer1'] = [(T0, {'mc_percent': 40}), (T0 + 1, {'nozzle_temper': 210.0})]
        self.assertEqual(self.readings(), [(None, 'unavailable', None), (None, 'unavailable', None)])
        # Even with the printer currently in error, the rows stay unavailable.
        self.rows = [report('RUNNING', name='printer1', percent=40, has_error=True)]
        self.studio.tick()
        self.assertEqual(self.readings(), [(None, 'unavailable', None), (None, 'unavailable', None)])
        # The first report that states a state starts the reading.
        self.reports['printer1'].append((T0 + 2, {'gcode_state': 'IDLE'}))
        self.assertEqual(self.readings()[-1], ('idle', 'report', None))

    def test_an_error_flag_alone_reads_as_error_before_any_state(self):
        self.reports['printer1'] = [(T0, {'print_error': 7}), (T0 + 1, {'print_error': 0})]
        self.assertEqual([r[0] for r in self.readings()], ['error', None])

    def test_limited_windows_and_cursors_keep_the_carried_context(self):
        self.reports['printer1'] = [(T0, full()), (T0 + 1, {'mc_percent': 11}),
                                    (T0 + 2, {'mc_percent': 12}), (T0 + 3, {'mc_percent': 13})]
        # The window shows only the last rows, but they still know the state
        # stated by the first report that is outside the window.
        self.assertEqual(self.readings(limit=2), [('printing', 'carried', 'standard')] * 2)
        self.assertEqual(self.readings(since=T0 + 1.5), [('printing', 'carried', 'standard')] * 2)

    def test_unknown_states_and_odd_values_do_not_crash_the_reading(self):
        self.reports['printer1'] = [(T0, {'gcode_state': 'WEIRD', 'print_error': 'x', 'spd_lvl': 'fast'}),
                                    (T0 + 1, {'gcode_state': ''}), (T0 + 2, {'print_error': None})]
        self.assertEqual([r[0] for r in self.readings()], ['unknown', 'unknown', 'unknown'])
        self.assertEqual([r[2] for r in self.readings()], [None, None, None])

    def test_the_reading_survives_folding_and_raw_expansion(self):
        self.reports['printer1'] = [(T0, full()), (T0 + 1, full()), (T0 + 2, {'print_error': 9})]
        entries = self.entries(raw=True)
        self.assertEqual([e['repeats'] for e in entries], [1, 0])
        self.assertEqual([e['wall']['state'] for e in entries], ['printing', 'error'])
        self.assertIn('raw', entries[0])


class PayloadShapeTests(unittest.TestCase):
    def test_every_entry_carries_a_wall_reading_with_three_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            reports = {'printer2': [(T0, full(gcode_state='PREPARE'))]}
            studio = Studio(Path(tmp), MAP, lambda: [report()], None, lambda: True,
                            demo=True, clock=lambda: NOW, raw_reports=lambda: reports)
            studio.tick()
            entry = studio.logs(printer='printer2')['printers'][0]['entries'][0]
            self.assertEqual(set(entry['wall']), {'state', 'basis', 'speed'})
            self.assertEqual(entry['wall'], {'state': 'preparing', 'basis': 'report', 'speed': 'standard'})


if __name__ == '__main__':
    unittest.main()
