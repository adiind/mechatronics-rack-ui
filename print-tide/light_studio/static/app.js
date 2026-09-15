'use strict';
/* Filament wall · browser client.
 *
 * Two rules shape this file:
 *   1. It renders no light itself. Every rope pixel it draws came from the
 *      Python renderer as a run-length encoded filmstrip, so the screen and the
 *      wall cannot drift apart. There is no JavaScript animation of state.
 *   2. It never turns printer text into markup. Everything user- or
 *      printer-supplied goes through textContent or an attribute.
 *
 * Views: Live wall (one installation stage, one inspector with the camera
 * image), Configure (mapping, names, order, direction, caps) and Animation lab
 * (simulation plus theme and wall settings). All three share one draft; the
 * save bar exists only while the draft differs from the wall.
 */

const $ = id => document.getElementById(id);
const clone = o => JSON.parse(JSON.stringify(o));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/* Word, one-line meaning, glyph. The glyph is a shape, never only a colour. */
const STATE_TEXT = {
  idle:      ['Available',   'Free for the next print.', '·'],
  preparing: ['Preparing',   'The printer is getting the job ready.', '↑'],
  printing:  ['Printing',    'The print is running; the light fills upward as it progresses.', '≈'],
  paused:    ['Paused',      'Paused on the printer. Needs a look.', '❚❚'],
  error:     ['Error',       'The printer reports an error. Inspect the printer; the wall cannot diagnose it.', '⚠'],
  finished:  ['Ready to collect', 'The print finished and the part is still on the bed.', '✓'],
  stopped:   ['Stopped early', 'Cancelled, or a failure dismissed on the printer. The bed may still hold a part.', '■'],
  offline:   ['Offline',     'No fresh telemetry from this printer.', '◯'],
  unknown:   ['Unknown',     'Connected, but the reported state is not one the wall recognises.', '?'],
};
const SIM_STATES = Object.keys(STATE_TEXT);
/* Short state words for the stage labels, where width is scarce. The
 * inspector always uses the full word. */
const STATE_SHORT = { finished: 'Collect', stopped: 'Stopped', preparing: 'Preparing' };
/* States that need a person, in the order they are listed. Collect is a
 * normal outcome and is counted in the summary rather than shouted. */
const URGENT = ['error', 'paused', 'stopped', 'offline', 'unknown'];

// Bambu speed profiles as the printer reports them (spd_lvl 1-4).
const SPEED_TEXT = { silent: 'Silent', standard: 'Standard', sport: 'Sport', ludicrous: 'Ludicrous' };

const ACCENT_MODES = {
  white: 'Pure white',
  color: 'Solid colour',
  rainbow: 'Hue sweep (theme)',
};

/* Colour helpers. No regular expressions anywhere in this file. */
function toHex(channels) {
  let out = '#';
  for (const value of channels) {
    const clamped = Math.max(0, Math.min(255, Math.round(value)));
    out += clamped.toString(16).padStart(2, '0');
  }
  return out;
}

function fromHex(text) {
  const body = text.charAt(0) === '#' ? text.slice(1) : text;
  return [0, 2, 4].map(i => {
    const value = parseInt(body.slice(i, i + 2), 16);
    return Number.isFinite(value) ? value : 0;
  });
}

function accentSummary(accent) {
  if (accent.mode === 'rainbow') return 'rainbow';
  if (accent.mode === 'color') return 'colour ' + toHex(accent.color);
  return 'white';
}

/* Scenarios cycle their pattern to however many bays the wall actually has. */
function scenario(pattern) {
  return count => Array.from({ length: count }, (unused, i) => ({
    state: pattern[i % pattern.length][0],
    percent: pattern[i % pattern.length][1],
  }));
}

const SCENARIOS = {
  'Busy afternoon': scenario([['printing', 18], ['printing', 64], ['idle', null],
                              ['preparing', 0], ['printing', 91], ['finished', 100], ['idle', null]]),
  'Quiet night': scenario([['idle', null]]),
  'Needs attention': scenario([['error', 37], ['paused', 52], ['printing', 73],
                               ['idle', null], ['offline', null], ['unknown', null], ['finished', 100]]),
  'Progress check': scenario([['printing', 0], ['printing', 25], ['printing', 50],
                              ['printing', 75], ['printing', 100], ['printing', null], ['idle', null]]),
  'Every state': scenario(SIM_STATES.map((s, i) => [s, 12 + i * 11])),
};
const DEFAULT_SCENARIO = 'Busy afternoon';

let live = null;           // last /api/state payload
let media = null;          // last /api/media payload
let draft = null;          // editable layout (mapping + wall settings)
let dirty = false;
let busy = false;
let tab = 'live';
let dragFrom = null;
let toastTimer = null;
let renaming = null;       // node id currently being renamed
let sim = SCENARIOS[DEFAULT_SCENARIO](7);
let simName = DEFAULT_SCENARIO;
let walk = { active: false, index: 0 };
const accentOpen = new Set();   // node ids whose cap panel is expanded
let labAccent = { mode: 'white', color: [255, 255, 255], brightness: 100 };
let selected = null;            // selected printer identity (never a bay index)
let userChoseSelection = false;
let focusNode = null;           // ?focus=nodeNN from a reference page
let lastPhotoKey = null;        // which image the inspector currently shows

/* ---------------------------------------------------------------- helpers */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function toast(text) {
  const box = $('toast');
  box.textContent = text;
  box.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove('show'), 5200);
}

async function api(path, data) {
  const options = data === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': (live && live.csrf) || '' },
    body: JSON.stringify(data),
  };
  const response = await fetch('/api/' + path, options);
  let body = {};
  try { body = await response.json(); } catch (e) { body = {}; }
  if (!response.ok) throw new Error(body.error || 'The Pi refused that request');
  return body;
}

function fmt(value, digits) {
  return value === null || value === undefined ? null : Number(value).toFixed(digits || 0);
}

function minutesText(minutes) {
  if (minutes === null || minutes === undefined) return null;
  const total = Math.round(minutes);
  if (total < 60) return total + ' min left';
  return Math.floor(total / 60) + ' h ' + String(total % 60).padStart(2, '0') + ' min left';
}

function ageText(seconds) {
  if (seconds === null || seconds === undefined) return null;
  if (seconds < 60) return 'just now';
  if (seconds < 5400) return Math.round(seconds / 60) + ' min ago';
  if (seconds < 172800) return Math.round(seconds / 3600) + ' h ago';
  return Math.round(seconds / 86400) + ' days ago';
}

