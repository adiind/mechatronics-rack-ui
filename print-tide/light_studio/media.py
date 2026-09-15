"""Read-only bridge to the cached printer camera images.

The Discord dashboard on this Pi (``/home/edi/printer-discord-dashboard``)
captures one JPEG per printer into a fixed cache directory and writes a
``metadata.json`` beside them (status, source, capture time, size, and the
printer state and job name at capture time). Nothing in here talks to a
printer, a camera or the network, and nothing here writes: this module only
reads that cache, checks what it finds, and describes it truthfully.

Boundaries, on purpose:

* Only allowlisted printer aliases (the identities in the wall's own printer
  map) can be asked for, and each maps to exactly one fixed filename inside
  one fixed directory. There is no path parameter and no URL parameter.
* A file is served only if it is a bounded-size JPEG: the magic bytes are
  checked, and when Pillow is present on the host the image is also decoded
  once (``Image.verify``) so a truncated or garbage body is refused here
  rather than in the browser. The browser still treats a decode failure as a
  visible error state, because no server check is complete.
* Freshness is reported, never assumed: a capture is *recent* only when the
  capture timestamp **recorded by the dashboard** is within
  :data:`FRESH_SECONDS`; anything older is a *saved* capture. A file with no
  recorded capture time is "capture time unknown" and never recent (a file's
  modification time says when it was copied, not when the camera saw it). A
  capture stamped in the future is reported as a clock inconsistency, not as
  "0 seconds ago". Missing, empty, unreadable and corrupt files are distinct
  outcomes, not "no image".
* Images are keyed by **printer identity**, never by bay position, so
  reassigning a rope never shows the wrong machine.
"""
from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path

try:                                  # optional, already installed on this Pi
    from PIL import Image as _PILImage
except Exception:                     # pragma: no cover - depends on the host
    _PILImage = None

#: Where the dashboard writes its captures. Read-only from here.
DEFAULT_CAMERA_DIR = Path('/home/edi/printer-discord-dashboard/data/cameras')
#: The dashboard keeps a capture for 30 minutes; the same limit says "recent".
FRESH_SECONDS = 1800.0
#: A chamber JPEG from these printers is a few hundred kilobytes.
MAX_BYTES = 8 * 1024 * 1024
ALIAS = re.compile(r'^printer[1-9][0-9]?$')
JPEG_MAGIC = b'\xff\xd8\xff'
#: Metadata keys that may leave the host. Anything else in the file (there is
#: nothing else today, but the dashboard may grow) stays behind.
PUBLIC_META = ('status', 'source', 'captured_at', 'attempted_at', 'width', 'height',
               'bytes', 'state', 'job')


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        and value == value and value not in (float('inf'), float('-inf')) else None


def decodes(data):
    """True when the bytes decode as an image, or when no decoder is available.

    ``Image.verify`` reads the whole (bounded) buffer once and raises on a
    truncated or corrupt body; it never writes anything.
    """
    if _PILImage is None:
        return True
    try:
        with _PILImage.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False


#: Whether the host can decode-check images; reported so the UI can say how
#: far the server check goes.
DECODER = 'pillow' if _PILImage is not None else None


