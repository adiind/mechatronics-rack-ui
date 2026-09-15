/* States & animations reference. Read-only: it only ever calls the preview and
 * film endpoints, so nothing here can change the wall. */
'use strict';

const $ = id => document.getElementById(id);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const SCREEN_GAIN = 1.8;

let live = null;          // last /api/state payload (csrf, themes, config, printers)
let settings = { theme: 'pink', brightness: 100, reduced_motion: false };

/* ------------------------------------------------------------- reference */

/* Each state: what you see on the rope, what it means, what to do, then the
 * protocol detail for people who want it. Glyphs are shapes, never only colour. */
const STATE_DOCS = [
  { key: 'idle', title: 'Available', glyph: '·',
    see: 'One solid resting colour, no motion, above a dark foot.',
    means: 'The printer is free. It reported IDLE or READY, or a finished / stopped job has been collected.',
    action: 'Nothing. Start a print when you are ready.',
    rule: 'gcode_state IDLE or READY with fresh telemetry, or a finished / stopped bay after collection.',
    ends: 'Any other state. A new job takes it to Preparing or Printing.',
    keys: ['idle'] },
  { key: 'preparing', title: 'Preparing', glyph: '↑',
    see: 'A dark body breathes slowly while a light sweep rises up the rope.',
    means: 'The printer said PREPARE or SLICING: it is getting the job ready. The wall does not know which step that is.',
    action: 'Wait. Progress appears when the print itself starts.',
    rule: 'gcode_state PREPARE or SLICING. Never inferred from temperatures.',
    ends: 'RUNNING → Printing.',
    keys: ['prep_low', 'prep_high', 'prep'] },
  { key: 'printing', title: 'Printing', glyph: '≈',
    see: 'The printed share is water rising from the foot; the remainder is six bands darkening towards the cap. Printing motion follows the saved look: Rain drops and splashes, Flow glows gently, Comet travels inside the filled portion, and Still holds solid progress.',
    means: 'The print is running. The waterline is the reported percentage, measured across the 60 active positions only.',
    action: 'Nothing. If the printer has not reported a percentage yet, a marker replaces the percentage instead of showing a false 0 %; Still and reduced motion hold it in place.',
    rule: 'gcode_state RUNNING. mc_percent sets the waterline; spd_lvl sets the rain.',
    ends: 'FINISH → Collect, PAUSE → Paused, print_error → Error, FAILED → Stopped.',
    keys: ['water', 'rest', 'deep', 'drop', 'splash'],
    rows: [['0 %', 'printing', 0], ['25 %', 'printing', 25], ['50 %', 'printing', 50],
           ['75 %', 'printing', 75], ['100 %', 'printing', 100], ['% unknown', 'printing', null]] },
  { key: 'paused', title: 'Paused', glyph: '❚❚',
    see: 'The whole active region breathes slowly; both ends carry steady marks.',
    means: 'The printer is paused: by a person, a filament runout, or a printer check.',
    action: 'Go and look at the printer. Resume it there; the wall cannot.',
    rule: 'gcode_state PAUSE or PAUSED. Alarm floor: stays visible in quiet mode and at low brightness. Ripples never touch it.',
    ends: 'RUNNING resumes → Printing.',
    keys: ['pause', 'pause_mark'] },
  { key: 'error', title: 'Error', glyph: '⚠',
    see: 'Deeper, faster breathing than pause, with steady end marks. It never strobes.',
    means: 'The printer reports a print error. The wall shows that an error exists; it does not know the cause.',
    action: 'Inspect the printer and read the message on its screen. Do not rely on the wall for a diagnosis.',
    rule: 'print_error is non-zero, in any gcode_state. Overrides everything else. HMS warnings alone do not count. Alarm floor applies; ripples never touch it.',
    ends: 'Dismissing the error on the printer clears print_error → Stopped (or the state the printer reports).',
    keys: ['error', 'error_mark'] },
  { key: 'finished', title: 'Collect', glyph: '✓',
    see: 'A smooth 12-second hue wash, then a held collect colour.',
    means: 'The print finished and the part is still on the bed. The wash plays only when the wall watched the print reach 100 % live; a FINISH seen at startup or after a reconnect goes straight to the held colour.',
    action: 'Collect the part. Opening the door, or pressing Mark collected in the studio, returns the bay to Available.',
    rule: 'gcode_state FINISH. Celebration requires a witnessed RUNNING → FINISH with continuous fresh telemetry; it never replays on restart.',
    ends: 'The door opens (home_flag bit 23) or someone presses Mark collected → Available.',
    keys: ['collect', 'hue_low', 'hue_high'] },
  { key: 'stopped', title: 'Stopped early', glyph: '■',
    see: 'Still, with dimmer ends. Darker than Available, and it never breathes.',
    means: 'The print was cancelled, or a failure was dismissed on the printer. Something may still be on the bed.',
    action: 'Clear the bed. The door, or Mark collected, returns the bay to Available.',
    rule: 'gcode_state FAILED with print_error cleared. Bambu keeps FAILED until the next job starts.',
    ends: 'The door opens or Mark collected → Available; or a new job.',
    keys: ['stopped'] },
  { key: 'offline', title: 'Offline', glyph: '◯',
    see: 'A dim double-thump heartbeat. No percentage is ever shown.',
    means: 'No fresh telemetry: the link is down, no report has arrived, the last report is older than two minutes, or its timestamp is in the future.',
    action: 'Check that the printer is on and on the network. The last known state is deliberately not shown.',
    rule: 'freshness is an input, never inferred: MQTT link down, no timestamp yet, age > 120 s, or a future timestamp.',
    ends: 'The next fresh report.',
    keys: ['offline'] },
  { key: 'unknown', title: 'Unknown', glyph: '?',
    see: 'Dim dashes with a slow pulse.',
    means: 'The printer is connected and fresh, but its state is not one the wall recognises.',
    action: 'Look at the printer, and tell whoever maintains the wall which state it shows so it can be mapped.',
    rule: 'Connected and fresh, but gcode_state is not in the mapping table. Deliberately not "available".',
    ends: 'A recognised gcode_state.',
    keys: ['unknown'] },
];

