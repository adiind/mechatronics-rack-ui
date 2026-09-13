'use strict';
/* Print Tide browser client.
 *
 * Two rules shape this file:
 *   1. It renders nothing itself. Every pixel it draws came from the Python
 *      renderer as a run-length encoded filmstrip, so the screen and the wall
 *      cannot drift apart. There is no JavaScript animation of printer state.
 *   2. It never turns printer text into markup. Everything user- or
 *      printer-supplied goes through textContent.
 */

const $ = id => document.getElementById(id);
const clone = o => JSON.parse(JSON.stringify(o));
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

const STATE_TEXT = {
  idle:      ['Available',   'Free and ready. Resting cyan.'],
  preparing: ['Preparing',   'The printer reported PREPARE. Cyan sweep.'],
  printing:  ['Printing',    'Orange fill rises through deep blue; drops fall into it.'],
  paused:    ['Paused',      'Yellow breathing with a steady marker. Needs a look.'],
  error:     ['Error',       'Deep red breathing. The printer reported a print error.'],
  finished:  ['Collect',     'A smooth rainbow on completion, then steady green until the door opens or it is marked collected.'],
  stopped:   ['Stopped',     'Cancelled or failed and dismissed. Steady magenta until the door opens or it is marked collected.'],
  offline:   ['Offline',     'No fresh telemetry. Dim purple heartbeat, no percentage.'],
  unknown:   ['Unknown',     'Connected, but the reported state is not one we recognise.'],
};
const SIM_STATES = Object.keys(STATE_TEXT);

const ACCENT_MODES = {
  white: 'Pure white',
  color: 'Solid colour',
  rainbow: 'Rainbow',
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

/* Scenarios cycle their pattern to however many bays the wall actually has, so
 * the simulation always matches the real bay count the server will accept. */
function scenario(pattern) {
  return count => Array.from({ length: count }, (unused, i) => ({
    state: pattern[i % pattern.length][0],
    percent: pattern[i % pattern.length][1],
  }));
}

const SCENARIOS = {
  'Quiet night': scenario([['idle', null]]),
  'Busy afternoon': scenario([['printing', 18], ['printing', 64], ['idle', null],
                              ['preparing', 0], ['printing', 91], ['finished', 100],
                              ['idle', null]]),
  'Needs attention': scenario([['error', 37], ['paused', 52], ['printing', 73],
                               ['idle', null], ['offline', null], ['unknown', null],
                               ['finished', 100]]),
  'Every state': scenario(SIM_STATES.map((s, i) => [s, 12 + i * 11])),
};
const DEFAULT_SCENARIO = 'Busy afternoon';

let live = null;           // last /api/state payload
let draft = null;          // editable layout (mapping + wall settings)
let dirty = false;
let busy = false;
let tab = 'map';
let dragFrom = null;
let toastTimer = null;
let renaming = null;       // node id currently being renamed
let sim = SCENARIOS[DEFAULT_SCENARIO](7);
let walk = { active: false, index: 0 };
const accentOpen = new Set();   // node ids whose cap panel is expanded
let labAccent = { mode: 'white', color: [255, 255, 255], brightness: 100 };

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
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': (live && live.csrf) || '',
    },
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
  return Math.floor(total / 60) + ' h ' + (total % 60) + ' min left';
}

/* ------------------------------------------------------- filmstrip player */

/* A film is a short run of *future* frames from the pure Python renderer. The
 * browser plays it on its own clock and asks for the next run before it runs
 * out, which buys smooth motion for about one request per second. */

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
    accentStart: payload.accent_start,
    startedAt: performance.now() / 1000,
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
  if (player.busy) return;
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
    const source = (pixels - 1 - row) * 3;     // pixel 0 sits at the bottom
    const target = row * 4;
    data[target] = Math.min(255, buffer[source] * SCREEN_GAIN);
    data[target + 1] = Math.min(255, buffer[source + 1] * SCREEN_GAIN);
    data[target + 2] = Math.min(255, buffer[source + 2] * SCREEN_GAIN);
    data[target + 3] = 255;
  }
  scratchCtx.putImageData(scratchData, 0, 0);
}

