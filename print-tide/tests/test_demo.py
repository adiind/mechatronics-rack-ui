"""Demo mode: hardware-free by construction, and useful enough to review."""
import json
import tempfile
import unittest
from pathlib import Path

from light_studio.__main__ import REFERENCE_MAP, demo_rows
from light_studio.model import normalize
from light_studio.studio import Studio


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.initial = json.loads(REFERENCE_MAP.read_text())
        self.now = 1788670000.0
        self.studio = Studio(Path(self.tmp.name), self.initial,
                             lambda: demo_rows(self.now, self.started),
                             None, lambda: True, demo=True,
                             clock=lambda: self.now)
        self.started = self.now

    def test_the_demo_fleet_covers_every_state_the_wall_can_show(self):
        seen = set()
        for offset in range(0, 145, 5):
            for row in demo_rows(self.now + offset, self.started):
                seen.add(normalize(row, self.now + offset)['state'])
        self.assertTrue({'idle', 'preparing', 'printing', 'paused', 'error',
                         'finished', 'offline'} <= seen, seen)

    def test_the_demo_produces_a_genuine_observed_completion(self):
        for offset in range(0, 140, 2):
            self.now = self.started + offset
            self.studio.tick()
            if any(e['kind'] == 'complete' for e in self.studio.events):
                break
        else:
            self.fail('the demo cycle never completes a print')

    def test_the_demo_studio_has_no_publisher_at_all(self):
        self.assertIsNone(self.studio.publish)
        self.studio.tick()
        self.assertTrue(self.studio.demo)
        self.assertTrue(self.studio.view()['demo'])

    def test_demo_rows_carry_no_plausible_real_identifiers(self):
        blob = json.dumps(demo_rows(self.now, self.started))
        self.assertIn('demo-not-a-real-host', blob)
        self.assertNotIn('10.106.', blob)

    def test_the_demo_map_is_the_reference_map_and_excludes_node01(self):
        self.assertEqual(self.studio.mapping(), self.initial)
        self.assertNotIn('node01', {t['node'] for t in self.initial.values()})


if __name__ == '__main__':
    unittest.main()
