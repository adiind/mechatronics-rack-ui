"""The telemetry log: redaction, diffing, cursors, labels and the read-only route."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from light_studio.studio import Studio, redact_report
from light_studio.web import make_server
from test_core import MAP, NOW, report

T0 = 1_760_000_000.0


def raw(**fields):
    base = {'gcode_state': 'RUNNING', 'mc_percent': 10, 'layer_num': 3,
            'nozzle_temper': 220.0, 'bed_temper': 55.0, 'print_error': 0,
            'spd_lvl': 2, 'spd_mag': 100, 'subtask_name': 'part'}
    base.update(fields)
    return base


class RedactionTests(unittest.TestCase):
    def test_sensitive_keys_are_dropped_recursively(self):
        payload = {
            'gcode_state': 'RUNNING', 'sn': 'SECRETSERIAL', 'access_code': 'SECRETCODE',
            'ipcam': {'rtsp_url': 'rtsps://SECRET', 'ipcam_record': 'enable'},
            'net': {'conf': 16, 'info': [{'ip': 12345, 'mask': 999}]},
            'ams': {'ams': [{'id': 0, 'tray': [{'tray_uuid': 'SECRETUUID', 'tray_type': 'PLA'}]}]},
            'upload': {'oss_url': 'https://SECRET', 'progress': 3},
            'wifi_signal': '-55dBm', 'mc_percent': 10,
        }
        clean = redact_report(payload)
        text = json.dumps(clean)
        for secret in ('SECRETSERIAL', 'SECRETCODE', 'rtsp', 'SECRETUUID', 'https://SECRET', '12345'):
            self.assertNotIn(secret, text, secret)
        self.assertEqual(clean['gcode_state'], 'RUNNING')
        self.assertEqual(clean['mc_percent'], 10)
        self.assertEqual(clean['wifi_signal'], '-55dBm')
        self.assertEqual(clean['net'], {'conf': 16, 'info': [{'mask': 999}]})
        self.assertEqual(clean['ams']['ams'][0]['tray'][0], {'tray_type': 'PLA'})
        self.assertEqual(clean['upload'], {'progress': 3})
        self.assertNotIn('ipcam', clean)

    def test_the_host_copy_of_the_redactor_matches_this_one(self):
        """reference/server.py carries the same function; keep them in step."""
        source = (Path(__file__).resolve().parent.parent / 'reference' / 'server.py').read_text()
        self.assertIn('def redact_report(value):', source)
        for token in ('"sn"', '"serial"', '"access"', '"code"', '"ip"', '"url"', '"rtsp"', '"ipcam"'):
            self.assertIn(token, source, token)
        self.assertIn('self.reports.append((time.time(), redact_report(p)))', source)


class LogsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reports = {
            'printer1': [
                (T0 + 0, raw()),
                (T0 + 5, raw()),                                        # identical: folded
                (T0 + 10, raw(mc_percent=11, nozzle_temper=220.3)),
                (T0 + 15, raw(mc_percent=11, nozzle_temper=220.3, sn='SECRETSERIAL')),
                (T0 + 20, raw(mc_percent=12, gcode_state='PAUSE', nozzle_temper=220.3)),
            ],
            'printer2': [(T0 + 1, raw(spd_lvl=4, spd_mag=166))],
        }
        self.studio = Studio(Path(self.tmp.name), MAP, lambda: [report()], None,
                             lambda: True, clock=lambda: NOW, demo=True,
                             raw_reports=lambda: self.reports)
        self.studio.tick()

    def printer(self, logs, name):
        return next(p for p in logs['printers'] if p['printer'] == name)

    def test_only_changed_fields_are_listed_and_repeats_are_folded(self):
        logs = self.studio.logs()
        p1 = self.printer(logs, 'printer1')
        entries = p1['entries']
        self.assertEqual([e['at'] for e in entries], [T0, T0 + 10, T0 + 20])
        self.assertTrue(entries[0]['first'])
        self.assertEqual(entries[0]['repeats'], 1)
        self.assertEqual(entries[0]['last_at'], T0 + 5)
        self.assertEqual(set(entries[1]['changed']), {'mc_percent', 'nozzle_temper'})
        self.assertEqual(entries[1]['changed']['mc_percent'], [10, 11])
        self.assertEqual(set(entries[2]['changed']), {'mc_percent', 'gcode_state'})
        self.assertEqual(entries[2]['changed']['gcode_state'], ['RUNNING', 'PAUSE'])
        self.assertEqual(p1['reports_kept'], 5)

    def test_secrets_never_reach_the_log_even_if_the_host_forgot(self):
        text = json.dumps(self.studio.logs(raw=True))
        self.assertNotIn('SECRETSERIAL', text)
        self.assertNotIn('10.0.0.9', text)          # host from the snapshot
        self.assertNotIn('SECRETCODE', text)

    def test_every_bay_is_listed_with_its_label_even_without_reports(self):
        logs = self.studio.logs()
        names = [p['printer'] for p in logs['printers']]
        self.assertEqual(len(names), len(MAP))
        for p in logs['printers']:
            self.assertTrue(p['label'])
            self.assertIsNotNone(p['position'])
        self.assertEqual(self.printer(logs, 'printer3')['entries'], [])
        self.assertEqual(self.printer(logs, 'printer3')['reports_kept'], 0)

    def test_the_current_wall_status_rides_along(self):
        logs = self.studio.logs()
        self.assertEqual(self.printer(logs, 'printer1')['status']['state'], 'printing')
        # A bay with no telemetry is still described: offline, never a secret.
        self.assertEqual(self.printer(logs, 'printer3')['status']['state'], 'offline')
        self.assertNotIn('host', self.printer(logs, 'printer1')['status'])

    def test_since_and_limit_and_printer_filters(self):
        newer = self.studio.logs(since=T0 + 10)
        self.assertEqual([e['at'] for e in self.printer(newer, 'printer1')['entries']], [T0 + 20])
        last = self.studio.logs(limit=1)
        self.assertEqual([e['at'] for e in self.printer(last, 'printer1')['entries']], [T0 + 20])
        only = self.studio.logs(printer='printer2', raw=True)
        self.assertEqual([p['printer'] for p in only['printers']], ['printer2'])
        self.assertEqual(only['printers'][0]['entries'][0]['raw']['spd_lvl'], 4)

    def test_raw_payloads_are_only_sent_when_asked_for(self):
        # A full Bambu report is ~100 keys about once a second per printer:
        # the default answer carries the diff and a key count, not the body.
        default = self.printer(self.studio.logs(), 'printer1')['entries'][0]
        self.assertNotIn('raw', default)
        self.assertEqual(default['keys'], len(raw()))
        full = self.printer(self.studio.logs(raw=True), 'printer1')['entries'][0]
        self.assertEqual(full['raw']['gcode_state'], 'RUNNING')
        with self.assertRaises(ValueError):
            self.studio.logs(since='yesterday')
        with self.assertRaises(ValueError):
            self.studio.logs(limit='lots')

    def test_a_studio_without_a_host_feed_still_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = Studio(Path(tmp), MAP, lambda: [], None, lambda: True, demo=True)
            logs = studio.logs()
            self.assertEqual(len(logs['printers']), len(MAP))
            self.assertTrue(all(p['entries'] == [] for p in logs['printers']))

    def test_malformed_host_items_are_skipped_not_fatal(self):
        self.reports['printer1'].append('garbage')
        self.reports['printer1'].append((T0 + 30, 'not a dict'))
        self.reports['printer1'].append(('when?', raw()))
        logs = self.studio.logs()
        self.assertEqual(len(self.printer(logs, 'printer1')['entries']), 3)


class LogsRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        reports = {'printer1': [(T0, raw(sn='SECRETSERIAL')), (T0 + 9, raw(mc_percent=50))]}
        self.studio = Studio(Path(self.tmp.name), MAP, lambda: [report()], None,
                             lambda: True, demo=True, clock=lambda: NOW,
                             raw_reports=lambda: reports)
        self.studio.tick()
        self.http = make_server(self.studio, '127.0.0.1', 0)
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def get(self, path, data=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.url + path, data=body,
                                     headers=headers or ({'Content-Type': 'application/json'} if body else {}))
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def test_the_log_is_a_read_only_get(self):
        status, body = self.get('/api/logs')
        self.assertEqual(status, 200)
        logs = json.loads(body)
        p1 = next(p for p in logs['printers'] if p['printer'] == 'printer1')
        self.assertEqual(len(p1['entries']), 2)
        self.assertNotIn('raw', p1['entries'][0])
        status, body = self.get('/api/logs?raw=1&printer=printer1&limit=1')
        self.assertEqual(status, 200)
        self.assertIn('"raw"', body.decode())
        self.assertNotIn('SECRETSERIAL', body.decode())
        # Even an authorized writer finds no POST twin: the path does not exist there.
        csrf = json.loads(self.get('/api/state')[1])['csrf']
        auth = {'Origin': self.url, 'Content-Type': 'application/json', 'X-CSRF-Token': csrf}
        self.assertEqual(self.get('/api/logs', {}, auth)[0], 404, 'no POST twin')

    def test_query_parameters_are_validated(self):
        self.assertEqual(self.get('/api/logs?since=abc')[0], 400)
        self.assertEqual(self.get('/api/logs?limit=abc')[0], 400)
        self.assertEqual(self.get('/api/logs?printer=../etc')[0], 400)
        status, body = self.get(f'/api/logs?since={T0 + 1}&limit=5&printer=printer1')
        self.assertEqual(status, 200)
        logs = json.loads(body)
        self.assertEqual([p['printer'] for p in logs['printers']], ['printer1'])
        self.assertEqual([e['at'] for e in logs['printers'][0]['entries']], [T0 + 9])

    def test_the_page_and_its_assets_are_served(self):
        for path, needle in (('/logs', b'logs.js'), ('/logs.js', b'/api/logs'), ('/logs.css', b'.entry')):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(needle, body, path)


if __name__ == '__main__':
    unittest.main()
