/* Telemetry log: what each printer actually reports, one row per change.
   Read-only. Polls /api/logs (diffs only); the raw payload of a row is fetched
   on demand when the row is expanded. Nothing here can touch a light.

   Pause is a real freeze of the reading surface: polling continues quietly,
   new changes are counted, and nothing on screen moves until Resume. */
'use strict';

const $ = id => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

/* ---- what the wall does with a report (mirrors light_studio/model.py) ---- */
const RAW_STATES = {
  RUNNING: 'printing', PREPARE: 'preparing', SLICING: 'preparing', PAUSE: 'paused',
  PAUSED: 'paused', FINISH: 'finished', FINISHED: 'finished', FAILED: 'stopped',
  IDLE: 'idle', READY: 'idle', OFFLINE: 'offline',
};
const ANIMATION = {
  printing: 'water rising through darker bands, rain falling in',
  preparing: 'light sweep rising through a dark breathing body',
  paused: 'slow breathing with steady end marks',
  error: 'deep, fast breathing (the alarm)',
  finished: 'hue wash, then the collect colour until the door opens',
  stopped: 'dark and still until the door opens or Mark collected (then idle)',
  idle: 'resting colour, no motion',
  offline: 'dim heartbeat',
  unknown: 'dashes',
};
const SPEED = { 1: 'Silent', 2: 'Standard', 3: 'Sport', 4: 'Ludicrous' };

/* Plain-English glosses. Fields the wall reads are in USED_BY_WALL; everything
   else is tagged "not used by the wall yet" so Adi can see what is available. */