function capsule(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(x, y, width, height, radius);
  else ctx.rect(x, y, width, height);
  ctx.fill();
}

/* Physical index 0 is the data-in wire and is drawn at the BOTTOM; the accent
 * cap therefore always sits at the top of the drawing, whichever way the status
 * region runs. That is the one orientation we actually know, so it is the one
 * the preview commits to. */
function drawRope(ctx, buffer, pixels, x, y, width, height, accentStart) {
  loadScratch(buffer, pixels);
  ctx.save();
  ctx.fillStyle = '#050d12';
  capsule(ctx, x - 4, y - 7, width + 8, height + 14, width);
  ctx.imageSmoothingEnabled = true;
  ctx.drawImage(scratch, 0, 0, 1, pixels, x, y, width, height);
  ctx.globalCompositeOperation = 'lighter';
  ctx.globalAlpha = 0.28;
  ctx.drawImage(scratch, 0, 0, 1, pixels, x - width * 1.5, y - 4,
                width * 4, height + 8);
  ctx.globalAlpha = 0.16;
  ctx.drawImage(scratch, 0, 0, 1, pixels, x - width * 3.5, y - 10,
                width * 8, height + 20);
  ctx.restore();

  if (accentStart && accentStart < pixels) {
    // Zone boundary: everything above this line is the fixed far-end cap.
    const edge = y + height * (1 - accentStart / pixels);
    ctx.save();
    ctx.strokeStyle = '#0b151a';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(x - 5, edge);
    ctx.lineTo(x + width + 5, edge);
    ctx.stroke();
    ctx.strokeStyle = '#93a7ae';
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(x - 7, edge);
    ctx.lineTo(x + width + 7, edge);
    ctx.stroke();
    ctx.restore();
  }
}

function endLabels(ctx, x, y, width, height, small) {
  ctx.save();
  ctx.fillStyle = '#6d858c';
  ctx.font = (small ? 8 : 10) + 'px ui-monospace, monospace';
  ctx.textAlign = 'center';
  ctx.fillText('CAP', x + width / 2, y - 12);
  ctx.fillText('WIRE', x + width / 2, y + height + (small ? 16 : 18));
  ctx.restore();
}

function blankCanvas(canvas) {
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#0a171d';
  capsule(ctx, canvas.width / 2 - 11, 10, 22, canvas.height - 20, 11);
}

/* --------------------------------------------------------------- bay cards */

function liveSlotFor(printer) {
  if (!live) return null;
  return live.config.slots.find(slot => slot.printer === printer) || null;
}

function bayChanged(index) {
  if (!live || !draft) return false;
  const drafted = draft.slots[index];
  const saved = live.config.slots[index];
  if (!saved) return true;
  return saved.node !== drafted.node || saved.printer !== drafted.printer
      || saved.reverse !== drafted.reverse || saved.label !== drafted.label;
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
      : node + ' - Identify queued for five seconds. Watch the physical rope; '
             + 'the studio cannot confirm the node received it.');
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

/* The far-end cap. Collapsed by default so a seven-across card stays readable,
 * but the summary always states the current mode without opening it. */
