"""Layout schema, validation and atomic persistence.

A layout separates three things that installers keep conflating:

* ``printer``  — the stable telemetry identity (``printer1`` ...). Never renamed.
* ``node``     — the rope controller identity (``node02``..``node08``).
* list order   — the *physical* left-to-right bay order, which is what ripple
                 propagation and the walk-through use.

Swapping printers between ropes changes ``printer``; rearranging the wall
changes the list order. They are independent on purpose.

Each rope also carries an ``accent``: the fixed ten-position cap at the far end
of the strip, opposite the data-in wire. It belongs to the *rope*, not to the
printer, so swapping printers between bays never moves it.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from .model import number
from .themes import DEFAULT_THEME, names as theme_names

#: 1 = original, 2 = added waterline_marks, 3 = added the per-rope accent cap.
SCHEMA = 3

#: node01 is the unrelated rack/wall pilot. It is rejected everywhere, even if
#: it somehow appeared in an injected CAST_MAP.
FORBIDDEN_NODES = frozenset({'node01'})

DEFAULTS = {
    'brightness': 65,
    'speed': 1.0,
    'ripples': True,
    'reduced_motion': False,
    'quiet': False,
    'waterline_marks': True,
    'theme': DEFAULT_THEME,
}

SETTING_RANGES = {'brightness': (0, 100), 'speed': (0.25, 2.0)}
SETTING_FLAGS = ('ripples', 'reduced_motion', 'quiet', 'waterline_marks')

#: ``accent`` is optional on input so a schema-1/2 file migrates cleanly; it is
#: always present on output.
SLOT_REQUIRED = {'node', 'printer', 'reverse', 'label'}
SLOT_KEYS = SLOT_REQUIRED | {'accent'}

ACCENT_MODES = ('white', 'color', 'rainbow')
DEFAULT_ACCENT = {'mode': 'white', 'color': [255, 255, 255], 'brightness': 100}


def merge_accent(accent):
    """Fill missing accent keys from the default. Never mutates the input."""
    merged = copy.deepcopy(DEFAULT_ACCENT)
    if isinstance(accent, dict):
        for key, value in accent.items():
            if key in DEFAULT_ACCENT:
                merged[key] = copy.deepcopy(value)
    return merged


def validate_accent(accent):
    """Validate one rope's far-end accent settings.

    Colour channels must be plain integers 0-255: ``True`` is not a colour and
    ``NaN`` is not a channel.
    """
    if accent is None:
        return copy.deepcopy(DEFAULT_ACCENT)
    if not isinstance(accent, dict):
        raise ValueError('Accent settings must be an object')
    if set(accent) - set(DEFAULT_ACCENT):
        raise ValueError('Unknown accent setting')
    clean = copy.deepcopy(DEFAULT_ACCENT)

    if 'mode' in accent:
        if accent['mode'] not in ACCENT_MODES:
            raise ValueError('Accent mode must be white, color or rainbow')
        clean['mode'] = accent['mode']

    if 'color' in accent:
        colour = accent['color']
        if not isinstance(colour, (list, tuple)) or len(colour) != 3:
            raise ValueError('Accent colour needs exactly three channels')
        channels = []
        for channel in colour:
            if type(channel) is not int or not 0 <= channel <= 255:
                raise ValueError('Accent colour channels must be integers 0-255')
            channels.append(channel)
        clean['color'] = channels

    if 'brightness' in accent:
        level = number(accent['brightness'])
        if level is None or not 0 <= level <= 100:
            raise ValueError('Accent brightness must be 0-100')
        clean['brightness'] = level

    return clean


class Conflict(ValueError):
    """Raised when a save is based on a revision that is no longer current."""


def default_label(printer):
    return printer.replace('printer', 'Printer ') if printer.startswith('printer') else printer


def default_layout(initial):
    """The 'restore original configuration' target, straight from CAST_MAP."""
    return {
        'schema': SCHEMA,
        'revision': 0,
        'slots': [
            {
                'printer': name,
                'node': initial[name]['node'],
                'reverse': False,
                'label': default_label(name),
                'accent': copy.deepcopy(DEFAULT_ACCENT),
            }
            for name in sorted(initial)
        ],
        'settings': dict(DEFAULTS),
    }


def merge_settings(settings):
    """Fill missing keys from defaults; used by the renderer and by validation."""
    merged = dict(DEFAULTS)
    if isinstance(settings, dict):
        for key, value in settings.items():
            if key in DEFAULTS:
                merged[key] = value
    return merged


def validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError('Animation settings must be an object')
    unknown = set(settings) - set(DEFAULTS)
    if unknown:
        raise ValueError('Unknown animation setting')
    clean = dict(DEFAULTS)
    for key, (low, high) in SETTING_RANGES.items():
        if key in settings:
            value = number(settings[key])
            if value is None or not low <= value <= high:
                raise ValueError('Animation setting out of range')
            clean[key] = value
    for key in SETTING_FLAGS:
        if key in settings:
            if type(settings[key]) is not bool:
                raise ValueError('Animation toggles must be true or false')
            clean[key] = settings[key]
    if 'theme' in settings:
        if settings['theme'] not in theme_names():
            raise ValueError('Unknown colour theme')
        clean['theme'] = settings['theme']
    return clean


def allowed_nodes(initial):
    return {target['node'] for target in initial.values()} - FORBIDDEN_NODES


def validate(config, initial):
    """Validate a whole layout against the injected allowlist.

    Returns a normalized copy at the current schema. Raises ``ValueError`` with
    an installer-readable, token-free message.
    """
    if not isinstance(config, dict):
        raise ValueError('Layout must be an object')
    # Older schemas are migrated, not rejected: a schema-1 or schema-2 file keeps
    # its mapping, names, direction, settings and undo history and simply gains
    # default white accent caps.
    if config.get('schema') not in (1, 2, SCHEMA):
        raise ValueError('Unsupported layout schema')
    revision = config.get('revision')
    if type(revision) is not int or type(revision) is bool or revision < 0:
        raise ValueError('Invalid layout revision')

    slots = config.get('slots')
    if not isinstance(slots, list) or len(slots) != len(initial):
        raise ValueError(f'Keep exactly {len(initial)} bays')

    nodes = allowed_nodes(initial)
    seen_printers, seen_nodes, clean_slots = set(), set(), []
    for slot in slots:
        if not isinstance(slot, dict) or set(slot) - SLOT_KEYS \
                or not SLOT_REQUIRED <= set(slot):
            raise ValueError('Each bay needs a printer, node, reverse and label')
        printer, node = slot['printer'], slot['node']
        if not isinstance(printer, str) or printer not in initial:
            raise ValueError('Unknown printer in layout')
        if printer in seen_printers:
            raise ValueError('Each printer must appear exactly once')
        if not isinstance(node, str) or node not in nodes:
            raise ValueError('Only the seven approved printer ropes may be used')
        if node in seen_nodes:
            raise ValueError('Each rope must appear exactly once')
        if type(slot['reverse']) is not bool:
            raise ValueError('Rope direction must be true or false')
        label = slot['label']
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40:
            raise ValueError('Display names must be 1-40 characters')
        seen_printers.add(printer)
        seen_nodes.add(node)
        clean_slots.append({
            'printer': printer,
            'node': node,
            'reverse': slot['reverse'],
            'label': label.strip(),
            'accent': validate_accent(slot.get('accent')),
        })

    # One-to-one both ways: every printer placed, every approved rope used.
    if seen_printers != set(initial) or seen_nodes != nodes:
        raise ValueError('Every printer and every rope must be used exactly once')

    return {
        'schema': SCHEMA,
        'revision': revision,
        'slots': clean_slots,
        'settings': validate_settings(config.get('settings', {})),
    }


def atomic_write(path, value):
    """Write JSON so a crash mid-write can never leave a half layout behind."""
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    with open(temp, 'w', encoding='utf8') as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    try:                                   # make the rename itself durable
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf8'))


class LayoutStore:
    """Persisted current+previous layout with revision checking and undo."""

    def __init__(self, root, initial):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'layout.json'
        self.initial = copy.deepcopy(initial)
        self.default = default_layout(initial)
        stored = read_json(self.path)
        if stored is None:
            self.current = copy.deepcopy(self.default)
            self.previous = None
        else:
            if not isinstance(stored, dict):
                raise ValueError('Saved layout file is not an object')
            self.current = validate(stored.get('current'), initial)
            self.previous = validate(stored['previous'], initial) if stored.get('previous') else None

    def snapshot(self):
        return copy.deepcopy(self.current)

    def save(self, config):
        """Validate, check the revision, then persist current+previous atomically."""
        clean = validate(config, self.initial)
        if clean['revision'] != self.current['revision']:
            raise Conflict('This layout changed in another window. Reload before saving.')
        clean['revision'] += 1
        atomic_write(self.path, {'current': clean, 'previous': self.current})
        self.previous, self.current = self.current, clean
        return self.snapshot()

    def undo(self, revision):
        if not self.previous:
            raise ValueError('There is no previous saved layout to restore')
        target = copy.deepcopy(self.previous)
        target['revision'] = revision
        return self.save(target)

    def reset(self, revision):
        target = copy.deepcopy(self.default)
        target['revision'] = revision
        return self.save(target)

    @property
    def can_undo(self):
        return self.previous is not None
