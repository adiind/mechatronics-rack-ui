"""Coordinator: lifecycle rules, identify, budgets, cast gating, persistence.

Everything here runs against a fake clock and a fake publisher. No thread is
started, no socket is opened and no hardware module is imported.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from light_studio import transport as tp
from light_studio.layout import Conflict
from light_studio.renderer import (ACCENT_POSITIONS, IDENTIFY_SECONDS,
                                   STATUS_POSITIONS)
from light_studio.studio import RESYNC_SECONDS, Studio
from test_core import MAP, NOW

NODES = {target['node'] for target in MAP.values()}


def fleet(**overrides):
    rows = []
    for index in range(1, 8):
        name = f'printer{index}'
        row = dict(name=name, state='IDLE', percent=None, job=None,
                   remaining_min=None, nozzle=30.0, bed=25.0, layer=None,
                   total_layer=None, has_error=False, mqtt_connected=True,
                   updated=NOW, host='10.0.0.5', serial='SECRETSERIAL')
        row.update(overrides.get(name, {}))
        rows.append(row)
    return rows


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = NOW
        self.rows = fleet()
        self.sent = []
        self.enabled = True
        self.ok = True
        self.studio = self.build()

    def build(self, root=None):
        return Studio(root or self.root, MAP, lambda: self.rows, self.publish,
                      lambda: self.enabled, clock=lambda: self.now)

    def publish(self, node, payload):
        self.sent.append((node, payload))
        return self.ok

    def set(self, name, **fields):
        for row in self.rows:
            if row['name'] == name:
                row.update(fields)

    def tick(self, seconds=0.125, count=1, restamp=True):
        for _ in range(count):
            self.now += seconds
            if restamp:
                for row in self.rows:
                    if row['mqtt_connected']:
                        row['updated'] = self.now
            self.studio.tick()

    def completions(self):
        return [e for e in self.studio.events if e['kind'] == 'complete']

    def starts(self):
        return [e for e in self.studio.events if e['kind'] == 'start']


class StartupTests(Harness):
    def test_a_bad_initial_map_is_refused_before_any_writer_exists(self):
        for bad in ({'printer1': {'node': 'node01', 'pixels': 100}},
                    {'printer1': {'node': 'node02', 'pixels': 20}},
                    {'printer1': {'node': 'nodeXX', 'pixels': 100}},
                    {'printer1': {'node': 'node02', 'pixels': 100},
                     'printer2': {'node': 'node02', 'pixels': 100}},
                    {}, None):
            with self.assertRaises(ValueError, msg=bad):
                Studio(self.root, bad, lambda: [],
                       lambda n, p: self.fail('must not publish'), lambda: True)

    def test_a_corrupt_layout_stops_startup_before_publishing(self):
        (self.root / 'layout.json').write_text('{broken')
        with self.assertRaises(ValueError):
            Studio(self.root, MAP, lambda: [],
                   lambda n, p: self.fail('must not publish'), lambda: True)

    def test_nothing_is_published_merely_by_constructing_a_studio(self):
        self.assertEqual(self.sent, [])
        self.assertFalse(self.studio.running)


class LifecycleTests(Harness):
    def test_an_initial_finish_is_never_a_completion(self):
        self.set('printer1', state='FINISH', percent=100)
        self.tick(count=5)
        self.assertEqual(self.completions(), [])
        self.assertEqual(self.studio.status['printer1']['state'], 'finished')
        self.assertFalse(self.studio.status['printer1']['completion_observed'])

    def test_a_watched_completion_fires_exactly_once_and_is_marked_observed(self):
        self.set('printer1', state='RUNNING', percent=80)
        self.tick(count=3)
        self.set('printer1', state='FINISH', percent=100)
        self.tick(count=6)
        self.assertEqual(len(self.completions()), 1)
        self.assertTrue(self.studio.status['printer1']['completion_observed'])
        self.assertIsNotNone(self.studio.status['printer1']['completed_ago'])

    def test_a_reconnect_that_reveals_finish_does_not_celebrate(self):
        self.set('printer1', state='RUNNING', percent=50)
        self.tick(count=2)
        self.set('printer1', mqtt_connected=False)
        self.tick(count=2)
        self.set('printer1', mqtt_connected=True, state='FINISH', percent=100)
        self.tick(count=3)
        self.assertEqual(self.completions(), [])
        self.assertFalse(self.studio.status['printer1']['completion_observed'])

    def test_an_observation_gap_breaks_continuity(self):
        self.set('printer1', state='RUNNING', percent=50)
        self.tick()
        self.set('printer1', state='FINISH', percent=100)
        self.tick(seconds=200)
        self.assertEqual(self.completions(), [])

    def test_a_process_restart_can_never_celebrate(self):
        self.set('printer1', state='RUNNING', percent=90)
        self.tick(count=3)
        restarted = self.build()
        self.set('printer1', state='FINISH', percent=100)
        self.now += 1
        for row in self.rows:
            row['updated'] = self.now
        restarted.tick()
        self.assertEqual([e for e in restarted.events if e['kind'] == 'complete'], [])
        self.assertFalse(restarted.status['printer1']['completion_observed'])

    def test_an_observed_completion_is_bound_to_the_job_it_watched(self):
        """Regression: a witnessed completion could be shown against a later job.

        The claim is "we watched *this* job finish". A different job name, or any
        interval we did not watch, retires it -- job names are not unique IDs, so
        the rule stays conservative rather than clever.
        """
        self.set('printer1', state='RUNNING', percent=90, job='tray A')
        self.tick(count=3)
        self.set('printer1', state='FINISH', percent=100, job='tray A')
        self.tick(count=2)
        self.assertTrue(self.studio.status['printer1']['completion_observed'])

        # Same printer, still FINISH, but a different retained job name.
        self.set('printer1', job='tray B')
        self.tick(count=2)
        self.assertFalse(self.studio.status['printer1']['completion_observed'])
        self.assertIsNone(self.studio.status['printer1']['completed_ago'])

    def test_a_disconnected_interval_retires_an_observed_completion(self):
        self.set('printer1', state='RUNNING', percent=90, job='tray A')
        self.tick(count=3)
        self.set('printer1', state='FINISH', percent=100, job='tray A')
        self.tick(count=2)
        self.assertTrue(self.studio.status['printer1']['completion_observed'])

        self.set('printer1', mqtt_connected=False)
        self.tick(count=2)
        # Same job name on return, but we did not watch the gap: still not ours.
        self.set('printer1', mqtt_connected=True, state='FINISH', job='tray A')
        self.tick(count=3)
        self.assertFalse(self.studio.status['printer1']['completion_observed'])

    def test_a_restart_never_reports_a_completion_as_observed(self):
        self.set('printer1', state='RUNNING', percent=90, job='tray A')
        self.tick(count=3)
        self.set('printer1', state='FINISH', percent=100, job='tray A')
        self.tick(count=2)
        restarted = self.build()
        self.now += 1
        for row in self.rows:
            row['updated'] = self.now
        restarted.tick()
        self.assertFalse(restarted.status['printer1']['completion_observed'])
        self.assertEqual(restarted.observed_complete, {})

    def test_observed_completions_are_not_persisted_at_all(self):
        self.set('printer1', state='RUNNING', percent=90, job='tray A')
        self.tick(count=3)
        self.set('printer1', state='FINISH', percent=100, job='tray A')
        self.tick(count=3)
        stored = json.loads((self.root / 'lifecycle.json').read_text())
        self.assertNotIn('observed_complete', stored)

    def test_a_legacy_lifecycle_file_with_observed_completions_is_ignored(self):
        (self.root / 'lifecycle.json').write_text(json.dumps({
            'version': 1,
            'printers': {'printer1': {'state': 'finished', 'fresh': True,
                                      'wall': NOW}},
            'observed_complete': {'printer1': NOW},
        }))
        studio = self.build()
        self.assertEqual(studio.observed_complete, {})
        self.set('printer1', state='FINISH', percent=100, job='tray A')
        self.now += 1
        for row in self.rows:
            row['updated'] = self.now
        studio.tick()
        self.assertFalse(studio.status['printer1']['completion_observed'])

    def test_a_new_print_start_ripples_once(self):
        self.tick(count=2)
        self.set('printer1', state='RUNNING', percent=1)
        self.tick(count=4)
        self.assertEqual(len(self.starts()), 1)

    def test_events_are_deduplicated_and_bounded(self):
        for index in range(1, 8):
            self.set(f'printer{index}', state='RUNNING', percent=5)
        self.tick(count=2)
        for index in range(1, 8):
            self.set(f'printer{index}', state='FINISH', percent=100)
        self.tick(count=8)
        self.assertLessEqual(len(self.studio.events), 16)
        self.assertEqual(len(self.completions()), 7)

    def test_events_expire_and_stop_driving_ripples(self):
        self.set('printer1', state='RUNNING', percent=90)
        self.tick(count=2)
        self.set('printer1', state='FINISH', percent=100)
        self.tick(count=2)
        self.assertTrue(self.completions())
        self.tick(seconds=1.0, count=6)
        self.assertEqual(self.studio.events, [])

    def test_a_wall_clock_jump_does_not_teleport_the_animation(self):
        self.tick(count=2)
        before = self.studio.phase
        self.now += 100000        # NTP step
        for row in self.rows:
            row['updated'] = self.now
        self.studio.tick()
        self.assertLess(self.studio.phase - before, 2.0)


class CollectionTests(Harness):
    def test_only_a_live_finished_printer_can_be_marked_collected(self):
        self.tick()
        with self.assertRaises(ValueError):
            self.studio.collected('printer1')
        self.set('printer1', state='FINISH', percent=100)
        self.tick()
        self.assertEqual(self.studio.collected('printer1')['printer'], 'printer1')
        with self.assertRaises(ValueError):
            self.studio.collected('printer9')

    def test_collection_only_changes_display_and_never_publishes_a_command(self):
        self.set('printer1', state='FINISH', percent=100, job='tray')
        self.tick()
        before = len(self.sent)
        self.studio.collected('printer1')
        self.assertEqual(len(self.sent), before)
        self.tick()
        state = self.studio.status['printer1']
        self.assertEqual(state['state'], 'idle')
        self.assertTrue(state['collected'])
        for node, payload in self.sent:
            self.assertIn(node, NODES)
            self.assertIn(payload['op'], ('fill', 'range', 'pixel', 'off'))

    def test_collection_survives_a_restart_and_a_new_job_clears_it(self):
        self.set('printer1', state='FINISH', percent=100, job='tray')
        self.tick()
        self.studio.collected('printer1')
        restarted = self.build()
        self.now += 1
        for row in self.rows:
            row['updated'] = self.now
        restarted.tick()
        self.assertEqual(restarted.status['printer1']['state'], 'idle')
        self.set('printer1', state='RUNNING', percent=2, job='next')
        self.tick(count=2)
        self.set('printer1', state='FINISH', percent=100, job='next')
        self.tick(count=2)
        self.assertEqual(self.studio.status['printer1']['state'], 'finished')

    def test_a_different_job_name_is_not_covered_by_an_old_acknowledgement(self):
        self.set('printer1', state='FINISH', percent=100, job='tray')
        self.tick()
        self.studio.collected('printer1')
        self.set('printer1', job='something else')
        self.tick()
        self.assertEqual(self.studio.status['printer1']['state'], 'finished')


class IdentifyTests(Harness):
    def test_identify_is_bounded_and_restores_state_without_the_browser(self):
        self.tick()
        self.studio.identify('node04')
        self.tick()
        self.assertEqual(self.studio.view()['identify']['node'], 'node04')
        identify_frame = list(self.studio.frames['node04'])
        # Browser goes away; only the fake clock advances.
        self.tick(seconds=IDENTIFY_SECONDS + 0.5)
        self.assertIsNone(self.studio.view()['identify'])
        self.assertNotEqual(self.studio.frames['node04'], identify_frame)
        self.tick(count=8)
        self.assertEqual(self.studio.frames['node04'],
                         self.studio.frames['node04'])

    def test_identify_refuses_unknown_ropes_and_node01(self):
        for node in ('node01', 'node99', '', None, 42):
            with self.assertRaises(ValueError, msg=node):
                self.studio.identify(node)

    def test_identify_is_refused_while_casting_is_disabled(self):
        self.enabled = False
        with self.assertRaises(ValueError):
            self.studio.identify('node02')
        self.assertIsNone(self.studio.ident)

    def test_identify_reports_a_queue_not_a_confirmation(self):
        result = self.studio.identify('node02')
        self.assertNotIn('confirm', result['delivery'].split()[0].lower())
        self.assertIn('queued', result['delivery'])

    def test_identify_only_changes_the_selected_rope(self):
        self.tick()
        others = {n: list(self.studio.frames[n]) for n in NODES if n != 'node05'}
        self.studio.identify('node05')
        self.tick()
        for node, frame in others.items():
            self.assertEqual(len(self.studio.frames[node]), len(frame))
        self.assertNotEqual(self.studio.frames['node05'], others.get('node05'))


class CastGateTests(Harness):
    def test_disabling_casting_stops_output_promptly(self):
        self.tick(count=4)
        self.assertTrue(self.sent)
        self.enabled = False
        before = len(self.sent)
        self.tick(count=10)
        self.assertEqual(len(self.sent), before)

    def test_re_enabling_casting_repaints_from_scratch(self):
        self.tick(count=20)
        self.enabled = False
        self.tick(count=2)
        self.enabled = True
        self.sent.clear()
        self.tick()
        self.assertTrue(self.sent)
        # A repaint starts from an unknown rope, so a full-strip op appears.
        self.assertTrue(any(p['op'] in ('fill', 'off') or
                            (p['op'] == 'range' and p['start'] == 0)
                            for _, p in self.sent))

    def test_a_studio_with_no_publisher_still_renders_for_the_browser(self):
        quiet = Studio(self.root / 'nopub', MAP, lambda: self.rows, None,
                       lambda: True, clock=lambda: self.now)
        quiet.tick()
        self.assertEqual(len(quiet.frames), 7)


class LossRecoveryTests(Harness):
    """QoS 0 loss must not strand a pixel.

    Publishes are fire-and-forget and a pixel is marked painted once the broker
    accepts it, so a dropped packet leaves the diff believing a stale pixel is
    already correct. Measured loss on this wall is ~7%; a falling drop moves
    ~8x/second, so without a bound this shows up as a droplet stuck in mid-air.
    """

    def lossy_node(self, loss=0.2, seed=0):
        import random
        rng = random.Random(seed)
        truth = {node: [[0, 0, 0] for _ in range(100)] for node in NODES}

        def publish(node, payload):
            if rng.random() >= loss:          # delivered
                op = payload['op']
                if op == 'off':
                    truth[node] = [[0, 0, 0] for _ in range(100)]
                elif op == 'fill':
                    truth[node] = [list(payload['rgb']) for _ in range(100)]
                elif op == 'pixel':
                    truth[node][payload['index']] = list(payload['rgb'])
                elif op == 'range':
                    for i in range(payload['start'],
                                   payload['start'] + payload['count']):
                        truth[node][i] = list(payload['rgb'])
            return True                        # the broker accepted it either way
        return truth, publish

    def test_a_lost_publish_is_repaired_by_the_resync(self):
        self.set('printer1', state='RUNNING', percent=40)
        config = self.studio.config()
        config['settings']['reduced_motion'] = True   # hold the target still
        self.studio.save(config)
        truth, publish = self.lossy_node(loss=0.2)
        self.studio.publish = publish
        self.tick(seconds=0.125, count=int(RESYNC_SECONDS * 8 / 0.125))
        for node in NODES:
            self.assertEqual(truth[node], self.studio.frames[node],
                             f'{node} never converged despite resyncs')

    def test_the_resync_interval_bounds_a_stale_pixel(self):
        """It is the only thing that can repair a silently dropped packet."""
        self.assertLessEqual(RESYNC_SECONDS, 4.0)
        self.assertGreater(RESYNC_SECONDS, 0.0)

    def test_resync_repaints_are_cheap_enough_to_run_often(self):
        """A solid red/green bar is a handful of runs, so frequent is affordable."""
        self.set('printer1', state='RUNNING', percent=40)
        config = self.studio.config()
        config['settings']['reduced_motion'] = True
        self.studio.save(config)
        self.tick(count=40)
        self.sent.clear()
        self.tick(seconds=0.125, count=int(RESYNC_SECONDS * 4 / 0.125))
        per_second = len(self.sent) / (RESYNC_SECONDS * 4)
        self.assertLess(per_second, tp.AGGREGATE_RATE * 0.5,
                        f'resync alone costs {per_second:.0f} msg/s')


class TransportTests(Harness):
    def test_every_message_is_allowlisted_and_firmware_legal(self):
        self.tick(count=60)
        self.assertTrue(self.sent)
        for node, payload in self.sent:
            self.assertIn(node, NODES)
            self.assertNotEqual(node, 'node01')
            self.assertIn(payload['op'], ('fill', 'range', 'pixel', 'off'))
            if payload['op'] != 'off':
                self.assertTrue(all(isinstance(c, int) and 0 <= c <= 255
                                    for c in payload['rgb']))
            if payload['op'] == 'range':
                self.assertGreaterEqual(payload['start'], 0)
                self.assertGreaterEqual(payload['count'], 1)
                self.assertLessEqual(payload['start'] + payload['count'], 100)
            if payload['op'] == 'pixel':
                self.assertTrue(0 <= payload['index'] < 100)

    def test_the_aggregate_rate_stays_inside_its_budget(self):
        seconds = 20.0
        self.tick(seconds=0.125, count=int(seconds / 0.125))
        allowed = tp.AGGREGATE_BURST + tp.AGGREGATE_RATE * seconds
        self.assertLessEqual(len(self.sent), allowed)

    def test_no_single_rope_starves_while_another_is_busy(self):
        self.set('printer1', state='RUNNING', percent=45)
        self.tick(seconds=0.125, count=120)
        per_node = {node: 0 for node in NODES}
        for node, _ in self.sent:
            per_node[node] += 1
        self.assertTrue(all(count > 0 for count in per_node.values()), per_node)

    def test_a_failed_publish_is_never_counted_as_delivered(self):
        self.ok = False
        self.tick(count=3)
        view = self.studio.view()
        self.assertEqual(view['transport']['sent'], 0)
        self.assertGreater(view['transport']['failed'], 0)
        self.assertEqual(view['transport']['node_receipt'], 'unverified')

    def test_output_resumes_and_repaints_after_the_broker_comes_back(self):
        self.ok = False
        self.tick(count=4)
        self.ok = True
        self.sent.clear()
        self.tick(count=6)
        self.assertTrue(self.sent)

    def test_a_publisher_that_raises_does_not_break_the_tick(self):
        def angry(node, payload):
            raise RuntimeError('broker gone')
        self.studio.publish = angry
        self.tick(count=3)
        self.assertGreater(self.studio.view()['transport']['failed'], 0)

    def test_ropes_are_periodically_resynchronized(self):
        self.tick(seconds=0.125, count=40)
        self.sent.clear()
        self.tick(seconds=RESYNC_SECONDS + 1)
        self.tick(count=4)
        self.assertTrue(self.sent)

    def test_reversing_a_rope_flips_the_status_region_only(self):
        """Reverse must never drag the accent to the wire end."""
        self.set('printer1', state='RUNNING', percent=30)
        config = self.studio.config()
        config['settings']['reduced_motion'] = True
        self.studio.save(config)
        self.tick(count=60)
        forward = list(self.studio.applied['node02'])
        status_a, cap_a = forward[:STATUS_POSITIONS], forward[STATUS_POSITIONS:]
        self.assertNotEqual(status_a[0], status_a[STATUS_POSITIONS - 1])

        config = self.studio.config()
        config['slots'][0]['reverse'] = True
        self.studio.save(config)
        self.tick(count=60)
        flipped = list(self.studio.applied['node02'])
        status_b, cap_b = flipped[:STATUS_POSITIONS], flipped[STATUS_POSITIONS:]

        self.assertEqual(status_b, list(reversed(status_a)))
        self.assertEqual(cap_b, cap_a, 'the accent moved when direction flipped')
        self.assertEqual(len(cap_b), ACCENT_POSITIONS)


class MappingTests(Harness):
    def test_saving_a_swap_is_visible_to_the_mcp_map_immediately(self):
        config = self.studio.config()
        config['slots'][0]['printer'], config['slots'][1]['printer'] = \
            config['slots'][1]['printer'], config['slots'][0]['printer']
        self.studio.save(config)
        self.assertEqual(self.studio.mapping()['printer2']['node'], 'node02')
        self.assertEqual(self.studio.mapping()['printer1']['node'], 'node03')
        self.assertEqual(set(self.studio.mapping()), set(MAP))

    def test_saving_forces_a_clean_repaint(self):
        self.tick(count=40)
        config = self.studio.config()
        config['slots'][2]['reverse'] = True
        self.studio.save(config)
        self.assertTrue(all(px is None for px in self.studio.applied['node04']))

    def test_a_stale_revision_conflicts_through_the_studio_too(self):
        config = self.studio.config()
        self.studio.save(copy.deepcopy(config))
        with self.assertRaises(Conflict):
            self.studio.save(config)

    def test_undo_and_reset_go_through_validation(self):
        config = self.studio.config()
        config['slots'][0]['label'] = 'Corner'
        self.studio.save(config)
        self.studio.undo(self.studio.config()['revision'])
        self.assertEqual(self.studio.config()['slots'][0]['label'], 'Printer 1')
        self.studio.reset(self.studio.config()['revision'])
        self.assertEqual(self.studio.config()['slots'][0]['node'], 'node02')


class ViewTests(Harness):
    def test_the_view_carries_no_hosts_serials_or_access_codes(self):
        self.tick()
        blob = json.dumps(self.studio.view())
        for secret in ('SECRETSERIAL', '10.0.0.5'):
            self.assertNotIn(secret, blob)

    def test_the_view_is_json_serializable_and_finite(self):
        self.tick()
        json.dumps(self.studio.view(), allow_nan=False)

    def test_a_render_failure_is_reported_without_leaking_detail(self):
        self.studio.last_error = 'Lighting update failed; check the Pi service log'
        self.assertNotIn('/home', self.studio.view()['last_error'])

    def test_the_view_reports_the_budget_and_honest_receipt_state(self):
        view = self.studio.view()
        self.assertEqual(view['transport']['node_rate_ceiling'], tp.NODE_RATE)
        self.assertEqual(view['transport']['node_receipt'], 'unverified')


class FilmTests(Harness):
    def test_a_live_film_matches_the_renderer_and_never_publishes(self):
        self.set('printer1', state='RUNNING', percent=40)
        self.tick()
        before = len(self.sent)
        film = self.studio.live_film(frames=4, fps=8)
        self.assertEqual(len(self.sent), before)
        self.assertEqual(len(film['ropes']), 7)
        self.assertEqual(len(film['ropes'][0]), 4)
        rebuilt = []
        for count, index in film['ropes'][0][0]:
            rebuilt.extend([list(film['palette'][index])] * count)
        self.assertEqual(rebuilt, self.studio.frames['node02'])

    def test_building_a_film_does_not_hold_the_writer_lock(self):
        """A browser poll must never be able to stall the coordinator mid-tick."""
        self.tick()
        held = []

        def watcher():
            got = self.studio.lock.acquire(timeout=2)
            held.append(got)
            if got:
                self.studio.lock.release()

        import threading
        original = self.studio._film

        def slow(*args, **kw):
            thread = threading.Thread(target=watcher)
            thread.start()
            thread.join(3)
            return original(*args, **kw)

        self.studio._film = slow
        self.studio.live_film(frames=2, fps=4)
        self.assertEqual(held, [True])

    def test_rendering_a_full_film_is_not_pathologically_slow(self):
        """Smoke bound with generous headroom for a loaded Pi.

        336 rope renders is four seconds of browser playback; on this Pi the
        loop takes a fraction of a second, so 3s catches only a real regression.
        """
        import time as wallclock
        self.tick()
        began = wallclock.monotonic()
        for _ in range(4):
            self.studio.live_film(frames=12, fps=12)
        elapsed = wallclock.monotonic() - began
        self.assertLess(elapsed, 3.0, f'336 rope frames took {elapsed:.2f}s')

    def test_film_size_and_rate_are_clamped(self):
        film = self.studio.live_film(frames=9999, fps=9999)
        self.assertLessEqual(film['frames'], 24)
        self.assertLessEqual(film['fps'], 20)
        self.assertGreaterEqual(self.studio.live_film(frames=0, fps=0)['fps'], 1)

    def test_the_lab_can_simulate_a_different_state_per_bay(self):
        bays = [{'state': s, 'percent': 50} for s in
                ('idle', 'printing', 'paused', 'error', 'finished', 'offline', 'unknown')]
        film = self.studio.sim_film(bays, frames=2, fps=4, start=1.0)
        self.assertTrue(film['simulated'])
        self.assertEqual(len(film['ropes']), 7)
        first = {tuple(tuple(r) for r in rope[0]) for rope in film['ropes']}
        self.assertEqual(len(first), 7)

    def test_simulation_never_publishes_and_never_moves_real_state(self):
        self.tick()
        before_status = copy.deepcopy(self.studio.status)
        before_sent = len(self.sent)
        self.studio.sim_film([{'state': 'error', 'percent': 10}] * 7,
                             frames=3, fps=6, start=2.0)
        self.studio.preview('printing', 90, 1.0)
        self.assertEqual(len(self.sent), before_sent)
        self.assertEqual(self.studio.status, before_status)
        self.assertIsNone(self.studio.ident)

    def test_simulation_input_is_validated(self):
        with self.assertRaises(ValueError):
            self.studio.sim_film([{'state': 'idle'}] * 6, start=0)
        with self.assertRaises(ValueError):
            self.studio.sim_film([{'state': 'melting'}] * 7, start=0)
        with self.assertRaises(ValueError):
            self.studio.sim_film([{'state': 'printing', 'percent': 900}] * 7, start=0)
        with self.assertRaises(ValueError):
            self.studio.sim_film('not a list', start=0)
        with self.assertRaises(ValueError):
            self.studio.sim_film([{'state': 'idle'}] * 7, start=0,
                                 settings={'brightness': 9000})

    def test_the_legacy_single_state_preview_still_returns_seven_frames(self):
        frames = self.studio.preview('idle', 50, 3.0)
        self.assertEqual(len(frames), 7)
        self.assertEqual(len(frames[0]), 100)


if __name__ == '__main__':
    unittest.main()