function accentPanel(index, slot) {
  const accent = slot.accent;
  const panel = el('details', 'accent');
  panel.open = accentOpen.has(slot.node);
  panel.addEventListener('toggle', () => {
    if (panel.open) accentOpen.add(slot.node);
    else accentOpen.delete(slot.node);
  });

  const summary = el('summary', '',
    'Far-end cap · ' + accentSummary(accent));
  summary.title = 'The last ' + (live.accent_positions || 10)
    + ' positions, at the end opposite the wire. Never shows printer state.';
  panel.append(summary);

  const mode = el('select', 'accent-mode');
  mode.setAttribute('aria-label', 'Cap mode for ' + slot.node);
  for (const key of Object.keys(ACCENT_MODES)) {
    const option = el('option', '', ACCENT_MODES[key]);
    option.value = key;
    mode.append(option);
  }
  mode.value = accent.mode;
  mode.addEventListener('change', () => {
    accent.mode = mode.value;
    refresh();
    keepFed(labPlayer, true);
  });
  panel.append(mode);

  if (accent.mode === 'color') {
    const picker = el('input', 'accent-color');
    picker.type = 'color';
    picker.value = toHex(accent.color);
    picker.setAttribute('aria-label', 'Cap colour for ' + slot.node);
    picker.addEventListener('input', () => {
      accent.color = fromHex(picker.value);
      updateSaveBar();
      keepFed(labPlayer, true);
    });
    picker.addEventListener('change', () => refresh());
    panel.append(picker);
  }

  const level = el('label', 'accent-level', 'Cap brightness');
  const range = el('input');
  range.type = 'range';
  range.min = '0';
  range.max = '100';
  range.step = '1';
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

  const all = el('button', 'subtle accent-all', 'Apply this cap to all ropes');
  all.addEventListener('click', () => {
    applyAccentToAll(accent);
    toast('Every rope now uses this cap. Save & apply to send it to the wall.');
  });
  panel.append(all);
  return panel;
}

function applyAccentToAll(accent) {
  for (const slot of draft.slots) {
    slot.accent = clone(accent);
  }
  refresh();
  keepFed(labPlayer, true);
  keepFed(livePlayer, true);
}

