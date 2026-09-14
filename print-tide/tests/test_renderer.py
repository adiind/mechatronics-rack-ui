"""Renderer: bounds, determinism, state distinctness, ripple order and priority."""
import unittest

from light_studio.layout import DEFAULTS
from light_studio.model import STATES
from light_studio.renderer import (ACCENT_POSITIONS, ALARM_FLOOR, BRIGHT_CAP,
                                   CELEBRATE_SECONDS, IDENTIFY_SECONDS, PIXELS,
                                   QUANT, RIPPLE_DELAY, RIPPLE_SECONDS,
                                   SHADE_BANDS, SPLASH_SECONDS, STATUS_POSITIONS,
                                   droplet_timing, is_pink, is_water, render_accent,
                                   render_rope)


def brightness(frame):
    return sum(sum(px) for px in frame)


def event(at=0.0, position=0, kind='complete'):
    return [{'at': at, 'position': position, 'kind': kind, 'printer': 'printer1'}]


class BoundsTests(unittest.TestCase):
    def test_every_state_is_in_range_quantized_and_the_right_length(self):
        for state in STATES:
            for percent in (None, 0, 37.5, 100):
                for t in (0.0, 1.3, 7.7, 91.25):
                    frame = render_rope(state, percent, t, 3, {'brightness': 100})
                    self.assertEqual(len(frame), STATUS_POSITIONS, state)
                    for pixel in frame:
                        self.assertEqual(len(pixel), 3)
                        for channel in pixel:
                            self.assertIsInstance(channel, int)
                            self.assertGreaterEqual(channel, 0)
                            self.assertLessEqual(channel, BRIGHT_CAP)
                            self.assertEqual(channel % QUANT, 0)

    def test_an_unrecognized_state_renders_as_unknown_not_as_an_exception(self):
        self.assertEqual(render_rope('banana', None, 1.0, 0),
                         render_rope('unknown', None, 1.0, 0))

    def test_rendering_is_deterministic(self):
        args = ('printing', 61.0, 12.5, 4, {'speed': 1.5}, event(12.0, 1))
        self.assertEqual(render_rope(*args), render_rope(*args))

    def test_brightness_zero_is_dark_and_uses_the_off_shaped_frame(self):
        frame = render_rope('idle', None, 2.0, 0, {'brightness': 0})
        self.assertEqual(brightness(frame), 0)

    def test_pixel_count_is_configurable_but_must_be_positive(self):
        self.assertEqual(len(render_rope('idle', None, 0, 0, pixels=20)), 20)
        with self.assertRaises(ValueError):
            render_rope('idle', None, 0, 0, pixels=0)