function clockText(epoch) {
  if (!epoch) return '';
  const d = new Date(epoch * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  return sameDay ? time : d.toLocaleDateString([], { day: 'numeric', month: 'short' }) + ' ' + time;
}

function stateWord(status) {
  if (!status || !status.state) return 'No data';
  if (status.collected) return 'Available';
  return (STATE_TEXT[status.state] || STATE_TEXT.unknown)[0];
}

function stateGlyph(status) {
  if (!status || !status.state) return '?';
  return (STATE_TEXT[status.state] || STATE_TEXT.unknown)[2];
}

/* Short reading for a printer: big figure plus a small qualifier. */
function reading(status) {
  const state = status.state || 'unknown';
  const known = status.percent !== null && status.percent !== undefined;
  if (state === 'printing') {
    return known ? [Math.round(status.percent) + '%', minutesText(status.remaining_min) || 'time left not reported']
                 : ['?%', 'progress not reported yet'];
  }
  if (state === 'paused' || state === 'error') return [known ? Math.round(status.percent) + '%' : '—', 'held here'];
  if (state === 'finished') {
    return ['Ready', status.completion_observed
      ? 'finished ' + Math.round((status.completed_ago || 0) / 60) + ' min ago' : 'finish time unknown'];
  }
  if (state === 'stopped') return ['Clear bed', 'stopped early'];
  if (state === 'idle') return ['Free', status.collected ? 'collected' : 'no job'];
  if (state === 'preparing') return ['Preparing', 'getting the job ready'];
  if (state === 'offline') return ['—', status.reason_text || 'no fresh data'];
  return ['—', 'unknown state'];
}

/* ------------------------------------------------------- filmstrip player */

function decodeFilm(payload) {
  const pixels = payload.pixels;
  const ropes = payload.ropes.map(rope => rope.map(runs => {
    const buffer = new Uint8Array(pixels * 3);
    let at = 0;
    for (const [count, index] of runs) {
      const colour = payload.palette[index];
      for (let k = 0; k < count && at < pixels; k++, at++) {
        buffer[at * 3] = colour[0];
        buffer[at * 3 + 1] = colour[1];
        buffer[at * 3 + 2] = colour[2];
      }
    }
    return buffer;
  }));
  return {
    ropes, pixels,
    dt: payload.dt,
    count: payload.frames,
    bays: payload.bays || null,
    // Zone boundaries come from the server; the browser never assumes them.
    statusStart: payload.status_start || 0,
    accentStart: payload.accent_start,
    statusPositions: payload.status_positions,
    startedAt: performance.now() / 1000,
    serial: Math.random(),
  };
}

function makePlayer(load) {
  return { film: null, busy: false, failed: false, load, nextStart: 0, lastAsk: 0 };
}

function frameIndex(player) {
  if (!player.film) return -1;
  const elapsed = performance.now() / 1000 - player.film.startedAt;
  return clamp(Math.round(elapsed / player.film.dt), 0, player.film.count - 1);
}

async function keepFed(player, force) {
  if (!live || !live.csrf || player.busy) return;
  const now = performance.now() / 1000;
  const nearEnd = !player.film || frameIndex(player) >= player.film.count - 2;
  if (!force && !nearEnd) return;
  if (!force && now - player.lastAsk < 0.2) return;
  player.busy = true;
  player.lastAsk = now;
  try {
    const payload = await player.load(player);
    player.film = decodeFilm(payload);
    player.failed = false;
  } catch (error) {
    player.failed = true;
    player.lastError = error.message;
  } finally {
    player.busy = false;
  }
}

const livePlayer = makePlayer(() => {
  const still = draft && draft.settings.reduced_motion;
  return api('film?frames=' + (still ? 2 : 12) + '&fps=' + (still ? 2 : 12));
});

const labPlayer = makePlayer(async player => {
  const still = draft && draft.settings.reduced_motion;
  const frames = still ? 2 : 12;
  const fps = still ? 2 : 12;
  const payload = await api('preview', {
    bays: sim,
    settings: draft ? draft.settings : undefined,
    // Preview the *draft* caps and directions through the real compositor.
    accents: draft ? draft.slots.map(slot => slot.accent) : undefined,
    reverses: draft ? draft.slots.map(slot => slot.reverse) : undefined,
    time: player.nextStart,
    frames, fps,
  });
  player.nextStart += frames / fps;
  return payload;
});

/* ------------------------------------------------------------- rope canvas */

const scratch = document.createElement('canvas');
scratch.width = 1;
scratch.height = 100;
const scratchCtx = scratch.getContext('2d', { willReadFrequently: true });
let scratchData = scratchCtx.createImageData(1, 100);

/* LED values are capped at the wall's hardware ceiling; screens are not. The
 * amplification below is for legibility on a monitor and is not a photometric
 * match to the physical rope. */
const SCREEN_GAIN = 1.8;

function loadScratch(buffer, pixels) {
  if (scratch.height !== pixels) {
    scratch.height = pixels;
    scratchData = scratchCtx.createImageData(1, pixels);
  }
  const data = scratchData.data;
  for (let row = 0; row < pixels; row++) {
    const source = (pixels - 1 - row) * 3;     // pixel 0 (the wire) sits at the bottom
    const target = row * 4;
    data[target] = Math.min(255, buffer[source] * SCREEN_GAIN);
    data[target + 1] = Math.min(255, buffer[source + 1] * SCREEN_GAIN);
    data[target + 2] = Math.min(255, buffer[source + 2] * SCREEN_GAIN);
    data[target + 3] = 255;
  }
  scratchCtx.putImageData(scratchData, 0, 0);
}

function capsulePath(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, width, height, radius);
  else ctx.rect(x, y, width, height);
}

function dashed(ctx, x1, x2, yy, colour) {
  ctx.save();
  ctx.strokeStyle = colour;
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(x1, yy);
  ctx.lineTo(x2, yy);
  ctx.stroke();
  ctx.restore();
}

/* Physical index 0 is the data-in wire and is drawn at the BOTTOM; the accent
 * cap therefore always sits at the top and the inactive foot at the bottom,
 * whichever way the active region runs. Zone edges come from the film's
 * metadata (status_start, accent_start), never from a constant here. The rope
 * is one slender object: a dark body, the LED core, a restrained halo. */
function drawRope(ctx, buffer, pixels, x, y, width, height, accentStart, statusStart, marks, quiet) {
  loadScratch(buffer, pixels);
  const foot = statusStart || 0;
  const footEdge = y + height * (1 - foot / pixels);
  const capEdge = accentStart && accentStart < pixels ? y + height * (1 - accentStart / pixels) : null;

  ctx.save();
  ctx.fillStyle = '#120C1F';
  capsulePath(ctx, x - 3, y - 5, width + 6, height + 10, width);
  ctx.fill();
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = 0.26;
  if ('filter' in ctx) ctx.filter = 'blur(' + Math.max(4, width * 0.8).toFixed(1) + 'px)';
  ctx.drawImage(scratch, 0, 0, 1, pixels, x - width * 0.7, y - 2, width * 2.4, height + 4);
  ctx.restore();

  ctx.save();
  capsulePath(ctx, x, y, width, height, width / 2);
  ctx.clip();
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(scratch, 0, 0, 1, pixels, x, y, width, height);
  if (foot > 0) {
    // The inactive foot is part of the physical object: an unlit section.
    ctx.fillStyle = '#231A33';
    ctx.fillRect(x, footEdge, width, y + height - footEdge);
    ctx.strokeStyle = '#332846';
    ctx.lineWidth = 1;
    for (let yy = footEdge + 4; yy < y + height; yy += 5) {
      ctx.beginPath();
      ctx.moveTo(x + 2, yy);
      ctx.lineTo(x + width - 2, yy);
      ctx.stroke();
    }
  }
  ctx.restore();

  if (!quiet) {
    if (foot > 0) dashed(ctx, x - 6, x + width + 6, footEdge, '#8E82A8');
    if (capEdge !== null) dashed(ctx, x - 6, x + width + 6, capEdge, '#8E82A8');
  }
  if (marks && capEdge !== null) {
    ctx.save();
    ctx.strokeStyle = '#6B5E85';
    ctx.lineWidth = 1;
    for (const share of [0.25, 0.5, 0.75]) {
      const yy = footEdge - (footEdge - capEdge) * share;
      ctx.beginPath();
      ctx.moveTo(x + width + 3, yy);
      ctx.lineTo(x + width + 7, yy);
      ctx.stroke();
    }
    ctx.restore();
  }
  return { footEdge, capEdge };
}

function endLabels(ctx, x, y, width, height, small, edges, footShare) {
  ctx.save();
  ctx.fillStyle = '#8E82A8';
  ctx.font = (small ? 9 : 11) + 'px ui-monospace, monospace';
  ctx.textAlign = 'center';
  ctx.fillText('CAP', x + width / 2, y - 10);
  ctx.fillText('WIRE', x + width / 2, y + height + (small ? 14 : 16));
  if (edges && edges.footEdge && footShare) {
    ctx.textAlign = 'left';
    ctx.fillStyle = '#6B5E85';
    ctx.font = (small ? 8 : 10) + 'px ui-monospace, monospace';
    const mid = (edges.footEdge + y + height) / 2;
    ctx.fillText('OFF', x + width + 9, mid - 2);
    ctx.fillText(footShare, x + width + 9, mid + (small ? 8 : 10));
  }
  ctx.restore();
}

/* A rope drawn lengthways: wire at the left, cap at the right. For the phone
 * rows, where a tall rope has no room. */