function buildBays() {
  const host = $('bays');
  host.replaceChildren();
  draft.slots.forEach((slot, index) => {
    const card = el('article', 'bay');
    card.setAttribute('role', 'listitem');
    card.dataset.index = String(index);
    card.dataset.node = slot.node;
    card.dataset.printer = slot.printer;
    if (bayChanged(index)) card.classList.add('drafted');

    const top = el('div', 'bay-top');
    top.append(el('span', '', 'BAY ' + String(index + 1).padStart(2, '0')));
    top.append(el('b', '', slot.node));
    card.append(top);

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
      card.append(input);
    } else {
      const name = el('button', 'printer-name', slot.label);
      name.draggable = true;
      name.title = 'Drag onto another bay to swap printers. Click to rename.';
      name.setAttribute('aria-label', 'Rename ' + slot.label + ', or drag to swap');
      name.addEventListener('click', () => startRename(index));
      name.addEventListener('dragstart', event => {
        dragFrom = index;
        event.dataTransfer.setData('text/plain', String(index));
        event.dataTransfer.effectAllowed = 'move';
      });
      name.addEventListener('dragend', () => {
        dragFrom = null;
        document.querySelectorAll('.dragover')
          .forEach(node => node.classList.remove('dragover'));
      });
      card.append(name);
    }

    card.addEventListener('dragover', event => {
      if (dragFrom !== null) { event.preventDefault(); card.classList.add('dragover'); }
    });
    card.addEventListener('dragleave', () => card.classList.remove('dragover'));
    card.addEventListener('drop', event => {
      event.preventDefault();
      card.classList.remove('dragover');
      if (dragFrom !== null) swapPrinters(dragFrom, index);
      dragFrom = null;
    });

    // Direction is described relative to the wire, which is the only end whose
    // physical location we actually know.
    card.append(el('div', 'alias', slot.printer + ' · '
      + live.status_positions + ' status + ' + live.accent_positions + ' cap · '
      + (slot.reverse ? 'fills toward the wire' : 'fills away from the wire')));

    const stage = el('div', 'rope-stage');
    const canvas = el('canvas');
    canvas.width = 130;
    canvas.height = 420;
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', 'Live light pattern for ' + slot.label);
    stage.append(canvas);
    stage.append(el('div', 'reading', '—'));
    card.append(stage);

    card.append(el('div', 'state', 'Connecting…'));
    const meta = el('div', 'status-meta');
    meta.append(el('div', 'job', ''), el('div', 'eta', ''),
                el('div', 'temps', ''), el('div', 'freshness', ''),
                el('div', 'rope-health', ''));
    card.append(meta);

    const find = el('button', 'identify', '◎ Identify rope');
    find.addEventListener('click', () => identify(slot.node));
    card.append(find);

    const collect = el('button', 'collected', '✓ Mark collected');
    collect.hidden = true;
    collect.addEventListener('click', async () => {
      try {
        await api('collected', { printer: slot.printer });
        toast('Marked collected here. Nothing was sent to the printer.');
        poll();
      } catch (error) { toast(error.message); }
    });
    card.append(collect);

    const assignLabel = el('label', 'assign-label', 'DRIVEN BY');
    assignLabel.htmlFor = 'assign-' + slot.node;
    card.append(assignLabel);
    const select = el('select');
    select.id = 'assign-' + slot.node;
    draft.slots.forEach(other => {
      const option = el('option', '', other.label + ' (' + other.printer + ')');
      option.value = other.printer;
      select.append(option);
    });
    select.value = slot.printer;
    select.addEventListener('change', () => {
      const other = draft.slots.findIndex(s => s.printer === select.value);
      if (other >= 0) swapPrinters(index, other);
    });
    card.append(select);

    const reverse = el('label', 'reverse');
    const box = el('input');
    box.type = 'checkbox';
    box.checked = slot.reverse;
    box.addEventListener('change', () => {
      draft.slots[index].reverse = box.checked;
      refresh();
    });
    reverse.append(box, document.createTextNode('Reverse status direction'));
    card.append(reverse);

    card.append(accentPanel(index, slot));

    const reorder = el('div', 'reorder');
    for (const [delta, glyph, word] of [[-1, '←', 'left'], [1, '→', 'right']]) {
      const button = el('button', '', glyph);
      button.setAttribute('aria-label', 'Move ' + slot.node + ' one bay ' + word);
      button.disabled = index + delta < 0 || index + delta >= draft.slots.length;
      button.addEventListener('click', () => moveBay(index, index + delta));
      reorder.append(button);
    }
    card.append(reorder);
    host.append(card);
  });
  updateCards();
}

