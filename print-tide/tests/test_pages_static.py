"""Static checks on the reference pages and the shared chrome (PM handoff 01).

The Pi has no JS runtime in the unit-test workspace, so these read the served
files for the properties that would otherwise regress silently: the shared top
bar on every page, the logs page defaulting to meaningful changes with a real
Pause, the states page explaining each state as see / means / do, and every
drawing path taking the zone boundaries from the server payload.
"""
import unittest

from light_studio.web import FILES, STATIC

INDEX = (STATIC / 'index.html').read_text(encoding='utf8')
STATES_HTML = (STATIC / 'states.html').read_text(encoding='utf8')
STATES_JS = (STATIC / 'states.js').read_text(encoding='utf8')
LOGS_HTML = (STATIC / 'logs.html').read_text(encoding='utf8')
LOGS_JS = (STATIC / 'logs.js').read_text(encoding='utf8')
LOGS_CSS = (STATIC / 'logs.css').read_text(encoding='utf8')
APP = (STATIC / 'app.js').read_text(encoding='utf8')
CSS = (STATIC / 'style.css').read_text(encoding='utf8')


class SharedChromeTests(unittest.TestCase):
    def test_every_page_uses_the_same_masthead_and_names_the_five_views(self):
        for name, html in (('index', INDEX), ('states', STATES_HTML), ('logs', LOGS_HTML)):
            self.assertIn('class="masthead"', html, name)
            self.assertIn('aria-label="Filament wall home">Filament wall</a>', html, name)
            for banned in ('Northwestern', 'Engineering Design Innovation', 'EDI'):
                self.assertNotIn(banned, html, (name, banned))
            for view in ('Live wall', 'Configure', 'Animation lab', 'States', 'Telemetry'):
                self.assertIn(view, html, (name, view))
            self.assertIn('href="/style.css"', html, name)

    def test_the_reference_pages_link_back_into_the_studio_views(self):
        for html in (STATES_HTML, LOGS_HTML):
            self.assertIn('href="/#map"', html)
            self.assertIn('href="/#lab"', html)
        self.assertIn("window.addEventListener('hashchange'", APP)

    def test_every_served_file_exists(self):
        for path, (name, mime) in FILES.items():
            self.assertTrue((STATIC / name).exists(), path)


class StatesPageTests(unittest.TestCase):
    def test_each_state_is_explained_as_see_means_do(self):
        for key in ('idle', 'preparing', 'printing', 'paused', 'error', 'finished',
                    'stopped', 'offline', 'unknown'):
            block = STATES_JS.split("key: '" + key + "'")[1].split('keys:')[0]
            for field in ('see:', 'means:', 'action:', 'rule:', 'ends:'):
                self.assertIn(field, block, (key, field))
        for label in ("'What you see'", "'What it means'", "'What to do'", "'Technical detail'"):
            self.assertIn(label, STATES_JS, label)

    def test_error_copy_directs_to_the_printer_and_never_diagnoses(self):
        block = STATES_JS.split("key: 'error'")[1].split("key: 'finished'")[0]
        self.assertIn('Inspect the printer', block)
        self.assertIn('does not know the cause', block)
        self.assertNotIn('nozzle clog', block.lower())

    def test_the_geometry_scale_bar_is_driven_by_the_payload(self):
        for token in ('id="scale-foot"', 'id="scale-active"', 'id="scale-cap"'):
            self.assertIn(token, STATES_HTML, token)
        self.assertIn('live.inactive_positions', STATES_JS)
        self.assertIn('live.status_positions', STATES_JS)
        self.assertIn('status_start', STATES_JS)
        # Reference strips paint the foot and both boundaries from the film.
        body = STATES_JS.split('function paint(')[1].split('function drawAll')[0]
        self.assertIn('statusStart', body)
        self.assertIn('accentStart', body)
        for literal in ('30', '60', '90'):
            self.assertNotIn(literal, body, literal)

    def test_the_live_strip_links_each_printer_to_its_live_card(self):
        self.assertIn("'/?focus='", STATES_JS)
        self.assertIn("params.get('focus')", APP)
        self.assertIn("live.config.slots.find(s => s.node === focusNode)", APP)

    def test_the_reference_comes_first_and_the_live_comparison_is_optional(self):
        self.assertLess(STATES_HTML.index('id="cards"'), STATES_HTML.index('id="live"'))
        self.assertIn('<details class="settings live-compare"', STATES_HTML)
        self.assertIn("'Demo frames from the simulated printers", STATES_JS)