function drawStrip(ctx, buffer, pixels, statusStart, accentStart, width, height) {
  const foot = statusStart || 0;
  const image = ctx.createImageData(pixels, 1);
  for (let i = 0; i < pixels; i++) {
    const o = i * 4;
    if (i < foot) { image.data[o] = 35; image.data[o + 1] = 26; image.data[o + 2] = 51; image.data[o + 3] = 255; continue; }
    image.data[o] = Math.min(255, buffer[i * 3] * SCREEN_GAIN);
    image.data[o + 1] = Math.min(255, buffer[i * 3 + 1] * SCREEN_GAIN);
    image.data[o + 2] = Math.min(255, buffer[i * 3 + 2] * SCREEN_GAIN);
    image.data[o + 3] = 255;
  }
  scratch.height = 1;
  scratch.width = pixels;
  scratch.getContext('2d').putImageData(image, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(scratch, 0, 0, pixels, 1, 0, 0, width, height);
  scratch.width = 1;
  scratch.height = 100;
  scratchData = scratchCtx.createImageData(1, 100);
  if (accentStart && accentStart < pixels) {
    ctx.fillStyle = '#8E82A8';
    ctx.fillRect(Math.round(width * accentStart / pixels), 0, 1, height);
  }
}

/* --------------------------------------------------------------- live view */

function slotByPrinter(printer) {
  return live ? live.config.slots.find(s => s.printer === printer) || null : null;
}

function positionOf(printer) {
  return live ? live.config.slots.findIndex(s => s.printer === printer) : -1;
}

function chooseDefaultSelection() {
  if (!live) return null;
  const slots = live.config.slots;
  for (const state of ['error', 'paused', 'stopped']) {
    const hit = slots.find(s => (live.printers[s.printer] || {}).state === state);
    if (hit) return hit.printer;
  }
  const printing = slots.find(s => (live.printers[s.printer] || {}).state === 'printing');
  return printing ? printing.printer : slots[0].printer;
}

function phoneLayout() { return window.matchMedia('(max-width: 700px)').matches; }

function openSheet() {
  document.body.classList.add('sheet-open');
  $('inspector').setAttribute('role', 'dialog');
  $('inspector').setAttribute('aria-modal', 'true');
  setBehindInert(true);
  $('sheet-title').textContent = (slotByPrinter(selected) || {}).label || '';
  $('inspector').scrollTop = 0;
  $('sheet-back').focus();
}

function closeSheet() {
  if (!document.body.classList.contains('sheet-open')) return;
  document.body.classList.remove('sheet-open');
  $('inspector').removeAttribute('role');
  $('inspector').removeAttribute('aria-modal');
  setBehindInert(false);
  const row = document.querySelector('.fleet-row[aria-selected="true"]');
  if (row) row.focus();
}

function select(printer, byUser) {
  if (!live || !slotByPrinter(printer)) return;
  selected = printer;
  if (byUser) userChoseSelection = true;
  updateLive();
  keepFed(livePlayer, false);
  refreshMedia();
  if (byUser && phoneLayout()) openSheet();
}

function moveSelection(delta) {
  const slots = live.config.slots;
  const at = Math.max(0, positionOf(selected));
  const next = clamp(at + delta, 0, slots.length - 1);
  select(slots[next].printer, true);
}

function buildStage() {
  const labels = $('stage-labels');
  labels.replaceChildren();
  const rows = $('fleet-rows');
  rows.replaceChildren();
  live.config.slots.forEach((slot, index) => {
    const label = el('button', 'stage-label');
    label.type = 'button';
    label.setAttribute('role', 'option');
    label.dataset.printer = slot.printer;
    label.dataset.node = slot.node;
    label.append(el('span', 'sl-name', slot.label), el('span', 'sl-state', '—'),
                 el('span', 'sl-reading', '—'), el('span', 'sl-sub', ''));
    label.addEventListener('click', () => select(slot.printer, true));
    labels.append(label);

    const row = el('button', 'fleet-row');
    row.type = 'button';
    row.setAttribute('role', 'option');
    row.dataset.printer = slot.printer;
    row.append(el('span', 'fr-name', slot.label));
    const state = el('span', 'fr-state');
    const strip = el('canvas');
    strip.width = 100;
    strip.height = 1;
    strip.setAttribute('aria-hidden', 'true');
    state.append(el('span', 'fr-word', '—'), strip);
    row.append(state);
    const fr = el('span', 'fr-reading');
    fr.append(document.createTextNode('—'), el('small', '', ''));
    row.append(fr);
    row.addEventListener('click', () => select(slot.printer, true));
    rows.append(row);
  });
}

function updateLive() {
  if (!live) return;
  const slots = live.config.slots;
  if (!selected || !slotByPrinter(selected)) { selected = chooseDefaultSelection(); userChoseSelection = false; }
  else if (!userChoseSelection) selected = chooseDefaultSelection();

  const counts = {};
  const urgent = [];
  slots.forEach((slot, index) => {
    const status = live.printers[slot.printer] || {};
    const state = status.collected ? 'idle' : (status.state || 'unknown');
    counts[state] = (counts[state] || 0) + 1;
    const [figure, qualifier] = reading(status);

    const label = $('stage-labels').children[index];
    if (label) {
      label.setAttribute('aria-selected', String(slot.printer === selected));
      label.querySelector('.sl-name').textContent = slot.label;
      const st = label.querySelector('.sl-state');
      st.textContent = stateGlyph(status) + ' ' + (status.collected ? 'Available' : (STATE_SHORT[state] || stateWord(status)));
      st.className = 'sl-state ' + state;
      label.querySelector('.sl-reading').textContent = figure;
      label.querySelector('.sl-sub').textContent = qualifier;
      label.title = slot.label + ' · ' + stateWord(status) + ' · ' + figure + ' · ' + qualifier;
    }
    const row = $('fleet-rows').children[index];
    if (row) {
      row.setAttribute('aria-selected', String(slot.printer === selected));
      row.querySelector('.fr-name').textContent = slot.label;
      const st = row.querySelector('.fr-state');
      st.className = 'fr-state ' + state;
      st.querySelector('.fr-word').textContent = stateGlyph(status) + ' ' + (status.collected ? 'Available' : (STATE_SHORT[state] || stateWord(status)));
      const fr = row.querySelector('.fr-reading');
      fr.firstChild.textContent = figure;
      fr.querySelector('small').textContent = qualifier;
    }
    if (URGENT.includes(state)) urgent.push({ slot, status, state });
  });
  // Chips are listed most urgent first (the wall itself stays in physical
  // order); on a phone only the first is shown.
  urgent.sort((a, b) => URGENT.indexOf(a.state) - URGENT.indexOf(b.state));

  const parts = [];
  if (counts.printing) parts.push(counts.printing + ' printing');
  if (counts.preparing) parts.push(counts.preparing + ' preparing');
  if (counts.finished) parts.push(counts.finished + ' ready to collect');
  if (counts.idle) parts.push(counts.idle + ' available');
  const needs = urgent.length;
  $('attention-summary').textContent = (needs ? needs + ' need' + (needs === 1 ? 's' : '') + ' attention'
    : 'All quiet') + (parts.length ? ' · ' + parts.join(' · ') : '');
  const chips = $('attention-chips');
  chips.replaceChildren();
  for (const { slot, status, state } of urgent) {
    const chip = el('button', 'alert-chip ' + state);
    chip.type = 'button';
    chip.setAttribute('role', 'listitem');
    const why = state === 'error' ? 'error reported · look at the printer'
      : state === 'paused' ? 'paused · look at the printer'
      : state === 'stopped' ? 'stopped early · clear the bed'
      : state === 'offline' ? 'offline · ' + (status.reason_text || 'no fresh data')
      : 'unknown state';
    chip.textContent = stateGlyph(status) + ' ' + slot.label + ' · ' + why;
    chip.addEventListener('click', () => {
      select(slot.printer, true);
      if (!phoneLayout()) $('inspector').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    });
    chips.append(chip);
  }
  updateInspector();
}

/* One composed inspector for the selected printer. */
function updateInspector() {
  const slot = slotByPrinter(selected);
  if (!slot) return;
  const status = live.printers[slot.printer] || {};
  const state = status.collected ? 'idle' : (status.state || 'unknown');
  const index = positionOf(slot.printer);

  $('insp-bay').textContent = 'Bay ' + (index + 1) + ' of ' + live.config.slots.length;
  $('insp-name').textContent = slot.label;
  $('sheet-title').textContent = slot.label;
  const st = $('insp-state');
  st.className = 'inspector-state ' + state;
  st.querySelector('.glyph').textContent = stateGlyph(status);
  st.querySelector('.word').textContent = stateWord(status) + (status.collected ? ' · collected' : '');

  const [figure, qualifier] = reading(status);
  const progress = $('insp-progress');
  progress.replaceChildren(el('b', '', figure), el('span', '', qualifier));
  if (state === 'printing' && status.percent !== null && status.percent !== undefined) {
    const bar = el('div', 'bar');
    const fill = el('i');
    fill.style.width = clamp(status.percent, 0, 100) + '%';
    bar.append(fill);
    progress.append(bar);
  }

  const job = $('insp-job');
  job.replaceChildren();
  if (['printing', 'preparing', 'finished', 'stopped', 'paused', 'error'].includes(state)) {
    job.append(el('span', 'job-kicker', 'Job'), document.createTextNode(status.job || 'No job name reported'));
  } else {
    job.append(el('span', 'job-kicker', state === 'idle' ? 'Bed' : 'Job'),
               document.createTextNode(state === 'idle' ? (status.collected ? 'Collected here; free for the next print.' : 'Free for the next print.') : '—'));
  }

  const note = $('insp-note');
  note.className = 'inspector-note' + (state === 'error' ? ' alarm' : '');
  note.textContent = (STATE_TEXT[state] || STATE_TEXT.unknown)[1]
    + (state === 'finished' || state === 'stopped' ? ' Opening the door, or Mark collected, returns the bay to Available.' : '');

  const collect = $('insp-collect');
  collect.hidden = !(status.fresh && (state === 'finished' || state === 'stopped'));
  collect.textContent = state === 'stopped' ? 'Mark bed cleared' : 'Mark collected';

  const dl = $('insp-dl');
  dl.replaceChildren();
  const rope = live.ropes && live.ropes[slot.node];
  const identifying = live.identify && live.identify.node === slot.node;
  const rows = [
    ['Printer id', slot.printer],
    ['Layer', status.layer !== null && status.layer !== undefined
      ? status.layer + (status.total_layer ? ' / ' + status.total_layer : '') : 'not reported'],
    ['Speed profile', SPEED_TEXT[status.speed]
      ? SPEED_TEXT[status.speed] + (status.speed_percent ? ' · ' + status.speed_percent + '%' : '') : 'not reported'],
    ['Nozzle / bed', fmt(status.nozzle) === null && fmt(status.bed) === null ? 'unknown'
      : (fmt(status.nozzle) === null ? '?' : fmt(status.nozzle) + '°') + ' / ' + (fmt(status.bed) === null ? '?' : fmt(status.bed) + '°')],
    ['Telemetry', status.fresh ? 'live · last message ' + (ageText(status.age) || 'just now') : (status.reason_text || 'no fresh data')],
    ['Completion', state === 'finished'
      ? (status.completion_observed ? 'watched live by the wall' : 'retained FINISH · the wall did not observe the completion, so the finish time is unknown')
      : state === 'stopped' ? 'FAILED with the error cleared · bed not marked cleared' : '—'],
    ['Rope', slot.node + ' · ' + ropeHealthText(rope, identifying && live.identify.acked) + ' · an acknowledgement is not proof of how the LEDs look'],
    ['Geometry', 'bay ' + (index + 1) + ' · bottom ' + (live.inactive_positions || 0) + ' dark · ' + live.status_positions + ' active · cap ' + live.accent_positions + ' (' + accentSummary(slot.accent) + ') · ' + (slot.reverse ? 'fills toward the wire' : 'fills away from the wire')],
    ['Model preview', 'unavailable · the printers\' MQTT reports carry no thumbnail and no cached preview source exists on this Pi'],
  ];
  for (const [k, v] of rows) dl.append(el('dt', '', k), el('dd', '', v));
  $('insp-identify').disabled = !live.cast_enabled || busy;
  $('insp-log-link').href = '/logs#' + encodeURIComponent(slot.printer);
  updatePhoto(slot, status);
}

/* ------------------------------------------------------------ camera image */

async function refreshMedia() {
  try {
    media = await api('media');
  } catch (error) {
    media = null;
  }
  if (tab === 'live') updateInspector();
}

function updatePhoto(slot, status) {
  const frame = $('photo-frame');
  const img = $('photo-img');
  const empty = $('photo-empty');
  const emptyText = $('photo-empty-text');
  const zoom = $('photo-zoom');
  const caption = $('photo-caption');
  const info = media && media.printers ? media.printers[slot.printer] : null;
  caption.replaceChildren();
  frame.classList.remove('saved');

  frame.classList.remove('compact', 'loading');
  // Plain words first; the file-level reason is secondary.
  const showEmpty = (title, detail) => {
    img.hidden = true;
    img.removeAttribute('src');
    empty.hidden = false;
    empty.querySelector('b').textContent = title;
    emptyText.textContent = detail;
    zoom.hidden = true;
    lastPhotoKey = null;
    frame.classList.add('compact');
  };

  if (!media) {
    showEmpty('Camera image unavailable', 'The camera cache on the Pi could not be reached.');
    return;
  }
  if (!info || !info.available) {
    const status = info ? info.status : 'missing';
    if (status === 'missing') showEmpty('No camera image for this printer yet', 'The camera cache has no capture for ' + slot.label + '.');
    else showEmpty('No usable camera image', 'A file exists but it is not a usable picture (' + (info.note || 'not readable') + ').');
    return;
  }

  // Cache-bust only when the capture changes; never reload every poll.
  const key = info.url + '|' + (info.captured_at || '') + '|' + (info.file_updated_at || '') + '|' + (info.bytes || '');
  if (lastPhotoKey !== key) {
    lastPhotoKey = key;
    img.hidden = true;
    empty.hidden = false;
    frame.classList.add('loading');
    empty.querySelector('b').textContent = 'Loading camera image…';
    emptyText.textContent = '';
    zoom.hidden = true;
    img.onload = () => {
      if (lastPhotoKey !== key) return;
      if (!(img.naturalWidth > 0)) { img.onerror(); return; }
      frame.classList.remove('loading');
      img.hidden = false;
      empty.hidden = true;
      zoom.hidden = false;
    };
    img.onerror = () => {
      if (lastPhotoKey !== key) return;
      frame.classList.remove('loading');
      frame.classList.add('compact');
      img.hidden = true;
      img.removeAttribute('src');
      empty.hidden = false;
      empty.querySelector('b').textContent = 'No usable camera image';
      emptyText.textContent = 'The cached file for ' + slot.label + ' did not decode as a picture.';
      zoom.hidden = true;
      caption.replaceChildren();
    };
    img.alt = 'Camera image of ' + slot.label;
    img.src = info.url + '?v=' + encodeURIComponent(String(info.captured_at || info.file_updated_at || info.bytes || 0));
  }

  const when = info.recent ? 'Recent capture · ' + (ageText(info.age_seconds) || 'just now')
    : info.clock_skew ? 'Saved capture · time inconsistent'
    : info.time_known ? 'Saved capture · ' + clockText(info.captured_at) + ' · not live'
    : 'Saved capture · time not recorded · not live';
  const badge = el('span', 'badge-time' + (info.recent ? '' : ' saved'), when);
  caption.append(badge);
  const bits = [];
  if (live.demo) {
    // Real saved pixels from the Pi's cache, with illustrative timing. The
    // pictured object is never the simulated job.
    caption.append(el('span', 'photo-demo', 'Demo photo'));
    bits.push('real saved photo from the Pi\'s camera cache, timing illustrative · not the simulated job');
  } else if (info.job_at_capture && status.job && info.job_at_capture !== status.job) {
    bits.push('shows an earlier job: ' + info.job_at_capture);
  } else if (info.job_at_capture && !status.job) {
    bits.push('captured during ' + info.job_at_capture);
  }
  if (info.source === 'recording' && !live.demo) bits.push('from the printer\'s own recording');
  if (info.width && info.height) bits.push(info.width + '×' + info.height);
  if (bits.length) caption.append(document.createTextNode(bits.join(' · ')));
  if (!info.recent) frame.classList.add('saved');
  $('lightbox-caption').textContent = slot.label + ' · ' + when + (bits.length ? ' · ' + bits.join(' · ') : '');
}

/* The enlarged photo is a native modal dialog: Tab stays inside it, the page
 * behind is inert, Escape closes it, and focus returns to the Enlarge button. */
function openLightbox() {
  const img = $('photo-img');
  if (img.hidden || !img.src) return;
  const dialog = $('lightbox');
  $('lightbox-img').src = img.src;
  $('lightbox-img').alt = img.alt;
  if (typeof dialog.showModal === 'function') {
    if (!dialog.open) dialog.showModal();
  } else {
    dialog.setAttribute('open', '');
  }
  $('lightbox-close').focus();
}

function closeLightbox() {
  const dialog = $('lightbox');
  if (!dialog.open) return;
  if (typeof dialog.close === 'function') dialog.close(); else dialog.removeAttribute('open');
  $('lightbox-img').removeAttribute('src');
  $('photo-zoom').focus();
}

/* While the phone sheet is open everything behind it is inert, so Tab cannot
 * wander into the hidden navigation; a small trap covers browsers without
 * inert. */
const SHEET_BEHIND = ['.masthead', '.fleet-line', '#stage', '#fleet-rows', '#key', '#mapping', '#lab', '#savebar', '.colophon'];

function setBehindInert(on) {
  for (const selector of SHEET_BEHIND) {
    document.querySelectorAll(selector).forEach(node => {
      if (on) node.setAttribute('inert', ''); else node.removeAttribute('inert');
    });
  }
}

function trapSheetFocus(event) {
  if (event.key !== 'Tab' || !document.body.classList.contains('sheet-open')) return;
  const sheet = $('inspector');
  const focusable = [...sheet.querySelectorAll('button, [href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])')]
    .filter(node => !node.hidden && node.offsetParent !== null && !node.disabled);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && (document.activeElement === first || !sheet.contains(document.activeElement))) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !sheet.contains(document.activeElement))) {
    event.preventDefault(); first.focus();
  }
}