class DistinctnessTests(unittest.TestCase):
    def test_all_eight_states_look_different_at_the_same_instant(self):
        # 'finished' deliberately rests in the same cyan as 'idle' once its
        # rainbow is over, so it is sampled mid-celebration here.
        seen = {}
        for state in STATES:
            since = 1.0 if state == 'finished' else None
            frame = tuple(tuple(p) for p in render_rope(state, 50, 3.0, 0,
                                                        {'brightness': 100},
                                                        since_complete=since))
            self.assertNotIn(frame, seen, f'{state} looks identical to {seen.get(frame)}')
            seen[frame] = state

    def test_error_and_pause_are_not_mistakable_for_idle(self):
        # Idle is static; both alarms breathe, and error is the one saturated
        # (almost no green) pink while pause is a light one.
        idles = {tuple(tuple(p) for p in render_rope('idle', None, t, 0)) for t in (0.0, 1.0, 2.0, 3.0)}
        self.assertEqual(len(idles), 1)
        for state in ('error', 'paused'):
            frames = {tuple(tuple(p) for p in render_rope(state, None, t, 0)) for t in (0.0, 1.0, 2.0, 3.0)}
            self.assertGreater(len(frames), 1, state)
        error = render_rope('error', None, 3.0, 0, {'brightness': 100})[10]
        paused = render_rope('paused', None, 3.0, 0, {'brightness': 100})[10]
        self.assertLess(error[1], error[0] * 0.15)
        self.assertGreater(paused[1], paused[0] * 0.35)

    def test_every_lit_pixel_of_every_state_is_a_shade_of_pink(self):
        """Adi, 2026-09-13: 'REPLACE EVERY COLOR WITH A SHADE OF PINK' and
        'I DONT WANT WHITE, JUST SHADES OF PINK': is_pink rejects pastel and
        near-white as well as every other hue."""
        for state in STATES:
            for t in (0.0, 0.3, 1.1, 2.6, 5.0, 9.7):
                for since in (None, 1.0, 4.0, CELEBRATE_SECONDS + 1):
                    frame = render_rope(state, 42, t, 1, {'brightness': 100},
                                        since_complete=since)
                    for pixel in frame:
                        if sum(pixel):
                            self.assertTrue(is_pink(pixel), (state, t, since, pixel))
        # The accent cap's "rainbow" is a pink sweep, on every bay, at any time.
        for position in range(7):
            for t in (0.0, 3.0, 11.0, 29.0, 47.0, 88.0, 133.0):
                for pixel in render_accent({'mode': 'rainbow'}, t, position):
                    if sum(pixel):
                        self.assertTrue(is_pink(pixel), ('accent', position, t, pixel))

    def test_offline_is_dim_and_never_shows_a_progress_edge(self):
        # Averaged over the heartbeat cycle: a single instant sits on a thump or
        # in the rest between them and says nothing about how dim offline is.
        ticks = [k * 0.1 for k in range(24)]
        offline_mean = sum(brightness(render_rope('offline', None, t, 0,
                                                  {'brightness': 100}))
                           for t in ticks) / len(ticks)
        printing_mean = sum(brightness(render_rope('printing', 50, t, 0,
                                                   {'brightness': 100}))
                            for t in ticks) / len(ticks)
        self.assertLess(offline_mean, printing_mean)
        offline = render_rope('offline', None, 0.5, 0, {'brightness': 100})
        self.assertEqual(len({tuple(p) for p in offline[:40]}
                             & {tuple(p) for p in offline[60:]}), 1)

    def test_unknown_is_a_washed_out_dashed_pink_not_the_offline_heartbeat(self):
        unknown = render_rope('unknown', None, 1.0, 0, {'brightness': 100})
        offline = render_rope('offline', None, 0.1, 0, {'brightness': 100})
        self.assertEqual(len({tuple(p) for p in offline[:40]}), 1, 'offline is uniform')
        lit = [p for p in unknown if sum(p) > 0]
        self.assertTrue(lit)
        self.assertGreater(len({tuple(p) for p in unknown}), 1, 'unknown is dashed')
        for pixel in lit:                      # washed out, but still pink
            self.assertLess(max(pixel) - min(pixel), max(pixel) * 0.5)
            self.assertTrue(is_pink(pixel), pixel)


class ProgressTests(unittest.TestCase):
    def test_the_waterline_moves_with_the_percentage(self):
        def waterline(percent):
            frame = render_rope('printing', percent, 0.0, 0,
                                {'reduced_motion': True, 'brightness': 100})
            water = [i for i, p in enumerate(frame) if is_water(p)]
            return max(water) if water else -1
        self.assertLess(waterline(10), waterline(50))
        self.assertLess(waterline(50), waterline(90))

    def test_zero_percent_and_unknown_percent_do_not_render_the_same(self):
        zero = render_rope('printing', 0, 1.0, 0, {'reduced_motion': True})
        unknown = render_rope('printing', None, 1.0, 0, {'reduced_motion': True})
        self.assertNotEqual(zero, unknown)

    def test_full_progress_fills_the_rope(self):
        frame = render_rope('printing', 100, 0.0, 0,
                            {'reduced_motion': True, 'brightness': 100})
        self.assertTrue(all(p[1] > 0 for p in frame))

    def test_droplets_only_fall_while_there_is_room_above_the_waterline(self):
        moving = {tuple(tuple(p) for p in render_rope('printing', 20, t, 0))
                  for t in (0.1, 0.4, 0.7, 1.0)}
        self.assertGreater(len(moving), 1)

    def test_the_droplet_cycle_always_outlasts_the_fall_and_the_splash(self):
        """Regression: a fixed 2 s cycle silently broke below ~40% progress.

        With a 90-position region an empty bucket needs ~3 s of falling, so the
        droplet used to reset in mid-air and never land.
        """
        for travel in range(0, STATUS_POSITIONS):
            fall, cycle = droplet_timing(travel)
            self.assertGreaterEqual(cycle, fall + SPLASH_SECONDS, travel)
            self.assertGreater(cycle, 0)

    def test_a_droplet_falls_through_the_shades_and_reaches_the_waterline(self):
        """The drop lives only above the waterline, and lands."""
        settings = {'brightness': 100}
        for percent in (0, 3, 12, 25, 50, 90):
            fill = round(percent / 100 * STATUS_POSITIONS)
            _, cycle = droplet_timing((STATUS_POSITIONS - 1) - fill)
            still = render_rope('printing', percent, 0.0, 0,
                                dict(settings, reduced_motion=True))
            seen = []
            steps = 600
            for step in range(steps):
                frame = render_rope('printing', percent, step * cycle / steps, 0,
                                    settings)
                # Any pixel above the waterline that differs from the still
                # base is the drop.
                drops = [i for i in range(fill, STATUS_POSITIONS)
                         if frame[i] != still[i]]
                if drops:
                    seen.append(min(drops))
            if fill >= STATUS_POSITIONS - 3:
                continue                      # no room above the waterline
            self.assertTrue(seen, f'no droplet at {percent}%')
            self.assertLessEqual(min(seen), fill + 2,
                                 f'droplet never reached the waterline at {percent}%')
            self.assertGreater(max(seen), fill + 5,
                               f'droplet never started high at {percent}%')


