"""Hardware-free demo mode.

Binds loopback only, has no publisher at all (``publish=None``), and labels
itself DEMO everywhere in the UI. Nothing here can reach a printer, a broker or
a rope; it exists so the interface and the animations can be reviewed on a
laptop or on this Pi without touching the live wall.

    python3 -m light_studio --demo --port 8872 --data /tmp/print-tide-demo
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import time
from collections import deque
from pathlib import Path

from .media import DEFAULT_CAMERA_DIR

from .studio import Studio
from .web import serve

REFERENCE_MAP = Path(__file__).parent.parent / 'reference' / 'initial-map.json'


def demo_rows(now, started):
    """A synthetic fleet covering every state, with one real print cycle.

    printer1 runs a full print -> FINISH -> idle -> prepare loop so a reviewer
    can watch a genuine observed completion (celebration plus wall ripple)
    without waiting for the real wall to finish something.
    """
    cycle = 140.0
    t = (now - started) % cycle
    if t < 95:
        first = ('RUNNING', round(t / 95 * 100, 1), 95 - t)
    elif t < 118:
        first = ('FINISH', 100, 0)
    elif t < 128:
        first = ('IDLE', None, None)
    else:
        first = ('PREPARE', 0, None)

    fixed = [
        ('RUNNING', 67.0, 41.0, False, True, 'DEMO job 2'),
        # A FINISH that was never watched: "retained", finish time unknown,
        # with a long job name so the card's truncation can be reviewed.
        ('FINISH', 100.0, None, False, True,
         'DEMO_Long_Job_Name_Enclosure_Bracket_Rev_C_with_Supports_v12.3mf'),
        # Stopped early: FAILED with the error dismissed, bed not cleared.
        ('FAILED', 31.0, None, False, True, 'DEMO cancelled spool holder'),
        ('PAUSE', 22.0, 88.0, False, True, 'DEMO job 5'),
        ('RUNNING', 12.0, 133.0, True, True, 'DEMO job 6'),   # has_error -> error wins
        (None, None, None, False, False, None),                # link down -> offline
    ]
    rows = [{
        'name': 'printer1',
        'state': first[0],
        'percent': first[1],
        'remaining_min': first[2],
        'job': 'DEMO bracket v4.3mf',
        'layer': None if first[1] is None else int(first[1] * 3),
        'total_layer': 300,
        'nozzle': 218.0, 'bed': 60.0,
        'has_error': False, 'mqtt_connected': True, 'updated': now,
        'host': 'demo-not-a-real-host', 'serial': 'demo-not-a-real-serial',
    }]
    for index, (state, percent, remaining, error, linked, job) in enumerate(fixed, start=2):
        rows.append({
            'name': f'printer{index}',
            'state': state,
            'percent': percent,
            'remaining_min': remaining,
            'job': job if state else None,
            'layer': None if percent is None else int(percent * 2),
            'total_layer': 200,
            'nozzle': 34.0 if state in (None, 'IDLE', 'FINISH', 'FAILED') else 220.0,
            'bed': 24.0 if state in (None, 'IDLE', 'FINISH', 'FAILED') else 58.0,
            'has_error': error,
            'mqtt_connected': linked,
            'updated': now if linked else now - 900,
            'host': 'demo-not-a-real-host', 'serial': 'demo-not-a-real-serial',
        })
    return rows


class DemoReports:
    """Synthetic raw Bambu-style reports so the /logs page can be reviewed.

    One report per printer per second, derived from the same demo rows the
    wall uses, with a little sensor jitter so the page's "ticking" fold has
    something to fold. No serials, hosts or addresses are ever generated.
    """

    def __init__(self):
        self.series = {}
        self.last = 0.0
        self.rng = random.Random(7)

    def feed(self, rows, now):
        if now - self.last < 1.0:
            return
        self.last = now
        for row in rows:
            name = row['name']
            bucket = self.series.setdefault(name, deque(maxlen=400))
            if not row.get('mqtt_connected'):
                continue
            state = row.get('state') or 'IDLE'
            percent = row.get('percent')
            report = {
                'gcode_state': 'FAILED' if row.get('has_error') else state,
                'print_error': 50348044 if row.get('has_error') else 0,
                'mc_percent': int(percent) if percent is not None else 0,
                'mc_remaining_time': int(row['remaining_min']) if row.get('remaining_min') else 0,
                'layer_num': row.get('layer') or 0,
                'total_layer_num': row.get('total_layer') or 0,
                'subtask_name': row.get('job') or '',
                'spd_lvl': 2, 'spd_mag': 100,
                'nozzle_temper': round(row.get('nozzle', 0) + self.rng.uniform(-0.4, 0.4), 1),
                'bed_temper': round(row.get('bed', 0) + self.rng.uniform(-0.2, 0.2), 1),
                'cooling_fan_speed': str(self.rng.choice([14, 15])),
                'wifi_signal': '-%ddBm' % self.rng.choice([52, 53, 54]),
                'home_flag': 0,
                'hms': [],
            }
            bucket.append((now, report))

    def snapshot(self):
        return {name: list(series) for name, series in self.series.items()}


def demo_cameras(root, now):
    """Build a hardware-free camera cache that exercises every image outcome.

    Copies of the Pi's real read-only cache are used where readable, so the
    review sees genuine chamber photographs; otherwise Pillow paints a plainly
    labelled placeholder. The live cache is never written to. Cases:

        printer1  recent capture (copy, timestamp 2 min ago)
        printer2  saved capture from 9 hours ago
        printer3  metadata says unavailable and no file exists
        printer4  a file with a valid JPEG header and a garbage body
        printer5  a file with no metadata entry (falls back to file time)
        printer6  an empty file
        printer7  recent capture (copy)
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for old in root.glob('*'):
        if old.is_file():
            old.unlink()

    def source_jpeg(alias, label):
        real = DEFAULT_CAMERA_DIR / f'{alias}.jpg'
        try:
            data = real.read_bytes()
            if data[:3] == b'\xff\xd8\xff' and len(data) < 8 * 1024 * 1024:
                return data
        except OSError:
            pass
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            return None
        import io
        image = Image.new('RGB', (1280, 720), (54, 40, 70))
        draw = ImageDraw.Draw(image)
        draw.rectangle((40, 40, 1240, 680), outline=(228, 224, 238), width=6)
        draw.text((80, 320), f'DEMO placeholder · {label}', fill=(228, 224, 238))
        buf = io.BytesIO()
        image.save(buf, 'JPEG', quality=80)
        return buf.getvalue()

    meta = {}
    recent = source_jpeg('printer1', 'printer1')
    if recent:
        (root / 'printer1.jpg').write_bytes(recent)
        meta['printer1'] = {'status': 'captured', 'source': 'rtsp', 'captured_at': now - 120,
                            'attempted_at': now - 120, 'width': 1920, 'height': 1080,
                            'bytes': len(recent), 'state': 'RUNNING', 'job': 'DEMO bracket v4.3mf'}
    saved = source_jpeg('printer2', 'printer2')
    if saved:
        (root / 'printer2.jpg').write_bytes(saved)
        meta['printer2'] = {'status': 'unavailable', 'source': 'recording',
                            'captured_at': now - 9 * 3600, 'attempted_at': now - 60,
                            'width': 1920, 'height': 1080, 'bytes': len(saved),
                            'state': 'FINISH', 'job': 'DEMO job 2 (earlier)'}
    meta['printer3'] = {'status': 'unavailable', 'attempted_at': now - 30, 'state': 'FINISH',
                        'job': 'DEMO_Long_Job_Name_Enclosure_Bracket_Rev_C_with_Supports_v12.3mf'}
    # Valid magic bytes, unusable body: the server's decode check (when Pillow
    # is present) refuses it; a browser would otherwise fire onerror.
    corrupt = b'\xff\xd8\xff\xe0' + bytes(range(256)) * 64
    (root / 'printer4.jpg').write_bytes(corrupt)
    meta['printer4'] = {'status': 'captured', 'source': 'rtsp', 'captured_at': now - 300,
                        'attempted_at': now - 300, 'bytes': len(corrupt), 'state': 'FAILED',
                        'job': 'DEMO cancelled spool holder'}
    orphan = source_jpeg('printer5', 'printer5')
    if orphan:
        (root / 'printer5.jpg').write_bytes(orphan)
    (root / 'printer6.jpg').write_bytes(b'')
    meta['printer6'] = {'status': 'captured', 'source': 'rtsp', 'captured_at': now - 45,
                        'attempted_at': now - 45, 'bytes': 0, 'state': 'RUNNING', 'job': 'DEMO job 6'}
    fresh7 = source_jpeg('printer7', 'printer7')
    if fresh7:
        (root / 'printer7.jpg').write_bytes(fresh7)
        meta['printer7'] = {'status': 'captured', 'source': 'rtsp', 'captured_at': now - 20,
                            'attempted_at': now - 20, 'width': 1168, 'height': 720,
                            'bytes': len(fresh7), 'state': 'IDLE', 'job': 'Screw Gauge'}
    (root / 'metadata.json').write_text(json.dumps(meta, indent=1))
    return root


def main():
    parser = argparse.ArgumentParser(description='Print Tide hardware-free demo')
    parser.add_argument('--demo', action='store_true', required=True,
                        help='required; this entry point has no non-demo mode')
    parser.add_argument('--port', type=int, default=8872)
    parser.add_argument('--host', default='127.0.0.1',
                        help='loopback (default) or this Pi\'s Tailscale address; '
                             'anything else is refused by the UI server')
    parser.add_argument('--data', default='demo-data')
    args = parser.parse_args()

    initial = json.loads(REFERENCE_MAP.read_text())
    started = time.time()
    reports = DemoReports()

    def snapshots():
        now = time.time()
        rows = demo_rows(now, started)
        reports.feed(rows, now)
        return rows

    media_dir = demo_cameras(Path(args.data) / 'cameras', time.time())
    studio = Studio(Path(args.data), initial, snapshots,
                    None,                      # no publisher: cannot reach hardware
                    lambda: True, demo=True, raw_reports=reports.snapshot,
                    media_dir=media_dir)
    serve(studio, args.host, args.port)
    print(f'DEMO ONLY (no hardware output): http://{args.host}:{args.port}', flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        studio.stop()


if __name__ == '__main__':
    main()
