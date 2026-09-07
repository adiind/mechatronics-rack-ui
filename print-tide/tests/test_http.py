"""HTTP surface: bind policy, host/origin/CSRF, read-write separation, leakage."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from light_studio.studio import Studio
from light_studio.web import MAX_BODY, make_server
from test_core import MAP, NOW


def rows():
    return [dict(name='printer1', state='FINISH', percent=100, updated=NOW,
                 mqtt_connected=True, job='<script>alert(1)</script>',
                 has_error=False, host='10.0.0.5', serial='SECRETSERIAL',
                 code='SECRETCODE')]


class BindPolicyTests(unittest.TestCase):
    def test_the_ui_refuses_to_bind_outside_loopback_or_the_tailnet(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = Studio(Path(tmp), MAP, rows, None, lambda: True, demo=True)
            for host in ('0.0.0.0', '10.106.4.15', '192.168.1.10', '::'):
                with self.assertRaises(ValueError, msg=host):
                    make_server(studio, host, 0)
            server = make_server(studio, '127.0.0.1', 0)
            server.server_close()

    def test_a_tailscale_address_is_accepted_by_the_bind_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            studio = Studio(Path(tmp), MAP, rows, None, lambda: True, demo=True)
            try:
                server = make_server(studio, '100.65.17.33', 0)
            except OSError:
                return          # address not present on this machine: still allowed
            server.server_close()


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = NOW
        self.studio = Studio(Path(self.tmp.name), MAP, rows, None,
                             lambda: True, demo=True, clock=lambda: self.now)
        self.studio.tick()
        self.http = make_server(self.studio, '127.0.0.1', 0)
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def request(self, path, data=None, headers=None, method=None, raw=None):
        body = raw if raw is not None else (
            json.dumps(data).encode() if data is not None else None)
        req = urllib.request.Request(self.url + path, data=body,
                                     headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def auth(self):
        _, body = self.request('/api/state')
        return {'Origin': self.url, 'Content-Type': 'application/json',
                'X-CSRF-Token': json.loads(body)['csrf']}

    # -- authorization -------------------------------------------------
    def test_writes_need_origin_and_csrf_together(self):
        good = self.auth()
        self.assertEqual(self.request('/api/identify', {'node': 'node02'})[0], 403)
        self.assertEqual(self.request('/api/identify', {'node': 'node02'},
                                      {'Content-Type': 'application/json',
                                       'Origin': good['Origin']})[0], 403)
        bad_origin = dict(good, Origin='https://evil.invalid')
        self.assertEqual(self.request('/api/identify', {'node': 'node02'},
                                      bad_origin)[0], 403)
        bad_token = dict(good, **{'X-CSRF-Token': 'x' * 43})
        self.assertEqual(self.request('/api/identify', {'node': 'node02'},
                                      bad_token)[0], 403)
        self.assertIsNone(self.studio.ident)
        self.assertEqual(self.request('/api/identify', {'node': 'node02'}, good)[0], 200)
        self.assertIsNotNone(self.studio.ident)

    def test_a_wrong_host_header_is_refused_for_reads_and_writes(self):
        self.assertEqual(self.request('/api/state',
                                      headers={'Host': 'evil.invalid'})[0], 403)
        headers = dict(self.auth(), Host='evil.invalid')
        self.assertEqual(self.request('/api/identify', {'node': 'node02'},
                                      headers)[0], 403)

    def test_reads_are_get_and_writes_are_post_with_no_overlap(self):
        self.assertEqual(self.request('/api/state', method='GET')[0], 200)
        self.assertEqual(self.request('/api/film?frames=3&fps=6')[0], 200)
        # A read path is not a write path and vice versa: neither is merely
        # unauthorized on the other verb, it does not exist there at all.
        self.assertEqual(self.request('/api/state', {}, self.auth())[0], 404)
        self.assertEqual(self.request('/api/config')[0], 404)
        self.assertEqual(self.request('/api/film', {}, self.auth())[0], 404)

    def test_json_content_type_and_size_limits_are_enforced(self):
        headers = dict(self.auth(), **{'Content-Type': 'text/plain'})
        self.assertEqual(self.request('/api/identify', {'node': 'node02'},
                                      headers)[0], 415)
        big = b'{"node":"' + b'n' * (MAX_BODY + 64) + b'"}'
        self.assertEqual(self.request('/api/identify', raw=big,
                                      headers=self.auth())[0], 413)
        self.assertEqual(self.request('/api/identify', raw=b'{not json',
                                      headers=self.auth())[0], 400)
        self.assertEqual(self.request('/api/identify', raw=b'[1,2,3]',
                                      headers=self.auth())[0], 400)

    def test_a_rejected_post_does_not_poison_a_keep_alive_connection(self):
        """The body must be consumed even when the request is refused."""
        import http.client
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port,
                                                timeout=10)
        self.addCleanup(connection.close)
        body = json.dumps({'node': 'node02', 'padding': 'x' * 500})
        connection.request('POST', '/api/identify', body,
                           {'Content-Type': 'application/json',
                            'Origin': 'https://evil.invalid'})
        first = connection.getresponse()
        self.assertEqual(first.status, 403)
        first.read()
        # Same socket, next request: it must be understood, not read as garbage.
        connection.request('GET', '/api/state')
        second = connection.getresponse()
        self.assertEqual(second.status, 200)
        self.assertIn('config', json.loads(second.read()))

    # -- routing --------------------------------------------------------
    def test_only_the_three_static_assets_and_the_api_are_reachable(self):
        for path in ('/', '/app.js', '/style.css'):
            self.assertEqual(self.request(path)[0], 200, path)
        for path in ('/reference/server.py', '/../server.staged.py', '/data/layout.json',
                     '/light_studio/core.py', '/api/nope', '/index.html'):
            self.assertEqual(self.request(path)[0], 404, path)

    def test_query_strings_do_not_confuse_the_router(self):
        self.assertEqual(self.request('/api/state?x=1')[0], 200)
        self.assertEqual(self.request('/api/film?frames=abc')[0], 400)

    # -- payload safety -------------------------------------------------
    def test_state_never_carries_hosts_serials_access_codes_or_the_layout_path(self):
        _, body = self.request('/api/state')
        text = body.decode()
        for secret in ('SECRETSERIAL', 'SECRETCODE', '10.0.0.5', self.tmp.name):
            self.assertNotIn(secret, text)

    def test_job_names_survive_verbatim_as_json_data(self):
        payload = json.loads(self.request('/api/state')[1])
        self.assertEqual(payload['printers']['printer1']['job'],
                         '<script>alert(1)</script>')
        # It arrives as data. Safety therefore depends on the browser never
        # turning printer text into markup, which the next test pins down.
        self.assertEqual(self.request('/', headers={})[0], 200)

    def test_the_browser_code_never_builds_markup_from_printer_text(self):
        from light_studio.web import STATIC
        script = (STATIC / 'app.js').read_text()
        for unsafe in ('innerHTML', 'outerHTML', 'insertAdjacentHTML',
                       'document.write', 'eval(', 'new Function('):
            self.assertNotIn(unsafe, script, unsafe)

    def test_error_bodies_are_json_and_token_free(self):
        for status, body in (self.request('/api/identify', {'node': 'node01'},
                                          self.auth()),
                             self.request('/api/nowhere', {}, self.auth()),
                             self.request('/api/state', headers={'Host': 'x'})):
            payload = json.loads(body)
            self.assertIn('error', payload)
            self.assertNotIn('csrf', body.decode().lower())

    def test_security_headers_are_present_on_every_response(self):
        request = urllib.request.Request(self.url + '/')
        with urllib.request.urlopen(request, timeout=10) as response:
            headers = response.headers
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Cache-Control'], 'no-store')

    # -- behaviour ------------------------------------------------------
    def test_node01_is_refused_over_http(self):
        self.assertEqual(self.request('/api/identify', {'node': 'node01'},
                                      self.auth())[0], 400)
        self.assertIsNone(self.studio.ident)

    def test_a_second_save_of_the_same_revision_conflicts(self):
        config = self.studio.config()
        self.assertEqual(self.request('/api/config', config, self.auth())[0], 200)
        self.assertEqual(self.request('/api/config', config, self.auth())[0], 409)

    def test_an_invalid_layout_is_rejected_without_changing_the_saved_one(self):
        before = self.studio.config()
        bad = self.studio.config()
        bad['slots'][0]['node'] = 'node01'
        self.assertEqual(self.request('/api/config', bad, self.auth())[0], 400)
        self.assertEqual(self.studio.config(), before)

    def test_preview_is_read_only(self):
        before = self.studio.config()
        bays = [{'state': 'error', 'percent': 5} for _ in range(7)]
        status, body = self.request('/api/preview',
                                    {'bays': bays, 'time': 1.0, 'frames': 2, 'fps': 4},
                                    self.auth())
        self.assertEqual(status, 200)
        film = json.loads(body)
        self.assertTrue(film['simulated'])
        self.assertEqual(len(film['ropes']), 7)
        self.assertEqual(self.studio.config(), before)
        self.assertIsNone(self.studio.ident)

    def test_the_live_film_is_readable_without_a_token(self):
        _, body = self.request('/api/film?frames=2&fps=4')
        film = json.loads(body)
        self.assertEqual(len(film['ropes']), 7)
        self.assertEqual(len(film['bays']), 7)
        self.assertIn('t0', film)


if __name__ == '__main__':
    unittest.main()