class ThemeTests(unittest.TestCase):
    """Adi, 2026-09-13: 'make it a theme thing'. Every theme fills the whole
    palette, every state renders in every theme, and the theme setting reaches
    the renderer through the wall settings."""

    def test_every_theme_is_complete(self):
        from light_studio.themes import PALETTE_KEYS, THEMES, names, palette, describe
        self.assertEqual(set(names()), set(THEMES))
        for name in names():
            self.assertEqual(set(palette(name)), set(PALETTE_KEYS), name)
            for key in PALETTE_KEYS:
                value = palette(name)[key]
                if key.startswith('hue_'):
                    self.assertTrue(0.0 <= value <= 1.0, (name, key))
                else:
                    self.assertEqual(len(value), 3, (name, key))
                    self.assertTrue(all(0 <= c <= 255 for c in value), (name, key))
                    self.assertGreater(sum(value), 40, (name, key, 'reads as off'))
        self.assertEqual([t['name'] for t in describe()], names())
        self.assertEqual(palette('nonsense'), palette('pink'))

    def test_every_state_renders_and_states_stay_distinct_in_every_theme(self):
        from light_studio.themes import names
        for theme in names():
            seen = {}
            for state in STATES:
                since = 1.0 if state == 'finished' else None
                frame = render_rope(state, 50, 3.0, 0, {'brightness': 100, 'theme': theme},
                                    since_complete=since)
                self.assertTrue(any(sum(p) for p in frame), (theme, state))
                key = tuple(tuple(p) for p in frame)
                self.assertNotIn(key, seen, (theme, state, seen.get(key)))
                seen[key] = state

    def test_theme_setting_recolours_the_wall_and_the_hue_sweep(self):
        pink = render_rope('printing', 50, 0.0, 0, {'reduced_motion': True, 'brightness': 100})
        classic = render_rope('printing', 50, 0.0, 0,
                              {'reduced_motion': True, 'brightness': 100, 'theme': 'classic'})
        self.assertNotEqual(pink, classic)
        water = classic[0]
        self.assertGreater(water[0], water[1])          # classic water is orange ...
        self.assertEqual(water[2], 0)
        top = classic[STATUS_POSITIONS - 1]
        self.assertGreater(top[2], top[0])              # ... over deep blue
        error = render_rope('error', None, 1.0, 0, {'brightness': 100, 'theme': 'classic'})[10]
        self.assertEqual((error[1], error[2]), (0, 0))  # classic error is pure red
        # The explicit argument wins over the setting.
        self.assertEqual(render_rope('idle', None, 0.0, 0, {'theme': 'ocean'}, theme='classic'),
                         render_rope('idle', None, 0.0, 0, {'theme': 'classic'}))
        # The accent's hue sweep follows the theme: classic is a true rainbow.
        seen = {tuple(render_accent({'mode': 'rainbow'}, t, 0, theme='classic')[0])
                for t in [k * 0.25 for k in range(96)]}
        reds = [c for c in seen if c[0] > c[1] and c[0] > c[2]]
        greens = [c for c in seen if c[1] > c[0] and c[1] > c[2]]
        blues = [c for c in seen if c[2] > c[0] and c[2] > c[1]]
        self.assertTrue(reds and greens and blues)

    def test_water_is_recognised_in_every_theme(self):
        from light_studio.themes import names
        for theme in names():
            frame = render_rope('printing', 50, 0.0, 0, {'reduced_motion': True, 'theme': theme})
            fill = round(0.5 * STATUS_POSITIONS)
            for i in range(fill):
                self.assertTrue(is_water(frame[i], theme), (theme, i, frame[i]))
            for i in range(fill, STATUS_POSITIONS):
                self.assertFalse(is_water(frame[i], theme), (theme, i, frame[i]))

    def test_every_theme_stays_inside_the_message_budget(self):
        from light_studio import transport as tp
        from light_studio.studio import TICK_HZ
        from light_studio.themes import names
        for theme in names():
            for state in STATES:
                prev, changed = None, []
                for k in range(120):
                    since = 999 if state == 'finished' else None
                    frame = render_rope(state, 42, k / TICK_HZ, 2,
                                        dict(DEFAULTS, brightness=100, theme=theme),
                                        since_complete=since, speed='ludicrous')
                    if prev is not None:
                        changed.append(len(tp.diff_runs(prev, frame)))
                    prev = frame
                self.assertLessEqual(sum(changed) / len(changed), tp.NODE_RATE / TICK_HZ * 0.75,
                                     (theme, state))
                self.assertLessEqual(max(changed), tp.MAX_MSGS_PER_NODE_TICK, (theme, state))