function updateCards() {
  if (!live || !draft) return;
  const cards = document.querySelectorAll('.bay');
  cards.forEach((card, index) => {
    const slot = draft.slots[index];
    const status = live.printers[slot.printer] || {};
    const label = (STATE_TEXT[status.state] || ['Unknown'])[0];

    const stateNode = card.querySelector('.state');
    stateNode.textContent = status.collected ? 'Available (collected)' : label;
    stateNode.className = 'state ' + (status.state || 'unknown');

    const reading = card.querySelector('.reading');
    if (status.state === 'printing' && status.percent !== null
        && status.percent !== undefined) {
      reading.textContent = Math.round(status.percent) + '%';
    } else if (status.state === 'finished') {
      reading.textContent = 'Ready';
    } else if (status.state === 'stopped') {
      reading.textContent = 'Clear bed';
    } else if (status.state === 'idle') {
      reading.textContent = '≈';
    } else {
      reading.textContent = '—';
    }

    card.querySelector('.job').textContent =
      status.state === 'idle' ? 'Free for the next print'
        : (status.job || 'No job name reported');

    const bits = [];
    const eta = minutesText(status.remaining_min);
    if (status.state === 'printing' && eta) bits.push(eta);
    if (status.layer !== null && status.layer !== undefined) {
      bits.push('layer ' + status.layer
        + (status.total_layer ? '/' + status.total_layer : ''));
    }
    if (status.state === 'finished') {
      bits.push(status.completion_observed
        ? 'finished ' + Math.round((status.completed_ago || 0) / 60) + ' min ago (seen)'
        : 'retained FINISH · completion not observed');
    }
    card.querySelector('.eta').textContent = bits.join(' · ') || ' ';

    const nozzle = fmt(status.nozzle);
    const bed = fmt(status.bed);
    card.querySelector('.temps').textContent =
      nozzle === null && bed === null ? 'Temperatures unknown'
        : 'nozzle ' + (nozzle === null ? '?' : nozzle + '°')
          + ' · bed ' + (bed === null ? '?' : bed + '°');

    card.querySelector('.freshness').textContent = status.fresh
      ? 'message ' + Math.round(status.age || 0) + 's ago'
      : (status.reason_text || 'no fresh data');

    const collect = card.querySelector('.collected');
    collect.hidden = status.state !== 'finished' && status.state !== 'stopped';

    const find = card.querySelector('.identify');
    find.disabled = !live.cast_enabled || busy;
    find.title = live.cast_enabled ? 'Flash this rope for five seconds'
                                   : 'Lighting output is paused by the controller';

    const identifying = live.identify && live.identify.node === slot.node;
    card.classList.toggle('identifying', Boolean(identifying));
    const healthEl = card.querySelector('.rope-health');
    const rope = live.ropes && live.ropes[slot.node];
    healthEl.textContent = ropeHealthText(rope, identifying && live.identify.acked);
    healthEl.dataset.receipt = rope ? rope.receipt : 'unverified';
    card.classList.toggle('walking', walk.active && walk.index === index);
    card.classList.toggle('drafted', bayChanged(index));
  });
}

function ropeHealthText(rope, identifyAcked) {
  // Delivery evidence, in words the wall can back up. 'unverified' means the
  // host is not feeding controller acks, so nothing here is a claim.
  if (!rope || rope.receipt === 'unverified') return 'rope receipt unverified';
  if (identifyAcked) return 'controller answered the identify';
  const age = rope.ack_age === null ? null
    : (rope.ack_age < 1 ? 'just now' : rope.ack_age.toFixed(0) + ' s ago');
  switch (rope.receipt) {
    case 'confirmed': return 'rope online · acked ' + age;
    case 'offline':   return 'rope OFFLINE · controller not on the broker';
    case 'silent':    return 'rope silent · no ack since ' + (age || 'start');
    case 'pending':   return 'rope online · waiting for ack';
    case 'idle':      return rope.status === 'online' ? 'rope online · nothing sent yet'
                                                      : 'rope not heard from yet';
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
  const changed = Object.keys(draft.settings)
    .filter(key => draft.settings[key] !== live.config.settings[key]);
  if (changed.length) notes.push('wall settings');
  return notes;
}

function updateSaveBar() {
  dirty = JSON.stringify(draft) !== JSON.stringify(live.config);
  const notes = summarize();
  $('draft-status').textContent = dirty ? 'Draft · not on the wall' : 'Saved layout';
  $('draft-status').classList.toggle('dirty', dirty);
  $('save').disabled = !dirty || busy;
  $('cancel').disabled = !dirty || busy;
  $('undo').disabled = !live.can_undo || dirty || busy;
  $('savebar').classList.toggle('active', dirty);
  $('save-note').textContent = dirty
    ? 'Draft only. Pending: ' + notes.join(', ') + '. The wall changes when you save.'
    : 'Identify flashes a real rope for five seconds, then restores it.';
}

/* Full rebuild: only for structural changes, so a poll never steals focus from
 * a rename field or resets a slider mid-drag. */
function refresh() {
  updateSaveBar();
  buildBays();
  syncSettingControls();
  syncCapControls();
  updateWalk();
}

/* Cheap per-second pass: text and classes only. */
function touch() {
  updateSaveBar();
  updateCards();
  updateWalk();
}

function adopt() {
  draft = clone(live.config);
  if (sim.length !== draft.slots.length) {
    sim = SCENARIOS[DEFAULT_SCENARIO](draft.slots.length);
  }
  labAccent = clone(draft.slots[0].accent);
  dirty = false;
  walk.index = Math.min(walk.index, draft.slots.length - 1);
}

function syncSettingControls() {
  if (!draft) return;
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
    dirty = false;            // our own save must not read as someone else's
    await poll();
    toast(live.demo ? 'Demo layout saved. No physical rope changed.'
                    : 'Saved. The wall is using this mapping and these settings now.');
    keepFed(livePlayer, true);
  } catch (error) {
    toast(error.message);
  } finally {
    busy = false;
    refresh();
  }
}

