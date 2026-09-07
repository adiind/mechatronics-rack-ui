"""Static checks on the browser assets.

The Pi has no JS runtime in this workspace, so instead of pretending the UI was
executed these tests check the things that actually break silently: unbalanced
brackets, a ``$('id')`` that no longer exists in the HTML, markup injection
routes, and inline styles that the Content-Security-Policy would block.
"""
import re
import unittest

from light_studio.web import CSP, FILES, STATIC

APP = (STATIC / 'app.js').read_text(encoding='utf8')
HTML = (STATIC / 'index.html').read_text(encoding='utf8')
CSS = (STATIC / 'style.css').read_text(encoding='utf8')

PAIRS = {')': '(', ']': '[', '}': '{'}


def strip_js(source):
    """Remove comments and string bodies so brackets can be counted.

    Deliberately simple: it assumes the file contains no regular-expression
    literals, which :meth:`JavaScriptTests.test_no_regex_literals` enforces.
    """
    out = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ''
        if ch == '/' and nxt == '/':
            i = source.find('\n', i)
            if i < 0:
                break
            continue
        if ch == '/' and nxt == '*':
            end = source.find('*/', i + 2)
            i = n if end < 0 else end + 2
            continue
        if ch in '"\'`':
            quote, i = ch, i + 1
            while i < n:
                if source[i] == '\\':
                    i += 2
                    continue
                if source[i] == quote:
                    i += 1
                    break
                i += 1
            out.append('""')
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def balanced(text):
    stack = []
    for ch in text:
        if ch in '([{':
            stack.append(ch)
        elif ch in PAIRS:
            if not stack or stack.pop() != PAIRS[ch]:
                return False
    return not stack


class JavaScriptTests(unittest.TestCase):
    def test_no_regex_literals(self):
        self.assertNotIn('.match(', APP)
        self.assertNotIn('.replace(/', APP)
        self.assertNotIn('RegExp', APP)

    def test_brackets_balance(self):
        self.assertTrue(balanced(strip_js(APP)))

    def test_it_declares_strict_mode_and_no_globals_leak(self):
        self.assertTrue(APP.lstrip().startswith("'use strict'"))
        self.assertNotIn('var ', APP)

    def test_printer_text_can_never_become_markup(self):
        for unsafe in ('innerHTML', 'outerHTML', 'insertAdjacentHTML',
                       'document.write', 'eval(', 'new Function(',
                       'setTimeout("', 'javascript:'):
            self.assertNotIn(unsafe, APP, unsafe)

    def test_every_element_lookup_exists_in_the_html(self):
        ids = set(re.findall(r'\bid="([^"]+)"', HTML))
        used = set(re.findall(r"\$\('([^']+)'\)", APP))
        self.assertTrue(used)
        self.assertEqual(used - ids, set(), 'app.js looks up ids the page lacks')

    def test_the_client_only_calls_endpoints_the_server_serves(self):
        calls = set(re.findall(r"api\('([a-z]+)", APP))
        self.assertEqual(calls, {'state', 'film', 'preview', 'config', 'undo',
                                 'reset', 'identify', 'collected'})

    def test_simulation_and_live_playback_use_different_endpoints(self):
        self.assertIn("api('film", APP)          # live: read-only GET
        self.assertIn("api('preview'", APP)      # lab: explicitly simulated
        # The lab must never post a mapping change as a side effect.
        lab = APP.split('const labPlayer')[1].split('/* ---')[0]
        for write in ("api('config'", "api('identify'", "api('collected'"):
            self.assertNotIn(write, lab)

    def test_the_hardware_ceiling_is_not_worked_around_in_the_browser(self):
        """Screen gain is presentation only and must be labelled as such."""
        self.assertIn('SCREEN_GAIN', APP)
        self.assertIn('not a photometric', APP)


