"""FW-01: the bottom 30 % of every strip is physically inactive.

Adi, 2026-09-14: "ignore the bottom 30% of every strip; loading starts above
that point." Physical index 0 is the data-in wire and hangs at the bottom, so
the inactive foot is physical 0-29, the active region 30-89 and the accent cap
90-99. These tests check the *composed* frames, the payloads that would reach
the wire, and the film payloads the browser plays -- not the renderer alone --
across every state, both directions, reduced motion, quiet mode, Identify,
ripples and a witnessed celebration.
"""
import unittest

from light_studio.renderer import (ACCENT_POSITIONS, ACCENT_START, IDENTIFY_SECONDS,
                                   INACTIVE_POSITIONS, PIXELS, STATUS_POSITIONS,
                                   STATUS_START, compose_rope, is_water, mask_inactive,
                                   render_accent, render_rope)
from light_studio.model import STATES
from test_studio import Harness

DARK = [0, 0, 0]
FOOT = [DARK] * INACTIVE_POSITIONS
NODES_ALL = [f'node0{i}' for i in range(2, 9)]


def decode(film):
    """Expand a run-length film into ``ropes[rope][frame][pixel]``."""
    palette = film['palette']
    out = []
    for rope in film['ropes']:
        frames = []
        for runs in rope:
            frame = []
            for count, index in runs:
                frame.extend([list(palette[index])] * count)
            frames.append(frame)
        out.append(frames)
    return out


class GeometryTests(unittest.TestCase):
    def test_the_default_geometry_is_thirty_sixty_ten(self):
        self.assertEqual((INACTIVE_POSITIONS, STATUS_POSITIONS, ACCENT_POSITIONS), (30, 60, 10))
        self.assertEqual(STATUS_START, 30)
        self.assertEqual(ACCENT_START, 90)
        self.assertEqual(PIXELS, 100)

    def test_the_renderer_only_ever_produces_the_active_region(self):
        for state in STATES:
            frame = render_rope(state, 40, 1.0, 0, {'brightness': 100})
            self.assertEqual(len(frame), STATUS_POSITIONS, state)

    def test_compose_prepends_a_dark_foot_in_both_directions(self):
        status = [[1, 2, 3]] * (STATUS_POSITIONS - 1) + [[9, 9, 9]]
        cap = [[140, 140, 140]] * ACCENT_POSITIONS
        for reverse in (False, True):
            frame = compose_rope(status, cap, reverse=reverse)
            self.assertEqual(len(frame), PIXELS)
            self.assertEqual(frame[:STATUS_START], FOOT, reverse)
            self.assertEqual(frame[ACCENT_START:], cap, reverse)
        forward = compose_rope(status, cap, reverse=False)
        backward = compose_rope(status, cap, reverse=True)
        # Reverse flips inside 30-89 only: the wire end of the active region
        # moves from physical 30 to physical 89, never into the foot.
        self.assertEqual(forward[STATUS_START], [1, 2, 3])
        self.assertEqual(forward[ACCENT_START - 1], [9, 9, 9])
        self.assertEqual(backward[STATUS_START], [9, 9, 9])
        self.assertEqual(backward[ACCENT_START - 1], [1, 2, 3])

    def test_the_mask_is_a_final_invariant_not_a_courtesy(self):
        # Even an over-long status list cannot light the foot.
        too_long = [[200, 200, 200]] * PIXELS
        frame = compose_rope(too_long, [], reverse=False)
        self.assertEqual(frame[:STATUS_START], FOOT)
        lit = [[200, 200, 200]] * PIXELS
        self.assertEqual(mask_inactive(lit)[:STATUS_START], FOOT)
        self.assertEqual(lit[STATUS_START], [200, 200, 200])

    def test_progress_maps_to_the_active_region_in_the_composed_frame(self):
        settings = {'brightness': 100, 'reduced_motion': True, 'theme': 'classic'}
        cap = render_accent(None)
        for percent, expected in ((0, 0), (25, 15), (50, 30), (75, 45), (100, 60)):
            frame = compose_rope(render_rope('printing', percent, 0.0, 0, settings,
                                             theme='classic'), cap)
            water = [i for i, p in enumerate(frame) if is_water(p, 'classic')]
            self.assertEqual(len(water), expected, percent)
            if expected:
                # Fills from the top of the foot upwards, contiguous.
                self.assertEqual(water, list(range(STATUS_START, STATUS_START + expected)),
                                 percent)
            self.assertEqual(frame[:STATUS_START], FOOT, percent)

    def test_unknown_progress_has_no_waterline_and_stays_above_the_foot(self):
        cap = render_accent(None)
        for t in (0.0, 0.7, 1.9, 3.3):
            frame = compose_rope(render_rope('printing', None, t, 0,
                                             {'brightness': 100}, theme='classic'), cap)
            self.assertEqual(frame[:STATUS_START], FOOT, t)
            # Unknown progress is a dim water-coloured body with one bright
            # travelling marker: the bright run is a handful of positions at
            # most, so it can never read as a waterline.
            bright = [i for i, p in enumerate(frame[:ACCENT_START])
                      if is_water(p, 'classic') and max(p) > 150]
            self.assertLess(len(bright), 15, t)
            self.assertTrue(all(i >= STATUS_START for i in bright), t)