const IDENTIFY_DOC = {
  title: 'Identify (not a printer state)',
  rule: 'The Identify rope button, or Walk the wall. Server-side and bounded to 5 seconds; it stays inside the 60 active positions.',
  look: 'Three pulses with a bright core sweeping along the rope, then the rope goes straight back to its state. Cannot be previewed here; press Identify in the studio to see it on a real rope. A controller acknowledgement means the command arrived, not that the rope looks right.',
  keys: ['ident_body', 'ident_core'],
};

const MAPPING = [
  ['RUNNING', 'Printing', 'mc_percent → waterline; spd_lvl → rain tempo and tail'],
  ['PREPARE, SLICING', 'Preparing', 'whatever the printer does before RUNNING; the wall does not read stage codes and never guesses from temperatures'],
  ['PAUSE, PAUSED', 'Paused', ''],
  ['FINISH, FINISHED', 'Collect', 'celebration only if the completion was witnessed live; held until door / Mark collected'],
  ['FAILED + print_error ≠ 0', 'Error', 'the error itself, until dismissed'],
  ['FAILED + print_error = 0', 'Stopped early', 'cancel or dismissed failure; bed may still hold a part'],
  ['IDLE, READY', 'Available', ''],
  ['OFFLINE', 'Offline', ''],
  ['any state + print_error ≠ 0', 'Error', 'error outranks every state'],
  ['anything else', 'Unknown', ''],
  ['no report for 120 s, no timestamp, or link down', 'Offline', 'freshness is an input, never inferred'],
  ['home_flag bit 23 rises (door opens) on Collect / Stopped', 'Available', 'the door is the collection gate; Mark collected is the fallback for printers without a door sensor'],
];

/* --------------------------------------------------------------- network */

async function api(path, data) {
  const options = data === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': (live && live.csrf) || '' },
    body: JSON.stringify(data),
  };
  const response = await fetch('/api/' + path, options);
  let body = {};
  try { body = await response.json(); } catch (e) { body = {}; }
  if (!response.ok) throw new Error(body.error || ('HTTP ' + response.status));
  return body;
}

function decodeFilm(payload) {
  const pixels = payload.pixels;
  const ropes = payload.ropes.map(rope => rope.map(runs => {
    const buffer = new Uint8Array(pixels * 3);
    let at = 0;
    for (const [count, index] of runs) {
      const colour = payload.palette[index];
      for (let k = 0; k < count && at < pixels; k++, at++) {
        buffer[at * 3] = colour[0]; buffer[at * 3 + 1] = colour[1]; buffer[at * 3 + 2] = colour[2];
      }
    }
    return buffer;
  }));
  return { ropes, pixels, dt: payload.dt, count: payload.frames, bays: payload.bays || null,
           statusStart: payload.status_start || 0, accentStart: payload.accent_start,
           startedAt: performance.now() / 1000 };
}

function makePlayer(load) { return { film: null, busy: false, failed: false, load, nextStart: 0, lastAsk: 0 }; }