const GLOSS = {
  gcode_state: v => 'printer state machine: ' + v + (RAW_STATES[v] ? ' → wall reads it as ' + RAW_STATES[v] : ' (wall: unknown)'),
  mc_percent: v => 'progress ' + v + '% → the waterline',
  percent: v => 'duplicate of mc_percent on newer firmware',
  mc_remaining_time: v => v + ' min remaining → time left on the card',
  remain_time: v => 'duplicate of mc_remaining_time on newer firmware',
  layer_num: v => 'now on layer ' + v,
  total_layer_num: v => v + ' layers in this job',
  nozzle_temper: v => 'nozzle ' + v + '° (card only)',
  bed_temper: v => 'bed ' + v + '° (card only)',
  nozzle_target_temper: v => 'nozzle target ' + v + '°',
  bed_target_temper: v => 'bed target ' + v + '°',
  chamber_temper: v => 'chamber ' + v + '°',
  print_error: v => Number(v) ? 'error code ' + v + ' → the rope goes to the alarm breath; the wall does not interpret the code' : 'no error → alarm clears',
  hms: v => (Array.isArray(v) && v.length ? v.length + ' HMS notice(s) — warnings only, the wall ignores them' : 'HMS list empty'),
  spd_lvl: v => 'speed profile ' + (SPEED[v] || v) + ' → rain tempo, trail and splash',
  spd_mag: v => 'feed rate ' + v + '% (shown on the card)',
  home_flag: v => 'flag bits; bit 23 = door ' + ((Number(v) >>> 23) & 1 ? 'OPEN → marks a finished bay collected' : 'closed'),
  subtask_name: v => 'job name on the card: "' + v + '"',
  gcode_file: v => 'file being printed',
  stg_cur: v => 'stage code ' + v + ' (not interpreted by the wall)',
  stg: v => 'list of stages this job will go through',
  stg_cd: v => 'stage countdown ' + v,
  mc_print_stage: v => 'print stage ' + v,
  mc_print_sub_stage: v => 'print sub-stage ' + v,
  mc_print_line_number: v => 'g-code line ' + v,
  mc_stage: v => 'stage ' + v,
  mc_action: v => 'action ' + v,
  mc_err: v => 'controller error ' + v,
  cooling_fan_speed: v => 'part fan ' + v,
  big_fan1_speed: v => 'aux fan ' + v,
  big_fan2_speed: v => 'chamber fan ' + v,
  heatbreak_fan_speed: v => 'heatbreak fan ' + v,
  aux_part_fan: v => 'aux part fan ' + v,
  fan_gear: v => 'all fan speeds packed into one number',
  wifi_signal: v => 'Wi-Fi ' + v,
  lights_report: v => 'printer chamber light',
  ams: v => 'AMS: trays, humidity, temperature',
  ams_status: v => 'AMS status code ' + v,
  ams_rfid_status: v => 'AMS RFID status ' + v,
  vt_tray: v => 'external spool',
  device: v => 'H2D device block: airduct fans, nozzles, bed, extruder (changes every second)',
  info: v => 'firmware info block (changes every second on some models)',
  stat: v => 'status bits ' + v,
  '2D': v => 'laser/2D module block',
  '3D': v => '3D print module block',
  err: v => 'error field ' + v,
  err2: v => 'error field 2: ' + v,
  ap_err: v => 'access point error ' + v,
  aux: v => 'aux block',
  care: v => 'maintenance care block',
  cfg: v => 'config bits ' + v,
  fun: v => 'feature bits ' + v,
  fun2: v => 'feature bits 2 ' + v,
  job: v => 'job block (times, stage list)',
  job_attr: v => 'job attributes ' + v,
  file: v => 'file field ' + v,
  mapping: v => 'filament slot mapping',
  design_id: v => 'MakerWorld design ' + v,
  canvas_id: v => 'canvas ' + v,
  batch_id: v => 'batch ' + v,
  lan_task_id: v => 'LAN task ' + v,
  plate_cnt: v => v + ' plates in the project',
  plate_idx: v => 'plate index ' + v,
  plate_id: v => 'plate ' + v,
  model_id: v => 'model ' + v,
  prepare_per: v => 'prepare ' + v + '%',
  gcode_file_prepare_percent: v => 'file prepare ' + v + '%',
  ver: v => 'report version ' + v,
  vir_slot: v => 'virtual slots',
  state: v => 'state field ' + v,
  printer: v => 'printer block',
  queue: v => 'print queue block',
  hw_switch_state: v => 'hardware switch ' + v,
  upgrade_state: v => 'firmware upgrade state',
  online: v => 'online flags',
  sdcard: v => 'SD card ' + (v ? 'present' : 'absent'),
  print_type: v => 'print started from: ' + (v || 'nothing'),
  print_gcode_action: v => 'g-code action ' + v,
  print_real_action: v => 'real action ' + v,
  nozzle_diameter: v => 'nozzle ' + v + ' mm',
  nozzle_type: v => 'nozzle type ' + v,
  job_id: v => 'job id ' + v,
  task_id: v => 'task id ' + v,
  profile_id: v => 'slicer profile ' + v,
  project_id: v => 'project ' + v,
  subtask_id: v => 'subtask ' + v,
  sequence_id: v => 'message counter',
  command: v => 'message kind: ' + v,
  msg: v => 'msg ' + v,
  force_upgrade: v => 'force upgrade ' + v,
  maintain: v => 'maintenance flags ' + v,
  lifecycle: v => 'lifecycle ' + v,
  s_obj: v => 'skipped objects list',
  filam_bak: v => 'filament backup slots',
  net: v => 'network block (addresses removed)',
  xcam: v => 'AI camera settings',
  xcam_status: v => 'AI camera status ' + v,
  upload: v => 'upload progress block',
  wifi: v => 'Wi-Fi details (removed)',
  queue_number: v => 'queue number ' + v,
  queue_total: v => 'queue total ' + v,
  queue_est: v => 'queue estimate ' + v,
  queue_sts: v => 'queue status ' + v,
  fail_reason: v => 'failure reason ' + (v || 'none'),
  cali_version: v => 'calibration version ' + v,
  flag3: v => 'flag3 bits ' + v,
};
/* Fields the wall actually consumes. Rows with any of these are bold. */
const MEANINGFUL = new Set(['gcode_state', 'mc_percent', 'layer_num', 'total_layer_num', 'print_error',
  'spd_lvl', 'spd_mag', 'home_flag', 'subtask_name', 'mc_remaining_time']);
/* Sensor and firmware churn: rows made only of these are "ticking". */
const NOISY = new Set(['nozzle_temper', 'bed_temper', 'chamber_temper', 'wifi_signal', 'cooling_fan_speed',
  'big_fan1_speed', 'big_fan2_speed', 'heatbreak_fan_speed', 'aux_part_fan', 'fan_gear', 'sequence_id',
  'mc_print_line_number', 'nozzle_target_temper', 'bed_target_temper', 'device', 'info', 'stat',
  'remain_time', 'percent', 'ams', 'net', '3D', '2D', 'aux', 'stg_cd', 'prepare_per',
  'gcode_file_prepare_percent', 'upload', 'xcam_status', 'online']);
const USED_BY_WALL = new Set([...MEANINGFUL, 'nozzle_temper', 'bed_temper']);
const STATE_WORD = { printing: 'Printing', preparing: 'Preparing', paused: 'Paused', error: 'Error',
  finished: 'Collect', stopped: 'Stopped early', idle: 'Available', offline: 'Offline', unknown: 'Unknown' };

