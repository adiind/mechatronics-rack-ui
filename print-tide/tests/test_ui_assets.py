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
                                 'reset', 'identify', 'collected', 'media'})
        # The only image URL the client ever builds is the one the server hands it.
        self.assertIn("img.src = info.url + '?v='", APP)
        self.assertNotIn("'/media/camera/' +", APP)

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
        # Files the server hands out, plus the routes it serves pages from
        # (the header links to the /states and /logs reference pages).
        served = {name for name, _ in FILES.values()} | {'style.css', 'app.js'}
        routes = {path.lstrip('/') for path in FILES}
        for href in re.findall(r'(?:href|src)="/([^"]*)"', HTML):
            self.assertIn(href or 'index.html', served | routes | {'index.html', ''}, href)

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

    def test_every_state_has_a_word_a_meaning_and_a_shape(self):
        table = APP.split('const STATE_TEXT = {')[1].split('};')[0]
        for key in ('idle', 'preparing', 'printing', 'paused', 'error', 'finished',
                    'stopped', 'offline', 'unknown'):
            line = [l for l in table.splitlines() if l.strip().startswith(key + ':')][0].rstrip()
            self.assertTrue(line.endswith("'],"), key)
            glyph = line[:-3]
            glyph = glyph[glyph.rfind(", '") + 3:]
            self.assertTrue(1 <= len(glyph) <= 2, (key, glyph))
        for word in ('Available', 'Preparing', 'Printing', 'Paused', 'Error',
                     'Ready to collect', 'Stopped early', 'Offline', 'Unknown'):
            self.assertIn("'" + word + "'", APP, word)

    def test_simulation_is_labelled_and_separated_from_wall_settings(self):
        self.assertIn('id="lab-badge"', HTML)
        self.assertIn('change only this preview', HTML)
        self.assertIn('never the physical', HTML)
        self.assertIn('become a draft that reaches the wall when you save', HTML)
        self.assertIn('saved with the wall', HTML)
        self.assertIn('screen only', HTML)

    def test_the_three_zones_are_explained_in_words_not_only_drawn(self):
        for phrase in ('bottom 30 positions', 'always dark', 'next 60 carry printer state',
                       'last 10 are an always-on cap', 'measured across these 60 only',
                       'the dark foot and the cap never move', 'Bottom 30% stays dark'):
            self.assertIn(phrase, HTML, phrase)
        self.assertNotIn('Bottom third', HTML)

    def test_the_live_wall_is_the_default_view_and_carries_no_marketing(self):
        self.assertIn('id="live-tab"', HTML)
        self.assertIn('aria-selected="true">Live wall', HTML)
        for banned in ('Everything in flow', 'Where it can go', 'journey', 'magic'):
            self.assertNotIn(banned, HTML, banned)
        self.assertIn('id="attention-summary"', HTML)
        self.assertIn('id="stage-canvas"', HTML)
        self.assertIn('id="inspector"', HTML)

    def test_the_masthead_names_only_the_product(self):
        # Adi, 14 Sep 2026: "Don't use Northwestern name in it. That was just context."
        head = HTML.split('<main>')[0]
        self.assertIn('class="product"><a href="/" aria-label="Filament wall home">Filament wall</a>', head)
        self.assertIn('<title>Filament wall</title>', HTML)
        for banned in ('Northwestern', 'Engineering Design Innovation', 'EDI'):
            self.assertNotIn(banned, HTML, banned)
            self.assertNotIn(banned, APP, banned)
        for view in ('Live wall', 'Configure', 'Animation lab', 'States', 'Telemetry'):
            self.assertIn(view, head, view)
        # Implementation detail stays out of the ordinary composition.
        live = HTML.split('<section id="live"')[1].split('</section>')[0]
        for noise in ('Tailscale', 'node0', 'ACK', 'WIRE', 'CAP</'):
            self.assertNotIn(noise, live, noise)

    def test_the_inspector_carries_a_camera_image_with_honest_states(self):
        self.assertIn('id="photo-img"', HTML)
        self.assertIn('id="photo-empty"', HTML)
        self.assertIn('id="photo-caption"', HTML)
        self.assertIn('<dialog class="lightbox"', HTML)
        for phrase in ("'Recent capture · '", "'Saved capture · '", "not live",
                       'No usable camera image', 'No camera image for this printer yet',
                       'did not decode as a picture', 'img.naturalWidth > 0', 'img.onerror = '):
            self.assertIn(phrase, APP, phrase)
        self.assertIn('showModal', APP)
        self.assertIn("'Demo photo'", APP)
        self.assertIn('not the simulated job', APP)

    def test_the_save_bar_is_absent_until_there_is_a_draft(self):
        bar = HTML.split('id="savebar"')[1].split('>')[0]
        self.assertIn('hidden', bar)
        self.assertIn("$('savebar').hidden = !dirty", APP)
        self.assertNotIn('id="undo"', HTML.split('id="savebar"')[1],
                         'Undo belongs to Configure, not to a permanent bar')

    def test_the_navigation_names_the_five_surfaces(self):
        for word in ('Live wall', 'Configure', 'Animation lab', 'href="/states"', 'href="/logs"'):
            self.assertIn(word, HTML, word)

    def test_the_lab_offers_the_progress_endpoints_and_unknown(self):
        check = APP.split("'Progress check'")[1].split(']),')[0]
        for token in ("['printing', 0]", "['printing', 25]", "['printing', 50]",
                      "['printing', 75]", "['printing', 100]", "['printing', null]"):
            self.assertIn(token, check, token)
        self.assertIn('% not reported', APP)

    def test_the_cap_controls_offer_white_colour_rainbow_and_apply_to_all(self):
        for token in ('value="white"', 'value="color"', 'value="rainbow"',
                      'id="cap-color"', 'id="cap-brightness"', 'Apply to all ropes'):
            self.assertIn(token, HTML, token)