class StudioFootTests(Harness):
    """The coordinator's composed frames and the payloads it would send."""

    def foot(self, node='node02'):
        return self.studio.frames[node][:STATUS_START]

    def active(self, node='node02'):
        return self.studio.frames[node][STATUS_START:ACCENT_START]

    def assert_foot_dark_everywhere(self, label):
        for node in NODES_ALL:
            frame = self.studio.frames[node]
            self.assertEqual(len(frame), PIXELS, (label, node))
            self.assertEqual(frame[:STATUS_START], FOOT, (label, node))

    def assert_no_payload_lights_the_foot(self, label):
        for node, payload in self.sent:
            op = payload.get('op')
            if op == 'off':
                continue
            if op == 'fill':
                self.assertEqual(payload['rgb'], DARK, (label, node, payload))
            elif op == 'pixel':
                if payload['index'] < STATUS_START:
                    self.assertEqual(payload['rgb'], DARK, (label, node, payload))
            elif op == 'range':
                if payload['start'] < STATUS_START:
                    self.assertEqual(payload['rgb'], DARK, (label, node, payload))
                    # And the foot is painted as one whole run, never piecemeal.
                    self.assertEqual((payload['start'], payload['count']),
                                     (0, INACTIVE_POSITIONS), (label, node, payload))

    def settings(self, **changes):
        config = self.studio.config()
        config['settings'].update(changes)
        self.studio.save(config)

    def test_every_state_at_many_times_keeps_the_foot_dark(self):
        raws = [('IDLE', None, False), ('PREPARE', 0, False), ('RUNNING', 0, False),
                ('RUNNING', 25, False), ('RUNNING', 50, False), ('RUNNING', 75, False),
                ('RUNNING', 100, False), ('RUNNING', None, False), ('PAUSE', 40, False),
                ('RUNNING', 40, True), ('FINISH', 100, False), ('FAILED', 30, False),
                ('WEIRD', None, False)]
        for raw, percent, error in raws:
            for index in range(1, 8):
                self.set(f'printer{index}', state=raw, percent=percent, has_error=error)
            self.tick(count=25)                       # ~3 s of animation
            self.assert_foot_dark_everywhere(raw)
            self.assert_no_payload_lights_the_foot(raw)
        self.set('printer1', mqtt_connected=False)
        self.tick(count=25)
        self.assert_foot_dark_everywhere('offline')
        self.assert_no_payload_lights_the_foot('offline')

    def test_reverse_keeps_the_foot_at_the_wire_end(self):
        self.set('printer1', state='RUNNING', percent=25)
        self.settings(reduced_motion=True)
        self.tick(count=3)
        forward = list(self.studio.frames['node02'])
        config = self.studio.config()
        config['slots'][0]['reverse'] = True
        self.studio.save(config)
        self.tick(count=3)
        flipped = list(self.studio.frames['node02'])
        self.assertEqual(forward[:STATUS_START], FOOT)
        self.assertEqual(flipped[:STATUS_START], FOOT)
        self.assertEqual(flipped[STATUS_START:ACCENT_START],
                         list(reversed(forward[STATUS_START:ACCENT_START])))
        self.assertEqual(flipped[ACCENT_START:], forward[ACCENT_START:])
        # 25 % forward = physical 30..44 water; reversed = physical 75..89.
        water_f = [i for i, p in enumerate(forward) if is_water(p)]
        water_b = [i for i, p in enumerate(flipped) if is_water(p)]
        self.assertEqual(water_f, list(range(30, 45)))
        self.assertEqual(water_b, list(range(75, 90)))

    def test_reduced_motion_and_quiet_mode_keep_the_geometry(self):
        for index in range(1, 8):
            self.set(f'printer{index}', state='RUNNING', percent=50)
        self.set('printer2', state='PAUSE')
        self.set('printer3', has_error=True)
        for still, quiet in ((True, False), (False, True), (True, True)):
            self.settings(reduced_motion=still, quiet=quiet, brightness=20)
            self.tick(count=10)
            self.assert_foot_dark_everywhere((still, quiet))
            # Alarms keep their floor, but only inside the active region.
            self.assertGreater(max(max(p) for p in self.active('node04')), 0)
            self.assertGreater(max(max(p) for p in self.active('node03')), 0)
            self.assert_no_payload_lights_the_foot((still, quiet))

    def test_identify_stays_inside_the_active_region_and_ends(self):
        self.tick(count=2)
        before = list(self.active())
        self.studio.identify('node02')
        for _ in range(int(IDENTIFY_SECONDS / 0.125) + 2):
            self.tick()
            self.assertEqual(self.foot(), FOOT)
            self.assertEqual(len(self.studio.frames['node02']), PIXELS)
        self.assertIsNone(self.studio.ident, 'identify did not end on its own')
        self.assert_no_payload_lights_the_foot('identify')
        self.tick(count=2)
        self.assertEqual(self.active(), before)

    def test_a_witnessed_completion_celebrates_above_the_foot_only(self):
        for index in range(1, 8):
            self.set(f'printer{index}', state='RUNNING', percent=95)
        self.tick(count=3)
        for index in range(1, 8):
            self.set(f'printer{index}', state='FINISH', percent=100)
        self.tick(count=2)
        self.assertTrue([e for e in self.studio.events if e['kind'] == 'complete'])
        for _ in range(40):
            self.tick(seconds=0.3)
            self.assert_foot_dark_everywhere('celebration')
            self.assertGreater(max(max(p) for p in self.active()), 0)
        self.assert_no_payload_lights_the_foot('celebration')

    def test_ripples_never_reach_the_foot(self):
        for index in range(1, 8):
            self.set(f'printer{index}', state='RUNNING', percent=60)
        self.tick(count=3)
        self.set('printer4', state='FINISH', percent=100)
        self.tick(count=2)
        self.assertTrue(self.studio.events)
        for _ in range(30):
            self.tick(seconds=0.1)
            self.assert_foot_dark_everywhere('ripple')
        self.assert_no_payload_lights_the_foot('ripple')

    def test_the_view_and_both_films_describe_the_foot(self):
        view = self.studio.view()
        self.assertEqual(view['inactive_positions'], 30)
        self.assertEqual(view['status_start'], 30)
        self.assertEqual(view['status_positions'], 60)
        self.assertEqual(view['accent_start'], 90)
        self.assertEqual(view['pixels'], 100)
        for index in range(1, 8):
            self.set(f'printer{index}', state='RUNNING', percent=10 * index)
        self.tick(count=2)
        self.sent.clear()
        live = self.studio.live_film(frames=6, fps=6)
        self.assertEqual(live['inactive_positions'], 30)
        self.assertEqual(live['status_start'], 30)
        for rope in decode(live):
            for frame in rope:
                self.assertEqual(len(frame), PIXELS)
                self.assertEqual(frame[:STATUS_START], FOOT)
        bays = [{'state': s, 'percent': p} for s, p in
                (('error', None), ('paused', None), ('printing', 73), ('idle', None),
                 ('offline', None), ('unknown', None), ('finished', 100))]
        sim = self.studio.sim_film(bays, frames=6, fps=6, start=1.7,
                                   reverses=[True] * 7,
                                   accents=[{'mode': 'rainbow', 'color': [255, 255, 255],
                                             'brightness': 100}] * 7)
        self.assertEqual(sim['inactive_positions'], 30)
        self.assertEqual(sim['status_start'], 30)
        for rope in decode(sim):
            for frame in rope:
                self.assertEqual(frame[:STATUS_START], FOOT)
                self.assertEqual(len(frame), PIXELS)
        self.assertEqual(self.sent, [], 'a preview published something')

    def test_the_foot_costs_at_most_one_message_per_full_repaint(self):
        self.set('printer1', state='RUNNING', percent=50)
        self.tick(count=40)
        self.sent.clear()
        self.tick(seconds=3.0)                     # forces a resync of every rope
        self.tick(count=4)
        foot_messages = [(n, p) for n, p in self.sent
                         if p.get('op') == 'range' and p.get('start') == 0]
        per_node = {}
        for node, _ in foot_messages:
            per_node[node] = per_node.get(node, 0) + 1
        for node, count in per_node.items():
            self.assertLessEqual(count, 1, node)


if __name__ == '__main__':
    unittest.main()
