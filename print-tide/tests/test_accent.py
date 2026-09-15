"""The fixed ten-position far-end accent cap.

The cap is a *fixture*: it sits at physical 90-99, the end opposite the data-in
wire, and nothing about printer state may ever reach it. These tests exist to
keep it that way. Since 2026-09-14 the active region is physical 30-89: the
bottom 30 positions are a dark foot (see test_inactive_foot.py).
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from light_studio.layout import (DEFAULT_ACCENT, LayoutStore, SCHEMA,
                                 default_layout, validate, validate_accent)
from light_studio.model import STATES
from light_studio.renderer import (ACCENT_POSITIONS, ACCENT_QUIET_SCALE, ACCENT_START,
                                   BRIGHT_CAP, INACTIVE_POSITIONS, is_water,
                                   PIXELS, QUANT, RAINBOW_PERIOD, RAINBOW_STEPS,
                                   STATUS_POSITIONS, STATUS_START, compose_rope,
                                   render_accent, render_rope)
from light_studio.studio import RESYNC_SECONDS, Studio
from test_core import MAP, NOW
from test_studio import Harness, fleet

WHITE = {'mode': 'white', 'color': [255, 255, 255], 'brightness': 100}
RED = {'mode': 'color', 'color': [255, 0, 0], 'brightness': 100}
RAINBOW = {'mode': 'rainbow', 'color': [255, 255, 255], 'brightness': 100}


class GeometryTests(unittest.TestCase):
    def test_the_wall_splits_into_foot_active_and_accent(self):
        self.assertEqual(ACCENT_POSITIONS, 10)
        self.assertEqual(INACTIVE_POSITIONS, 30)
        self.assertEqual(STATUS_POSITIONS, 60)
        self.assertEqual(STATUS_START, 30)
        self.assertEqual(ACCENT_START, 90)
        self.assertEqual(INACTIVE_POSITIONS + STATUS_POSITIONS + ACCENT_POSITIONS, PIXELS)

    def test_compose_puts_the_cap_at_the_far_end_both_directions(self):
        status = [[1, 2, 3]] * (STATUS_POSITIONS - 1) + [[9, 9, 9]]
        cap = [[140, 140, 140]] * ACCENT_POSITIONS
        forward = compose_rope(status, cap, reverse=False)
        backward = compose_rope(status, cap, reverse=True)
        self.assertEqual(len(forward), PIXELS)
        self.assertEqual(forward[ACCENT_START:], cap)
        self.assertEqual(backward[ACCENT_START:], cap)
        # Only the active region flipped; the foot stayed dark at the wire end.
        self.assertEqual(backward[STATUS_START:ACCENT_START],
                         list(reversed(forward[STATUS_START:ACCENT_START])))
        self.assertEqual(forward[:STATUS_START], [[0, 0, 0]] * STATUS_START)
        self.assertEqual(backward[:STATUS_START], [[0, 0, 0]] * STATUS_START)

    def test_compose_copies_rather_than_aliasing_its_inputs(self):
        cap = [[10, 10, 10]] * ACCENT_POSITIONS
        frame = compose_rope([[0, 0, 0]] * STATUS_POSITIONS, cap)
        frame[ACCENT_START][0] = 99
        self.assertEqual(cap[0], [10, 10, 10])


class AccentRenderTests(unittest.TestCase):
    def test_default_is_neutral_white_with_all_channels_equal(self):
        cap = render_accent(None)
        self.assertEqual(len(cap), ACCENT_POSITIONS)
        for pixel in cap:
            self.assertEqual(len(set(pixel)), 1, pixel)
            self.assertGreaterEqual(pixel[0], BRIGHT_CAP - QUANT)
        self.assertEqual(len(set(tuple(p) for p in cap)), 1, 'cap is not uniform')

    def test_the_default_accent_setting_is_white_at_full(self):
        self.assertEqual(DEFAULT_ACCENT['mode'], 'white')
        self.assertEqual(DEFAULT_ACCENT['brightness'], 100)

    def test_white_stays_neutral_at_every_brightness(self):
        for level in (0, 1, 17, 50, 99, 100):
            for pixel in render_accent({'mode': 'white', 'brightness': level}):
                self.assertEqual(len(set(pixel)), 1, (level, pixel))

    def test_solid_colour_is_reproduced_and_capped(self):
        cap = render_accent(RED)
        for pixel in cap:
            self.assertGreater(pixel[0], 0)
            self.assertEqual(pixel[1], 0)
            self.assertEqual(pixel[2], 0)
            self.assertLessEqual(pixel[0], BRIGHT_CAP)

    def test_brightness_zero_is_dark_and_never_negative(self):
        for pixel in render_accent({'mode': 'white', 'brightness': 0}):
            self.assertEqual(pixel, [0, 0, 0])

    def test_the_cap_never_exceeds_the_physical_ceiling(self):
        for accent in (WHITE, RED, RAINBOW, {'mode': 'color', 'color': [255, 255, 255]}):
            for t in (0.0, 3.3, 11.7, 23.9, 100.0):
                for pixel in render_accent(accent, t, 3):
                    for channel in pixel:
                        self.assertIsInstance(channel, int)
                        self.assertGreaterEqual(channel, 0)
                        self.assertLessEqual(channel, BRIGHT_CAP)
                        self.assertEqual(channel % QUANT, 0)

    def test_accent_brightness_is_the_only_level_control_it_has(self):
        """Separate from the wall slider: dimming the animation must not dim it."""
        full = render_accent({'mode': 'white', 'brightness': 100})[0][0]
        half = render_accent({'mode': 'white', 'brightness': 50})[0][0]
        self.assertLess(half, full)
        self.assertGreater(half, 0)

    def test_quiet_mode_dims_the_cap_but_leaves_it_clearly_lit(self):
        loud = render_accent(WHITE, quiet=False)[0][0]
        quiet = render_accent(WHITE, quiet=True)[0][0]
        self.assertLess(quiet, loud)
        self.assertGreater(quiet, 0)
        self.assertAlmostEqual(quiet / loud, ACCENT_QUIET_SCALE, delta=0.06)


class RainbowTests(unittest.TestCase):
    def _colours(self, samples, period=RAINBOW_PERIOD, position=0):
        step = period / samples
        return [tuple(render_accent(RAINBOW, i * step, position)[0])
                for i in range(samples)]

    def test_rainbow_moves_with_time(self):
        early = render_accent(RAINBOW, 0.0)
        later = render_accent(RAINBOW, RAINBOW_PERIOD / 4)
        self.assertNotEqual(early, later)

    def test_rainbow_is_uniform_across_the_ten_positions(self):
        """A per-position gradient would cost ten messages instead of one."""
        for t in (0.0, 2.5, 9.1, 18.4):
            cap = render_accent(RAINBOW, t, 2)
            self.assertEqual(len(set(tuple(p) for p in cap)), 1, t)

    def test_rainbow_is_stepped_to_stay_inside_the_message_budget(self):
        colours = self._colours(RAINBOW_STEPS * 8)
        changes = sum(1 for a, b in zip(colours, colours[1:]) if a != b)
        # One hue step is at most one message; 72 steps over 24 s is 3 per second.
        # The sweep only covers the pink hues, so after quantization fewer of
        # the 72 steps are distinct than a full-circle rainbow's were.
        self.assertLessEqual(changes, RAINBOW_STEPS + 2)
        self.assertGreaterEqual(changes, RAINBOW_STEPS // 3)
        self.assertLessEqual(changes / RAINBOW_PERIOD, 4.0)

    def test_rainbow_sweeps_the_pink_hues_only(self):
        # Adi, 2026-09-13: every colour on the wall is a shade of pink, the
        # "rainbow" included. It still has to move through many distinct shades.
        colours = set(self._colours(RAINBOW_STEPS * 4))
        self.assertGreater(len(colours), 12)
        for c in colours:
            self.assertGreater(c[0], c[1], c)          # red-led ...
            self.assertGreaterEqual(c[2], c[1], c)     # ... blue over green: pink
        self.assertTrue(any(c[2] > c[0] * 0.6 for c in colours), 'reaches magenta-pink')
        self.assertTrue(any(c[2] < c[0] * 0.55 for c in colours), 'reaches rose')

    def test_each_bay_is_offset_so_the_wall_reads_as_one_rainbow(self):
        first = render_accent(RAINBOW, 1.0, 0)[0]
        fourth = render_accent(RAINBOW, 1.0, 3)[0]
        self.assertNotEqual(first, fourth)

    def test_reduced_motion_freezes_the_rainbow(self):
        held = render_accent(RAINBOW, 0.0, 1, still=True)
        for t in (2.0, 8.0, 19.0):
            self.assertEqual(render_accent(RAINBOW, t, 1, still=True), held)

    def test_static_caps_are_genuinely_static(self):
        for accent in (WHITE, RED):
            base = render_accent(accent, 0.0, 4)
            for t in (0.5, 7.0, 60.0, 3600.0):
                self.assertEqual(render_accent(accent, t, 4), base)


class AccentValidationTests(unittest.TestCase):
    def test_defaults_fill_in(self):
        self.assertEqual(validate_accent(None), DEFAULT_ACCENT)
        self.assertEqual(validate_accent({}), DEFAULT_ACCENT)
        self.assertEqual(validate_accent({'mode': 'rainbow'})['color'],
                         DEFAULT_ACCENT['color'])

    def test_modes_are_restricted(self):
        for mode in ('white', 'color', 'rainbow'):
            self.assertEqual(validate_accent({'mode': mode})['mode'], mode)
        for mode in ('off', 'strobe', '', None, 3, True):
            with self.assertRaises(ValueError, msg=mode):
                validate_accent({'mode': mode})

    def test_colour_channels_reject_bools_floats_and_out_of_range(self):
        self.assertEqual(validate_accent({'color': [1, 2, 3]})['color'], [1, 2, 3])
        for colour in ([True, 0, 0], [0, 0, 256], [-1, 0, 0], [1.5, 0, 0],
                       [float('nan'), 0, 0], [1, 2], [1, 2, 3, 4], 'red', None,
                       {'r': 1}, [None, 0, 0]):
            with self.assertRaises(ValueError, msg=colour):
                validate_accent({'color': colour})

    def test_brightness_range_and_type(self):
        self.assertEqual(validate_accent({'brightness': 0})['brightness'], 0)
        self.assertEqual(validate_accent({'brightness': 100})['brightness'], 100)
        for level in (-1, 101, float('nan'), float('inf'), True, 'bright', None):
            with self.assertRaises(ValueError, msg=level):
                validate_accent({'brightness': level})

    def test_unknown_accent_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_accent({'mode': 'white', 'sparkle': True})

    def test_accent_must_be_an_object(self):
        for bad in ('white', 7, [1, 2, 3], True):
            with self.assertRaises(ValueError, msg=bad):
                validate_accent(bad)

    def test_validate_never_mutates_or_aliases_the_input(self):
        source = {'mode': 'color', 'color': [1, 2, 3], 'brightness': 50}
        clean = validate_accent(source)
        clean['color'][0] = 200
        self.assertEqual(source['color'], [1, 2, 3])


class MigrationTests(unittest.TestCase):
    """A schema-1/2 layout must gain caps without losing anything else."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def legacy(self, schema):
        layout = default_layout(MAP)
        layout['schema'] = schema
        layout['revision'] = 4
        for slot in layout['slots']:
            del slot['accent']
        layout['slots'].reverse()
        layout['slots'][0]['label'] = 'Corner rope'
        layout['slots'][0]['reverse'] = True
        layout['settings']['brightness'] = 42
        if schema == 1:
            del layout['settings']['waterline_marks']
        return layout

    def test_schema_1_and_2_migrate_and_keep_everything_else(self):
        for schema in (1, 2):
            clean = validate(self.legacy(schema), MAP)
            self.assertEqual(clean['schema'], SCHEMA)
            self.assertEqual(clean['revision'], 4)
            self.assertEqual(clean['slots'][0]['label'], 'Corner rope')
            self.assertTrue(clean['slots'][0]['reverse'])
            self.assertEqual(clean['settings']['brightness'], 42)
            self.assertEqual([s['node'] for s in clean['slots']],
                             [s['node'] for s in self.legacy(schema)['slots']])
            for slot in clean['slots']:
                self.assertEqual(slot['accent'], DEFAULT_ACCENT)

    def test_a_stored_legacy_file_migrates_on_load_and_keeps_undo(self):
        previous = self.legacy(2)
        previous['revision'] = 3
        (self.root / 'layout.json').write_text(json.dumps(
            {'current': self.legacy(2), 'previous': previous}))
        store = LayoutStore(self.root, MAP)
        self.assertEqual(store.snapshot()['schema'], SCHEMA)
        self.assertTrue(store.can_undo)
        self.assertEqual(store.snapshot()['slots'][0]['accent'], DEFAULT_ACCENT)

        restored = store.undo(store.snapshot()['revision'])
        self.assertEqual(restored['slots'][0]['label'], 'Corner rope')
        self.assertEqual(restored['slots'][0]['accent'], DEFAULT_ACCENT)

    def test_migrated_accents_persist_once_edited(self):
        (self.root / 'layout.json').write_text(json.dumps({'current': self.legacy(2)}))
        store = LayoutStore(self.root, MAP)
        config = store.snapshot()
        config['slots'][2]['accent'] = copy.deepcopy(RED)
        store.save(config)
        reloaded = LayoutStore(self.root, MAP).snapshot()
        self.assertEqual(reloaded['slots'][2]['accent'], RED)
        self.assertEqual(reloaded['slots'][0]['accent'], DEFAULT_ACCENT)