/* ------------------------------------------------------ walk the wall flow */

function walkSlot() {
  return draft.slots[walk.index] || null;
}

function updateWalk() {
  $('walk-banner').hidden = !walk.active;
  if (!walk.active) return;
  const slot = walkSlot();
  if (!slot) return;
  $('walk-title').textContent =
    'Position ' + (walk.index + 1) + ' of ' + draft.slots.length
    + ' · currently ' + slot.node + ' (' + slot.label + ')';
  $('walk-copy').textContent =
    'Stand at bay ' + (walk.index + 1) + ' of your physical wall. Identify flashes '
    + slot.node + ' for five seconds. If that is the rope in front of you, confirm it; '
    + 'otherwise try the next rope until one lights up.';
  $('walk-next').textContent = 'Not this one · try another';
  $('walk-next').disabled = walk.index >= draft.slots.length - 1;
  $('walk-here').textContent = walk.index >= draft.slots.length - 1
    ? 'Yes · finish' : 'Yes, it’s here →';
}

function walkTryNext() {
  // Rotate the not-yet-placed bays so a different candidate lands here.
  const tail = draft.slots.splice(walk.index);
  tail.push(tail.shift());
  draft.slots.push(...tail);
  refresh();
  identify(walkSlot().node);
}

function walkConfirm() {
  if (walk.index >= draft.slots.length - 1) {
    walk.active = false;
    toast('Wall order recorded in your draft. Save & apply to keep it.');
  } else {
    walk.index += 1;
  }
  refresh();
}

/* ------------------------------------------------------------- lab controls */

function buildLab() {
  const host = $('bay-sim');
  host.replaceChildren();
  const heading = el('div', 'sim-head');
  heading.append(el('div', 'eyebrow', 'SIMULATED STATE PER BAY'));
  const applyAll = el('button', 'subtle', 'Copy bay 01 to every bay');
  applyAll.addEventListener('click', () => {
    sim = sim.map(() => clone(sim[0]));
    buildLab();
    keepFed(labPlayer, true);
  });
  heading.append(applyAll);
  host.append(heading);

  const grid = el('div', 'sim-grid');
  sim.forEach((entry, index) => {
    const cell = el('div', 'sim-cell');
    const slot = draft ? draft.slots[index] : null;
    cell.append(el('div', 'sim-title',
      'Bay ' + String(index + 1).padStart(2, '0')
      + (slot ? ' · ' + slot.label : '')));

    const select = el('select');
    select.setAttribute('aria-label', 'Simulated state for bay ' + (index + 1));
    for (const key of SIM_STATES) {
      const option = el('option', '', STATE_TEXT[key][0]);
      option.value = key;
      select.append(option);
    }
    select.value = entry.state;
    select.addEventListener('change', () => {
      entry.state = select.value;
      if (entry.state === 'printing' && entry.percent === null) entry.percent = 50;
      buildLab();
      keepFed(labPlayer, true);
      $('state-explainer').textContent = STATE_TEXT[entry.state][1];
    });
    cell.append(select);

    const range = el('input', 'sim-range');
    range.type = 'range';
    range.min = '0';
    range.max = '100';
    range.step = '1';
    range.value = String(entry.percent === null ? 0 : entry.percent);
    range.disabled = entry.state !== 'printing';
    range.setAttribute('aria-label', 'Simulated progress for bay ' + (index + 1));
    const readout = el('span', 'sim-pct',
      entry.state === 'printing' ? Math.round(entry.percent || 0) + '%' : 'n/a');
    range.addEventListener('input', () => {
      entry.percent = Number(range.value);
      readout.textContent = Math.round(entry.percent) + '%';
    });
    cell.append(range, readout);
    grid.append(cell);
  });
  host.append(grid);
}