/* --------------------------------------------------------------- bay cards */

function bayChanged(index) {
  if (!live || !draft) return false;
  const drafted = draft.slots[index];
  const saved = live.config.slots[index];
  if (!saved) return true;
  return saved.node !== drafted.node || saved.printer !== drafted.printer
      || saved.reverse !== drafted.reverse || saved.label !== drafted.label
      || JSON.stringify(saved.accent) !== JSON.stringify(drafted.accent);
}

function swapPrinters(a, b) {
  if (a === b) return;
  const first = draft.slots[a];
  const second = draft.slots[b];
  [first.printer, second.printer] = [second.printer, first.printer];
  [first.label, second.label] = [second.label, first.label];
  refresh();
}

function moveBay(from, to) {
  if (to < 0 || to >= draft.slots.length) return;
  const [slot] = draft.slots.splice(from, 1);
  draft.slots.splice(to, 0, slot);
  refresh();
}

async function identify(node) {
  try {
    await api('identify', { node });
    toast(live.demo
      ? 'Demo mode: Identify is simulated and no rope was addressed.'
      : 'Identify queued for five seconds on ' + node + '. Watch the physical rope; an acknowledgement is not proof of how it looks.');
    poll();
  } catch (error) {
    toast(error.message);
  }
}

function startRename(index) {
  renaming = draft.slots[index].node;
  buildBays();
  const input = document.querySelector('.rename-input');
  if (input) { input.focus(); input.select(); }
}