class StudioAccentTests(Harness):
    def cap(self, node='node02'):
        return self.studio.frames[node][ACCENT_START:]

    def status(self, node='node02'):
        return self.studio.frames[node][STATUS_START:ACCENT_START]

    def set_accent(self, accent, index=None):
        config = self.studio.config()
        for position, slot in enumerate(config['slots']):
            if index is None or position == index:
                slot['accent'] = copy.deepcopy(accent)
        self.studio.save(config)

    # -- the cap survives everything ------------------------------------
    def test_every_printer_state_leaves_the_chosen_cap_untouched(self):
        self.set_accent(RED)
        expected = render_accent(RED, 0, 0)
        raws = ['IDLE', 'PREPARE', 'RUNNING', 'PAUSE', 'FINISH', 'FAILED', None]
        for raw in raws:
            self.set('printer1', state=raw, percent=40)
            self.tick(count=3)
            self.assertEqual(self.cap(), expected, raw)
        # ... including an error flag and a dead link.
        self.set('printer1', state='RUNNING', has_error=True)
        self.tick(count=2)
        self.assertEqual(self.cap(), expected, 'error')
        self.set('printer1', mqtt_connected=False)
        self.tick(count=2)
        self.assertEqual(self.cap(), expected, 'offline')

    def test_identify_marks_the_status_region_and_preserves_the_cap(self):
        self.set_accent(RED)
        expected = render_accent(RED, 0, 0)
        self.tick(count=2)
        before = list(self.status())
        self.studio.identify('node02')
        self.tick()
        self.assertNotEqual(self.status(), before, 'identify did not mark the rope')
        self.assertEqual(self.cap(), expected, 'identify overwrote the cap')
        self.tick(seconds=6)
        self.assertEqual(self.cap(), expected, 'cap changed when identify ended')

    def test_celebrations_and_ripples_never_reach_the_cap(self):
        self.set_accent(RED)
        expected = render_accent(RED, 0, 0)
        for index in range(1, 8):
            self.set(f'printer{index}', state='RUNNING', percent=95)
        self.tick(count=3)
        for index in range(1, 8):
            self.set(f'printer{index}', state='FINISH', percent=100)
        self.tick(count=10)
        self.assertTrue(self.studio.events, 'expected a completion event')
        for node in NODES_ALL:
            self.assertEqual(self.studio.frames[node][ACCENT_START:], expected, node)

    def test_a_dark_wall_still_lights_the_cap(self):
        config = self.studio.config()
        config['settings']['brightness'] = 0
        self.studio.save(config)
        self.tick(count=2)
        self.assertEqual(max(max(p) for p in self.status()), 0)
        self.assertGreater(max(max(p) for p in self.cap()), 0)

    def test_the_wall_brightness_slider_does_not_move_the_cap(self):
        self.tick(count=2)
        bright = list(self.cap())
        config = self.studio.config()
        config['settings']['brightness'] = 15
        self.studio.save(config)
        self.tick(count=2)
        self.assertEqual(self.cap(), bright)

    # -- progress uses the status region only ---------------------------
    def test_progress_maps_to_the_sixty_position_active_region(self):
        config = self.studio.config()
        config['settings']['reduced_motion'] = True
        self.studio.save(config)
        for percent, expected in ((0, 0), (25, 15), (50, 30), (75, 45), (100, 60)):
            self.set('printer1', state='RUNNING', percent=percent)
            self.tick(count=2)
            filled = sum(1 for p in self.status() if is_water(p))
            self.assertEqual(filled, expected, percent)

    def test_the_cap_is_not_counted_as_progress(self):
        config = self.studio.config()
        config['settings']['reduced_motion'] = True
        self.studio.save(config)
        self.set('printer1', state='RUNNING', percent=100)
        self.tick(count=2)
        # Full progress fills exactly the active region, not 100 positions.
        self.assertEqual(len(self.status()), 60)
        self.assertEqual(sum(1 for p in self.status() if is_water(p)), 60)
        self.assertEqual(self.cap(), render_accent(WHITE, 0, 0))

    # -- per rope versus all ropes --------------------------------------
    def test_each_rope_keeps_its_own_cap(self):
        self.set_accent(RAINBOW, index=0)
        self.tick(count=2)
        self.assertEqual(self.studio.frames['node03'][ACCENT_START:],
                         render_accent(WHITE, 0, 1))
        self.assertNotEqual(self.cap(), render_accent(WHITE, 0, 0))

    def test_applying_one_cap_to_all_ropes(self):
        self.set_accent(RED)
        self.tick(count=2)
        for position, slot in enumerate(self.studio.config()['slots']):
            self.assertEqual(self.studio.frames[slot['node']][ACCENT_START:],
                             render_accent(RED, 0, position))

    def test_swapping_printers_does_not_move_a_cap(self):
        """The cap belongs to the rope, not to the printer driving it."""
        self.set_accent(RED, index=0)
        config = self.studio.config()
        config['slots'][0]['printer'], config['slots'][1]['printer'] = \
            config['slots'][1]['printer'], config['slots'][0]['printer']
        self.studio.save(config)
        after = self.studio.config()
        self.assertEqual(after['slots'][0]['node'], 'node02')
        self.assertEqual(after['slots'][0]['accent']['mode'], 'color')
        self.assertEqual(after['slots'][1]['accent']['mode'], 'white')

    # -- transport ------------------------------------------------------
    def _cap_messages(self, seconds):
        self.sent.clear()
        self.tick(seconds=0.125, count=int(seconds / 0.125))
        return [p for node, p in self.sent
                if p.get('op') == 'range' and p.get('start') == ACCENT_START
                and p.get('count') == ACCENT_POSITIONS]

    def test_a_static_cap_only_costs_its_periodic_resync(self):
        """White and colour caps generate no ongoing traffic of their own.

        They still reappear in the 12-second heal-the-rope resync, which is
        deliberate: that is what recovers a node that lost power.
        """
        self.set_accent(RED)
        self.tick(count=40)
        seconds = 24.0
        messages = self._cap_messages(seconds)
        resyncs_per_node = seconds / RESYNC_SECONDS
        self.assertLessEqual(len(messages), len(NODES_ALL) * (resyncs_per_node + 1))

    def test_a_rainbow_cap_costs_far_more_than_a_static_one(self):
        self.set_accent(RED)
        self.tick(count=40)
        static = len(self._cap_messages(8.0))
        self.set_accent(RAINBOW)
        self.tick(count=40)
        moving = len(self._cap_messages(8.0))
        self.assertGreater(moving, static * 3)

    def test_rainbow_caps_stay_inside_a_bounded_message_rate(self):
        self.set_accent(RAINBOW)
        self.tick(count=40)
        self.sent.clear()
        seconds = 8.0
        self.tick(seconds=0.125, count=int(seconds / 0.125))
        per_node = {}
        for node, payload in self.sent:
            if payload.get('op') == 'range' and payload.get('start') == ACCENT_START \
                    and payload.get('count') == ACCENT_POSITIONS:
                per_node[node] = per_node.get(node, 0) + 1
        self.assertTrue(per_node, 'the rainbow never repainted')
        for node, count in per_node.items():
            self.assertLessEqual(count / seconds, 4.5, (node, count))

    def test_no_message_ever_addresses_a_position_outside_the_rope(self):
        self.set_accent(RAINBOW)
        self.tick(count=60)
        for node, payload in self.sent:
            if payload['op'] == 'range':
                self.assertLessEqual(payload['start'] + payload['count'], PIXELS)
            if payload['op'] == 'pixel':
                self.assertLess(payload['index'], PIXELS)

    # -- API surface ----------------------------------------------------
    def test_the_view_describes_the_zone_split_for_the_browser(self):
        view = self.studio.view()
        self.assertEqual(view['pixels'], PIXELS)
        self.assertEqual(view['status_positions'], STATUS_POSITIONS)
        self.assertEqual(view['accent_positions'], ACCENT_POSITIONS)
        self.assertEqual(view['accent_start'], ACCENT_START)
        self.assertEqual(view['inactive_positions'], INACTIVE_POSITIONS)
        self.assertEqual(view['status_start'], STATUS_START)
        for slot in view['config']['slots']:
            self.assertEqual(set(slot['accent']), set(DEFAULT_ACCENT))

    def test_the_live_film_carries_the_zone_split_and_per_bay_caps(self):
        self.set_accent(RED, index=1)
        self.tick()
        film = self.studio.live_film(frames=2, fps=4)
        self.assertEqual(film['accent_start'], ACCENT_START)
        self.assertEqual(film['status_start'], STATUS_START)
        self.assertEqual(film['inactive_positions'], INACTIVE_POSITIONS)
        self.assertEqual(film['accent_positions'], ACCENT_POSITIONS)
        self.assertEqual(film['bays'][1]['accent']['mode'], 'color')
        self.assertEqual(film['bays'][0]['accent']['mode'], 'white')

    def test_the_lab_previews_draft_caps_and_direction_without_saving(self):
        before = self.studio.config()
        bays = [{'state': 'printing', 'percent': 50}] * 7
        film = self.studio.sim_film(bays, frames=1, fps=1, start=0.0,
                                    accents=[RED] * 7, reverses=[True] * 7)
        self.assertEqual(self.studio.config(), before, 'preview changed the layout')
        self.assertEqual(self.sent, [])
        palette = film['palette']
        frame = []
        for count, index in film['ropes'][0][0]:
            frame.extend([list(palette[index])] * count)
        self.assertEqual(frame[ACCENT_START:], render_accent(RED, 0, 0))

    def test_the_lab_rejects_malformed_accent_and_direction_input(self):
        bays = [{'state': 'idle'}] * 7
        for kwargs in ({'accents': [RED] * 6}, {'accents': 'red'},
                       {'accents': [{'mode': 'off'}] * 7},
                       {'accents': [{'color': [True, 0, 0]}] * 7},
                       {'reverses': [True] * 6}, {'reverses': ['yes'] * 7},
                       {'reverses': 'yes'}):
            with self.assertRaises(ValueError, msg=kwargs):
                self.studio.sim_film(bays, frames=1, fps=1, start=0.0, **kwargs)


NODES_ALL = sorted(target['node'] for target in MAP.values())


class DefaultWallTests(unittest.TestCase):
    def test_a_fresh_wall_starts_with_seven_white_caps(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = Studio(Path(tmp), MAP, fleet, None, lambda: True,
                            clock=lambda: NOW)
            studio.tick()
            for position, slot in enumerate(studio.config()['slots']):
                cap = studio.frames[slot['node']][ACCENT_START:]
                self.assertEqual(cap, render_accent(None, 0, position))
                for pixel in cap:
                    self.assertEqual(len(set(pixel)), 1)


if __name__ == '__main__':
    unittest.main()
