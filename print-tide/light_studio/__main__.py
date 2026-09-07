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
import time
from pathlib import Path

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
        ('RUNNING', 67.0, 41.0, False, True),
        ('IDLE', None, None, False, True),
        ('PREPARE', 0.0, None, False, True),
        ('PAUSE', 22.0, 88.0, False, True),
        ('RUNNING', 12.0, 133.0, True, True),      # has_error -> error wins
        (None, None, None, False, False),          # link down -> offline
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
    for index, (state, percent, remaining, error, linked) in enumerate(fixed, start=2):
        rows.append({
            'name': f'printer{index}',
            'state': state,
            'percent': percent,
            'remaining_min': remaining,
            'job': f'DEMO job {index}' if state else None,
            'layer': None if percent is None else int(percent * 2),
            'total_layer': 200,
            'nozzle': 34.0 if state in (None, 'IDLE') else 220.0,
            'bed': 24.0 if state in (None, 'IDLE') else 58.0,
            'has_error': error,
            'mqtt_connected': linked,
            'updated': now if linked else now - 900,
            'host': 'demo-not-a-real-host', 'serial': 'demo-not-a-real-serial',
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description='Print Tide hardware-free demo')
    parser.add_argument('--demo', action='store_true', required=True,
                        help='required; this entry point has no non-demo mode')
    parser.add_argument('--port', type=int, default=8872)
    parser.add_argument('--data', default='demo-data')
    args = parser.parse_args()

    initial = json.loads(REFERENCE_MAP.read_text())
    started = time.time()
    studio = Studio(Path(args.data), initial,
                    lambda: demo_rows(time.time(), started),
                    None,                      # no publisher: cannot reach hardware
                    lambda: True, demo=True)
    serve(studio, '127.0.0.1', args.port)
    print(f'DEMO ONLY (no hardware output): http://127.0.0.1:{args.port}', flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        studio.stop()


if __name__ == '__main__':
    main()