function commitRename(index, value) {
  const name = (value || '').trim().slice(0, 40);
  renaming = null;
  if (name) draft.slots[index].label = name;
  refresh();
}

function accentPanel(index, slot) {
  const accent = slot.accent;
  const panel = el('div', 'accent');
  panel.hidden = !accentOpen.has(slot.node);
  panel.setAttribute('aria-label', 'Far-end cap for ' + slot.node);

  const modeLabel = el('label', 'range', 'Mode');
  const mode = el('select', 'accent-mode');
  mode.setAttribute('aria-label', 'Cap mode for ' + slot.node);
  for (const key of Object.keys(ACCENT_MODES)) {
    const option = el('option', '', ACCENT_MODES[key]);
    option.value = key;
    mode.append(option);
  }
  mode.value = accent.mode;
  mode.addEventListener('change', () => { accent.mode = mode.value; refresh(); keepFed(labPlayer, true); });
  modeLabel.append(mode);
  panel.append(modeLabel);

  if (accent.mode === 'color') {
    const colourLabel = el('label', 'range', 'Colour');
    const picker = el('input', 'accent-color');
    picker.type = 'color';
    picker.value = toHex(accent.color);
    picker.setAttribute('aria-label', 'Cap colour for ' + slot.node);
    picker.addEventListener('input', () => { accent.color = fromHex(picker.value); updateSaveBar(); keepFed(labPlayer, true); });
    picker.addEventListener('change', () => refresh());
    colourLabel.append(picker);
    panel.append(colourLabel);
  }

  const level = el('label', 'range accent-level', 'Cap brightness');
  const range = el('input');
  range.type = 'range'; range.min = '0'; range.max = '100'; range.step = '1';
  range.value = String(accent.brightness);
  range.setAttribute('aria-label', 'Cap brightness for ' + slot.node);
  const readout = el('output', '', Math.round(accent.brightness) + '%');
  range.addEventListener('input', () => {
    accent.brightness = Number(range.value);
    readout.textContent = range.value + '%';
    updateSaveBar();
    keepFed(labPlayer, true);
  });
  level.append(readout, range);
  panel.append(level);

  const all = el('button', 'secondary accent-all', 'Apply this cap to all ropes');
  all.addEventListener('click', () => {
    applyAccentToAll(accent);
    toast('Every rope now uses this cap in the draft. Save to send it to the wall.');
  });
  panel.append(all);
  const close = el('button', 'text accent-close', 'Close');
  close.addEventListener('click', () => { accentOpen.delete(slot.node); buildBays(); });
  panel.append(close);
  return panel;
}

function applyAccentToAll(accent) {
  for (const slot of draft.slots) slot.accent = clone(accent);
  refresh();
  keepFed(labPlayer, true);
  keepFed(livePlayer, true);
}

/* Configure: one row per physical bay. On a desktop every field sits in its
 * column; on a phone each row is a compact summary and one editor is open at
 * a time. The same draft semantics as before: nothing reaches the wall until
 * Save, except Identify, which flashes a real rope at once. */
let openBay = null;

function buildBays() {
  const host = $('bays');
  host.replaceChildren();
  draft.slots.forEach((slot, index) => {
    const row = el('div', 'assign-row bay');
    row.setAttribute('role', 'row');
    row.dataset.index = String(index);
    row.dataset.node = slot.node;
    row.dataset.printer = slot.printer;
    if (bayChanged(index)) row.classList.add('drafted');
    if (openBay === slot.node) row.classList.add('open');

    // Bay: physical ordinal, rope controller, a tiny live strip and the state.
    const bay = el('div', 'ar-bay');
    bay.setAttribute('role', 'cell');
    bay.append(el('span', 'ar-ordinal', 'Bay ' + String(index + 1).padStart(2, '0')));
    bay.append(el('span', 'ar-node', slot.node));
    const strip = el('canvas', 'ar-strip');
    strip.width = 100; strip.height = 1;
    strip.setAttribute('aria-hidden', 'true');
    bay.append(strip);
    bay.append(el('span', 'ar-state state', '—'));
    row.append(bay);

    // Printer: display name (drag to swap), rename, telemetry identity.
    const nameCell = el('div', 'ar-name');
    nameCell.setAttribute('role', 'cell');
    if (renaming === slot.node) {
      const input = el('input', 'rename-input');
      input.value = slot.label;
      input.maxLength = 40;
      input.setAttribute('aria-label', 'Display name for ' + slot.printer);
      input.addEventListener('keydown', event => {
        if (event.key === 'Enter') commitRename(index, input.value);
        if (event.key === 'Escape') { renaming = null; buildBays(); }
      });
      input.addEventListener('blur', () => commitRename(index, input.value));
      nameCell.append(input);
    } else {
      const name = el('button', 'printer-name', slot.label);
      name.draggable = true;
      name.title = 'Drag onto another bay to swap printers.';
      name.setAttribute('aria-label', slot.label + ' · drag to swap');
      name.addEventListener('click', () => startRename(index));
      name.addEventListener('dragstart', event => {
        dragFrom = index;
        event.dataTransfer.setData('text/plain', String(index));
        event.dataTransfer.effectAllowed = 'move';
      });
      name.addEventListener('dragend', () => {
        dragFrom = null;
        document.querySelectorAll('.dragover').forEach(node => node.classList.remove('dragover'));
      });
      nameCell.append(name);
      const rename = el('button', 'text', 'Rename');
      rename.addEventListener('click', () => startRename(index));
      nameCell.append(rename);
    }
    nameCell.append(el('span', 'alias', slot.printer));
    row.append(nameCell);
    row.addEventListener('dragover', event => { if (dragFrom !== null) { event.preventDefault(); row.classList.add('dragover'); } });
    row.addEventListener('dragleave', () => row.classList.remove('dragover'));
    row.addEventListener('drop', event => {
      event.preventDefault();
      row.classList.remove('dragover');
      if (dragFrom !== null) swapPrinters(dragFrom, index);
      dragFrom = null;
    });

    // Driven by.
    const assign = el('div', 'ar-assign');
    assign.setAttribute('role', 'cell');
    const assignLabel = el('label', 'assign-label', 'Driven by');
    assignLabel.htmlFor = 'assign-' + slot.node;
    const select_ = el('select');
    select_.id = 'assign-' + slot.node;
    select_.setAttribute('aria-label', 'Printer driving bay ' + (index + 1));
    draft.slots.forEach(other => {
      const option = el('option', '', other.label + ' · ' + other.printer);
      option.value = other.printer;
      select_.append(option);
    });
    select_.value = slot.printer;
    select_.addEventListener('change', () => {
      const other = draft.slots.findIndex(s => s.printer === select_.value);
      if (other >= 0) swapPrinters(index, other);
    });
    assign.append(assignLabel, select_);
    row.append(assign);

    // Direction.
    const dir = el('div', 'ar-dir');
    dir.setAttribute('role', 'cell');
    const reverse = el('label', 'reverse');
    const box = el('input');
    box.type = 'checkbox';
    box.checked = slot.reverse;
    box.addEventListener('change', () => { draft.slots[index].reverse = box.checked; refresh(); });
    reverse.append(box, document.createTextNode('Reverse fill'));
    dir.append(reverse, el('span', 'fineprint', slot.reverse ? 'fills toward the wire' : 'fills away from the wire'));
    row.append(dir);

    // Cap summary with an inline editor beneath the row.
    const cap = el('div', 'ar-cap');
    cap.setAttribute('role', 'cell');
    const swatch = el('i', 'cap-swatch' + (slot.accent.mode === 'rainbow' ? ' rainbow' : ''));
    if (slot.accent.mode !== 'rainbow') swatch.style.background = slot.accent.mode === 'color' ? toHex(slot.accent.color) : '#ffffff';
    cap.append(swatch, el('span', 'cap-word', accentSummary(slot.accent) + ' · ' + Math.round(slot.accent.brightness) + '%'));
    const edit = el('button', 'text', accentOpen.has(slot.node) ? 'Close' : 'Edit');
    edit.setAttribute('aria-expanded', String(accentOpen.has(slot.node)));
    edit.addEventListener('click', () => {
      if (accentOpen.has(slot.node)) accentOpen.delete(slot.node); else accentOpen.add(slot.node);
      buildBays();
    });
    cap.append(edit);
    row.append(cap);

    // Identify: immediate, physical, five seconds.
    const ident = el('div', 'ar-identify');
    ident.setAttribute('role', 'cell');
    const find = el('button', 'identify secondary', 'Identify · 5 s');
    find.title = 'Flashes the real rope now for five seconds, then restores it';
    find.addEventListener('click', () => identify(slot.node));
    ident.append(find);
    row.append(ident);

    // Move.
    const moveCell = el('div', 'ar-move');
    moveCell.setAttribute('role', 'cell');
    const reorder = el('div', 'reorder');
    for (const [delta, glyph, word] of [[-1, '←', 'left'], [1, '→', 'right']]) {
      const button = el('button', 'secondary', glyph);
      button.setAttribute('aria-label', 'Move bay ' + (index + 1) + ' one place ' + word);
      button.disabled = index + delta < 0 || index + delta >= draft.slots.length;
      button.addEventListener('click', () => moveBay(index, index + delta));
      reorder.append(button);
    }
    moveCell.append(reorder);
    row.append(moveCell);

    // Phone: expand/collapse this bay's editor.
    const expand = el('button', 'secondary ar-expand', openBay === slot.node ? 'Done' : 'Edit');
    expand.setAttribute('aria-expanded', String(openBay === slot.node));
    expand.addEventListener('click', () => { openBay = openBay === slot.node ? null : slot.node; buildBays(); });
    row.append(expand);

    row.append(accentPanel(index, slot));
    host.append(row);
  });
  updateCards();
}