function frameIndex(player) {
  if (!player.film) return -1;
  const elapsed = performance.now() / 1000 - player.film.startedAt;
  return clamp(Math.round(elapsed / player.film.dt), 0, player.film.count - 1);
}

async function keepFed(player, force) {
  if (player.busy) return;
  const now = performance.now() / 1000;
  const nearEnd = !player.film || frameIndex(player) >= player.film.count - 3;
  if (!force && !nearEnd) return;
  if (!force && now - player.lastAsk < 0.25) return;
  player.busy = true; player.lastAsk = now;
  try {
    const payload = await player.load(player);
    player.film = decodeFilm(payload);
    player.failed = false;
  } catch (error) {
    player.failed = true;
    $('status').textContent = 'Preview unavailable: ' + error.message;
  } finally { player.busy = false; }
}

/* The preview endpoint wants exactly one bay per physical slot (seven). Each
 * group fills seven bays with the states this page needs and each card reads
 * the rope it was assigned. */
const GROUPS = [
  [['idle', null], ['preparing', null], ['printing', 25], ['paused', null], ['error', null], ['finished', 100], ['stopped', null]],
  [['offline', null], ['unknown', null], ['printing', 0], ['printing', 50], ['printing', 75], ['printing', null], ['printing', 100]],
];
const canvases = [];          // { canvas, group, rope } for preview cards
const liveCanvases = [];      // { canvas, rope } for the live wall

function slotCount() { return (live && live.config && live.config.slots.length) || 7; }

function groupBays(group) {
  const count = slotCount();
  const bays = [];
  for (let i = 0; i < count; i++) {
    const [state, percent] = group[i % group.length];
    bays.push({ state, percent });
  }
  return bays;
}

const previewPlayers = GROUPS.map(group => makePlayer(async player => {
  const still = settings.reduced_motion;
  const frames = still ? 2 : 24;
  const fps = still ? 2 : 12;
  const payload = await api('preview', {
    bays: groupBays(group), settings, time: player.nextStart, frames, fps,
    accents: Array.from({ length: slotCount() }, () => ({ mode: 'rainbow', color: [255, 255, 255], brightness: 100 })),
  });
  player.nextStart += frames / fps;
  return payload;
}));

const livePlayer = makePlayer(() => {
  const still = settings.reduced_motion;
  return api('film?frames=' + (still ? 2 : 12) + '&fps=' + (still ? 2 : 12));
});

/* --------------------------------------------------------------- drawing */

/* Horizontal reference strip: wire (physical 0) on the left, cap on the right.
 * The inactive foot is drawn as an unlit section so the geometry is visible. */
function paint(canvas, buffer, pixels, statusStart, accentStart) {
  if (canvas.width !== pixels) { canvas.width = pixels; canvas.height = 3; }
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(pixels, 3);
  for (let i = 0; i < pixels; i++) {
    const foot = i < statusStart;
    const boundary = i === statusStart || i === accentStart;
    for (let row = 0; row < 3; row++) {
      const o = (row * pixels + i) * 4;
      if (foot) {
        image.data[o] = 19; image.data[o + 1] = 31; image.data[o + 2] = 38; image.data[o + 3] = 255;
        continue;
      }
      image.data[o] = Math.min(255, buffer[i * 3] * SCREEN_GAIN);
      image.data[o + 1] = Math.min(255, buffer[i * 3 + 1] * SCREEN_GAIN);
      image.data[o + 2] = Math.min(255, buffer[i * 3 + 2] * SCREEN_GAIN);
      image.data[o + 3] = 255;
      if (boundary && row !== 1) { image.data[o] = 147; image.data[o + 1] = 167; image.data[o + 2] = 174; }
    }
  }
  ctx.putImageData(image, 0, 0);
}

function drawAll() {
  for (const { canvas, group, rope } of canvases) {
    const player = previewPlayers[group];
    const index = frameIndex(player);
    if (index < 0 || !player.film.ropes[rope]) continue;
    paint(canvas, player.film.ropes[rope][index], player.film.pixels,
          player.film.statusStart, player.film.accentStart);
  }
  const index = frameIndex(livePlayer);
  if (index >= 0) {
    for (const { canvas, rope } of liveCanvases) {
      if (livePlayer.film.ropes[rope]) {
        paint(canvas, livePlayer.film.ropes[rope][index], livePlayer.film.pixels,
              livePlayer.film.statusStart, livePlayer.film.accentStart);
      }
    }
  }
  previewPlayers.forEach(p => keepFed(p, false));
  keepFed(livePlayer, false);
  requestAnimationFrame(drawAll);
}

/* ------------------------------------------------------------------ build */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function rgb(c) { return 'rgb(' + c.join(',') + ')'; }

