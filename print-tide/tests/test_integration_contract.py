"""The staged host integration, checked by reading it -- never by running it.

``server.staged.py`` imports paho, uvicorn and the MCP server and would open
printer connections on import, so these tests parse it instead. They exist to
catch the failure mode that actually matters: this package drifting away from
the interface the installed host file calls.
"""
import ast
import inspect
import unittest
from pathlib import Path

from light_studio.core import Studio
from light_studio.web import serve

ROOT = Path(__file__).resolve().parent.parent
STAGED = ROOT / 'server.staged.py'
REFERENCE = ROOT / 'reference' / 'server.py'


def tree(path):
    return ast.parse(path.read_text(encoding='utf8'))


class StagedSourceTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(STAGED.exists(), 'server.staged.py is missing')
        self.source = STAGED.read_text(encoding='utf8')
        self.tree = tree(STAGED)

    def test_the_staged_file_is_valid_python(self):
        self.assertIsInstance(self.tree, ast.Module)

    def test_the_staged_file_starts_exactly_one_writer(self):
        """Seven NodeAnimator starts are replaced by one Studio, with a fallback."""
        starts = [node for node in ast.walk(self.tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)
                  and node.func.attr == 'start'
                  and isinstance(node.func.value, ast.Call)
                  and getattr(node.func.value.func, 'id', None) == 'NodeAnimator']
        # Exactly one remains, and it is inside the initialization-failure branch.
        self.assertEqual(len(starts), 1)
        handlers = [h for h in ast.walk(self.tree) if isinstance(h, ast.ExceptHandler)]
        self.assertTrue(any(any(node is starts[0] for node in ast.walk(h))
                            for h in handlers),
                        'the original animator must remain as the fallback only')

    def test_the_studio_is_constructed_the_way_this_package_expects(self):
        calls = [node for node in ast.walk(self.tree)
                 if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'Studio']
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(len(call.args), 5, 'Studio is called with five positionals')
        signature = inspect.signature(Studio.__init__)
        signature.bind(object(), *[object()] * 5)      # would raise on drift

    def test_serve_is_called_with_a_resolved_bind_host_and_the_studio_port(self):
        calls = [node for node in ast.walk(self.tree)
                 if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'serve']
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0].args), 3)
        inspect.signature(serve).bind(object(), object(), object())
        # Binding must not follow MCP_HOST, which is 0.0.0.0 in the live service.
        self.assertIn('resolve_bind_host("auto")', self.source)
        self.assertIn('8772', self.source)

    def test_the_mcp_tools_read_the_live_map_rather_than_the_startup_map(self):
        self.assertIn('def current_cast_map():', self.source)
        self.assertNotIn('s["cast_target"] = CAST_MAP.get(', self.source)
        self.assertEqual(self.source.count('current_cast_map().get('), 2)

    def test_only_the_lighting_writer_and_the_cast_map_reads_were_changed(self):
        """Every other line of the host file must survive the patch verbatim."""
        original = REFERENCE.read_text(encoding='utf8')
        # The four lines the integration is allowed to rewrite or drop.
        rewritten = {'CAST_MAP = load_cast_map()',
                     's["cast_target"] = CAST_MAP.get(p.name)',
                     's["cast_target"] = CAST_MAP.get(pr.name)',
                     'for name, tgt in CAST_MAP.items():',
                     'NodeAnimator(BY_NAME[name], tgt["node"], tgt["pixels"]).start()'}
        for line in original.splitlines():
            if not line.strip() or line.strip() in rewritten:
                continue
            self.assertIn(line, self.source,
                          f'staged file dropped an original line: {line.strip()[:60]}')

    def test_no_credential_or_token_names_appear_in_the_studio_glue(self):
        marker = '# Print Tide owns all seven printer ropes'
        self.assertIn(marker, self.source)
        glue = self.source.split(marker)[1].split('    reach = (')[0]
        self.assertIn('Studio(', glue)
        for word in ('MCP_TOKEN', 'password', 'access_code', 'PRINTER_CODE',
                     'LEDWALL_CONF', '_ledwall_creds'):
            self.assertNotIn(word, glue, word)


class WorkspaceHygieneTests(unittest.TestCase):
    def test_reference_copies_are_untouched_python_and_json(self):
        ast.parse(REFERENCE.read_text(encoding='utf8'))
        import json
        initial = json.loads((ROOT / 'reference' / 'initial-map.json').read_text())
        self.assertEqual(len(initial), 7)
        self.assertNotIn('node01', {v['node'] for v in initial.values()})

    def test_the_shipped_initial_map_is_acceptable_to_the_studio(self):
        import json
        import tempfile
        initial = json.loads((ROOT / 'reference' / 'initial-map.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            studio = Studio(Path(tmp), initial, lambda: [], None, lambda: False)
            self.assertEqual(studio.mapping(), initial)
            self.assertEqual(len(studio.config()['slots']), 7)


if __name__ == '__main__':
    unittest.main()