function updateCards() {
  if (!live || !draft) return;
  document.querySelectorAll('.assign-row').forEach((row, index) => {
    const slot = draft.slots[index];
    const status = live.printers[slot.printer] || {};
    const [figure, qualifier] = reading(status);
    const st = row.querySelector('.ar-state');
    st.textContent = stateGlyph(status) + ' ' + (STATE_SHORT[status.state] || stateWord(status)) + ' · ' + figure;
    st.title = stateWord(status) + ' · ' + figure + ' · ' + qualifier;
    st.className = 'ar-state state ' + (status.state || 'unknown');
    const find = row.querySelector('.identify');
    find.disabled = !live.cast_enabled || busy;
    const identifying = live.identify && live.identify.node === slot.node;
    row.classList.toggle('identifying', Boolean(identifying));
    row.classList.toggle('walking', walk.active && walk.index === index);
    row.classList.toggle('drafted', bayChanged(index));
  });
}

function ropeHealthText(rope, identifyAcked) {
  if (!rope || rope.receipt === 'unverified') return 'rope receipt unverified';
  if (identifyAcked) return 'controller answered the identify';
  const age = rope.ack_age === null ? null : (rope.ack_age < 1 ? 'just now' : rope.ack_age.toFixed(0) + ' s ago');
  switch (rope.receipt) {
    case 'confirmed': return 'rope online · acknowledged ' + age;
    case 'offline':   return 'rope offline · controller not on the broker';
    case 'silent':    return 'rope silent · no acknowledgement since ' + (age || 'start');
    case 'pending':   return 'rope online · waiting for acknowledgement';
    case 'idle':      return rope.status === 'online' ? 'rope online · nothing sent yet' : 'rope not heard from yet';
    default:          return 'rope ' + rope.receipt;
  }
}

/* --------------------------------------------------------- draft plumbing */

function summarize() {
  if (!live || !draft) return [];
  const notes = [];
  const savedOrder = live.config.slots.map(slot => slot.node).join(',');
  const draftOrder = draft.slots.map(slot => slot.node).join(',');
  if (savedOrder !== draftOrder) notes.push('bay order');
  let swaps = 0, flips = 0, names = 0, caps = 0;
  for (const slot of draft.slots) {
    const saved = live.config.slots.find(other => other.node === slot.node);
    if (!saved) continue;
    if (saved.printer !== slot.printer) swaps++;
    if (saved.reverse !== slot.reverse) flips++;
    if (saved.label !== slot.label) names++;
    if (JSON.stringify(saved.accent) !== JSON.stringify(slot.accent)) caps++;
  }
  if (swaps) notes.push(swaps + ' printer assignment' + (swaps > 1 ? 's' : ''));
  if (flips) notes.push(flips + ' rope direction' + (flips > 1 ? 's' : ''));
  if (names) notes.push(names + ' display name' + (names > 1 ? 's' : ''));
  if (caps) notes.push(caps + ' far-end cap' + (caps > 1 ? 's' : ''));
  const changed = Object.keys(draft.settings).filter(key => JSON.stringify(draft.settings[key]) !== JSON.stringify(live.config.settings[key]));
  if (changed.includes('theme')) notes.push('colour theme');
  if (changed.includes('waterline_marks')) notes.push('preview guides');
  if (changed.some(key => key !== 'theme' && key !== 'waterline_marks')) notes.push('wall settings');
  return notes;
}

/* The save bar exists only while there is something to save. */
function updateSaveBar() {
  dirty = JSON.stringify(draft) !== JSON.stringify(live.config);
  const notes = summarize();
  $('draft-status').hidden = !dirty;
  $('savebar').hidden = !dirty;
  document.body.classList.toggle('has-savebar', dirty);
  $('save').disabled = !dirty || busy;
  $('cancel').disabled = !dirty || busy;
  $('undo').disabled = !live.can_undo || dirty || busy;
  $('undo').title = live.can_undo
    ? (dirty ? 'Discard or save the draft first' : 'Restore the layout before the last save')
    : 'There is no previous saved layout';
  $('save-note').textContent = dirty ? 'Draft: ' + notes.join(', ') + '. The wall changes only when you save.' : '';
  $('lab-pending').hidden = !dirty;
  $('lab-pending-text').textContent = dirty
    ? notes.join(', ') + '. The wall still runs the saved layout until you save.' : '';
  $('lab-badge').textContent = dirty ? 'Simulation · draft pending' : 'Simulation · preview only';
  // On a phone the badge is hidden and this line carries the draft state.
  if ($('lab-context-state')) {
    $('lab-context-state').textContent = dirty
      ? 'Appearance draft · not on the wall until you save'
      : 'Appearance uses saved settings';
    $('lab-context-state').classList.toggle('dirty', dirty);
  }
}

function refresh() {
  updateSaveBar();
  buildBays();
  syncSettingControls();
  syncCapControls();
  updateWalk();
}

function touch() {
  updateSaveBar();
  updateLive();
  updateCards();
  updateWalk();
}

function adopt() {
  draft = clone(live.config);
  if (sim.length !== draft.slots.length) sim = SCENARIOS[DEFAULT_SCENARIO](draft.slots.length);
  labAccent = clone(draft.slots[0].accent);
  dirty = false;
  walk.index = Math.min(walk.index, draft.slots.length - 1);
}

function themeInfo(name) {
  const list = (live && live.themes) || [];
  return list.find(t => t.name === name) || list[0] || null;
}

function rgb(c) { return 'rgb(' + c.join(',') + ')'; }

function buildThemePicker() {
  if (!live || !live.themes || !window.FilamentLooks) return;
  FilamentLooks.mount({themes:live.themes, getSettings:()=>draft.settings,
    onChange:settings=>{draft.settings=settings;refresh();keepFed(labPlayer,true);},notify:toast});
}

function syncTheme() {
  if (window.FilamentLooks && draft) FilamentLooks.sync(draft.settings);
  const name = draft ? draft.settings.theme : null;
  const info = themeInfo(name);
  for (const card of document.querySelectorAll('.theme-card')) {
    card.setAttribute('aria-checked', String(card.dataset.theme === name));
  }
  if (!info) return;
  // The light key follows the theme the wall is actually running.
  const wallTheme = themeInfo(live.config.settings.theme) || info;
  const KEY = { printing: 'water', collect: 'collect', paused: 'pause', error: 'error', idle: 'idle' };
  for (const [cls, key] of Object.entries(KEY)) {
    const dot = document.querySelector('.key i.' + cls);
    if (dot && wallTheme.swatches[key]) dot.style.background = rgb((live.resolved_palette || wallTheme.swatches)[key]);
  }
}

function syncSettingControls() {
  if (!draft) return;
  syncTheme();
  $('brightness').value = draft.settings.brightness;
  $('brightness-out').textContent = Math.round(draft.settings.brightness) + '%';
  $('speed').value = draft.settings.speed;
  $('speed-out').textContent = draft.settings.speed + '×';
  for (const key of ['ripples', 'reduced_motion', 'quiet', 'waterline_marks']) {
    $(key).checked = Boolean(draft.settings[key]);
  }
}

async function save() {
  if (busy || !dirty) return;
  busy = true;
  $('save').disabled = true;
  try {
    const saved = await api('config', draft);
    draft = clone(saved);
    dirty = false;
    await poll();
    toast(live.demo ? 'Demo layout saved. No physical rope changed.' : 'Saved. The wall is using this layout and these settings now.');
    keepFed(livePlayer, true);
  } catch (error) {
    toast(error.message);
  } finally {
    busy = false;
    refresh();
  }
}

function discard() {
  adopt();
  walk.active = false;
  refresh();
  keepFed(labPlayer, true);
  keepFed(livePlayer, true);
}

/* ------------------------------------------------------ walk the wall flow */

function walkSlot() { return draft.slots[walk.index] || null; }