class HTMLTests(unittest.TestCase):
    def test_it_references_only_assets_the_server_serves(self):
        served = {name for name, _ in FILES.values()} | {'style.css', 'app.js'}
        for href in re.findall(r'(?:href|src)="/([^"]*)"', HTML):
            self.assertIn(href or 'index.html', served | {'index.html', ''}, href)

    def test_no_inline_script_or_style_that_the_csp_would_block(self):
        self.assertNotIn('<style', HTML)
        self.assertIsNone(re.search(r'\sstyle="', HTML))
        self.assertIsNone(re.search(r'\son[a-z]+="', HTML))
        body = re.findall(r'<script[^>]*>(.*?)</script>', HTML, re.S)
        self.assertEqual([b.strip() for b in body if b.strip()], [])

    def test_the_csp_actually_forbids_what_the_markup_avoids(self):
        self.assertNotIn("'unsafe-inline'", CSP)
        self.assertNotIn("'unsafe-eval'", CSP)

    def test_it_is_responsive_and_labelled(self):
        self.assertIn('name="viewport"', HTML)
        self.assertIn('lang="en"', HTML)
        self.assertGreaterEqual(HTML.count('aria-label'), 3)

    def test_the_legend_names_every_state_in_words_not_only_colour(self):
        for word in ('Available', 'Preparing', 'Printing', 'Paused', 'Error',
                     'Collect', 'Offline', 'Unknown'):
            self.assertIn(word, HTML, word)

    def test_simulation_is_labelled_as_simulation(self):
        self.assertIn('SIMULATION', HTML)
        self.assertIn('Nothing here reaches a printer or a light', HTML)

    def test_the_two_zones_are_explained_in_words_not_only_drawn(self):
        for phrase in ('90 positions of printer state', '10-position cap',
                       'opposite the wire', 'not part of the percentage',
                       'never moves the cap'):
            self.assertIn(phrase, HTML, phrase)

    def test_the_cap_controls_offer_white_colour_rainbow_and_apply_to_all(self):
        for token in ('value="white"', 'value="color"', 'value="rainbow"',
                      'id="cap-color"', 'id="cap-brightness"', 'Apply to all ropes'):
            self.assertIn(token, HTML, token)


class AccentClientTests(unittest.TestCase):
    def test_the_client_draws_the_zone_boundary_from_server_data(self):
        self.assertIn('accent_start', APP)
        self.assertIn('accentStart', APP)
        # The split is never hard-coded in the browser.
        self.assertNotIn('90', APP.split('function drawRope')[1].split('}')[0])

    def test_the_client_labels_the_wire_and_cap_ends(self):
        self.assertIn("'WIRE'", APP)
        self.assertIn("'CAP'", APP)

    def test_the_lab_previews_draft_caps_through_the_python_compositor(self):
        lab = APP.split('const labPlayer')[1].split('/* ---')[0]
        self.assertIn('accents:', lab)
        self.assertIn('reverses:', lab)

    def test_accent_editing_offers_every_mode_and_an_apply_to_all(self):
        self.assertIn('ACCENT_MODES', APP)
        for mode in ('white', 'color', 'rainbow'):
            self.assertIn(mode + ':', APP.split('const ACCENT_MODES')[1][:200], mode)
        self.assertIn('applyAccentToAll', APP)

    def test_colour_conversion_avoids_regular_expressions(self):
        self.assertIn('function toHex', APP)
        self.assertIn('function fromHex', APP)
        self.assertNotIn('exec(', APP)


class BrowserScriptTests(unittest.TestCase):
    """browser_check.py is written here but run by Codex; at least keep it valid."""

    def test_the_playwright_script_parses_and_is_not_auto_discovered(self):
        import ast
        from pathlib import Path
        script = Path(__file__).with_name('browser_check.py')
        source = script.read_text(encoding='utf8')
        ast.parse(source)
        self.assertFalse(script.name.startswith('test'))
        self.assertIn('NOT executed by Claude', source)

    def test_the_script_targets_selectors_the_client_actually_creates(self):
        from pathlib import Path
        source = Path(__file__).with_name('browser_check.py').read_text(encoding='utf8')
        for selector in ('.bay', '.sim-cell select', '.rename-input',
                         '#lab-canvas', '#save-note', '#walk-here'):
            self.assertIn(selector, source, selector)
        for token in ('data-node', 'dataset.node'):
            self.assertIn(token, source + APP, token)


class CSSTests(unittest.TestCase):
    def test_braces_balance(self):
        self.assertTrue(balanced(re.sub(r'/\*.*?\*/', '', CSS, flags=re.S)))

    def test_classes_the_client_creates_are_all_styled(self):
        needed = ['.bay', '.bay.drafted', '.bay.identifying', '.bay.walking',
                  '.rename-input', '.printer-name', '.rope-stage', '.reading',
                  '.state', '.status-meta', '.identify', '.collected',
                  '.reverse', '.reorder', '.sim-grid', '.sim-cell', '.sim-title',
                  '.sim-range', '.sim-pct', '.savebar', '.walk-actions', '#toast',
                  '.accent', '.accent-color', '.accent-level', '.accent-all',
                  '#cap-color', '#cap-mode', '.zone-note']
        for selector in needed:
            self.assertIn(selector, CSS, selector)

    def test_it_ships_no_external_font_or_image_requests(self):
        self.assertNotIn('@import', CSS)
        self.assertNotIn('http://', CSS)
        self.assertNotIn('https://', CSS)

    def test_the_wall_fits_on_a_phone(self):
        self.assertIn('repeat(2, minmax(0, 1fr))', CSS)
        self.assertIn('@media (max-width: 620px)', CSS)


if __name__ == '__main__':
    unittest.main()