class SpeedProfileTests(unittest.TestCase):
    """Bambu's Silent / Standard / Sport / Ludicrous show as the tempo of the
    rain, never as a colour (Adi, 2026-09-13: 'recognise ludicrous and sports
    mode on them')."""

    def drops(self, speed, seconds=12.0):
        settings = {'brightness': 100}
        still = render_rope('printing', 30, 0.0, 0, dict(settings, reduced_motion=True), speed=speed)
        fill = round(0.3 * STATUS_POSITIONS)
        landings = 0
        prev_air = False
        for k in range(int(seconds * 40)):
            frame = render_rope('printing', 30, k / 40.0, 0, settings, speed=speed)
            in_air = any(frame[i] != still[i] for i in range(fill + 3, STATUS_POSITIONS))
            if prev_air and not in_air:
                landings += 1
            prev_air = in_air
        return landings

    def test_faster_profiles_rain_harder(self):
        from light_studio.renderer import DROP_SPEED, SPEED_TEMPO
        # Each faster profile falls faster and cycles sooner ...
        cycles = [droplet_timing(60, DROP_SPEED * SPEED_TEMPO[s], SPEED_TEMPO[s])
                  for s in ('silent', 'standard', 'sport', 'ludicrous')]
        for (fall_a, cycle_a), (fall_b, cycle_b) in zip(cycles, cycles[1:]):
            self.assertLess(fall_b, fall_a)
            self.assertLess(cycle_b, cycle_a)
        # ... so over a long enough window more drops land.
        silent, standard = self.drops('silent', 40), self.drops('standard', 40)
        sport, ludicrous = self.drops('sport', 40), self.drops('ludicrous', 40)
        self.assertLess(silent, standard)
        self.assertLessEqual(standard, sport)
        self.assertLess(standard, ludicrous)

    def test_sport_and_ludicrous_leave_a_trail_behind_the_drop(self):
        """Adi, 2026-09-13: 'it should like leave trails or something'."""
        from light_studio.renderer import TRAIL_LENGTH
        settings = {'brightness': 100}
        still = render_rope('printing', 20, 0.0, 0, dict(settings, reduced_motion=True))
        fill = round(0.2 * STATUS_POSITIONS)

        def widest_streak(speed):
            widest = 0
            for k in range(400):
                frame = render_rope('printing', 20, k * 0.02, 0, settings, speed=speed)
                moving = [i for i in range(fill + 4, STATUS_POSITIONS) if frame[i] != still[i]]
                if moving:
                    widest = max(widest, max(moving) - min(moving) + 1)
            return widest

        self.assertEqual(widest_streak('standard'), 1, 'a plain drop at standard speed')
        self.assertGreaterEqual(widest_streak('sport'), 1 + TRAIL_LENGTH['sport'] - 1)
        self.assertGreater(widest_streak('ludicrous'), widest_streak('sport'))
        self.assertLessEqual(widest_streak('ludicrous'), 1 + TRAIL_LENGTH['ludicrous'])

    def test_the_trail_never_reaches_into_the_water(self):
        """Inside the water a pixel is water or the landing splash, never the
        drop or its tail."""
        from light_studio.renderer import PINK
        settings = {'brightness': 100}
        for percent in (5, 40, 85):
            still = render_rope('printing', percent, 0.0, 0, dict(settings, reduced_motion=True))
            fill = round(percent / 100 * STATUS_POSITIONS)
            for k in range(300):
                frame = render_rope('printing', percent, k * 0.03, 0, settings, speed='ludicrous')
                for i in range(fill):
                    if frame[i] == still[i]:
                        continue
                    # Not water: must be nearer the splash than the drop or tail.
                    from light_studio.renderer import _chroma_distance
                    to_splash = _chroma_distance(frame[i], PINK['splash'])
                    self.assertLess(to_splash, _chroma_distance(frame[i], PINK['drop']), (percent, k, i))

    def test_unknown_profile_is_standard_tempo(self):
        for t in (0.0, 0.7, 2.3, 5.5):
            self.assertEqual(render_rope('printing', 30, t, 0, {}, speed=None),
                             render_rope('printing', 30, t, 0, {}, speed='standard'))
            self.assertEqual(render_rope('printing', 30, t, 0, {}, speed='warp'),
                             render_rope('printing', 30, t, 0, {}, speed='standard'))

    def test_speed_never_changes_the_progress_or_the_colours(self):
        for speed in ('silent', 'standard', 'sport', 'ludicrous'):
            still = render_rope('printing', 40, 3.0, 0, {'reduced_motion': True}, speed=speed)
            self.assertEqual(still, render_rope('printing', 40, 3.0, 0, {'reduced_motion': True}))
            for t in (0.0, 0.9, 4.4):
                for pixel in render_rope('printing', 40, t, 0, {'brightness': 100}, speed=speed):
                    if sum(pixel):
                        self.assertTrue(is_pink(pixel), (speed, t, pixel))

    def test_ludicrous_rain_stays_inside_the_message_budget(self):
        from light_studio import transport as tp
        from light_studio.studio import TICK_HZ
        prev, changed = None, []
        for k in range(240):
            frame = render_rope('printing', 12, k / TICK_HZ, 2, dict(DEFAULTS, brightness=100),
                                speed='ludicrous')
            if prev is not None:
                changed.append(len(tp.diff_runs(prev, frame)))
            prev = frame
        self.assertLessEqual(sum(changed) / len(changed), tp.NODE_RATE / TICK_HZ * 0.75)
        self.assertLessEqual(max(changed), tp.MAX_MSGS_PER_NODE_TICK)


