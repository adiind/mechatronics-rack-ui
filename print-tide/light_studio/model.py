"""State interpretation.

Turns one raw host printer snapshot into the sanitized display record the rest
of the studio is allowed to see. Nothing here touches hardware, files or the
network, and nothing here forwards hosts, serials or access codes.

Freshness rules (Adi's wall, LAN-mode Bambu telemetry):

* ``updated`` is stamped by the host when a *message arrives*. It proves
  traffic, not that any particular field changed. We therefore never treat a
  cached percentage as current once the link or the clock says otherwise.
* The host writes ``time.strftime("%Y-%m-%d %H:%M:%S")``: a naive local-time
  string in the Pi's timezone. Naive stamps are interpreted as Pi local time;
  offset-aware and epoch stamps are also accepted.
* Missing, malformed, and future timestamps are all *not fresh*. Unknown is
  never quietly rendered as available.
"""
from __future__ import annotations

import datetime as dt
import math
import re

STATES = ('idle', 'preparing', 'printing', 'paused', 'error', 'finished', 'stopped',
          'offline', 'unknown')

#: A snapshot older than this many seconds is stale even if the link is up.
STALE_AFTER = 120.0
#: Tolerance for a source clock that runs slightly ahead of the Pi.
FUTURE_TOLERANCE = 5.0

#: Why a snapshot is not fresh. Shown to the installer as plain words.
REASON_TEXT = {
    'ok': 'live',
    'link_down': 'printer link down',
    'no_telemetry': 'no timestamped message yet',
    'stale': 'no message for over two minutes',
    'clock_skew': 'source timestamp is in the future',
}

# Bambu gcode_state -> our display state. Anything unlisted stays 'unknown';
# we deliberately do not guess a heating stage from temperatures.
RAW_STATES = {
    'RUNNING': 'printing',
    'PREPARE': 'preparing',
    'SLICING': 'preparing',
    'PAUSE': 'paused',
    'PAUSED': 'paused',
    'FINISH': 'finished',
    'FINISHED': 'finished',
    # FAILED lingers on the printer after a cancelled or failed print until the
    # next job starts. While print_error is set it is an error (red); once the
    # operator dismisses it the bay is 'stopped' (magenta) until the bed is
    # cleared, i.e. the door opens or someone marks it collected.
    'FAILED': 'stopped',
    'IDLE': 'idle',
    'READY': 'idle',
    'OFFLINE': 'offline',
}

_CONTROL = re.compile(r'[\x00-\x1f\x7f]')

# Bambu speed profile (``spd_lvl``) -> display name. Anything else is unknown.
SPEED_LEVELS = {1: 'silent', 2: 'standard', 3: 'sport', 4: 'ludicrous'}


def number(value):
    """Return ``value`` as a finite float, or ``None``.

    ``bool`` is rejected on purpose: ``True`` is not a percentage.
    """
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def _integer(value, low, high):
    if type(value) is not int or type(value) is bool:
        n = number(value)
        if n is None or n != int(n):
            return None
        value = int(n)
    return value if low <= value <= high else None


def epoch(stamp):
    """Best-effort epoch seconds for a host timestamp, else ``None``."""
    if type(stamp) in (int, float):
        return float(stamp) if math.isfinite(stamp) else None
    if not isinstance(stamp, str):
        return None
    text = stamp.strip()
    if not text:
        return None
    if text.endswith(('Z', 'z')):
        text = text[:-1] + '+00:00'
    try:
        parsed = dt.datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    try:
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()      # naive == Pi local time
        return parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _text(value, limit):
    if not isinstance(value, str):
        return None
    cleaned = _CONTROL.sub(' ', value).strip()
    return cleaned[:limit] or None


def freshness(row, now):
    """``(reason, age_seconds_or_None)`` for one snapshot."""
    connected = row.get('mqtt_connected') is True
    stamped = epoch(row.get('updated'))
    age = None if stamped is None else now - stamped
    if age is not None and not math.isfinite(age):
        age = None
    if not connected:
        return 'link_down', age
    if age is None:
        return 'no_telemetry', None
    if age < -FUTURE_TOLERANCE:
        return 'clock_skew', age
    if age > STALE_AFTER:
        return 'stale', age
    return 'ok', age


def normalize(row, now):
    """Sanitized display record for one printer.

    Only the fields listed here ever reach the browser. Hosts, serials and
    access codes present in the host snapshot are dropped, not redacted.
    """
    if not isinstance(row, dict):
        row = {}
    reason, age = freshness(row, now)
    fresh = reason == 'ok'

    raw = row.get('state')
    raw = raw.strip().upper() if isinstance(raw, str) and raw.strip() else None

    if not fresh:
        state = 'offline'
    elif row.get('has_error') is True:
        state = 'error'
    else:
        state = RAW_STATES.get(raw, 'unknown')

    percent = number(row.get('percent')) if fresh else None
    if percent is not None:
        percent = max(0.0, min(100.0, percent))
    remaining = number(row.get('remaining_min')) if fresh else None
    if remaining is not None and not 0 <= remaining <= 60 * 24 * 30:
        remaining = None

    return {
        'state': state,
        'raw_state': raw,
        'reason': reason,
        'reason_text': REASON_TEXT[reason],
        'connected': row.get('mqtt_connected') is True,
        'fresh': fresh,
        'percent': percent,
        'job': _text(row.get('job'), 180),
        'remaining_min': remaining,
        'nozzle': number(row.get('nozzle')) if fresh else None,
        'bed': number(row.get('bed')) if fresh else None,
        'layer': _integer(row.get('layer'), 0, 100000) if fresh else None,
        'total_layer': _integer(row.get('total_layer'), 0, 100000) if fresh else None,
        # Negative ages (source slightly ahead) are reported as 0s, not as a
        # negative "message from the future".
        'age': round(max(0.0, age), 1) if age is not None else None,
        # Door sensor (X1/H2D report it in home_flag); None when unknown.
        'door_open': row.get('door_open') if type(row.get('door_open')) is bool else None,
        # Speed profile: 'silent' | 'standard' | 'sport' | 'ludicrous' | None,
        # plus the feed-rate percentage the printer reports for it.
        'speed': SPEED_LEVELS.get(_integer(row.get('speed_level'), 1, 4)) if fresh else None,
        'speed_percent': _integer(row.get('speed_percent'), 1, 400) if fresh else None,
        'collected': False,
    }
