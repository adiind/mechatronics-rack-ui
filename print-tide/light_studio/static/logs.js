/* Telemetry log: what each printer actually reports, one row per change.
   Read-only. Polls /api/logs (diffs only); the raw payload of a row is fetched
   on demand when the row is expanded. Nothing here can touch a light. */
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
  finished: 'hue wash, then the lightest colour until the door opens',
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
  mc_remaining_time: v => v + ' min remaining → ETA on the card',
  remain_time: v => 'duplicate of mc_remaining_time on newer firmware',
  layer_num: v => 'now on layer ' + v,
  total_layer_num: v => v + ' layers in this job',
  nozzle_temper: v => 'nozzle ' + v + '° (card only)',
  bed_temper: v => 'bed ' + v + '° (card only)',
  nozzle_target_temper: v => 'nozzle target ' + v + '°',
  bed_target_temper: v => 'bed target ' + v + '°',
  chamber_temper: v => 'chamber ' + v + '°',
  print_error: v => Number(v) ? 'ERROR CODE ' + v + ' → the rope goes to the alarm breath' : 'no error → alarm clears',
  hms: v => (Array.isArray(v) && v.length ? v.length + ' HMS notice(s) — warnings only, the wall ignores them' : 'HMS list empty'),
  spd_lvl: v => 'speed profile ' + (SPEED[v] || v) + ' → rain tempo, trail and splash',
  spd_mag: v => 'feed rate ' + v + '% (shown on the card)',
  home_flag: v => 'flag bits; bit 23 = door ' + ((Number(v) >>> 23) & 1 ? 'OPEN → marks a finished bay collected' : 'closed'),
  subtask_name: v => 'job name on the card: "' + v + '"',
  gcode_file: v => 'file being printed',
  stg_cur: v => ({ '-1': 'stage -1: not printing', '0': 'stage 0: printing', '1': 'stage 1: auto bed levelling', '2': 'stage 2: heatbed preheating', '3': 'stage 3: sweeping XY mech mode', '4': 'stage 4: changing filament', '5': 'stage 5: M400 pause', '6': 'stage 6: paused, filament runout', '7': 'stage 7: heating hotend', '8': 'stage 8: calibrating extrusion', '9': 'stage 9: scanning bed surface', '10': 'stage 10: inspecting first layer', '11': 'stage 11: identifying build plate', '12': 'stage 12: calibrating micro lidar', '13': 'stage 13: homing toolhead', '14': 'stage 14: cleaning nozzle tip', '15': 'stage 15: checking extruder temperature', '16': 'stage 16: paused by the user', '17': 'stage 17: pause, front cover falling', '18': 'stage 18: calibrating micro lidar', '19': 'stage 19: calibrating extrusion flow', '20': 'stage 20: paused, nozzle temperature malfunction', '21': 'stage 21: paused, heat bed temperature malfunction' }[String(v)] || 'stage ' + v) + ' — could drive a "preparing" sub-animation',
  stg: v => 'list of stages this job will go through',
  stg_cd: v => 'stage countdown ' + v,
  mc_print_stage: v => 'print stage ' + v + ' (1 idle, 2 printing, 3 paused)',
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

/* ---- state ---- */
let paused = false;
let selected = 'all';
let data = { printers: [] };
let stickToBottom = true;
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
function after(entry, field) {
  return entry.changed[field] ? entry.changed[field][1] : undefined;
}

function wallReading(entry, status) {
  // Same rules as model.normalize: an error code wins, then the gcode_state map.
  const err = after(entry, 'print_error');
  const hasError = err !== undefined ? Number(err) !== 0 : Boolean(status && status.state === 'error');
  if (hasError) return 'error';
  const gs = after(entry, 'gcode_state');
  if (gs !== undefined) return RAW_STATES[String(gs).toUpperCase()] || 'unknown';
  return status ? status.state : 'unknown';
}

function classify(changed) {
  const keys = Object.keys(changed);
  if (keys.some(k => MEANINGFUL.has(k))) return 'meaningful';
  if (keys.every(k => NOISY.has(k))) return 'tick';
  return 'other';
}