class PinkThemeTests(unittest.TestCase):
    """A running print: hot-pink fill under a remainder of progressively
    darker pinks (Adi, 2026-09-13: "a full pink theme", no white). Pure red
    is reserved for the error state (2026-09-12: a red default read as an
    alarm)."""

    def bar(self, percent, t=0.0, **settings):
        return render_rope('printing', percent, t, 0,
                           dict({'brightness': 100, 'reduced_motion': True},
                                **settings))

    def test_zero_percent_is_entirely_shaded_pink(self):
        frame = self.bar(0)
        self.assertEqual(len(set(tuple(p) for p in frame)), SHADE_BANDS)
        for pink in frame:
            self.assertGreater(pink[0], pink[2])      # red-led ...
            self.assertGreater(pink[2], pink[1])      # ... with blue over green: pink, not orange
            self.assertGreater(pink[1], 0)            # never pure red / magenta
        self.assertGreater(frame[0][0], 200)

    def test_the_remainder_is_pink_shades_getting_progressively_darker(self):
        """Adi, 2026-09-13: 'different shades of pink ... progressively darker'.
        Hot pink at the waterline, SHADE_BANDS distinct shades, each darker than
        the one before it, the darkest still clearly lit."""
        for percent in (0, 10, 33, 50, 75, 90):
            frame = self.bar(percent)
            fill = round(percent / 100 * STATUS_POSITIONS)
            rest = [tuple(p) for p in frame[fill:STATUS_POSITIONS]]
            shades = []
            for pixel in rest:
                if not shades or shades[-1] != pixel:
                    shades.append(pixel)
            self.assertEqual(len(shades), SHADE_BANDS, (percent, shades))
            self.assertEqual(len(set(shades)), SHADE_BANDS, (percent, shades))
            for lighter, darker in zip(shades, shades[1:]):
                self.assertGreater(sum(lighter), sum(darker), (percent, shades))
                self.assertGreater(lighter[0], darker[0], (percent, shades))
            self.assertGreater(sum(shades[-1]), 40, shades[-1])

    def test_a_printing_rope_never_shows_a_pure_red_pixel(self):
        """Red means error and nothing else on this wall. Every lit pixel of a
        printing rope, drop and marker included, is a pink with blue well up
        against red, so none of it can read as red."""
        for percent in (None, 0, 5, 27, 50, 99, 100):
            for t in (0.0, 0.7, 2.3, 5.5, 9.1):
                for pixel in render_rope('printing', percent, t, 0,
                                         {'brightness': 100}):
                    if sum(pixel):
                        self.assertTrue(is_pink(pixel), (percent, t, pixel))

    def test_error_body_is_deep_saturated_fuchsia(self):
        """The alarm pink: strong red and blue, almost no green, across the breath."""
        for t in (0.0, 0.5, 1.0, 1.9, 3.3):
            frame = render_rope('error', 40, t, 0, {'brightness': 100})
            body = frame[3:-3]
            self.assertTrue(body)
            for pixel in body:
                self.assertGreater(pixel[0], 100, (t, pixel))
                self.assertLess(pixel[1], pixel[0] * 0.12, (t, pixel))
                self.assertGreater(pixel[2], pixel[0] * 0.3, (t, pixel))

    def test_one_hundred_percent_is_entirely_hot_pink(self):
        frame = self.bar(100)
        self.assertEqual(len(set(tuple(p) for p in frame)), 1)
        water = frame[0]
        self.assertTrue(is_pink(water), water)
        self.assertGreater(water[0], 200, 'the water is bright')

    def test_the_bar_is_hot_pink_below_the_waterline_and_deeper_above(self):
        for percent in (10, 25, 50, 75, 90):
            frame = self.bar(percent)
            fill = round(percent / 100 * STATUS_POSITIONS)
            water = frame[0]
            for i in range(fill):
                self.assertEqual(frame[i], water, (percent, i))
            for i in range(fill, STATUS_POSITIONS):
                r, g, b = frame[i]
                self.assertGreater(r, b, (percent, i))
                self.assertGreater(b, g, (percent, i))
                # Every remainder shade is deeper than the water.
                self.assertLess(g, water[1] - 30, (percent, i))
                self.assertLess(sum(frame[i]), sum(water), (percent, i))

    def test_the_water_is_a_single_solid_colour_and_the_remainder_is_banded(self):
        frame = self.bar(50)
        fill = round(0.5 * STATUS_POSITIONS)
        self.assertEqual(len(set(tuple(p) for p in frame[:fill])), 1)
        self.assertEqual(len(set(tuple(p) for p in frame[fill:])), SHADE_BANDS)

    def test_no_pixel_of_a_printing_rope_is_ever_dark(self):
        """The old blue remainder quantized to near-black and read as 'off'."""
        for percent in (0, 5, 27, 50, 99, 100):
            for t in (0.0, 0.7, 2.3, 5.5):
                for pixel in render_rope('printing', percent, t, 0,
                                         {'brightness': 100}):
                    self.assertGreater(sum(pixel), 40, (percent, t, pixel))

    def test_a_still_bar_is_one_run_per_zone_and_fits_a_single_tick(self):
        """Water plus SHADE_BANDS bands: the whole base paints in one tick and
        then holds still, so it costs nothing between percent changes."""
        from light_studio import transport as tp
        frame = self.bar(40)
        runs = 1 + sum(1 for a, b in zip(frame, frame[1:]) if a != b)
        self.assertEqual(runs, 1 + SHADE_BANDS)
        self.assertLessEqual(runs, tp.MAX_MSGS_PER_NODE_TICK)