function themeInfo() {
  const list = (live && live.themes) || [];
  const info = list.find(t => t.name === settings.theme) || list[0] || null;
  return info ? {...info, swatches: FilamentLooks.resolve(info.palette, settings.palette_overrides || {})} : null;
}

function ropeFor(state, percent) {
  for (let g = 0; g < GROUPS.length; g++) {
    const i = GROUPS[g].findIndex(([s, p]) => s === state && p === percent);
    if (i >= 0) return { group: g, rope: i };
  }
  return null;
}

function swatchList(keys) {
  const info = themeInfo();
  const box = el('div', 'swatches');
  for (const key of keys) {
    const item = el('span', 'swatch');
    const dot = el('i');
    const colour = info && info.swatches && info.swatches[key];
    if (Array.isArray(colour)) dot.style.background = rgb(colour);
    else if (key.startsWith('hue')) dot.style.background = 'linear-gradient(90deg,#f0f,#f66)';
    else dot.style.background = 'transparent';
    item.append(dot, document.createTextNode(key));
    box.append(item);
  }
  return box;
}

function newCanvas() {
  const canvas = el('canvas', 'rope');
  canvas.width = 100; canvas.height = 3;
  return canvas;
}

function buildCards() {
  const host = $('cards');
  host.replaceChildren();
  canvases.length = 0;
  for (const doc of STATE_DOCS) {
    const card = el('article', 'card ' + doc.key);
    const h = el('h2');
    h.append(el('span', 'glyph', doc.glyph), document.createTextNode(doc.title), el('span', 'key', doc.key));
    card.append(h);
    const rows = doc.rows || [[null, doc.key, doc.key === 'finished' ? 100 : null]];
    for (const [tag, state, percent] of rows) {
      const row = el('div', 'rope-row');
      row.append(el('span', 'tag', tag || 'preview'));
      const canvas = newCanvas();
      row.append(canvas);
      card.append(row);
      const where = ropeFor(state, percent);
      if (where) canvases.push({ canvas, group: where.group, rope: where.rope });
    }
    const dl = el('dl');
    dl.append(el('dt', '', 'What you see'), el('dd', '', doc.see));
    dl.append(el('dt', '', 'What it means'), el('dd', '', doc.means));
    dl.append(el('dt', '', 'What to do'), el('dd', 'action', doc.action));
    card.append(dl);
    const tech = el('details');
    tech.append(el('summary', '', 'Technical detail'));
    tech.append(el('p', 'key', 'state key: ' + doc.key));
    tech.append(el('p', '', 'Trigger: ' + doc.rule));
    tech.append(el('p', '', 'Ends when: ' + doc.ends));
    tech.append(swatchList(doc.keys));
    card.append(tech);
    host.append(card);
  }
  const ident = el('article', 'card');
  ident.append(el('h2', '', IDENTIFY_DOC.title));
  const dl = el('dl');
  dl.append(el('dt', '', 'What you see'), el('dd', '', IDENTIFY_DOC.look));
  dl.append(el('dt', '', 'What it means'), el('dd', '', IDENTIFY_DOC.rule));
  dl.append(el('dt', '', 'What to do'), el('dd', 'action', 'Confirm the rope that flashed is the one in front of you; use Configure → Walk the wall to record the order.'));
  ident.append(dl, swatchList(IDENTIFY_DOC.keys));
  host.append(ident);
}

function buildMapping() {
  const body = $('mapping');
  body.replaceChildren();
  for (const [says, state, note] of MAPPING) {
    const tr = el('tr');
    const td = el('td'); td.append(el('code', '', says));
    tr.append(td, el('td', '', state), el('td', '', note));
    body.append(tr);
  }
}

function buildPalette() {
  const info = themeInfo();
  const host = $('palette');
  host.replaceChildren();
  if (!info) return;
  for (const [key, colour] of Object.entries(info.swatches)) {
    if (!Array.isArray(colour)) continue;
    const item = el('span', 'swatch');
    const dot = el('i'); dot.style.background = rgb(colour);
    item.append(dot, document.createTextNode(key + ' ' + colour.join(',')));
    host.append(item);
  }
}

const SPEED_TEXT = { silent: 'Silent', standard: 'Standard', sport: 'Sport', ludicrous: 'Ludicrous' };
const STATE_TITLE = Object.fromEntries(STATE_DOCS.map(d => [d.key, d.title]));
const STATE_GLYPH = Object.fromEntries(STATE_DOCS.map(d => [d.key, d.glyph]));

