/* States & animations reference. Read-only: it only ever calls the preview and
 * film endpoints, so nothing here can change the wall. */
'use strict';

const $ = id => document.getElementById(id);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const SCREEN_GAIN = 1.8;

let live = null;          // last /api/state payload (csrf, themes, config, printers)
let settings = { theme: 'pink', brightness: 100, reduced_motion: false };

/* ------------------------------------------------------------- reference */

const STATE_DOCS = [
  { key: 'idle', title: 'Available',
    rule: 'gcode_state IDLE or READY with fresh telemetry, or a finished / stopped bay after collection',
    look: 'One solid resting colour. No motion, so it costs nothing on the wire.',
    ends: 'Any other state. A new job takes it to Preparing or Printing.',
    keys: ['idle'] },
  { key: 'preparing', title: 'Preparing',
    rule: 'gcode_state PREPARE or SLICING. Never inferred from temperatures.',
    look: 'A dark body breathes slowly while a light sweep rises up the rope. Two runs change per tick.',
    ends: 'RUNNING → Printing.',
    keys: ['prep_low', 'prep_high', 'prep'] },
  { key: 'printing', title: 'Printing',
    rule: 'gcode_state RUNNING. mc_percent sets the waterline; spd_lvl sets the rain.',
    look: 'The printed share is water; the remainder is six bands darkening towards the top. A drop falls from the top into the water and splashes. Faster Bambu profiles rain faster, leave a comet tail and splash wider. If the printer has not reported a percentage yet, a marker travels the rope instead of a false 0%.',
    ends: 'FINISH → Collect, PAUSE → Paused, print_error → Error, FAILED → Stopped.',
    keys: ['water', 'rest', 'deep', 'drop', 'splash'],
    rows: [['0 %', 'printing', 0], ['25 %', 'printing', 25], ['60 %', 'printing', 60],
           ['95 %', 'printing', 95], ['% unknown', 'printing', null]] },
  { key: 'paused', title: 'Paused',
    rule: 'gcode_state PAUSE.',
    look: 'The body breathes slowly; both ends carry steady marks. Alarm floor: stays visible in quiet mode and at low brightness. Ripples never touch it.',
    ends: 'RUNNING resumes → Printing.',
    keys: ['pause', 'pause_mark'] },
  { key: 'error', title: 'Error',
    rule: 'print_error is non-zero, in any gcode_state. Overrides everything else. HMS warnings alone do not count.',
    look: 'Deeper, faster breathing than pause, with steady end marks. Alarm floor applies. Ripples never touch it.',
    ends: 'Dismissing the error on the printer clears print_error → Stopped (or the state the printer reports).',
    keys: ['error', 'error_mark'] },
  { key: 'finished', title: 'Collect',
    rule: 'gcode_state FINISH. If the wall watched the print reach 100 % live, it celebrates first; a FINISH seen at startup goes straight to the collect colour.',
    look: 'A 12 s smooth hue wash (one colour per tick, sweeping the theme’s hue range) cross-fades into the collect colour, which is then held still.',
    ends: 'The door opens (home_flag bit 23) or someone presses Mark collected → Available.',
    keys: ['collect', 'hue_low', 'hue_high'] },
  { key: 'stopped', title: 'Stopped early',
    rule: 'gcode_state FAILED with print_error cleared: a cancelled print, or a failure that has been dismissed. Bambu keeps FAILED until the next job starts.',
    look: 'Still, with dimmer ends. Darker than Available, lighter than nothing else that is static, and it never breathes.',
    ends: 'The door opens or Mark collected → Available; or a new job.',
    keys: ['stopped'] },
  { key: 'offline', title: 'Offline',
    rule: 'No fresh telemetry: MQTT link down, no timestamp yet, last report older than 120 s, or a timestamp from the future.',
    look: 'A dim double-thump heartbeat. It never shows a stale percentage.',
    ends: 'The next fresh report.',
    keys: ['offline'] },
  { key: 'unknown', title: 'Unknown',
    rule: 'Connected and fresh, but the gcode_state is not one the wall recognises.',
    look: 'Dim dashes with a slow pulse. Deliberately not “available”.',
    ends: 'A recognised gcode_state.',
    keys: ['unknown'] },
];

const IDENTIFY_DOC = {
  title: 'Identify (not a printer state)',
  rule: 'The Identify rope button, or Walk the wall. Server-side and bounded to 5 seconds.',
  look: 'Three pulses with a bright core sweeping along the rope, then the rope goes straight back to its state. Cannot be previewed here; press Identify in the Studio to see it on a real rope.',
  keys: ['ident_body', 'ident_core'],
};

