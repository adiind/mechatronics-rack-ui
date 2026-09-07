"""Print Tide - the seven-printer lighting studio for the EDI printer bay.

Modules, in dependency order:

    model      raw host snapshot  -> sanitized display state
    layout     mapping schema, validation and atomic persistence
    renderer   display state      -> pixels (pure, shared with the browser)
    transport  pixels             -> firmware messages under a budget
    studio     the coordinator: one writer, the clock, the lifecycle rules
    web        loopback/tailnet UI server
    core       frozen façade for the host integration

Standard library only. Nothing here imports the host service or its hardware.
"""

__all__ = ['core', 'layout', 'model', 'renderer', 'studio', 'transport', 'web']
__version__ = '2.0'
