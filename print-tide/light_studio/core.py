"""Stable façade for the host integration.

The installed ``server.py`` integration does ``from light_studio.core import
Studio`` and constructs it positionally. That contract is frozen here so the
package internals can be reorganized without needing a new staged host file --
a restart of the existing service is enough.

New code should import from the specific modules instead.
"""
from __future__ import annotations

from .layout import (ACCENT_MODES, Conflict, DEFAULT_ACCENT, DEFAULTS, LayoutStore,
                     SCHEMA, atomic_write, default_layout, merge_accent,
                     merge_settings, validate, validate_accent, validate_settings)
from .model import STALE_AFTER, STATES, freshness, normalize, number
from .renderer import (ACCENT_POSITIONS, ACCENT_START, BRIGHT_CAP, INACTIVE_POSITIONS,
                       PIXELS, QUANT, STATUS_POSITIONS, STATUS_START, compose_rope,
                       mask_inactive, render, render_accent, render_rope)
from .studio import Studio

#: Kept for older callers that imported ``core.atomic``.
atomic = atomic_write

__all__ = [
    'Studio', 'Conflict', 'LayoutStore', 'DEFAULTS', 'SCHEMA', 'STATES',
    'STALE_AFTER', 'PIXELS', 'STATUS_POSITIONS', 'ACCENT_POSITIONS',
    'INACTIVE_POSITIONS', 'STATUS_START', 'ACCENT_START', 'mask_inactive',
    'ACCENT_MODES', 'DEFAULT_ACCENT', 'BRIGHT_CAP', 'QUANT',
    'normalize', 'freshness', 'number', 'validate', 'validate_settings',
    'validate_accent', 'default_layout', 'merge_settings', 'merge_accent',
    'render', 'render_rope', 'render_accent', 'compose_rope',
    'atomic', 'atomic_write',
]