const MAPPING = [
  ['RUNNING', 'Printing', 'mc_percent → waterline; spd_lvl → rain tempo and tail'],
  ['PREPARE, SLICING', 'Preparing', 'heating and levelling included; temperatures are never used to guess this'],
  ['PAUSE, PAUSED', 'Paused', ''],
  ['FINISH, FINISHED', 'Collect', 'celebration only if the completion was witnessed live; held until door / Mark collected'],
  ['FAILED + print_error ≠ 0', 'Error', 'the error itself, until dismissed'],
  ['FAILED + print_error = 0', 'Stopped early', 'cancel or dismissed failure; bed still holds a part'],
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
  [['offline', null], ['unknown', null], ['printing', 0], ['printing', 60], ['printing', 95], ['printing', null], ['idle', null]],
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

function paint(canvas, buffer, pixels) {
  if (canvas.width !== pixels) { canvas.width = pixels; canvas.height = 1; }
  const ctx = canvas.getContext('2d');
  const image = ctx.createImageData(pixels, 1);
  for (let i = 0; i < pixels; i++) {
    image.data[i * 4] = Math.min(255, buffer[i * 3] * SCREEN_GAIN);
    image.data[i * 4 + 1] = Math.min(255, buffer[i * 3 + 1] * SCREEN_GAIN);
    image.data[i * 4 + 2] = Math.min(255, buffer[i * 3 + 2] * SCREEN_GAIN);
    image.data[i * 4 + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
}

function drawAll() {
  for (const { canvas, group, rope } of canvases) {
    const player = previewPlayers[group];
    const index = frameIndex(player);
    if (index < 0 || !player.film.ropes[rope]) continue;
    paint(canvas, player.film.ropes[rope][index], player.film.pixels);
  }
  const index = frameIndex(livePlayer);
  if (index >= 0) {
    for (const { canvas, rope } of liveCanvases) {
      if (livePlayer.film.ropes[rope]) paint(canvas, livePlayer.film.ropes[rope][index], livePlayer.film.pixels);
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
  return list.find(t => t.name === settings.theme) || list[0] || null;
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
    if (colour) dot.style.background = rgb(colour);
    else if (key.startsWith('hue')) dot.style.background = 'linear-gradient(90deg,#f0f,#f66)';
    else dot.style.background = 'transparent';
    item.append(dot, document.createTextNode(key));
    box.append(item);
  }
  return box;
}

function buildCards() {
  const host = $('cards');
  host.replaceChildren();
  canvases.length = 0;
  for (const doc of STATE_DOCS) {
    const card = el('article', 'card');
    const h = el('h2', '', doc.title);
    h.append(el('span', 'key', doc.key));
    card.append(h, el('p', 'rule', doc.rule));
    const rows = doc.rows || [[null, doc.key, doc.key === 'finished' ? 100 : null]];
    for (const [tag, state, percent] of rows) {
      const row = el('div', 'rope-row');
      row.append(el('span', 'tag', tag || 'preview'));
      const canvas = el('canvas', 'rope');
      canvas.width = 100; canvas.height = 1;
      row.append(canvas);
      card.append(row);
      const where = ropeFor(state, percent);
      if (where) canvases.push({ canvas, group: where.group, rope: where.rope });
    }
    const dl = el('dl');
    dl.append(el('dt', '', 'Animation'), el('dd', '', doc.look));
    dl.append(el('dt', '', 'Ends when'), el('dd', '', doc.ends));
    card.append(dl, swatchList(doc.keys));
    host.append(card);
  }
  const ident = el('article', 'card');
  ident.append(el('h2', '', IDENTIFY_DOC.title), el('p', 'rule', IDENTIFY_DOC.rule));
  const dl = el('dl');
  dl.append(el('dt', '', 'Animation'), el('dd', '', IDENTIFY_DOC.look));
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
    const item = el('span', 'swatch');
    const dot = el('i'); dot.style.background = rgb(colour);
    item.append(dot, document.createTextNode(key + ' ' + colour.join(',')));
    host.append(item);
  }
}

const SPEED_TEXT = { silent: 'Silent', standard: 'Standard', sport: 'Sport', ludicrous: 'Ludicrous' };
const STATE_TITLE = Object.fromEntries(STATE_DOCS.map(d => [d.key, d.title]));

function buildLive() {
  const host = $('live');
  host.replaceChildren();
  liveCanvases.length = 0;
  if (!live || !live.config) return;
  live.config.slots.forEach((slot, position) => {
    const status = live.printers[slot.printer] || {};
    const row = el('div', 'rope-row');
    const tag = el('span', 'tag');
    const name = el('b', '', slot.label);
    tag.append(name, document.createTextNode(' · ' + slot.printer + ' · ' + slot.node));
    tag.append(el('br'));
    const bits = [STATE_TITLE[status.state] || status.state || '—'];
    if (status.state === 'printing' && status.percent !== null && status.percent !== undefined) bits.push(Math.round(status.percent) + ' %');
    if (SPEED_TEXT[status.speed]) bits.push(SPEED_TEXT[status.speed] + (status.speed_percent ? ' ' + status.speed_percent + '%' : ''));
    tag.append(document.createTextNode(bits.join(' · ')));
    const canvas = el('canvas', 'rope');
    canvas.width = 100; canvas.height = 1;
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
      buildThemeSelect();
      buildCards();
      buildMapping();
      buildPalette();
      resetPreviews();
      keepFed(livePlayer, true);
      requestAnimationFrame(drawAll);
    }
    buildLive();
    const wall = data.config ? data.config.settings.theme : '?';
    $('status').textContent = (data.demo ? 'Demo · simulated printers' : 'Pi connected') +
      ' · wall theme: ' + wall + (wall !== settings.theme ? ' · previewing: ' + settings.theme : '');
  } catch (error) {
    $('status').textContent = 'Cannot reach the studio: ' + error.message;
  }
  setTimeout(poll, 2000);
}

$('theme').addEventListener('change', () => {
  settings.theme = $('theme').value;
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