/* ---- state ---- */
let paused = false;
/* One printer at a time by default: the one deep-linked in the hash
   (/logs#printer3), otherwise the first bay. "All printers" is the optional
   side-by-side comparison, with each feed bounded and scrolling on its own. */
const wanted = decodeURIComponent(window.location.hash.slice(1));
let selected = wanted && wanted.startsWith('printer') && wanted.length <= 12 ? wanted : 'first';
let demo = false;
let data = { printers: [] };           // what is on screen
let pending = null;                    // newest payload received while paused
let pausedAt = null;
let lastSignature = '';
let stickToBottom = true;
let failures = 0;
const open = new Set();               // "printer|at" of expanded rows
const rawCache = new Map();           // "printer|at" -> raw payload or null

function fmtTime(epoch) {
  return new Date(epoch * 1000).toLocaleTimeString([], { hour12: false });
}
function fmtVal(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'object') {
    const s = JSON.stringify(v);
    return s.length > 60 ? s.slice(0, 57) + '…' : s;
  }
  return String(v);
}

/* The wall reading for a row comes from the server's historical interpretation
   of the buffer up to that report (entry.wall). Nothing here ever falls back to
   the printer's *current* status: an old row must never be relabelled by a
   later error, and today's speed profile is not evidence about yesterday. */
function wallLine(entry) {
  const w = entry.wall || { state: null, basis: 'unavailable', speed: null };
  const line = el('div', 'wall');
  if (!w.state) {
    line.append('wall → ');
    line.append(el('b', '', 'historical reading unavailable'));
    line.append(' · no state field in the buffer up to this report');
    return line;
  }
  line.append('wall → ');
  line.append(el('b', '', STATE_WORD[w.state] || w.state));
  line.append(' · ' + (ANIMATION[w.state] || '') + (w.state === 'printing' && w.speed ? ' at ' + w.speed : ''));
  if (w.basis === 'carried') line.append(el('span', 'tag', 'state carried from an earlier report'));
  line.append(el('span', 'tag', 'from the reports up to this one · not live status'));
  return line;
}

function classify(changed) {
  const keys = Object.keys(changed);
  if (keys.some(k => MEANINGFUL.has(k))) return 'meaningful';
  if (keys.every(k => NOISY.has(k))) return 'tick';
  return 'other';
}

/* One readable sentence per row. Meaningful fields first, in reading order. */
function headline(entry) {
  const c = entry.changed;
  const bits = [];
  if (c.gcode_state) {
    const to = String(c.gcode_state[1]).toUpperCase();
    bits.push((entry.first ? 'State ' : 'State ' + fmtVal(c.gcode_state[0]) + ' → ') + to
      + (RAW_STATES[to] ? ' (' + STATE_WORD[RAW_STATES[to]] + ')' : ''));
  }
  if (c.print_error) bits.push(Number(c.print_error[1]) ? 'Error reported · code ' + c.print_error[1] : 'Error cleared');
  if (c.mc_percent) bits.push('Progress ' + (entry.first ? '' : fmtVal(c.mc_percent[0]) + '% → ') + c.mc_percent[1] + '%');
  if (c.layer_num) bits.push('Layer ' + c.layer_num[1] + (c.total_layer_num ? ' of ' + c.total_layer_num[1] : ''));
  else if (c.total_layer_num) bits.push(c.total_layer_num[1] + ' layers in job');
  if (c.spd_lvl) bits.push('Speed → ' + (SPEED[c.spd_lvl[1]] || c.spd_lvl[1]));
  if (c.home_flag) {
    const door = (Number(c.home_flag[1]) >>> 23) & 1;
    const was = (Number(c.home_flag[0]) >>> 23) & 1;
    if (door !== was) bits.push(door ? 'Door opened' : 'Door closed');
  }
  if (c.subtask_name) bits.push('Job "' + fmtVal(c.subtask_name[1]) + '"');
  if (c.mc_remaining_time) bits.push(c.mc_remaining_time[1] + ' min left');
  if (bits.length) return bits.join(' · ');
  const keys = Object.keys(c);
  if (keys.every(k => NOISY.has(k))) {
    const shown = keys.filter(k => typeof c[k][1] !== 'object').slice(0, 4);
    const short = k => k.endsWith('_temper') ? k.slice(0, -7) : k.endsWith('_speed') ? k.slice(0, -6) : k;
    return 'ticking · ' + (shown.length ? shown.map(k => short(k) + ' ' + fmtVal(c[k][1])).join(' · ')
      : keys.slice(0, 4).join(', ')) + (keys.length > 4 ? ' · +' + (keys.length - 4) : '');
  }
  return keys.length + ' field' + (keys.length === 1 ? '' : 's') + ' changed: ' + keys.slice(0, 5).join(', ') + (keys.length > 5 ? ', …' : '');
}