class MotionTests(unittest.TestCase):
    def test_idle_is_a_still_uniform_rose(self):
        # The resting state is deliberately static: no motion, no transport cost.
        frames = {tuple(tuple(p) for p in render_rope('idle', None, t, 0))
                  for t in (0.0, 0.7, 1.4, 2.1, 2.8)}
        self.assertEqual(len(frames), 1)
        frame = render_rope('idle', None, 1.0, 0)
        self.assertEqual(len({tuple(p) for p in frame}), 1)
        r, g, b = frame[0]
        self.assertGreater(r, b)
        self.assertGreater(b, g)
        self.assertGreater(r, 100)

    def test_idle_ignores_speed_and_position(self):
        slow = render_rope('idle', None, 4.0, 0, {'speed': 0.25})
        fast = render_rope('idle', None, 4.0, 3, {'speed': 2.0})
        self.assertEqual(slow, fast)

    def test_quiet_mode_dims_idle_a_lot(self):
        loud = render_rope('idle', None, 1.0, 0, {'brightness': 100, 'quiet': False})
        quiet = render_rope('idle', None, 1.0, 0, {'brightness': 100, 'quiet': True})
        self.assertLess(brightness(quiet), brightness(loud) * 0.4)

    def test_alarms_keep_a_visibility_floor_under_quiet_and_low_brightness(self):
        for state in ('error', 'paused'):
            dim = render_rope(state, None, 1.0, 0, {'brightness': 1, 'quiet': True})
            self.assertGreater(max(max(p) for p in dim), BRIGHT_CAP * ALARM_FLOOR * 0.5,
                               state)

    def test_quiet_mode_does_not_hide_an_error_behind_idle(self):
        idle = render_rope('idle', None, 1.0, 0, {'quiet': True})
        error = render_rope('error', None, 1.0, 0, {'quiet': True})
        self.assertGreater(brightness(error), brightness(idle))