function buildScenarios() {
  const host = $('scenarios');
  host.replaceChildren();
  for (const name of Object.keys(SCENARIOS)) {
    const button = el('button', '', name);
    button.addEventListener('click', () => {
      sim = SCENARIOS[name](draft.slots.length);
      buildLab();
      keepFed(labPlayer, true);
      $('preview-label').textContent = name.toUpperCase();
    });
    host.append(button);
  }
}

/* --------------------------------------------------------------- rendering */

function paintBays() {
  const cards = document.querySelectorAll('.bay');
  const film = livePlayer.film;
  const index = frameIndex(livePlayer);
  cards.forEach((card, position) => {
    const canvas = card.querySelector('canvas');
    const ctx = canvas.getContext('2d');
    const slot = draft.slots[position];
    const saved = liveSlotFor(slot.printer);
    // Draft cards show the rope their printer drives *today*, because the draft
    // is not on the wall yet.
    const ropeIndex = film && film.bays
      ? film.bays.findIndex(bay => saved && bay.node === saved.node)
      : -1;
    if (index < 0 || ropeIndex < 0 || !film.ropes[ropeIndex]) {
      blankCanvas(canvas);
      return;
    }
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const top = 26;
    const tall = canvas.height - 52;
    drawRope(ctx, film.ropes[ropeIndex][index], film.pixels,
             canvas.width / 2 - 11, top, 22, tall, film.accentStart);
    endLabels(ctx, canvas.width / 2 - 11, top, 22, tall, true);
  });
}

function paintLab() {
  const canvas = $('lab-canvas');
  const ctx = canvas.getContext('2d');
  const film = labPlayer.film;
  const index = frameIndex(labPlayer);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (index < 0) return;
  const count = film.ropes.length;
  const width = 26;
  const top = 46;
  const tall = canvas.height - 122;
  for (let rope = 0; rope < count; rope++) {
    const x = canvas.width * (rope + 0.5) / count - width / 2;
    drawRope(ctx, film.ropes[rope][index], film.pixels, x, top, width, tall,
             film.accentStart);
    endLabels(ctx, x, top, width, tall, false);
    ctx.fillStyle = '#7f9ba3';
    ctx.font = '13px ui-monospace, monospace';
    ctx.textAlign = 'center';
    ctx.fillText(String(rope + 1).padStart(2, '0'), x + width / 2, canvas.height - 34);
    const slot = draft ? draft.slots[rope] : null;
    ctx.fillStyle = '#5d7a82';
    ctx.font = '11px ui-monospace, monospace';
    ctx.fillText(slot ? slot.label.slice(0, 14) : '', x + width / 2, canvas.height - 14);
  }
}