class AccentClientTests(unittest.TestCase):
    def test_the_client_draws_the_zone_boundaries_from_server_data(self):
        self.assertIn('accent_start', APP)
        self.assertIn('accentStart', APP)
        self.assertIn('status_start', APP)
        self.assertIn('statusStart', APP)
        # The split is never hard-coded in the browser.
        body = APP.split('function drawRope')[1].split('function endLabels')[0]
        for literal in ('90', '60', '30'):
            self.assertNotIn(literal, body, literal)
        # Both edges are computed from the film metadata, and the foot is drawn.
        self.assertIn('foot / pixels', body)
        self.assertIn('accentStart / pixels', body)

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
        needed = ['.assign-row', '.assign-row.drafted', '.assign-row.identifying', '.assign-row.walking',
                  '.rename-input', '.printer-name', '.ar-strip', '.ar-state',
                  '.identify', '.reverse', '.reorder', '.sim-grid', '.sim-cell', '.sim-title',
                  '.sim-range', '.sim-pct', '.savebar', '.walk-actions', '#toast',
                  '.accent', '.accent-color', '.accent-level', '.accent-all',
                  '.stage-label', '.sl-state', '.sl-reading', '.sl-sub',
                  '.fleet-row', '.fr-state', '.fr-reading', '.alert-chip', '.pending',
                  '.sim-unknown', '.masthead', '.inspector', '.photo-frame', '.photo-frame.compact',
                  '.sheet-bar', '.lightbox', '.lab-label', '.theme-card[aria-checked="true"]']
        for selector in needed:
            self.assertIn(selector, CSS, selector)

    def test_it_ships_no_external_font_or_image_requests(self):
        self.assertNotIn('@import', CSS)
        self.assertNotIn('http://', CSS)
        self.assertNotIn('https://', CSS)

    def test_the_wall_fits_on_a_phone(self):
        phone = CSS.split('@media (max-width: 700px)')[1]
        # Seven concise rows replace the stage; the inspector opens as a sheet.
        self.assertIn('.stage { display: none; }', phone)
        self.assertIn('.fleet-rows { display: flex;', phone)
        self.assertIn('body.sheet-open .inspector', phone)
        self.assertIn(".fleet-alerts .alert-chip:not(:first-child) { display: none; }", phone)
        # Navigation wraps so the fifth destination stays visible.
        self.assertIn('flex-wrap: wrap', CSS.split('@media (max-width: 900px)')[1].split('}')[0] + CSS.split('@media (max-width: 900px)')[1])
        # No permanent bottom padding: the save bar reserves space only when shown.
        self.assertIn('body.has-savebar', CSS)
        self.assertNotIn('padding-bottom: 92px', CSS)

    def test_fonts_are_local_open_licensed_and_declared(self):
        for face in ('Poppins-Regular.ttf', 'Poppins-SemiBold.ttf', 'IBMPlexSans-Variable.ttf'):
            self.assertIn("url('/fonts/" + face + "')", CSS, face)
            self.assertTrue((STATIC / 'fonts' / face).stat().st_size > 10000, face)
        for licence in ('OFL-Poppins.txt', 'OFL-IBMPlexSans.txt'):
            text = (STATIC / 'fonts' / licence).read_text(encoding='utf8')
            self.assertIn('SIL Open Font License', text, licence)
        self.assertIn('#4E2A84', CSS)                 # the purple anchor stays
        self.assertNotIn('#0b151a', CSS.lower())      # the rejected blue chrome is gone


if __name__ == '__main__':
    unittest.main()