class RippleTests(unittest.TestCase):
    def test_a_ripple_reaches_a_near_bay_before_a_far_bay(self):
        at, t = 0.0, RIPPLE_DELAY * 1.5
        self.assertNotEqual(render_rope('idle', None, t, 0, {}, event(at, 0)),
                            render_rope('idle', None, t, 0, {}, []))
        self.assertEqual(render_rope('idle', None, t, 6, {}, event(at, 0)),
                         render_rope('idle', None, t, 6, {}, []))

    def test_a_ripple_expires_and_does_not_linger(self):
        late = RIPPLE_SECONDS + 0.2
        self.assertEqual(render_rope('idle', None, late, 0, {}, event(0.0, 0)),
                         render_rope('idle', None, late, 0, {}, []))

    def test_ripples_never_touch_error_pause_offline_or_unknown(self):
        for state in ('error', 'paused', 'offline', 'unknown'):
            plain = render_rope(state, 50, 0.3, 0, {}, [])
            rippled = render_rope(state, 50, 0.3, 0, {}, event(0.0, 0))
            self.assertEqual(plain, rippled, state)

    def test_a_ripple_does_not_erase_printing_progress(self):
        def edge(frame):
            return [i for i, p in enumerate(frame) if is_water(p)]
        plain = render_rope('printing', 40, 0.3, 0, {'reduced_motion': False}, [])
        rippled = render_rope('printing', 40, 0.3, 0, {}, event(0.0, 0))
        self.assertNotEqual(plain, rippled)
        self.assertTrue(edge(rippled))
        self.assertLessEqual(abs(max(edge(rippled)) - max(edge(plain))), 4)

    def test_the_ripple_toggle_and_reduced_motion_both_suppress_ripples(self):
        for settings in ({'ripples': False}, {'reduced_motion': True}):
            self.assertEqual(render_rope('idle', None, 0.2, 0, settings, event()),
                             render_rope('idle', None, 0.2, 0, settings, []))


class CelebrationTests(unittest.TestCase):
    def test_a_fresh_completion_differs_from_the_steady_collect_signal(self):
        fresh = render_rope('finished', 100, 1.0, 0, {}, since_complete=0.3)
        steady = render_rope('finished', 100, 1.0, 0, {}, since_complete=None)
        self.assertNotEqual(fresh, steady)

    def test_celebration_is_a_uniform_pink_wash_that_turns_and_ends_in_collect_pink(self):
        seen = set()
        one_cycle = CELEBRATE_SECONDS / 2          # RAINBOW_CYCLES turns in total
        for k in range(6):
            since = 0.25 + k * one_cycle / 6
            frame = render_rope('finished', 100, 1.0, 0, {}, since_complete=since)
            self.assertEqual(len({tuple(p) for p in frame}), 1, 'one hue per tick')
            seen.add(tuple(frame[0]))
        self.assertGreaterEqual(len(seen), 5, 'the hue must actually turn')
        nearly = render_rope('finished', 100, 1.0, 0, {}, since_complete=CELEBRATE_SECONDS - 0.05)
        rest = render_rope('finished', 100, 1.0, 0, {}, since_complete=None)
        for a, b in zip(nearly, rest):
            for x, y in zip(a, b):
                self.assertLessEqual(abs(x - y), QUANT, 'cross-fade hands off to the collect pink with no visible step')
        self.assertNotEqual(rest, render_rope('idle', None, 1.0, 0, {}),
                            'ready-to-collect must be distinguishable from idle')
        self.assertEqual(len({tuple(p) for p in rest}), 1)
        # The lightest pink on the wall, but still clearly pink, never white.
        self.assertTrue(is_pink(rest[0]), rest[0])
        self.assertGreater(rest[0][1], rest[0][0] * 0.45)

    def test_stopped_is_a_steady_deep_plum_unlike_idle_and_collect(self):
        frame = render_rope('stopped', None, 3.0, 0, {'brightness': 100})
        self.assertEqual(frame, render_rope('stopped', None, 7.5, 0, {'brightness': 100}),
                         'stopped is static: it must cost nothing to hold')
        idle = render_rope('idle', None, 3.0, 0, {'brightness': 100})[10]
        rest = render_rope('finished', 100, 3.0, 0, {'brightness': 100}, since_complete=None)[10]
        body = frame[3:-3]
        for pixel in body:
            self.assertLess(sum(pixel), sum(idle), 'darker than idle')
            self.assertLess(sum(pixel), sum(rest), 'darker than collect')
            self.assertGreater(pixel[0], pixel[2])
            self.assertGreater(pixel[2], pixel[1])

    def test_the_celebration_is_bounded_and_settles_back_to_steady_green(self):
        steady = render_rope('finished', 100, 5.0, 0, {}, since_complete=None)
        after = render_rope('finished', 100, 5.0, 0, {},
                            since_complete=CELEBRATE_SECONDS + 0.1)
        self.assertEqual(steady, after)

    def test_the_collect_signal_rests_in_the_lightest_pink(self):
        for since in (CELEBRATE_SECONDS - 0.01, CELEBRATE_SECONDS + 5, None):
            frame = render_rope('finished', 100, 2.0, 0, {'brightness': 100},
                                since_complete=since)
            for pixel in frame[:STATUS_POSITIONS]:
                self.assertTrue(is_pink(pixel), (since, pixel))
                self.assertGreater(min(pixel), 100, (since, pixel))


