"""The /states reference page: served, self-contained, and its preview calls work."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from light_studio.studio import Studio
from light_studio.web import make_server
from test_core import MAP, NOW, report


class StatesPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        rows = [report('RUNNING', name=name, percent=40, speed_level=4, speed_percent=166)
                for name in MAP]
        self.studio = Studio(Path(self.tmp.name), MAP, lambda: rows, None,
                             lambda: True, demo=True, clock=lambda: NOW)
        self.studio.tick()
        self.http = make_server(self.studio, '127.0.0.1', 0)
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=10) as response:
            return response.status, response.headers.get('Content-Type', ''), response.read()

    def test_the_page_and_its_assets_are_served_with_the_right_types(self):
        status, ctype, body = self.get('/states')
        self.assertEqual(status, 200)
        self.assertIn('text/html', ctype)
        self.assertIn(b'/states.js', body)
        self.assertIn(b'/states.css', body)
        self.assertNotIn(b'<script>', body, 'CSP forbids inline scripts')
        self.assertNotIn(b'<style>', body, 'CSP forbids inline styles')
        for path, expected in (('/states.js', 'text/javascript'), ('/states.css', 'text/css')):
            status, ctype, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(expected, ctype, path)
            self.assertGreater(len(body), 500, path)

    def test_the_page_documents_every_state_the_renderer_knows(self):
        from light_studio.studio import STATES
        _, _, js = self.get('/states.js')
        for state in STATES:
            self.assertIn(f"key: '{state}'".encode(), js, state)

    def test_the_preview_shape_the_page_sends_is_accepted(self):
        _, _, body = self.get('/api/state')
        state = json.loads(body)
        count = len(state['config']['slots'])
        group = [['idle', None], ['preparing', None], ['printing', 25], ['paused', None],
                 ['error', None], ['finished', 100], ['stopped', None]]
        bays = [{'state': s, 'percent': p} for s, p in (group[i % len(group)] for i in range(count))]
        bays[2]['percent'] = None                     # "% unknown" row
        for theme in (t['name'] for t in state['themes']):
            payload = {'bays': bays, 'frames': 24, 'fps': 12, 'time': 0.0,
                       'settings': {'theme': theme, 'brightness': 100, 'reduced_motion': False},
                       'accents': [{'mode': 'rainbow', 'color': [255, 255, 255], 'brightness': 100}] * count}
            req = urllib.request.Request(
                self.url + '/api/preview', data=json.dumps(payload).encode(),
                headers={'Content-Type': 'application/json', 'Origin': self.url,
                         'X-CSRF-Token': state['csrf']})
            with urllib.request.urlopen(req, timeout=10) as response:
                film = json.loads(response.read())
            self.assertEqual(film['frames'], 24, theme)
            self.assertEqual(len(film['ropes']), count, theme)

    def test_the_live_state_carries_what_the_live_wall_section_shows(self):
        _, _, body = self.get('/api/state')
        state = json.loads(body)
        printer = next(iter(state['printers'].values()))
        for key in ('state', 'percent', 'speed', 'speed_percent'):
            self.assertIn(key, printer)
        self.assertEqual(printer['speed'], 'ludicrous')
        for slot in state['config']['slots']:
            self.assertTrue(slot['label'] and slot['printer'] and slot['node'])


if __name__ == '__main__':
    unittest.main()
