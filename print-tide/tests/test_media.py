"""The read-only camera image bridge: identity, boundaries, truth and errors.

Everything here runs against a temporary directory shaped like the dashboard's
cache. Nothing touches the real cache, a camera, a printer or the network.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from light_studio.media import CameraCache, DECODER, FRESH_SECONDS, MAX_BYTES, decodes
from light_studio.studio import Studio
from light_studio.web import make_server
from test_core import MAP, NOW, report

T0 = 1_760_000_000.0


def real_jpeg():
    """A tiny but genuinely decodable JPEG (built with Pillow when present)."""
    if DECODER == 'pillow':
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (8, 8), (78, 42, 132)).save(buf, 'JPEG')
        return buf.getvalue()
    return b'\xff\xd8\xff\xe0' + b'\x00' * 200 + b'\xff\xd9'


JPEG = real_jpeg()
#: Valid magic bytes, garbage body: the case the PM asked to be covered.
HEADER_ONLY = b'\xff\xd8\xff\xe0' + bytes(range(256)) * 8


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = CameraCache(self.root, aliases=MAP, clock=lambda: T0)

    def write(self, alias, data=JPEG):
        (self.root / f'{alias}.jpg').write_bytes(data)

    def meta(self, **rows):
        (self.root / 'metadata.json').write_text(json.dumps(rows))

    def test_a_recent_capture_is_recent_and_carries_only_public_metadata(self):
        self.write('printer1')
        self.meta(printer1={'status': 'captured', 'source': 'rtsp', 'captured_at': T0 - 90,
                            'attempted_at': T0 - 90, 'width': 1920, 'height': 1080, 'bytes': 206,
                            'state': 'RUNNING', 'job': 'bracket',
                            'rtsp_url': 'rtsps://SECRET', 'access_code': 'SECRET', 'ip': '10.0.0.9'})
        d = self.cache.describe('printer1')
        self.assertTrue(d['available'])
        self.assertEqual(d['status'], 'recent')
        self.assertTrue(d['recent'])
        self.assertEqual(d['age_seconds'], 90.0)
        self.assertTrue(d['time_known'])
        self.assertFalse(d['clock_skew'])
        self.assertEqual((d['source'], d['state_at_capture'], d['job_at_capture']),
                         ('rtsp', 'RUNNING', 'bracket'))
        text = json.dumps(d)
        for secret in ('SECRET', '10.0.0.9', 'rtsp_url', 'access'):
            self.assertNotIn(secret, text, secret)

    def test_an_old_capture_is_saved_never_live(self):
        self.write('printer1')
        self.meta(printer1={'captured_at': T0 - FRESH_SECONDS - 1})
        d = self.cache.describe('printer1')
        self.assertTrue(d['available'])
        self.assertEqual(d['status'], 'saved')
        self.assertFalse(d['recent'])
        self.assertIn('not a live view', d['note'])
        # Exactly at the limit still counts as recent.
        self.meta(printer1={'captured_at': T0 - FRESH_SECONDS})
        self.assertTrue(self.cache.describe('printer1')['recent'])

    def test_a_future_capture_time_is_a_clock_problem_not_zero_seconds_ago(self):
        self.write('printer1')
        self.meta(printer1={'captured_at': T0 + 600})
        d = self.cache.describe('printer1')
        self.assertTrue(d['available'])
        self.assertFalse(d['recent'])
        self.assertTrue(d['clock_skew'])
        self.assertIsNone(d['age_seconds'])
        self.assertEqual(d['status'], 'saved')
        self.assertIn('ahead of this clock', d['note'])

    def test_a_freshly_copied_file_without_capture_metadata_is_never_recent(self):
        # PM review: mtime says when a file was copied, not when the camera saw it.
        self.write('printer2')
        os.utime(self.root / 'printer2.jpg', (T0 - 5, T0 - 5))          # copied just now
        d = self.cache.describe('printer2')
        self.assertTrue(d['available'])
        self.assertEqual(d['status'], 'saved')
        self.assertFalse(d['recent'])
        self.assertFalse(d['time_known'])
        self.assertIsNone(d['captured_at'])
        self.assertIsNone(d['age_seconds'])
        self.assertEqual(d['file_updated_at'], T0 - 5)
        self.assertIn('not recorded', d['note'])
        # Metadata present but without a capture time reads the same way.
        self.meta(printer2={'status': 'captured', 'source': 'rtsp'})
        self.assertFalse(self.cache.describe('printer2')['recent'])

    def test_a_valid_header_with_a_garbage_body_is_corrupt_when_a_decoder_exists(self):
        self.write('printer3', HEADER_ONLY)
        d = self.cache.describe('printer3')
        if DECODER == 'pillow':
            self.assertEqual(d['status'], 'corrupt')
            self.assertFalse(decodes(HEADER_ONLY))
            with self.assertRaises(ValueError):
                self.cache.image('printer3')
        else:
            # Without a decoder the server can only check magic bytes; the
            # browser's onerror path is the guard (see test_pages_static).
            self.assertEqual(d['status'], 'saved')
        self.assertTrue(decodes(JPEG))

    def test_missing_empty_corrupt_and_oversized_files_are_distinct_and_unserved(self):
        self.meta(printer3={'status': 'unavailable'})
        self.assertEqual(self.cache.describe('printer3')['status'], 'missing')
        self.write('printer4', b'')
        self.assertEqual(self.cache.describe('printer4')['status'], 'empty')
        self.write('printer5', b'GIF89a not a jpeg')
        self.assertEqual(self.cache.describe('printer5')['status'], 'corrupt')
        self.write('printer6', b'\xff\xd8\xff' + b'\x00' * (MAX_BYTES + 1))
        self.assertEqual(self.cache.describe('printer6')['status'], 'too_large')
        for alias in ('printer4', 'printer5', 'printer6'):
            self.assertFalse(self.cache.describe(alias)['available'], alias)
            with self.assertRaises(ValueError, msg=alias):
                self.cache.image(alias)
        with self.assertRaises(LookupError):
            self.cache.image('printer3')

    def test_only_allowlisted_aliases_and_only_their_own_file(self):
        self.write('printer1')
        for bad in ('printer9', 'printer01', '../metadata', 'metadata', 'printer1/../printer1',
                    'node02', '', None, 'PRINTER1'):
            self.assertEqual(self.cache.describe(bad)['status'], 'unknown_printer', bad)
            with self.assertRaises(LookupError, msg=bad):
                self.cache.image(bad)
        data, mtime = self.cache.image('printer1')
        self.assertEqual(data, JPEG)
        self.assertGreater(mtime, 0)

    def test_a_symlink_that_escapes_the_directory_is_refused(self):
        outside = Path(self.tmp.name).parent / f'outside-{os.getpid()}.jpg'
        outside.write_bytes(JPEG)
        self.addCleanup(lambda: outside.exists() and outside.unlink())
        os.symlink(outside, self.root / 'printer1.jpg')
        self.assertEqual(self.cache.describe('printer1')['status'], 'missing')
        with self.assertRaises(LookupError):
            self.cache.image('printer1')

    def test_a_corrupt_metadata_file_does_not_break_the_images(self):
        self.write('printer1')
        (self.root / 'metadata.json').write_text('{broken')
        d = self.cache.describe('printer1')
        self.assertTrue(d['available'])
        self.assertEqual(d['status'], 'saved')
        self.assertFalse(d['time_known'])

    def test_a_missing_directory_is_just_missing_images(self):
        cache = CameraCache(self.root / 'nowhere', aliases=MAP, clock=lambda: T0)
        self.assertEqual(cache.describe('printer1')['status'], 'missing')
        self.assertEqual(set(cache.describe_all()), set(MAP))


class StudioMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.media = Path(self.tmp.name) / 'cameras'
        self.media.mkdir()
        (self.media / 'printer2.jpg').write_bytes(JPEG)
        (self.media / 'metadata.json').write_text(json.dumps(
            {'printer2': {'captured_at': NOW - 30, 'source': 'rtsp', 'state': 'RUNNING', 'job': 'x'}}))
        self.studio = Studio(Path(self.tmp.name) / 'data', MAP, lambda: [report()], None,
                             lambda: True, demo=True, clock=lambda: NOW, media_dir=self.media)
        self.studio.tick()

    def test_images_follow_the_printer_not_the_bay(self):
        before = self.studio.media()['printers']
        self.assertTrue(before['printer2']['available'])
        bay_before = before['printer2']['position']
        config = self.studio.config()
        # Move printer2 to the other end of the wall.
        slot = next(s for s in config['slots'] if s['printer'] == 'printer2')
        config['slots'].remove(slot)
        config['slots'].append(slot)
        self.studio.save(config)
        after = self.studio.media()['printers']
        self.assertTrue(after['printer2']['available'])
        self.assertEqual(after['printer2']['url'], '/media/camera/printer2.jpg')
        self.assertNotEqual(after['printer2']['position'], bay_before)
        # Every other printer is described, honestly, as missing.
        self.assertEqual({p for p, r in after.items() if r['status'] == 'missing'}, set(MAP) - {'printer2'})
        self.assertTrue(all(r['url'] is None for p, r in after.items() if p != 'printer2'))

    def test_no_model_preview_is_ever_invented(self):
        for row in self.studio.media()['printers'].values():
            self.assertIsNone(row['model_preview'])

    def test_the_default_cache_directory_is_the_dashboards_and_read_only(self):
        from light_studio.media import DEFAULT_CAMERA_DIR
        studio = Studio(Path(self.tmp.name) / 'd2', MAP, lambda: [report()], None,
                        lambda: True, demo=True, clock=lambda: NOW)
        self.assertEqual(studio.cameras.root, DEFAULT_CAMERA_DIR)
        self.assertIn('printer-discord-dashboard/data/cameras', str(DEFAULT_CAMERA_DIR))
        # Describing never creates anything.
        studio.media()
        self.assertFalse((Path(self.tmp.name) / 'd2' / 'cameras').exists())


class MediaRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        media = Path(self.tmp.name) / 'cameras'
        media.mkdir()
        (media / 'printer1.jpg').write_bytes(JPEG)
        (media / 'printer4.jpg').write_bytes(HEADER_ONLY if DECODER == 'pillow' else b'not a jpeg')
        (media / 'metadata.json').write_text(json.dumps(
            {'printer1': {'captured_at': NOW - 30, 'source': 'rtsp', 'rtsp_url': 'rtsps://SECRET'}}))
        self.studio = Studio(Path(self.tmp.name) / 'data', MAP, lambda: [report()], None,
                             lambda: True, demo=True, clock=lambda: NOW, media_dir=media)
        self.studio.tick()
        self.http = make_server(self.studio, '127.0.0.1', 0)
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def get(self, path, method='GET', headers=None, data=None):
        req = urllib.request.Request(self.url + path, method=method, headers=headers or {}, data=data)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def test_the_image_route_serves_jpeg_bytes_with_an_image_type(self):
        status, headers, body = self.get('/media/camera/printer1.jpg')
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/jpeg')
        self.assertEqual(body, JPEG)
        self.assertIn('ETag', headers)
        self.assertIn('Last-Modified', headers)
        self.assertIn("img-src 'self'", headers['Content-Security-Policy'])

    def test_missing_corrupt_and_foreign_paths_are_refused_without_detail(self):
        self.assertEqual(self.get('/media/camera/printer2.jpg')[0], 404)
        self.assertEqual(self.get('/media/camera/printer4.jpg')[0], 415)
        for bad in ('/media/camera/printer9.jpg', '/media/camera/metadata.json',
                    '/media/camera/../metadata.json', '/media/camera/printer1.png',
                    '/media/camera/printer1.jpg/', '/media/printer1.jpg', '/media/camera/'):
            status, headers, body = self.get(bad)
            self.assertIn(status, (404, 400), bad)
            self.assertNotIn(b'cameras', body, bad)

    def test_the_media_description_is_public_and_keyed_by_printer(self):
        status, headers, body = self.get('/api/media')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(set(data['printers']), set(MAP))
        self.assertEqual(data['printers']['printer1']['status'], 'recent')
        self.assertEqual(data['printers']['printer1']['url'], '/media/camera/printer1.jpg')
        self.assertEqual(data['printers']['printer4']['status'], 'corrupt')
        self.assertNotIn('SECRET', body.decode())
        self.assertNotIn('rtsp_url', body.decode())

    def test_there_is_no_write_twin_and_the_host_check_still_applies(self):
        status, _, _ = self.get('/media/camera/printer1.jpg', method='POST',
                                headers={'Content-Type': 'application/json'}, data=b'{}')
        self.assertIn(status, (403, 404, 411))
        status, _, _ = self.get('/media/camera/printer1.jpg', headers={'Host': 'evil.example:1'})
        self.assertEqual(status, 403)

    def test_fonts_are_served_with_their_licences(self):
        for path in ('/fonts/Poppins-Regular.ttf', '/fonts/IBMPlexSans-Variable.ttf'):
            status, headers, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertEqual(headers['Content-Type'], 'font/ttf')
            self.assertEqual(body[:4], b'\x00\x01\x00\x00', path)
        for path in ('/fonts/OFL-Poppins.txt', '/fonts/OFL-IBMPlexSans.txt'):
            status, headers, body = self.get(path)
            self.assertEqual(status, 200, path)
            self.assertIn(b'SIL Open Font License', body, path)


if __name__ == '__main__':
    unittest.main()
