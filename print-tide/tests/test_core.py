"""Shared fixtures plus the frozen host-integration façade contract."""
import inspect
import tempfile
import unittest
from pathlib import Path

from light_studio import core

#: Seven printers on the seven approved ropes, 100 pixels each.
MAP = {f'printer{i}': {'node': f'node{i + 1:02}', 'pixels': 100} for i in range(1, 8)}
NOW = 1788670000.0


def report(state='RUNNING', name='printer1', **kw):
    """One raw host snapshot, shaped like Printer.snapshot() in server.py."""
    row = dict(name=name, state=state, updated=NOW, mqtt_connected=True,
               percent=35, job='test part', has_error=False,
               layer=42, total_layer=300, nozzle=210.0, bed=55.0,
               host='10.0.0.9', serial='SECRETSERIAL', code='SECRETCODE')
    row.update(kw)
    return row


def studio(tmp, rows=None, publish=None, enabled=True, clock=None, **kw):
    from light_studio.studio import Studio
    return Studio(Path(tmp), MAP, (lambda: rows if rows is not None else []),
                  publish, (lambda: enabled), clock=clock or (lambda: NOW), **kw)


class FacadeTests(unittest.TestCase):
    """The staged host file imports these names and calls them positionally."""

    def test_core_exports_the_names_the_host_integration_uses(self):
        for name in ('Studio', 'Conflict', 'normalize', 'render', 'validate'):
            self.assertTrue(hasattr(core, name), name)

    def test_studio_constructor_signature_is_positionally_stable(self):
        params = list(inspect.signature(core.Studio.__init__).parameters.values())[1:]
        positional = [p.name for p in params
                      if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
        self.assertEqual(positional[:5],
                         ['root', 'initial', 'snapshots', 'publish', 'enabled'])
        for extra in params[len(positional):]:
            self.assertIs(extra.kind, inspect.Parameter.KEYWORD_ONLY)
            self.assertIsNot(extra.default, inspect.Parameter.empty)

    def test_mapping_is_readable_without_starting_any_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = studio(tmp, publish=lambda n, p: self.fail('must not publish'))
            self.assertEqual(s.mapping(), MAP)
            self.assertFalse(s.running)


if __name__ == '__main__':
    unittest.main()