function buildLive() {
  const host = $('live');
  host.replaceChildren();
  liveCanvases.length = 0;
  if (!live || !live.config) return;
  live.config.slots.forEach((slot, position) => {
    const status = live.printers[slot.printer] || {};
    const row = el('div', 'rope-row');
    const tag = el('span', 'tag');
    const link = el('a', '', slot.label);
    link.href = '/?focus=' + encodeURIComponent(slot.node);
    link.title = 'Open ' + slot.label + ' on the Live wall';
    tag.append(link, document.createTextNode(' · bay ' + (position + 1) + ' · ' + slot.node));
    const st = el('span', 'st ' + (status.state || 'unknown'));
    const bits = [(STATE_GLYPH[status.state] || '') + ' ' + (STATE_TITLE[status.state] || status.state || 'no data')];
    if (status.state === 'printing') {
      bits.push(status.percent !== null && status.percent !== undefined ? Math.round(status.percent) + ' %' : '% not reported');
    }
    if (SPEED_TEXT[status.speed]) bits.push(SPEED_TEXT[status.speed] + (status.speed_percent ? ' ' + status.speed_percent + '%' : ''));
    if (status.state === 'offline') bits.push(status.reason_text || 'no fresh data');
    st.textContent = bits.join(' · ');
    tag.append(st);
    const canvas = newCanvas();
    row.append(tag, canvas);
    host.append(row);
    liveCanvases.push({ canvas, rope: position });
  });
}

function buildThemeSelect() {
  const select = $('theme');
  select.replaceChildren();
  for (const theme of (live.themes || [])) {
    const option = el('option', '', theme.label);
    option.value = theme.name;
    select.append(option);
  }
  select.value = settings.theme;
}

function describeGeometry() {
  if (!live) return;
  const foot = live.inactive_positions || 0;
  const active = live.status_positions;
  const cap = live.accent_positions;
  $('scale-foot').textContent = 'bottom ' + foot + ' · always dark';
  $('scale-active').textContent = active + ' active positions · 0–100 % fills 0–' + active;
  $('scale-cap').textContent = 'cap ' + cap;
  $('scale-foot').style.flexGrow = String(foot);
  $('scale-active').style.flexGrow = String(active);
  $('scale-cap').style.flexGrow = String(cap);
  $('scale-foot').dataset.short = foot + ' dark';
  $('scale-active').dataset.short = active + ' active';
  $('scale-cap').dataset.short = 'cap ' + cap;
}

function resetPreviews() {
  for (const player of previewPlayers) { player.nextStart = 0; keepFed(player, true); }
}

/* ------------------------------------------------------------------- boot */

async function poll() {
  try {
    const data = await api('state');
    const first = !live;
    live = data;
    if (first) {
      settings.theme = (data.config && data.config.settings.theme) || settings.theme;
      settings.palette_overrides = data.config.settings.palette_overrides || {};
      settings.printing_motion = data.config.settings.printing_motion || 'rain';
      buildThemeSelect();
      describeGeometry();
      buildCards();
      buildMapping();
      buildPalette();
      resetPreviews();
      keepFed(livePlayer, true);
      requestAnimationFrame(drawAll);
    }
    buildLive();
    const wall = data.config ? data.config.settings.theme : '?';
    $('status').textContent = (data.demo ? 'Demo · simulated printers' : 'Connected to the wall') +
      ' · wall theme: ' + wall + (Object.keys(data.config.settings.palette_overrides || {}).length ? ' (custom)' : '') + (wall !== settings.theme ? ' · previewing: ' + settings.theme : '');
    $('live-summary').textContent = data.demo ? 'Compare with the demo wall (simulated printers)' : 'Compare with the wall right now';
    $('live-caption').textContent = data.demo
      ? 'Demo frames from the simulated printers, in physical order. Select a name to open that printer on the Live wall.'
      : 'Frames from the wall, in physical order, with each bay\'s state and speed profile. Select a name to open that printer on the Live wall.';
  } catch (error) {
    $('status').textContent = 'Cannot reach the studio: ' + error.message;
  }
  setTimeout(poll, 2000);
}

$('theme').addEventListener('change', () => {
  settings.theme = $('theme').value;
  settings.palette_overrides = settings.theme === live.config.settings.theme ? live.config.settings.palette_overrides || {} : {};
  buildCards(); buildPalette(); resetPreviews();
});
$('brightness').addEventListener('input', () => {
  settings.brightness = Number($('brightness').value);
  $('brightness-out').textContent = settings.brightness + '%';
  resetPreviews();
});
$('still').addEventListener('change', () => {
  settings.reduced_motion = $('still').checked;
  resetPreviews();
});

poll();
