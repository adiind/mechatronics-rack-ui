"""The UI server: standard library only, tailnet or loopback only.

Threat model is small but real: this box also sits on a shared campus network.
So the server refuses to bind anywhere except loopback or the Tailscale CGNAT
range, validates the ``Host`` header, and requires ``Origin`` plus a per-process
CSRF token on every state-changing request. Reads are GET, writes are POST, and
the two sets never overlap.

Nothing here can reach a printer or the broker; it only calls the studio.
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .layout import Conflict

STATIC = Path(__file__).parent / 'static'
FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/style.css': ('style.css', 'text/css; charset=utf-8'),
}

MAX_BODY = 32768
TAILNET = ipaddress.ip_network('100.64.0.0/10')

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
       "img-src 'self' data:; connect-src 'self'; form-action 'none'; "
       "frame-ancestors 'none'; base-uri 'none'; object-src 'none'")


def make_server(studio, host, port):
    """Build (but do not start) the UI server. Raises before binding if unsafe."""
    address = ipaddress.ip_address(host)
    if not (address.is_loopback or address in TAILNET):
        raise ValueError('The studio UI may only bind loopback or a Tailscale address')
    csrf = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'PrintTide'
        sys_version = ''
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            """Silence: request lines could otherwise echo user input to the journal."""

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        # -- helpers ------------------------------------------------------
        def origin(self):
            return f'http://{host}:{self.server.server_port}'

        def host_ok(self):
            return self.headers.get('Host') == f'{host}:{self.server.server_port}'

        def write_allowed(self):
            token = self.headers.get('X-CSRF-Token', '')
            return (self.host_ok()
                    and self.headers.get('Origin') == self.origin()
                    and hmac.compare_digest(token, csrf))

        def reply(self, code, value, mime='application/json'):
            if mime == 'application/json':
                body = json.dumps(value, allow_nan=False).encode()
            else:
                body = value
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', CSP)
            self.end_headers()
            self.wfile.write(body)

        def read_raw(self):
            """Consume the request body up front.

            This runs before authorization on purpose. On a keep-alive
            connection an unread body would be parsed as the next request, so
            every POST must either read its body or close the connection.
            """
            length = self.headers.get('Content-Length', '')
            if not length.isdigit():
                self.close_connection = True
                return None, (411, 'Content-Length required')
            size = int(length)
            if size <= 0 or size > MAX_BODY:
                self.close_connection = True
                return None, (413, 'Request too large or empty')
            return self.rfile.read(size), None

        @staticmethod
        def parse_body(raw):
            try:
                body = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Never echo parser detail about a body we did not sanitize.
                raise ValueError('Malformed JSON body') from None
            if not isinstance(body, dict):
                raise ValueError('A JSON object is required')
            return body

        # -- routing ------------------------------------------------------
        def do_GET(self):
            if not self.host_ok():
                return self.reply(403, {'error': 'Unrecognized host'})
            parts = urlsplit(self.path)
            path, query = parts.path, parse_qs(parts.query)
            if path == '/api/state':
                return self.reply(200, dict(studio.view(), csrf=csrf))
            if path == '/api/film':
                try:
                    frames = int(query.get('frames', ['10'])[0])
                    fps = int(query.get('fps', ['10'])[0])
                except (ValueError, TypeError):
                    return self.reply(400, {'error': 'Invalid film request'})
                try:
                    return self.reply(200, studio.live_film(frames=frames, fps=fps))
                except (ValueError, TypeError):
                    return self.reply(400, {'error': 'Invalid film request'})
            if path in FILES:
                name, mime = FILES[path]
                try:
                    return self.reply(200, (STATIC / name).read_bytes(), mime)
                except OSError:
                    return self.reply(404, {'error': 'Not found'})
            self.reply(404, {'error': 'Not found'})

        def do_POST(self):
            raw, problem = self.read_raw()
            if problem:
                return self.reply(problem[0], {'error': problem[1]})
            if not self.write_allowed():
                return self.reply(403, {'error': 'Reload this page to authorize the action'})
            if self.headers.get('Content-Type') != 'application/json':
                return self.reply(415, {'error': 'JSON content type required'})
            path = urlsplit(self.path).path
            try:
                body = self.parse_body(raw)
                if path == '/api/config':
                    result = studio.save(body)
                elif path == '/api/undo':
                    result = studio.undo(body.get('revision'))
                elif path == '/api/reset':
                    result = studio.reset(body.get('revision'))
                elif path == '/api/identify':
                    result = studio.identify(body.get('node'))
                elif path == '/api/collected':
                    result = studio.collected(body.get('printer'))
                elif path == '/api/preview':
                    # Read-only simulation. Deliberately cannot publish anything.
                    result = studio.sim_film(
                        body.get('bays'),
                        frames=body.get('frames', 10),
                        fps=body.get('fps', 10),
                        start=body.get('time', 0.0),
                        settings=body.get('settings'),
                        ripples=body.get('ripples', True) is not False,
                        accents=body.get('accents'),
                        reverses=body.get('reverses'),
                    )
                else:
                    return self.reply(404, {'error': 'Not found'})
                self.reply(200, result)
            except Conflict as error:
                self.reply(409, {'error': str(error)})
            except (ValueError, TypeError, KeyError) as error:
                # Layout/validation messages are written to be safe to show.
                message = str(error) if isinstance(error, (Conflict, ValueError)) else ''
                self.reply(400, {'error': message or 'Invalid request'})
            except Exception:
                self.reply(500, {'error': 'Could not process this change; '
                                          'the saved layout is unchanged'})

    http = ThreadingHTTPServer((host, port), Handler)
    http.daemon_threads = True
    return http


def serve(studio, host, port):
    """Bind first, then start the writer, then serve.

    Ordering matters for the host integration: if the bind fails we raise before
    the studio has published anything, so the caller can fall back to the
    original animator with no two writers ever having existed.
    """
    http = make_server(studio, host, port)
    studio.start()
    threading.Thread(target=http.serve_forever, daemon=True, name='print-tide-web').start()
    return http