function updateWalk() {
  $('walk-banner').hidden = !walk.active;
  if (!walk.active) return;
  const slot = walkSlot();
  if (!slot) return;
  $('walk-title').textContent = 'Position ' + (walk.index + 1) + ' of ' + draft.slots.length + ' · currently ' + slot.node + ' (' + slot.label + ')';
  $('walk-copy').textContent = 'Stand at bay ' + (walk.index + 1) + ' of the physical wall. Identify flashes ' + slot.node
    + ' for five seconds. If that is the rope in front of you, confirm it; otherwise try the next rope until one lights up.';
  $('walk-next').textContent = 'Not this one · try another';
  $('walk-next').disabled = walk.index >= draft.slots.length - 1;
  $('walk-here').textContent = walk.index >= draft.slots.length - 1 ? 'Yes · finish' : 'Yes, it’s here';
}

function walkTryNext() {
  const tail = draft.slots.splice(walk.index);
  tail.push(tail.shift());
  draft.slots.push(...tail);
  refresh();
  identify(walkSlot().node);
}

function walkConfirm() {
  if (walk.index >= draft.slots.length - 1) {
    walk.active = false;
    toast('Wall order recorded in the draft. Save to keep it.');
  } else {
    walk.index += 1;
  }
  refresh();
}

/* ------------------------------------------------------------- lab controls */

function simLabel(entry) {
  const word = STATE_TEXT[entry.state][0];
  if (entry.state !== 'printing') return word;
  return word + ' ' + (entry.percent === null ? '?%' : Math.round(entry.percent) + '%');
}

function buildLab() {
  const host = $('bay-sim');
  host.replaceChildren();
  const heading = el('div', 'sim-head');
  heading.append(el('div', 'label', 'Simulated state per bay · preview only'));
  const applyAll = el('button', 'secondary', 'Copy bay 01 to every bay');
  applyAll.addEventListener('click', () => {
    sim = sim.map(() => clone(sim[0]));
    simName = null;
    buildLab();
    buildScenarios();
    keepFed(labPlayer, true);
  });
  heading.append(applyAll);
  host.append(heading);

  const grid = el('div', 'sim-grid');
  sim.forEach((entry, index) => {
    const cell = el('div', 'sim-cell');
    const slot = draft ? draft.slots[index] : null;
    cell.append(el('div', 'sim-title', 'Bay ' + String(index + 1).padStart(2, '0') + (slot ? ' · ' + slot.label : '')));
    const select_ = el('select');
    select_.setAttribute('aria-label', 'Simulated state for bay ' + (index + 1));
    for (const key of SIM_STATES) {
      const option = el('option', '', STATE_TEXT[key][0]);
      option.value = key;
      select_.append(option);
    }
    select_.value = entry.state;
    select_.addEventListener('change', () => {
      entry.state = select_.value;
      if (entry.state === 'printing' && entry.percent === null && !entry.unknown) entry.percent = 50;
      simName = null;
      buildLab();
      buildScenarios();
      keepFed(labPlayer, true);
      $('state-explainer').textContent = STATE_TEXT[entry.state][1];
    });
    cell.append(select_);
    const printing = entry.state === 'printing';
    const range = el('input', 'sim-range');
    range.type = 'range'; range.min = '0'; range.max = '100'; range.step = '1';
    range.value = String(entry.percent === null ? 0 : entry.percent);
    range.disabled = !printing || entry.percent === null;
    range.setAttribute('aria-label', 'Simulated progress for bay ' + (index + 1));
    const readout = el('span', 'sim-pct', printing ? simLabel(entry).slice('Printing '.length) : 'n/a');
    range.addEventListener('input', () => { entry.percent = Number(range.value); readout.textContent = Math.round(entry.percent) + '%'; });
    range.addEventListener('change', () => keepFed(labPlayer, true));
    cell.append(range, readout);
    const unknown = el('label', 'sim-unknown');
    const box = el('input');
    box.type = 'checkbox';
    box.checked = printing && entry.percent === null;
    box.disabled = !printing;
    box.addEventListener('change', () => {
      entry.percent = box.checked ? null : 50;
      simName = null;
      buildLab();
      buildScenarios();
      keepFed(labPlayer, true);
    });
    unknown.append(box, document.createTextNode('% not reported'));
    cell.append(unknown);
    grid.append(cell);
  });
  host.append(grid);
  updateLabLabels();
}

function buildScenarios() {
  const host = $('scenarios');
  host.replaceChildren();
  for (const name of Object.keys(SCENARIOS)) {
    const button = el('button', '', name);
    button.setAttribute('aria-pressed', String(simName === name));
    button.addEventListener('click', () => {
      sim = SCENARIOS[name](draft.slots.length);
      simName = name;
      buildLab();
      buildScenarios();
      keepFed(labPlayer, true);
      $('preview-label').textContent = 'Simulation · ' + name;
      $('state-explainer').textContent = 'Scenario applied to the preview only; the physical wall keeps showing the real printers.';
    });
    host.append(button);
  }
}

/* --------------------------------------------------------------- rendering */

const painted = new WeakMap();

function stale(canvas, film, index) {
  const key = film.serial + ':' + index;
  if (painted.get(canvas) === key) return false;
  painted.set(canvas, key);
  return true;
}

function paintStage() {
  const canvas = $('stage-canvas');
  const film = livePlayer.film;
  const index = frameIndex(livePlayer);
  if (index < 0 || !film.bays) return;
  // The bitmap follows the box the stage gives it, so the ropes use the
  // stage's full height instead of leaving a slab beneath the labels.
  const boxW = Math.max(300, Math.round(canvas.clientWidth || canvas.width));
  const boxH = Math.max(260, Math.round(canvas.clientHeight || canvas.height));
  if (canvas.width !== boxW || canvas.height !== boxH) { canvas.width = boxW; canvas.height = boxH; }
  const key = film.serial + ':' + index + ':' + selected + ':' + boxW + 'x' + boxH;
  if (painted.get(canvas) === key) return;
  painted.set(canvas, key);
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const slots = live.config.slots;
  const count = slots.length;
  const width = clamp(Math.round(boxW / count * 0.16), 18, 30);
  const top = 22;
  const tall = canvas.height - 44;
  slots.forEach((slot, position) => {
    const ropeIndex = film.bays.findIndex(bay => bay.node === slot.node);
    if (ropeIndex < 0 || !film.ropes[ropeIndex]) return;
    const x = canvas.width * (position + 0.5) / count - width / 2;
    if (slot.printer === selected) {
      ctx.save();
      ctx.fillStyle = '#FFFFFF12';
      ctx.strokeStyle = '#B6ACD1';
      ctx.lineWidth = 1.5;
      capsulePath(ctx, x - 30, top - 14, width + 60, tall + 28, 22);
      ctx.fill();
      ctx.stroke();
      ctx.restore();
    }
    drawRope(ctx, film.ropes[ropeIndex][index], film.pixels, x, top, width, tall,
             film.accentStart, film.statusStart, false, true);
  });
}

function paintRows() {
  const film = livePlayer.film;
  const index = frameIndex(livePlayer);
  if (index < 0 || !film.bays) return;
  document.querySelectorAll('.fleet-row canvas').forEach((canvas, position) => {
    const slot = live.config.slots[position];
    const ropeIndex = film.bays.findIndex(bay => bay.node === slot.node);
    if (ropeIndex < 0 || !film.ropes[ropeIndex]) return;
    if (!stale(canvas, film, index)) return;
    if (canvas.width !== film.pixels) canvas.width = film.pixels;
    drawStrip(canvas.getContext('2d'), film.ropes[ropeIndex][index], film.pixels,
              film.statusStart, film.accentStart, canvas.width, canvas.height);
  });
}

function paintBays() {
  const film = livePlayer.film;
  const index = frameIndex(livePlayer);
  if (index < 0 || !film.bays) return;
  document.querySelectorAll('.assign-row').forEach(row => {
    const canvas = row.querySelector('.ar-strip');
    const saved = slotByPrinter(row.dataset.printer);
    const ropeIndex = saved ? film.bays.findIndex(bay => bay.node === saved.node) : -1;
    if (ropeIndex < 0 || !film.ropes[ropeIndex]) return;
    if (!stale(canvas, film, index)) return;
    if (canvas.width !== film.pixels) canvas.width = film.pixels;
    drawStrip(canvas.getContext('2d'), film.ropes[ropeIndex][index], film.pixels,
              film.statusStart, film.accentStart, canvas.width, canvas.height);
  });
}

function paintLab() {
  const canvas = $('lab-canvas');
  const film = labPlayer.film;
  const index = frameIndex(labPlayer);
  if (index < 0) return;
  if (!stale(canvas, film, index)) return;
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const count = film.ropes.length;
  const width = 22;
  const top = 26;
  const tall = canvas.height - 52;
  const marks = draft && draft.settings.waterline_marks;
  for (let rope = 0; rope < count; rope++) {
    const x = canvas.width * (rope + 0.5) / count - width / 2;
    drawRope(ctx, film.ropes[rope][index], film.pixels, x, top, width, tall,
             film.accentStart, film.statusStart, marks, false);
  }
}