function headline(entry) {
  const c = entry.changed;
  const bits = [];
  if (c.gcode_state) bits.push('state ' + fmtVal(c.gcode_state[0]) + ' → ' + fmtVal(c.gcode_state[1]));
  if (c.print_error) bits.push(Number(c.print_error[1]) ? 'ERROR ' + c.print_error[1] : 'error cleared');
  if (c.mc_percent) bits.push(c.mc_percent[1] + '%');
  if (c.layer_num) bits.push('layer ' + c.layer_num[1] + (c.total_layer_num ? '/' + c.total_layer_num[1] : ''));
  if (c.spd_lvl) bits.push('speed → ' + (SPEED[c.spd_lvl[1]] || c.spd_lvl[1]));
  if (c.home_flag) {
    const door = (Number(c.home_flag[1]) >>> 23) & 1;
    const was = (Number(c.home_flag[0]) >>> 23) & 1;
    if (door !== was) bits.push(door ? 'door opened' : 'door closed');
  }
  if (c.subtask_name) bits.push('job "' + fmtVal(c.subtask_name[1]) + '"');
  if (c.mc_remaining_time) bits.push(c.mc_remaining_time[1] + ' min left');
  if (bits.length) return bits.join(' · ');
  const keys = Object.keys(c);
  if (keys.every(k => NOISY.has(k))) {
    const shown = keys.filter(k => typeof c[k][1] !== 'object').slice(0, 4);
    return 'ticking · ' + (shown.length ? shown.map(k => k.replace(/_temper$/, '').replace(/_speed$/, '') + ' ' + fmtVal(c[k][1])).join(' · ')
      : keys.slice(0, 4).join(', ')) + (keys.length > 4 ? ' · +' + (keys.length - 4) : '');
  }
  return keys.length + ' field' + (keys.length === 1 ? '' : 's') + ' changed: ' + keys.slice(0, 5).join(', ') + (keys.length > 5 ? ', …' : '');
}

async function loadRaw(printer, entry, pre) {
  const key = printer + '|' + entry.at;
  if (rawCache.has(key)) {
    const cached = rawCache.get(key);
    pre.textContent = cached ? JSON.stringify(cached, null, 1) : 'This report has left the buffer on the Pi (it keeps the last 400 per printer).';
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
    pre.textContent = hit ? JSON.stringify(hit.raw, null, 1) : 'This report has left the buffer on the Pi (it keeps the last 400 per printer).';
  } catch (e) {
    pre.textContent = 'could not load: ' + e.message;
  }
}