class LogsPageTests(unittest.TestCase):
    def test_meaningful_changes_are_the_default_and_everything_stays_reachable(self):
        ticks = LOGS_HTML.split('id="hide-ticks"')[1].split('>')[0]
        self.assertIn('checked', ticks, 'sensor ticking folds by default')
        unknown = LOGS_HTML.split('id="show-unknown"')[1].split('>')[0]
        self.assertNotIn('checked', unknown, 'unused fields hide by default')
        self.assertIn('other field', LOGS_JS)              # counted, not lost
        self.assertIn('raw=1', LOGS_JS)                     # raw stays reachable
        self.assertIn('redact', LOGS_HTML.lower() + 'redacted')

    def test_pause_freezes_the_surface_and_counts_new_changes(self):
        self.assertIn('if (paused) {', LOGS_JS)
        self.assertIn('pending = payload', LOGS_JS)
        self.assertIn('countNew(', LOGS_JS)
        self.assertIn("'Paused since '", LOGS_JS)
        self.assertIn("'Resume'", LOGS_JS)
        self.assertIn('new change', LOGS_JS)
        # While paused the on-screen data is not replaced.
        paused_branch = LOGS_JS.split('if (paused) {')[1].split('return;')[0]
        self.assertNotIn('data = payload', paused_branch)

    def test_headlines_read_as_sentences_not_field_dumps(self):
        head = LOGS_JS.split('function headline')[1].split('function loadRaw')[0]
        for phrase in ("'Progress '", "'State '", "'Layer '", "'Door opened'", "'Error reported"):
            self.assertIn(phrase, head, phrase)

    def test_rerendering_is_keyed_so_scroll_and_open_rows_survive_polling(self):
        self.assertIn('function signature()', LOGS_JS)
        self.assertIn('lastSignature', LOGS_JS)
        self.assertIn("scrollers[col.dataset.printer]", LOGS_JS)
        self.assertIn('const open = new Set()', LOGS_JS)

    def test_history_and_empty_states_are_honest(self):
        self.assertIn('last 400', LOGS_HTML)
        self.assertIn("'history: '", LOGS_JS)
        self.assertIn('No reports from this printer yet', LOGS_JS)
        self.assertIn('Cannot load telemetry', LOGS_JS)
        self.assertIn('id="feed-loading"', LOGS_HTML)

    def test_rows_never_borrow_the_current_status_for_their_wall_reading(self):
        # LOG-TRUTH (PM 04:00): the reading comes from entry.wall, computed on
        # the server from the buffer up to that report; no fallback to status.
        self.assertIn('function wallLine(entry)', LOGS_JS)
        self.assertNotIn('wallReading', LOGS_JS)
        self.assertIn('entry.wall', LOGS_JS)
        self.assertIn('historical reading unavailable', LOGS_JS)
        self.assertIn('not live status', LOGS_JS)
        self.assertIn('state carried from an earlier report', LOGS_JS)
        body = LOGS_JS.split('function renderEntry(')[1].split('function renderPrinter')[0]
        self.assertNotIn('status', body, 'renderEntry must not see the current status')
        self.assertIn('renderEntry(p.printer, entry)', LOGS_JS)
        # The current status is shown in the printer header only, labelled as now.
        self.assertIn("'wall now: '", LOGS_JS)

    def test_no_regex_literals_in_the_page_scripts(self):
        for name, source in (('states', STATES_JS), ('logs', LOGS_JS)):
            self.assertNotIn('.match(', source, name)
            self.assertNotIn('.replace(/', source, name)
            self.assertNotIn('RegExp', source, name)