async function loadRaw(printer, entry, pre) {
  const key = printer + '|' + entry.at;
  const gone = 'This report has left the buffer on the Pi (it keeps the last 400 per printer).';
  if (rawCache.has(key)) {
    const cached = rawCache.get(key);
    pre.textContent = cached ? JSON.stringify(cached, null, 1) : gone;
    return;
  }
  pre.textContent = 'loading raw report…';
  try {
    const url = '/api/logs?raw=1&limit=400&printer=' + encodeURIComponent(printer) + '&since=' + encodeURIComponent(entry.at - 0.0015);
    const r = await fetch(url, { cache: 'no-store' });
    const body = await r.json();
    const p = body.printers[0];
    const hit = p && p.entries.find(e => Math.abs(e.at - entry.at) < 0.001);
    rawCache.set(key, hit ? hit.raw : null);
    pre.textContent = hit ? JSON.stringify(hit.raw, null, 1) : gone;
  } catch (e) {
    pre.textContent = 'could not load: ' + e.message;
  }
}

function renderEntry(printer, entry) {
  const kind = classify(entry.changed);
  const key = printer + '|' + entry.at;
  const row = el('article', 'entry ' + kind + (entry.first ? ' first' : '') + (open.has(key) ? ' open' : ''));
  row.dataset.key = key;
  const when = el('div', 'when');
  when.append(el('span', '', fmtTime(entry.at)));
  if (entry.repeats) when.append(el('span', 'rep', '×' + (entry.repeats + 1) + ' until ' + fmtTime(entry.last_at)));
  when.append(el('span', 'rep', entry.keys + ' fields in report'));
  row.append(when);
  row.append(el('div', 'head', headline(entry)));

  const list = el('ul', 'changes');
  const showUnknown = $('show-unknown').checked;
  let hiddenCount = 0;
  for (const [field, [before, now]] of Object.entries(entry.changed)) {
    const used = USED_BY_WALL.has(field);
    const known = Object.prototype.hasOwnProperty.call(GLOSS, field);
    if (!showUnknown && !used) { hiddenCount += 1; continue; }
    const li = el('li');
    li.append(el('span', 'field' + (used ? '' : ' unused'), field));
    const val = el('span', 'val');
    if (!entry.first) { val.append(fmtVal(before)); val.append(el('span', 'arrow', '→')); }
    val.append(fmtVal(now));
    let gloss = '';
    try { gloss = known ? GLOSS[field](now) : ''; } catch (e) { gloss = ''; }
    if (gloss) val.append(el('span', 'gloss', '  — ' + gloss));
    if (!used) val.append(el('span', 'tag', known ? 'not used by the wall yet' : 'unknown field · not used'));
    li.append(val);
    list.append(li);
  }
  row.append(list);
  if (hiddenCount) {
    row.append(el('div', 'more', '+ ' + hiddenCount + ' other field' + (hiddenCount === 1 ? '' : 's')
      + ' the wall does not use · tick "Show every field" or open the raw report'));
  }

  row.append(wallLine(entry));

  // The raw report is behind a real button, so it is reachable and operable
  // from the keyboard (Enter / Space) as well as by clicking the row.
  const pre = el('pre', '', '');
  pre.id = 'raw-' + printer + '-' + String(entry.at).replace('.', '-');
  const toggle = el('button', 'text raw-toggle', open.has(key) ? 'Hide raw report' : 'View raw report');
  toggle.type = 'button';
  toggle.setAttribute('aria-expanded', String(open.has(key)));
  toggle.setAttribute('aria-controls', pre.id);
  const setOpen = isOpen => {
    if (isOpen) { open.add(key); row.classList.add('open'); loadRaw(printer, entry, pre); }
    else { open.delete(key); row.classList.remove('open'); }
    toggle.textContent = isOpen ? 'Hide raw report' : 'View raw report';
    toggle.setAttribute('aria-expanded', String(isOpen));
  };
  toggle.addEventListener('click', event => { event.stopPropagation(); setOpen(!open.has(key)); });
  row.append(toggle, pre);
  if (open.has(key)) loadRaw(printer, entry, pre);
  row.addEventListener('click', event => {
    // Clicking anywhere on the row still works, but not on links or text
    // selections inside an opened report.
    if (event.target.closest('pre') || event.target.closest('a')) return;
    setOpen(!open.has(key));
  });
  return row;
}