function renderEntry(printer, entry, status) {
  const kind = classify(entry.changed);
  const key = printer + '|' + entry.at;
  const row = el('article', 'entry ' + kind + (entry.first ? ' first' : '') + (open.has(key) ? ' open' : ''));
  const when = el('div', 'when');
  when.append(el('span', '', fmtTime(entry.at)));
  if (entry.repeats) when.append(el('span', 'rep', '×' + (entry.repeats + 1) + ' until ' + fmtTime(entry.last_at)));
  when.append(el('span', 'rep', entry.keys + ' fields in report'));
  row.append(when);
  row.append(el('div', 'head', headline(entry)));

  const list = el('ul', 'changes');
  const showUnknown = $('show-unknown').checked;
  for (const [field, [before, now]] of Object.entries(entry.changed)) {
    const used = USED_BY_WALL.has(field);
    const known = Object.prototype.hasOwnProperty.call(GLOSS, field);
    if (!showUnknown && !used) continue;
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

  const state = wallReading(entry, status);
  const lvl = after(entry, 'spd_lvl');
  const speed = lvl !== undefined ? SPEED[lvl] : (status && status.speed ? status.speed : null);
  const wall = el('div', 'wall');
  wall.append('wall → ');
  wall.append(el('b', '', state));
  wall.append(' · ' + (ANIMATION[state] || '') + (state === 'printing' && speed ? ' at ' + speed : ''));
  row.append(wall);

  const pre = el('pre', '', '');
  row.append(pre);
  if (open.has(key)) loadRaw(printer, entry, pre);
  row.addEventListener('click', () => {
    if (open.has(key)) { open.delete(key); row.classList.remove('open'); }
    else { open.add(key); row.classList.add('open'); loadRaw(printer, entry, pre); }
  });
  return row;
}

function renderPrinter(p) {
  const col = el('section', 'printer-col');
  const head = el('header');
  const left = el('div');
  left.append(el('div', 'name', p.label + ' · ' + p.printer));
  const st = p.status || {};
  const sub = [];
  if (st.state) sub.push('wall: ' + st.state);
  if (st.speed) sub.push(st.speed + (st.speed_percent ? ' ' + st.speed_percent + '%' : ''));
  if (st.percent !== null && st.percent !== undefined && st.state === 'printing') sub.push(Math.round(st.percent) + '%');
  if (st.job && st.state !== 'idle') sub.push('"' + st.job + '"');
  left.append(el('div', 'sub', sub.join(' · ') || 'no telemetry yet'));
  head.append(left);
  head.append(el('div', 'sub', p.reports_kept + ' reports kept · ' + p.entries.length + ' changes shown'));
  col.append(head);

  const box = el('div', 'entries');
  const hideTicks = $('hide-ticks').checked;
  let shown = 0;
  for (const entry of p.entries) {
    if (hideTicks && classify(entry.changed) === 'tick') continue;
    box.append(renderEntry(p.printer, entry, p.status));
    shown += 1;
  }
  if (!shown) box.append(el('div', 'empty', p.reports_kept
    ? 'Nothing but ticking in this window. Untick "Hide ticking" or wait for the printer to do something.'
    : 'No reports from this printer yet. It may be off, or the Pi has just restarted; the buffer fills as reports arrive.'));
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
  all.append(el('span', 't-meta', 'side by side'));
  all.addEventListener('click', () => { selected = 'all'; render(); });
  tabs.append(all);
  for (const p of data.printers) {
    const b = el('button', 'logs-tab');
    b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', String(selected === p.printer));
    b.append(el('span', 't-label', p.label));
    const st = p.status || {};
    b.append(el('span', 't-meta', p.printer + (st.speed ? ' · ' + st.speed : '')));
    b.append(el('span', 't-state ' + (st.state || 'unknown'),
      (st.state || 'no telemetry') + (st.state === 'printing' && st.percent != null ? ' ' + Math.round(st.percent) + '%' : '')));
    b.addEventListener('click', () => { selected = p.printer; render(); });
    tabs.append(b);
  }
}

function render() {
  renderTabs();
  const feed = $('feed');
  const scrollers = [...feed.querySelectorAll('.entries')].map(e => e.scrollTop);
  feed.replaceChildren();
  feed.classList.toggle('all', selected === 'all');
  const printers = selected === 'all' ? data.printers : data.printers.filter(p => p.printer === selected);
  printers.forEach((p, i) => {
    const col = renderPrinter(p);
    feed.append(col);
    const box = col.querySelector('.entries');
    if (!paused && stickToBottom) box.scrollTop = box.scrollHeight;
    else if (scrollers[i] !== undefined) box.scrollTop = scrollers[i];
  });
  if (selected !== 'all' && !paused && stickToBottom) window.scrollTo(0, document.body.scrollHeight);
}

async function poll() {
  if (paused) return;
  try {
    const limit = $('limit').value;
    const r = await fetch('/api/logs?limit=' + encodeURIComponent(limit), { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    data = await r.json();
    const kept = data.printers.reduce((n, p) => n + p.reports_kept, 0);
    $('conn').textContent = 'live · ' + kept + ' reports buffered on the Pi · updated ' + fmtTime(data.at);
    render();
  } catch (e) {
    $('conn').textContent = 'not reachable: ' + e.message;
  }
}

$('pause').addEventListener('click', () => {
  paused = !paused;
  $('pause').textContent = paused ? 'Resume' : 'Pause';
  $('pause').classList.toggle('paused', paused);
  if (!paused) poll();
});
$('limit').addEventListener('change', poll);
$('hide-ticks').addEventListener('change', render);
$('show-unknown').addEventListener('change', render);
document.addEventListener('scroll', () => {
  stickToBottom = (window.innerHeight + window.scrollY) >= document.body.scrollHeight - 40;
}, { passive: true });

poll();
setInterval(poll, 3000);