class LiveWallTests(unittest.TestCase):
    def test_states_carry_a_shape_as_well_as_a_word(self):
        table = APP.split('const STATE_TEXT = {')[1].split('};')[0]
        for key in ('error', 'paused', 'stopped', 'finished', 'offline', 'unknown'):
            line = [l for l in table.splitlines() if l.strip().startswith(key + ':')][0].rstrip()
            self.assertTrue(line.endswith("'],"), key)
            glyph = line[:-3]
            glyph = glyph[glyph.rfind(", '") + 3:]
            self.assertTrue(1 <= len(glyph) <= 2, (key, glyph))   # a shape, not a word

    def test_error_and_unknown_language_is_honest(self):
        self.assertIn('the wall cannot diagnose it', APP)
        self.assertIn('an acknowledgement is not proof of how the LEDs look', APP)
        self.assertIn("'progress not reported yet'", APP)
        self.assertNotIn("'Bed empty'", APP)
        self.assertIn("'Model preview', 'unavailable", APP)

    def test_the_stage_keeps_physical_order_and_alerts_lead_with_errors(self):
        # The wall itself is never sorted: labels are built from live.config.slots in order.
        self.assertNotIn('.sort(', APP.split('function buildStage')[1].split('function updateLive')[0])
        self.assertIn("const URGENT = ['error', 'paused', 'stopped', 'offline', 'unknown']", APP)
        self.assertIn('urgent.sort((a, b) => URGENT.indexOf(a.state) - URGENT.indexOf(b.state))', APP)
        # Collect is counted in the summary, not shouted as a chip.
        self.assertNotIn("'finished'", APP.split('const URGENT')[1].split(';')[0])
        self.assertIn("' ready to collect'", APP)

    def test_selection_is_by_printer_identity_and_keyboard_reachable(self):
        self.assertIn('let selected = null;            // selected printer identity (never a bay index)', APP)
        self.assertIn("event.key === 'ArrowRight'", APP)
        self.assertIn("aria-selected", APP)
        self.assertIn("role', 'option'", APP)
        self.assertIn('function openSheet', APP)
        self.assertIn("setAttribute('inert', '')", APP)
        self.assertIn('function trapSheetFocus', APP)

    def test_preparing_is_not_described_as_a_physical_operation(self):
        # UI-TRUTH (PM 04:00): no stage report is read, so no stage is promised.
        for source in (APP, STATES_JS, INDEX):
            self.assertNotIn('heating and levelling', source)
            self.assertNotIn('heating, levelling', source)
        self.assertIn("'getting the job ready'", APP)

    def test_quarter_guides_are_labelled_screen_only_and_outside_wall_settings(self):
        wall = INDEX.split('Wall settings <span class="tag">')[1].split('Preview guides')[0]
        self.assertNotIn('waterline_marks', wall)
        guides = INDEX.split('Preview guides <span class="tag">screen only')[1].split('Far-end caps')[0]
        self.assertIn('id="waterline_marks"', guides)
        self.assertIn('renderer sends nothing to the LEDs', guides)
        self.assertNotIn('Quarter marks on the progress bar', INDEX)

    def test_collect_and_stopped_readings_stay_plain_and_protocol_words_stay_in_details(self):
        # COLLECT-CARD (PM 05:00), carried into the inspector: the reading says
        # Ready / Clear bed; "retained FINISH" lives in Technical details.
        read = APP.split('function reading(')[1].split('/* ---')[0]
        self.assertIn("'finish time unknown'", read)
        self.assertNotIn('retained FINISH', read)
        insp = APP.split('function updateInspector')[1].split('/* ---')[0]
        self.assertIn("['Completion', state === 'finished'", insp)
        self.assertIn('retained FINISH · the wall did not observe the completion', insp)
        self.assertIn("job.append(el('span', 'job-kicker', 'Job'), document.createTextNode(status.job || 'No job name reported'))", insp)

    def test_configure_is_an_assignment_table_not_seven_cards(self):
        self.assertIn('class="assign-table" role="table"', INDEX)
        for column in ('Bay', 'Printer', 'Driven by', 'Fill direction', 'Far-end cap', 'Rope', 'Move'):
            self.assertIn('<span role="columnheader">' + column + '</span>', INDEX, column)
        build = APP.split('function buildBays')[1].split('function updateCards')[0]
        for piece in ("'assign-row bay'", "'ar-ordinal'", "'ar-node'", "'ar-strip'", "'printer-name'",
                      "'Rename'", "'Driven by'", "'Reverse fill'", "'Identify · 5 s'", "ar-expand"):
            self.assertIn(piece, build, piece)
        self.assertIn('let openBay = null;', APP)
        self.assertIn('.assign-row.open .ar-assign', CSS)

    def test_the_lab_keeps_per_bay_controls_under_the_preview_and_labels_in_html(self):
        main = INDEX.split('<div class="lab-main">')[1].split('<aside class="controls">')[0]
        for item in ('id="scenarios"', 'id="lab-canvas"', 'id="lab-labels"'):
            self.assertIn(item, main, item)
        self.assertIn('function updateLabLabels', APP)
        self.assertNotIn("ctx.fillText(simLabel(entry)", APP)
        # Phone preview labels read "Bay N" (never seven identical "Printe…");
        # the full identity stays in the accessible name and tooltip.
        labels = APP.split('function updateLabLabels')[1].split('function loop')[0]
        self.assertIn("'lab-bay', 'Bay ' + (index + 1)", labels)
        self.assertIn("label.setAttribute('aria-label', 'Simulated ' + full)", labels)
        self.assertIn('id="bay-sim"', INDEX)
        self.assertGreater(INDEX.index('id="bay-sim"'), INDEX.index('id="lab-canvas"'))
        phone = CSS.split('@media (max-width: 700px)')[1]
        self.assertIn('.lab-labels .lab-label .lab-name { display: none; }', phone)
        self.assertIn('.lab-labels .lab-label .lab-bay { display: block; }', phone)

    def test_the_phone_lab_carries_exactly_one_simulation_notice(self):
        # PM visual review: the intro, the badge and the notice said the same
        # thing three times on a phone and pushed the preview out of the first
        # viewport. On a phone only the notice remains, and it keeps the
        # sample-states sentence, the real-status link and the draft state.
        notice = INDEX.split('class="lab-context"')[1].split('</div>\n    <div class="lab-layout"')[0]
        self.assertIn('sample states, not live printer status', notice)
        self.assertIn('href="#live"', notice)
        self.assertIn('id="lab-context-state"', notice)
        phone = CSS.split('@media(max-width:700px)')[1]
        self.assertIn('#lab-intro,#lab-badge{display:none}', phone)
        self.assertIn('.lab-context-state{display:block', phone)
        # Off a phone the badge still carries the draft state, so the extra
        # line stays hidden there.
        self.assertIn('.lab-context-state{display:none', CSS.split('@media(max-width:700px)')[0])
        self.assertIn('id="lab-intro"', INDEX)
        self.assertIn("$('lab-context-state').textContent = dirty", APP)
        # The line describes the appearance settings, never the simulated
        # states: "matches the wall" would re-create the confusion that the
        # simulation labelling was added to remove.
        self.assertIn('Appearance draft · not on the wall until you save', APP)
        self.assertIn('Appearance uses saved settings', APP)
        for banned in ('Preview matches the saved wall', 'matches the wall'):
            self.assertNotIn(banned, APP, banned)
            self.assertNotIn(banned, INDEX, banned)
        self.assertIn('<details class="control-group" open>', INDEX)
        self.assertIn('id="lab-pending" role="status" hidden', INDEX)

    def test_telemetry_defaults_to_one_printer_and_is_keyboard_operable(self):
        self.assertIn("let selected = wanted && wanted.startsWith('printer')", LOGS_JS)
        self.assertIn("'first'", LOGS_JS)
        self.assertIn("'View raw report'", LOGS_JS)
        self.assertIn("toggle.setAttribute('aria-expanded'", LOGS_JS)
        self.assertIn("toggle.setAttribute('aria-controls', pre.id)", LOGS_JS)
        self.assertIn("'Demo · simulated reports'", LOGS_JS)
        self.assertIn('.logs-feed.all .entries', LOGS_CSS.replace('.logs-feed:not(.all)', ''))
        self.assertIn('.entries { max-height: 42vh;', LOGS_CSS)

    def test_rope_drawing_marks_the_foot_and_reads_both_edges_from_the_film(self):
        self.assertIn("'OFF'", APP)
        self.assertIn('statusStart: payload.status_start', APP)
        self.assertIn('accentStart: payload.accent_start', APP)
        self.assertIn('film.statusStart', APP)


if __name__ == '__main__':
    unittest.main()