function renderPrinter(p) {
  const col = el('section', 'printer-col');
  col.dataset.printer = p.printer;
  const head = el('header');
  const left = el('div');
  left.append(el('div', 'name', p.label + ' · ' + p.printer + (demo ? ' · simulated' : '')));
  const st = p.status || {};
  const sub = [];
  if (st.state) sub.push('wall now: ' + (STATE_WORD[st.state] || st.state));
  if (st.speed) sub.push(st.speed + (st.speed_percent ? ' ' + st.speed_percent + '%' : ''));
  if (st.percent !== null && st.percent !== undefined && st.state === 'printing') sub.push(Math.round(st.percent) + '%');
  if (st.job && st.state !== 'idle') sub.push('"' + st.job + '"');
  left.append(el('div', 'sub', sub.join(' · ') || 'no telemetry yet'));
  head.append(left);
  head.append(el('div', 'sub', p.reports_kept + ' of the last 400 reports kept · ' + p.entries.length + ' changes in this window'));
  col.append(head);

  const box = el('div', 'entries');
  const hideTicks = $('hide-ticks').checked;
  let shown = 0;
  let folded = 0;
  for (const entry of p.entries) {
    if (hideTicks && classify(entry.changed) === 'tick') { folded += 1; continue; }
    box.append(renderEntry(p.printer, entry));
    shown += 1;
  }
  if (!shown) {
    box.append(el('div', 'empty', p.reports_kept
      ? (folded ? folded + ' ticking-only report' + (folded === 1 ? '' : 's') + ' folded away in this window. Nothing meaningful changed; untick "Fold sensor ticking" to see them.'
                : 'Nothing has changed in this window yet.')
      : 'No reports from this printer yet. It may be off, or the Pi has just restarted; the buffer fills as reports arrive.'));
  } else if (folded) {
    box.append(el('div', 'empty', folded + ' ticking-only report' + (folded === 1 ? '' : 's') + ' folded away.'));
  }
  col.append(box);
  return col;
}

function renderTabs() {
  const tabs = $('tabs');
  tabs.replaceChildren();
  const all = el('button', 'logs-tab');
  all.setAttribute('role', 'tab');
  all.setAttribute('aria-selected', String(selected === 'all'));
  all.append(el('span', 't-label', 'All printers'));
  all.append(el('span', 't-state', 'compare side by side'));
  all.addEventListener('click', () => { selected = 'all'; history.replaceState(null, '', '#all'); render(true); });
  for (const p of data.printers) {
    const b = el('button', 'logs-tab');
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', String(selected === p.printer));
    b.append(el('span', 't-label', p.label));
    const st = p.status || {};
    b.append(el('span', 't-state ' + (st.state || 'unknown'),
      (st.state ? (STATE_WORD[st.state] || st.state) : 'no telemetry')
      + (st.state === 'printing' && st.percent != null ? ' ' + Math.round(st.percent) + '%' : '')));
    b.addEventListener('click', () => { selected = p.printer; history.replaceState(null, '', '#' + p.printer); render(true); });
    tabs.append(b);
  }
  tabs.append(all);
}

function signature() {
  return [selected, $('limit').value, $('hide-ticks').checked, $('show-unknown').checked,
    ...data.printers.map(p => p.printer + ':' + p.reports_kept + ':' + p.entries.length + ':'
      + (p.entries.length ? p.entries[p.entries.length - 1].last_at : 0) + ':' + (p.status ? p.status.state : ''))].join('|');
}

/* Re-render only when something on screen would change, keeping scroll
   positions per printer and expanded rows (they are keyed by printer|time). */