function loop() {
  if (!document.hidden && live && draft) {
    if (tab === 'map') {
      keepFed(livePlayer, false);
      paintBays();
    } else {
      keepFed(labPlayer, false);
      paintLab();
      $('preview-rate').textContent = labPlayer.failed
        ? 'preview unavailable' : 'simulated · 12 fps';
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
      buildScenarios();
      buildLab();
    } else if (dirty && revisionMoved) {
      toast('Another window saved a different layout. Discard your draft to see it.');
    }

    $('connection').textContent = data.demo
      ? 'DEMO · simulated printers, no lights'
      : (data.cast_enabled ? 'Pi connected · lighting live'
                           : 'Pi connected · lighting paused by the controller');
    $('mode').textContent = data.demo
      ? 'HARDWARE-FREE DEMONSTRATION'
      : 'SEVEN PRINTERS · TAILSCALE ONLY';
    if (data.last_error) $('connection').textContent = data.last_error;

    const t = data.transport;
    $('transport').textContent =
      t.rate_per_second + ' msg/s of ' + t.aggregate_ceiling + ' · '
      + t.sent.toLocaleString() + ' sent · ' + t.failed + ' failed · '
      + 'receipt ' + t.node_receipt;

    if (adopting) refresh(); else touch();
  } catch (error) {
    $('connection').textContent = 'Lost the Pi · what you see may be stale';
    $('transport').textContent = 'Reconnect to see the real wall';
  }
}

/* ----------------------------------------------------------------- wiring */

$('save').addEventListener('click', save);
$('cancel').addEventListener('click', () => {
  adopt();
  walk.active = false;
  refresh();
  keepFed(labPlayer, true);
});
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
  if (!window.confirm('Restore the original printer assignments, order, directions '
    + 'and animation settings? Undo can still bring back the current layout.')) return;
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

$('walk').addEventListener('click', () => {
  walk = { active: !walk.active, index: 0 };
  refresh();
});
$('walk-close').addEventListener('click', () => { walk.active = false; refresh(); });
$('walk-identify').addEventListener('click', () => identify(walkSlot().node));
$('walk-here').addEventListener('click', walkConfirm);
$('walk-next').addEventListener('click', walkTryNext);

function showTab(which) {
  tab = which;
  $('mapping').hidden = which !== 'map';
  $('lab').hidden = which !== 'lab';
  for (const [id, name] of [['map-tab', 'map'], ['lab-tab', 'lab']]) {
    $(id).classList.toggle('active', which === name);
    $(id).setAttribute('aria-selected', String(which === name));
  }
  if (which === 'lab') keepFed(labPlayer, true);
}
$('map-tab').addEventListener('click', () => showTab('map'));
$('lab-tab').addEventListener('click', () => showTab('lab'));

for (const key of ['brightness', 'speed']) {
  $(key).addEventListener('input', () => {
    draft.settings[key] = Number($(key).value);
    refresh();
    keepFed(labPlayer, true);
  });
}
for (const key of ['ripples', 'reduced_motion', 'quiet', 'waterline_marks']) {
  $(key).addEventListener('change', () => {
    draft.settings[key] = $(key).checked;
    refresh();
    keepFed(labPlayer, true);
    keepFed(livePlayer, true);
  });
}

function syncCapControls() {
  $('cap-mode').value = labAccent.mode;
  $('cap-color').value = toHex(labAccent.color);
  $('cap-color').disabled = labAccent.mode !== 'color';
  $('cap-brightness').value = labAccent.brightness;
  $('cap-brightness-out').textContent = Math.round(labAccent.brightness) + '%';
}
$('cap-mode').addEventListener('change', () => {
  labAccent.mode = $('cap-mode').value;
  syncCapControls();
});
$('cap-color').addEventListener('input', () => {
  labAccent.color = fromHex($('cap-color').value);
});
$('cap-brightness').addEventListener('input', () => {
  labAccent.brightness = Number($('cap-brightness').value);
  $('cap-brightness-out').textContent = $('cap-brightness').value + '%';
});
$('cap-apply').addEventListener('click', () => {
  applyAccentToAll(labAccent);
  toast('Every rope now uses this cap. Save & apply to send it to the wall.');
});

window.addEventListener('beforeunload', event => {
  if (dirty) { event.preventDefault(); event.returnValue = ''; }
});

(async () => {
  await poll();
  setInterval(() => { if (!document.hidden) poll(); }, 1000);
  requestAnimationFrame(loop);
})();