class IdentifyTests(unittest.TestCase):
    def test_identify_overrides_whatever_the_printer_is_doing(self):
        for state in STATES:
            frame = render_rope(state, 50, 3.0, 0, {}, identify_age=0.5)
            self.assertEqual(frame, render_rope('idle', None, 3.0, 0, {},
                                                identify_age=0.5))

    def test_identify_pulses_rather_than_holding_one_colour(self):
        levels = {brightness(render_rope('idle', None, 0, 0, {}, identify_age=a))
                  for a in (0.05, 0.35, 0.75, 1.3, 2.0)}
        self.assertGreaterEqual(len(levels), 3)

    def test_identify_stays_visible_in_quiet_mode(self):
        quiet = render_rope('idle', None, 0, 0, {'quiet': True, 'brightness': 5},
                            identify_age=0.35)
        self.assertGreater(max(max(p) for p in quiet), BRIGHT_CAP * 0.3)

    def test_identify_age_is_clamped_to_its_bounded_window(self):
        frame = render_rope('idle', None, 0, 0, {}, identify_age=IDENTIFY_SECONDS + 5)
        self.assertEqual(len(frame), STATUS_POSITIONS)


class TransportFriendlinessTests(unittest.TestCase):
    """The renderer is written to be cheap to transmit; hold it to that."""

    def _runs(self, frame):
        count = 1
        for a, b in zip(frame, frame[1:]):
            if a != b:
                count += 1
        return count

    def test_frames_stay_within_a_modest_number_of_colour_runs(self):
        for state in STATES:
            for t in (0.0, 1.1, 2.7):
                frame = render_rope(state, 43, t, 0, dict(DEFAULTS, brightness=100))
                self.assertLessEqual(self._runs(frame), 34, (state, t))

    def test_consecutive_frames_differ_in_only_a_few_runs(self):
        for state in STATES:
            a = render_rope(state, 43, 2.000, 0, dict(DEFAULTS, brightness=100))
            b = render_rope(state, 43, 2.125, 0, dict(DEFAULTS, brightness=100))
            changed = 0
            i = 0
            while i < STATUS_POSITIONS:
                j = i + 1
                while j < STATUS_POSITIONS and b[j] == b[i]:
                    j += 1
                if any(a[k] != b[i] for k in range(i, j)):
                    changed += 1
                i = j
            self.assertLessEqual(changed, 8, state)

    def test_every_state_stays_under_the_sustained_message_budget(self):
        """Regression for the torn 'random colours' rope of 2026-09-06.

        The transport allows NODE_RATE messages/second per rope at TICK_HZ
        ticks/second. A scene whose consecutive frames differ in more runs than
        that, on average, is only partially applied each tick and the rope tears
        into a patchwork. Hold every state to comfortably under the budget.
        """
        from light_studio import transport as tp
        from light_studio.studio import TICK_HZ
        per_tick_budget = tp.NODE_RATE / TICK_HZ          # 3 runs per tick
        for state in STATES:
            prev = None
            changed = []
            for k in range(160):
                since = 999 if state == 'finished' else None
                frame = render_rope(state, 42, k / TICK_HZ, 2,
                                    dict(DEFAULTS, brightness=100), since_complete=since)
                if prev is not None:
                    changed.append(len(tp.diff_runs(prev, frame)))
                prev = frame
            mean = sum(changed) / len(changed)
            self.assertLessEqual(mean, per_tick_budget * 0.75, (state, mean))
            self.assertLessEqual(max(changed), tp.MAX_MSGS_PER_NODE_TICK, state)


if __name__ == '__main__':
    unittest.main()