function render(force) {
  const sig = signature();
  if (!force && sig === lastSignature) return;
  lastSignature = sig;
  renderTabs();
  const feed = $('feed');
  const scrollers = {};
  for (const col of feed.querySelectorAll('.printer-col')) {
    const box = col.querySelector('.entries');
    if (box) scrollers[col.dataset.printer] = box.scrollTop;
  }
  const pageY = window.scrollY;
  feed.replaceChildren();
  feed.classList.toggle('all', selected === 'all');
  const printers = selected === 'all' ? data.printers : data.printers.filter(p => p.printer === selected);
  if (!printers.length) {
    feed.append(el('div', 'empty', data.printers.length ? 'That printer is not in the current layout.' : 'No printers reported yet.'));
  }
  for (const p of printers) {
    const col = renderPrinter(p);
    feed.append(col);
    const box = col.querySelector('.entries');
    if (!paused && stickToBottom && !(p.printer in scrollers)) box.scrollTop = box.scrollHeight;
    else if (scrollers[p.printer] !== undefined) box.scrollTop = scrollers[p.printer];
    else box.scrollTop = box.scrollHeight;
  }
  if (selected !== 'all' && !paused && stickToBottom && force) window.scrollTo(0, document.body.scrollHeight);
  else window.scrollTo(0, pageY);
}

function countNew(oldData, newData) {
  let n = 0;
  for (const p of newData.printers) {
    const before = oldData.printers.find(q => q.printer === p.printer);
    const lastSeen = before && before.entries.length ? before.entries[before.entries.length - 1].last_at : 0;
    n += p.entries.filter(e => e.last_at > lastSeen).length;
  }
  return n;
}

function describe(payload, live) {
  const kept = payload.printers.reduce((n, p) => n + p.reports_kept, 0);
  const source = payload.demo ? 'Demo · simulated reports' : 'Live';
  $('conn').textContent = live
    ? source + ' · updated ' + fmtTime(payload.at)
    : 'Paused since ' + fmtTime(pausedAt) + ' · showing ' + fmtTime(data.at);
  $('conn-sub').textContent = payload.demo
    ? 'Hardware-free demonstration · nothing here touches the lights'
    : 'Read only · nothing here touches the lights';
  $('history').textContent = 'history: ' + kept + (payload.demo ? ' simulated' : '') + ' reports held on the Pi across '
    + payload.printers.length + ' printers (last 400 each, memory only) · redacted before display';
}

async function poll() {
  try {
    const limit = $('limit').value;
    const r = await fetch('/api/logs?limit=' + encodeURIComponent(limit), { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const payload = await r.json();
    failures = 0;
    if (paused) {
      pending = payload;
      const fresh = countNew(data, payload);
      $('pause-note').textContent = fresh
        ? fresh + ' new change' + (fresh === 1 ? '' : 's') + ' since you paused · Resume to jump to the latest'
        : 'no new changes since you paused';
      $('pause').textContent = 'Resume' + (fresh ? ' (' + fresh + ' new)' : '');
      describe(payload, false);
      return;
    }
    data = payload;
    demo = payload.demo === true;
    if (selected === 'first' || (selected !== 'all' && !data.printers.some(p => p.printer === selected))) {
      selected = data.printers.length ? data.printers[0].printer : 'all';
    }
    describe(payload, true);
    render(false);
  } catch (e) {
    failures += 1;
    $('conn').textContent = (paused ? 'Paused · ' : '') + 'Pi not reachable: ' + e.message;
    if (!data.printers.length) {
      $('feed').replaceChildren(el('div', 'empty error',
        'Cannot load telemetry from the Pi (' + e.message + '). Retrying every 3 seconds.'));
    }
  }
}

$('pause').addEventListener('click', () => {
  paused = !paused;
  $('pause').setAttribute('aria-pressed', String(paused));
  $('pause-note').hidden = !paused;
  if (paused) {
    pausedAt = Date.now() / 1000;
    pending = null;
    $('pause').textContent = 'Resume';
    $('pause-note').textContent = 'reading surface frozen · new changes are counted, not shown';
    $('conn').textContent = 'Paused since ' + fmtTime(pausedAt);
  } else {
    $('pause').textContent = 'Pause';
    if (pending) { data = pending; pending = null; }
    stickToBottom = true;
    describe(data, true);
    render(true);
    poll();
  }
});
$('limit').addEventListener('change', () => { if (!paused) poll(); else render(true); });
$('hide-ticks').addEventListener('change', () => render(true));
$('show-unknown').addEventListener('change', () => render(true));
document.addEventListener('scroll', () => {
  stickToBottom = (window.innerHeight + window.scrollY) >= document.body.scrollHeight - 40;
}, { passive: true });

if (window.innerWidth <= 720) $('limit').value = '20';   // a phone reads one printer, twenty changes
window.addEventListener('hashchange', () => {
  const target = decodeURIComponent(window.location.hash.slice(1));
  if (target === 'all' || data.printers.some(p => p.printer === target)) { selected = target; render(true); }
});
poll();
setInterval(poll, 3000);