class CameraCache:
    """Describe and read the cached camera image for allowlisted printers."""

    def __init__(self, root=None, aliases=(), clock=time.time):
        self.root = Path(root) if root is not None else DEFAULT_CAMERA_DIR
        self.aliases = frozenset(a for a in aliases if isinstance(a, str) and ALIAS.match(a))
        self.clock = clock

    # ------------------------------------------------------------ internals

    def _allowed(self, alias):
        return isinstance(alias, str) and alias in self.aliases and ALIAS.match(alias) is not None

    def _path(self, alias):
        """The one file an alias may ever map to, or None if it escapes the root."""
        if not self._allowed(alias):
            return None
        candidate = self.root / f'{alias}.jpg'
        try:
            real_root = os.path.realpath(self.root)
            real = os.path.realpath(candidate)
        except OSError:
            return None
        if os.path.dirname(real) != real_root:
            return None
        return Path(real)

    def _metadata(self):
        try:
            with open(self.root / 'metadata.json', 'rb') as handle:
                raw = handle.read(256 * 1024)
            data = json.loads(raw)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _inspect(self, path):
        """``(outcome, size, mtime)`` for a candidate file without reading it all."""
        try:
            stat = path.stat()
        except OSError:
            return 'missing', 0, None
        if not path.is_file():
            return 'missing', 0, None
        if stat.st_size == 0:
            return 'empty', 0, stat.st_mtime
        if stat.st_size > MAX_BYTES:
            return 'too_large', stat.st_size, stat.st_mtime
        try:
            with open(path, 'rb') as handle:
                data = handle.read(MAX_BYTES + 1)
        except OSError:
            return 'unreadable', stat.st_size, stat.st_mtime
        if len(data) > MAX_BYTES:
            return 'too_large', len(data), stat.st_mtime
        if data[:len(JPEG_MAGIC)] != JPEG_MAGIC or not decodes(data):
            return 'corrupt', len(data), stat.st_mtime
        return 'ok', len(data), stat.st_mtime

    # -------------------------------------------------------------- public

    def describe(self, alias, now=None, meta=None):
        """Truthful public description of one printer's cached camera image.

        ``status`` is one of ``recent`` (a valid image whose capture time is
        within FRESH_SECONDS), ``saved`` (a valid image with an older or
        unknown capture time), ``missing``, ``corrupt``, ``empty``,
        ``unreadable`` or ``too_large``. ``available`` is True only for
        ``recent`` and ``saved``.
        """
        now = self.clock() if now is None else now
        out = {'printer': alias, 'available': False, 'status': 'missing', 'captured_at': None,
               'age_seconds': None, 'recent': False, 'time_known': False, 'clock_skew': False,
               'file_updated_at': None, 'source': None,
               'width': None, 'height': None, 'bytes': None, 'state_at_capture': None,
               'job_at_capture': None, 'attempted_at': None, 'note': None}
        if not self._allowed(alias):
            out['status'] = 'unknown_printer'
            out['note'] = 'Not a printer on this wall.'
            return out
        meta = meta if meta is not None else self._metadata()
        row = meta.get(alias) if isinstance(meta.get(alias), dict) else {}
        public = {key: row[key] for key in PUBLIC_META if key in row}
        out['source'] = public.get('source') if isinstance(public.get('source'), str) else None
        out['state_at_capture'] = public.get('state') if isinstance(public.get('state'), str) else None
        out['job_at_capture'] = public.get('job') if isinstance(public.get('job'), str) else None
        out['attempted_at'] = _number(public.get('attempted_at'))
        out['width'] = int(public['width']) if _number(public.get('width')) else None
        out['height'] = int(public['height']) if _number(public.get('height')) else None

        path = self._path(alias)
        outcome, size, mtime = self._inspect(path) if path else ('missing', 0, None)
        out['status'] = outcome
        if outcome != 'ok':
            out['note'] = {
                'missing': 'No camera image has been cached for this printer.',
                'empty': 'The cached camera file is empty.',
                'corrupt': 'The cached camera file is not a readable JPEG.',
                'unreadable': 'The cached camera file could not be read.',
                'too_large': 'The cached camera file is larger than this page will serve.',
            }[outcome]
            return out

        out['available'] = True
        out['bytes'] = size
        # The file's own time says when it was written or copied here. It is
        # reported separately and is never used to call a capture recent.
        out['file_updated_at'] = round(float(mtime), 3) if mtime else None
        captured = _number(public.get('captured_at'))
        if captured is not None and captured > 0:
            age = now - captured
            out['captured_at'] = round(captured, 3)
            out['time_known'] = True
            if age < 0:
                # A capture "from the future": a clock is wrong somewhere.
                out['clock_skew'] = True
                out['status'] = 'saved'
                out['note'] = ('Capture time is ahead of this clock; treated as a '
                               'saved capture, not a live view.')
                return out
            out['age_seconds'] = round(age, 1)
            out['recent'] = age <= FRESH_SECONDS
            out['status'] = 'recent' if out['recent'] else 'saved'
            out['note'] = ('Recent camera capture.' if out['recent']
                           else 'Saved capture from earlier; not a live view.')
            return out
        out['status'] = 'saved'
        out['note'] = 'Saved capture; the capture time was not recorded, so it is not shown as recent.'
        return out

    def describe_all(self, aliases=None, now=None):
        now = self.clock() if now is None else now
        meta = self._metadata()
        names = list(aliases) if aliases is not None else sorted(self.aliases)
        return {alias: self.describe(alias, now=now, meta=meta) for alias in names}

    def image(self, alias):
        """``(jpeg_bytes, mtime)`` for a valid cached image, else raises.

        ``LookupError`` for an unknown or missing image, ``ValueError`` for a
        file that exists but must not be served (empty, corrupt, too large).
        """
        path = self._path(alias)
        if path is None:
            raise LookupError('Unknown printer')
        outcome, size, mtime = self._inspect(path)
        if outcome == 'missing':
            raise LookupError('No cached camera image')
        if outcome != 'ok':
            raise ValueError('Cached camera image is not servable')
        with open(path, 'rb') as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or not data.startswith(JPEG_MAGIC) or not decodes(data):
            raise ValueError('Cached camera image is not servable')
        return data, mtime