function updateLabLabels() {
  const host = $('lab-labels');
  host.replaceChildren();
  sim.forEach((entry, index) => {
    const slot = draft ? draft.slots[index] : null;
    // Desktop shows the printer name; a phone shows "Bay N" (seven long names
    // would all ellipsize to the same text). The full identity is always in
    // the accessible name and the tooltip.
    const label = el('div', 'lab-label');
    const name = slot ? slot.label : 'Printer ' + (index + 1);
    const full = 'Bay ' + (index + 1) + ' · ' + name + ' · ' + simLabel(entry);
    label.title = full;
    label.setAttribute('aria-label', 'Simulated ' + full);
    const b = el('b');
    b.setAttribute('aria-hidden', 'true');
    b.append(el('span', 'lab-bay', 'Bay ' + (index + 1)), el('span', 'lab-name', name));
    label.append(b);
    const state = el('span', entry.state, 'Simulated ' + simLabel(entry));
    state.setAttribute('aria-hidden', 'true');
    label.append(state);
    host.append(label);
  });
}

function loop() {
  if (!document.hidden && live && draft) {
    if (tab === 'live') {
      keepFed(livePlayer, false);
      paintStage();
      paintRows();
    } else if (tab === 'map') {
      keepFed(livePlayer, false);
      paintBays();
    } else {
      keepFed(labPlayer, false);
      paintLab();
      $('preview-rate').textContent = labPlayer.failed ? 'Preview unavailable: ' + labPlayer.lastError : 'simulated · 12 fps';
    }
  }
  requestAnimationFrame(loop);
}

/* ------------------------------------------------------------------- poll */

async function poll() {
  try {
    const data = await api('state');
    const first = !live;
    const revisionMoved = live && live.config.revision !== data.config.revision;
    live = data;
    const adopting = first || (!dirty && revisionMoved);
    if (adopting) {
      adopt();
      buildThemePicker();
      buildScenarios();
      buildLab();
      buildStage();
      if (focusNode) {
        const slot = live.config.slots.find(s => s.node === focusNode);
        if (slot) { selected = slot.printer; userChoseSelection = true; }
        focusNode = null;
      }
    } else if (dirty && revisionMoved) {
      toast('Another window saved a different layout. Discard your draft to see it.');
    }
    $('connection').textContent = data.demo ? 'Demo · simulated printers, no lights'
      : (data.cast_enabled ? 'Connected · lighting live' : 'Connected · lighting paused by the controller');
    $('mode').textContent = data.demo ? 'Hardware-free demonstration' : 'Seven printers · Pi at the wall';
    if (data.last_error) $('connection').textContent = data.last_error;
    const t = data.transport;
    $('transport').textContent = t.rate_per_second + ' msg/s of ' + t.aggregate_ceiling + ' · ' + t.sent.toLocaleString()
      + ' sent · ' + t.failed + ' failed · node receipt ' + t.node_receipt;
    if (adopting) { refresh(); updateLive(); refreshMedia(); } else touch();
  } catch (error) {
    $('connection').textContent = 'Lost the Pi · what you see may be stale';
    $('transport').textContent = 'Reconnect to see the real wall';
  }
}

/* ----------------------------------------------------------------- wiring */

$('save').addEventListener('click', save);
$('cancel').addEventListener('click', discard);
$('lab-discard').addEventListener('click', discard);
$('undo').addEventListener('click', async () => {
  try {
    await api('undo', { revision: live.config.revision });
    dirty = false;
    await poll();
    adopt();
    refresh();
    keepFed(livePlayer, true);
    toast('Previous saved layout restored.');
  } catch (error) { toast(error.message); }
});
$('reset').addEventListener('click', async () => {
  if (!window.confirm('Restore the original printer assignments, order, directions and animation settings? Undo can still bring back the current layout.')) return;
  try {
    await api('reset', { revision: live.config.revision });
    dirty = false;
    await poll();
    adopt();
    refresh();
    keepFed(livePlayer, true);
    toast('Original assignments restored.');
  } catch (error) { toast(error.message); }
});

$('walk').addEventListener('click', () => { walk = { active: !walk.active, index: 0 }; refresh(); });
$('walk-close').addEventListener('click', () => { walk.active = false; refresh(); });
$('walk-identify').addEventListener('click', () => identify(walkSlot().node));
$('walk-here').addEventListener('click', walkConfirm);
$('walk-next').addEventListener('click', walkTryNext);

$('insp-collect').addEventListener('click', async () => {
  const slot = slotByPrinter(selected);
  if (!slot) return;
  try {
    await api('collected', { printer: slot.printer });
    toast('Marked collected here. Nothing was sent to the printer.');
    poll();
  } catch (error) { toast(error.message); }
});
$('insp-identify').addEventListener('click', () => { const slot = slotByPrinter(selected); if (slot) identify(slot.node); });
$('photo-zoom').addEventListener('click', openLightbox);
$('photo-img').addEventListener('click', openLightbox);
$('lightbox-close').addEventListener('click', closeLightbox);
$('lightbox').addEventListener('click', event => { if (event.target === $('lightbox')) closeLightbox(); });
$('lightbox').addEventListener('cancel', event => { event.preventDefault(); closeLightbox(); });
$('lightbox').addEventListener('close', () => { $('lightbox-img').removeAttribute('src'); });
$('sheet-back').addEventListener('click', closeSheet);
document.addEventListener('keydown', event => {
  if (event.key === 'Tab') { trapSheetFocus(event); return; }
  if (event.key !== 'Escape') return;
  if ($('lightbox').open) closeLightbox();
  else closeSheet();
});
window.addEventListener('resize', () => { if (!phoneLayout()) closeSheet(); });
for (const id of ['stage', 'fleet-rows']) {
  $(id).addEventListener('keydown', event => {
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') { event.preventDefault(); moveSelection(1); }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') { event.preventDefault(); moveSelection(-1); }
    if (event.key === 'Home') { event.preventDefault(); select(live.config.slots[0].printer, true); }
    if (event.key === 'End') { event.preventDefault(); select(live.config.slots[live.config.slots.length - 1].printer, true); }
  });
}

const TABS = [['live-tab', 'live', 'live'], ['config-tab', 'map', 'mapping'], ['lab-tab', 'lab', 'lab']];
function showTab(which) {
  tab = which;
  for (const [id, name, section] of TABS) {
    $(section).hidden = which !== name;
    $(id).classList.toggle('active', which === name);
    $(id).setAttribute('aria-selected', String(which === name));
  }
  document.title = (which === 'live' ? 'Live wall' : which === 'map' ? 'Configure' : 'Animation lab') + ' · Filament wall';
  if (which === 'lab') keepFed(labPlayer, true);
  if (which !== 'lab' && live) keepFed(livePlayer, true);
  if (window.location.hash !== '#' + which) history.replaceState(null, '', '#' + which);
}
for (const [id, name] of TABS) $(id).addEventListener('click', () => showTab(name));
window.addEventListener('hashchange', () => {
  const hash = window.location.hash.slice(1);
  if (hash === 'map' || hash === 'lab' || hash === 'live') showTab(hash);
});

for (const key of ['brightness', 'speed']) {
  $(key).addEventListener('input', () => { draft.settings[key] = Number($(key).value); refresh(); keepFed(labPlayer, true); });
}
for (const key of ['ripples', 'reduced_motion', 'quiet', 'waterline_marks']) {
  $(key).addEventListener('change', () => { draft.settings[key] = $(key).checked; refresh(); keepFed(labPlayer, true); keepFed(livePlayer, true); });
}

function syncCapControls() {
  $('cap-mode').value = labAccent.mode;
  $('cap-color').value = toHex(labAccent.color);
  $('cap-color').disabled = labAccent.mode !== 'color';
  $('cap-brightness').value = labAccent.brightness;
  $('cap-brightness-out').textContent = Math.round(labAccent.brightness) + '%';
}
$('cap-mode').addEventListener('change', () => { labAccent.mode = $('cap-mode').value; syncCapControls(); });
$('cap-color').addEventListener('input', () => { labAccent.color = fromHex($('cap-color').value); });
$('cap-brightness').addEventListener('input', () => { labAccent.brightness = Number($('cap-brightness').value); $('cap-brightness-out').textContent = $('cap-brightness').value + '%'; });
$('cap-apply').addEventListener('click', () => { applyAccentToAll(labAccent); toast('Every rope now uses this cap in the draft. Save to send it to the wall.'); });

window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });

(async () => {
  const params = new URLSearchParams(window.location.search);
  const wanted = params.get('focus');
  if (wanted && wanted.startsWith('node') && wanted.length <= 8) focusNode = wanted;
  const hash = window.location.hash.slice(1);
  if (hash === 'map' || hash === 'lab') showTab(hash);
  await poll();
  setInterval(() => { if (!document.hidden) poll(); }, 1000);
  setInterval(() => { if (!document.hidden && tab === 'live') refreshMedia(); }, 15000);
  requestAnimationFrame(loop);
})();
