const state = { snapshot: null, results: [], selected: new Set(), operatorToken: null, activeBin: null, matched: null };

const CATEGORY_LABELS = {
  control_prototyping: 'Control & prototyping',
  inputs_sensing: 'Inputs & sensing',
  actuators_outputs: 'Actuators & outputs',
  drivers_power_electronics: 'Drivers, power & electronics',
  mechanisms_build_hardware: 'Mechanisms & build hardware',
  material: 'Material',
  electronic_part: 'Electronic part',
  tool: 'Tool',
  machine: 'Machine',
  consumable: 'Consumable',
};
const CATEGORY_COLORS = {
  control_prototyping: 'var(--cat-control)',
  inputs_sensing: 'var(--cat-inputs)',
  actuators_outputs: 'var(--cat-actuators)',
  drivers_power_electronics: 'var(--cat-drivers)',
  mechanisms_build_hardware: 'var(--cat-mechanisms)',
};

function categoryLabel(slug) {
  return CATEGORY_LABELS[slug] || slug.replace(/_/g, ' ');
}

function categoryColor(slug) {
  return CATEGORY_COLORS[slug] || 'var(--cat-other)';
}

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers || {});
  if (options.body) headers['Content-Type'] = 'application/json';
  if (state.operatorToken) headers['X-Rack-Operator'] = state.operatorToken;
  const response = await fetch(path, Object.assign({}, options, { headers }));
  const text = await response.text();
  const isJson = (response.headers.get('content-type') || '').includes('json');
  const payload = text && isJson ? JSON.parse(text) : text;
  if (!response.ok) throw new Error(payload && payload.error ? payload.error : 'request_failed');
  return payload;
}

function litBinIds() {
  const highlight = state.snapshot && state.snapshot.highlight;
  return new Set(highlight ? highlight.lit.map((entry) => entry.bin_id) : []);
}

function orderedBinIds(snapshot) {
  const ids = Object.keys(snapshot.bins).sort();
  if (snapshot.rack.origin !== 'bottom-left') return ids;
  const rows = [];
  for (let index = 0; index < ids.length; index += snapshot.rack.columns) {
    rows.push(ids.slice(index, index + snapshot.rack.columns));
  }
  return rows.reverse().flat();
}

function shortLabel(binId) {
  return binId.replace(/^bin-/, '');
}

function drawerFront(binId) {
  const cell = document.createElement('button');
  cell.type = 'button';
  cell.className = 'bin-cell';
  cell.dataset.binId = binId;

  const plate = document.createElement('span');
  plate.className = 'label-plate';
  const code = document.createElement('span');
  code.className = 'bin-label';
  code.textContent = shortLabel(binId);
  const name = document.createElement('span');
  name.className = 'bin-item';
  const qty = document.createElement('span');
  qty.className = 'bin-qty';
  plate.append(code, name, qty);

  const pull = document.createElement('span');
  pull.className = 'pull';
  pull.setAttribute('aria-hidden', 'true');

  cell.append(plate, pull);
  cell.addEventListener('click', () => {
    state.activeBin = binId;
    showBinDetail(binId);
    paintCells();
  });
  return cell;
}

function paintCell(cell, binId, bin, isLit) {
  const category = bin.items.length ? bin.items[0].category : null;
  const filtered = state.matched !== null;
  cell.className = [
    'bin-cell',
    `state-${bin.state}`,
    category ? `cat-${category}` : '',
    isLit ? 'is-lit' : '',
    state.activeBin === binId ? 'is-selected' : '',
    filtered && state.matched.has(binId) ? 'is-match' : '',
    filtered && !state.matched.has(binId) ? 'is-dimmed' : '',
  ]
    .filter(Boolean)
    .join(' ');

  const name = cell.querySelector('.bin-item');
  const qty = cell.querySelector('.bin-qty');
  if (bin.items.length) {
    const item = bin.items[0];
    name.textContent = item.display_name;
    name.title = item.display_name;
    qty.textContent = item.quantity === null ? '' : `${item.quantity} ${item.unit}`;
    cell.setAttribute('aria-label', `${shortLabel(binId)}, ${item.display_name}`);
  } else {
    name.textContent = bin.state === 'empty' ? 'empty' : 'not checked';
    name.title = '';
    qty.textContent = '';
    cell.setAttribute('aria-label', `${shortLabel(binId)}, ${bin.state}`);
  }
}

/* Cells are built once and repainted in place. Rebuilding them on every
   2-second poll would throw away keyboard focus mid-tab and reset scroll. */
function paintCells() {
  const snapshot = state.snapshot;
  if (!snapshot) return;
  const grid = document.getElementById('rack-grid');
  const wanted = orderedBinIds(snapshot);
  const existing = [...grid.querySelectorAll('.bin-cell')].map((cell) => cell.dataset.binId);
  if (existing.length !== wanted.length || existing.some((binId, index) => binId !== wanted[index])) {
    grid.style.gridTemplateColumns = `repeat(${snapshot.rack.columns}, minmax(0, 1fr))`;
    grid.replaceChildren(...wanted.map((binId) => drawerFront(binId)));
  }
  const lit = litBinIds();
  grid.querySelectorAll('.bin-cell').forEach((cell) => {
    const binId = cell.dataset.binId;
    paintCell(cell, binId, snapshot.bins[binId], lit.has(binId));
  });
}

function renderLegend() {
  const legend = document.getElementById('legend');
  if (!legend || !state.snapshot) return;
  const counts = new Map();
  let free = 0;
  Object.values(state.snapshot.bins).forEach((bin) => {
    if (!bin.items.length) {
      free += 1;
      return;
    }
    const slug = bin.items[0].category;
    counts.set(slug, (counts.get(slug) || 0) + 1);
  });
  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  legend.replaceChildren(
    ...entries.map(([slug, count]) => {
      const row = document.createElement('li');
      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.background = categoryColor(slug);
      row.append(swatch, document.createTextNode(`${categoryLabel(slug)} · ${count}`));
      return row;
    }),
    (() => {
      const row = document.createElement('li');
      const swatch = document.createElement('span');
      swatch.className = 'swatch';
      swatch.style.background = '#e6e3dc';
      row.append(swatch, document.createTextNode(`still free · ${free}`));
      return row;
    })()
  );
}

function renderRack() {
  const snapshot = state.snapshot;
  if (!snapshot) return;
  document.getElementById('rack-title').textContent = snapshot.rack.display_name;
  const filled = Object.values(snapshot.bins).filter((bin) => bin.items.length).length;
  const total = Object.keys(snapshot.bins).length;
  const noun = snapshot.rack.unit_style === 'drawer_cabinet' ? 'drawers' : 'bins';
  document.getElementById('rack-subtitle').textContent = `${filled} of ${total} ${noun} filled`;

  const availability = snapshot.endpoint_availability;
  const status = document.getElementById('endpoint-status');
  status.textContent = 'Rack lights: ' + availability;
  status.className =
    'endpoint' + (availability === 'online' ? ' is-online' : availability === 'offline' ? ' is-offline' : '');

  paintCells();
  renderLegend();

  const banner = document.getElementById('highlight-banner');
  if (snapshot.highlight) {
    const bins = snapshot.highlight.lit.map((entry) => shortLabel(entry.bin_id)).join(', ');
    banner.textContent = `Lit now: ${bins} — turning off in ${snapshot.highlight.expires_in}s`;
    banner.hidden = false;
  } else if (!banner.dataset.sticky) {
    banner.hidden = true;
  }
}

function field(term, value) {
  const dt = document.createElement('dt');
  dt.textContent = term;
  const dd = document.createElement('dd');
  dd.textContent = value;
  return [dt, dd];
}

function showBinDetail(binId) {
  const bin = state.snapshot.bins[binId];
  const detail = document.getElementById('bin-detail');
  detail.replaceChildren();

  const code = document.createElement('div');
  code.className = 'code';
  code.textContent = `${state.snapshot.rack.rack_id} · ${shortLabel(binId)}`;
  detail.append(code);

  if (!bin.items.length) {
    const heading = document.createElement('h3');
    heading.textContent = bin.state === 'empty' ? 'Verified empty' : 'Never checked';
    const why = document.createElement('p');
    why.className = 'why';
    why.textContent =
      bin.state === 'empty'
        ? 'Someone opened this drawer and confirmed there is nothing in it.'
        : 'Nothing has been recorded here yet. Unlock edit mode to put something in it.';
    detail.append(heading, why);
    return;
  }

  const item = bin.items[0];
  const heading = document.createElement('h3');
  heading.textContent = item.display_name;
  const tag = document.createElement('span');
  tag.className = 'cat-tag';
  tag.style.background = categoryColor(item.category);
  tag.textContent = categoryLabel(item.category);
  detail.append(heading, tag);

  if (item.priority) {
    const badge = document.createElement('span');
    badge.className = `prio prio-${item.priority.toLowerCase()}`;
    badge.textContent = item.priority;
    detail.append(badge);
  }

  const list = document.createElement('dl');
  // The sheet's own ids (A03, S02, E16) look just like drawer codes (A22),
  // so this says where the id comes from instead of calling it "Part id".
  list.append(...field('Sheet id', item.item_id));
  list.append(...field('Quantity', item.quantity === null ? 'not counted' : `${item.quantity} ${item.unit}`));
  if (item.unit_price_usd !== null && item.unit_price_usd !== undefined) {
    list.append(...field('Unit price', `$${item.unit_price_usd.toFixed(2)}`));
    if (item.quantity !== null) {
      list.append(...field('Stock value', `$${(item.unit_price_usd * item.quantity).toFixed(2)}`));
    }
  }
  list.append(...field('Availability', item.availability));
  list.append(...field('Last checked', item.last_verified_at ? new Date(item.last_verified_at).toLocaleDateString() : 'never'));
  detail.append(list);

  if (item.product_url) {
    const link = document.createElement('a');
    link.className = 'vendor-link';
    link.href = item.product_url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = `${item.vendor || 'Product page'} ↗`;
    detail.append(link);
  }

  if (item.notes) {
    const why = document.createElement('p');
    why.className = 'why';
    why.textContent = item.notes;
    detail.append(why);
  }

  const actions = document.createElement('div');
  actions.className = 'detail-actions';
  const light = document.createElement('button');
  light.type = 'button';
  light.id = 'light-this-bin';
  light.textContent = 'Light this drawer';
  light.addEventListener('click', async () => {
    await api('/api/locate', { method: 'POST', body: JSON.stringify({ item_ids: [item.item_id] }) });
    await refreshSnapshot();
  });
  actions.append(light);
  detail.append(actions);

  if (window.renderOperatorBin) window.renderOperatorBin(binId);
}

function resultRow(result, withCheckbox) {
  const row = document.createElement('li');
  row.className = 'result-row';
  row.dataset.itemId = result.item_id;
  if (withCheckbox) {
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.className = 'result-check';
    check.checked = state.selected.has(result.item_id);
    check.setAttribute('aria-label', `Select ${result.display_name}`);
    check.addEventListener('change', () => {
      if (check.checked) state.selected.add(result.item_id);
      else state.selected.delete(result.item_id);
    });
    row.append(check);
  } else {
    row.append(document.createElement('span'));
  }
  const body = document.createElement('div');
  const name = document.createElement('div');
  name.className = 'name';
  name.textContent = result.display_name;
  const meta = document.createElement('div');
  meta.className = 'meta';
  const where = result.mapped
    ? `${result.location_count} location${result.location_count === 1 ? '' : 's'}: ` +
      result.locations.map((entry) => shortLabel(entry.bin_id)).join(', ')
    : 'no drawer yet';
  const count = result.quantity === null ? 'quantity unknown' : `${result.quantity} ${result.unit}`;
  meta.textContent = `${categoryLabel(result.category)} · ${result.availability} · ${count} · ${where}`;
  body.append(name, meta);
  row.append(body);
  return row;
}

function isFiltering() {
  return Boolean(
    document.getElementById('search-input').value.trim() ||
      document.getElementById('category-filter').value ||
      document.getElementById('availability-filter').value
  );
}

function renderResults() {
  const mapped = state.results.filter((result) => result.mapped);
  state.matched = isFiltering()
    ? new Set(mapped.flatMap((result) => result.locations.map((entry) => entry.bin_id)))
    : null;
  if (state.snapshot) renderRack();
  const homeless = state.results.filter((result) => !result.mapped);
  document.getElementById('result-count').textContent = state.results.length
    ? `${state.results.length} match${state.results.length === 1 ? '' : 'es'}`
    : 'no matches';
  document.getElementById('result-list').replaceChildren(...mapped.map((result) => resultRow(result, true)));

  const block = document.getElementById('overflow-block');
  if (homeless.length) {
    document.getElementById('overflow-count').textContent = `${homeless.length} part${
      homeless.length === 1 ? '' : 's'
    } with no drawer yet`;
    document.getElementById('overflow-list').replaceChildren(...homeless.map((result) => resultRow(result, false)));
    block.hidden = false;
  } else {
    block.hidden = true;
  }
}

async function refreshSnapshot() {
  state.snapshot = await api('/api/rack');
  renderRack();
}

async function runSearch() {
  const params = new URLSearchParams();
  params.set('q', document.getElementById('search-input').value);
  const category = document.getElementById('category-filter').value;
  const availability = document.getElementById('availability-filter').value;
  if (category) params.set('category', category);
  if (availability) params.set('availability', availability);
  const payload = await api(`/api/search?${params.toString()}`);
  state.results = payload.results;
  renderResults();
  if (window.refreshItemOptions) window.refreshItemOptions();
}

async function locateSelected() {
  const banner = document.getElementById('highlight-banner');
  const itemIds = [...state.selected];
  if (!itemIds.length) {
    banner.textContent = 'Tick at least one result first.';
    banner.hidden = false;
    banner.dataset.sticky = '1';
    return;
  }
  delete banner.dataset.sticky;
  const result = await api('/api/locate', { method: 'POST', body: JSON.stringify({ item_ids: itemIds }) });
  const problems = [];
  if (result.unmapped.length) problems.push(`${result.unmapped.length} selected item(s) have no drawer yet`);
  if (result.unknown_items.length)
    problems.push(`${result.unknown_items.length} selected item(s) are not in the inventory`);
  await refreshSnapshot();
  if (problems.length) {
    banner.textContent = problems.join(' · ');
    banner.hidden = false;
    banner.dataset.sticky = '1';
  }
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

const operator = { bin: null, previewed: new Set() };

function setOperatorError(message) {
  document.getElementById('operator-error').textContent = message || '';
}

function renderItemOptions() {
  const select = document.getElementById('edit-item');
  if (!select) return;
  const items = [];
  if (state.snapshot) {
    Object.values(state.snapshot.bins).forEach((bin) => bin.items.forEach((item) => items.push(item)));
  }
  state.results.forEach((result) => items.push(result));
  const unique = new Map(items.map((item) => [item.item_id, item.display_name]));
  const previous = select.value;
  select.replaceChildren(
    ...[...unique.entries()]
      .sort((a, b) => a[1].localeCompare(b[1]))
      .map(([id, name]) => {
        const option = document.createElement('option');
        option.value = id;
        option.textContent = name;
        return option;
      })
  );
  if (previous && unique.has(previous)) select.value = previous;
}

window.refreshItemOptions = renderItemOptions;

window.renderOperatorBin = function renderOperatorBin(binId) {
  operator.bin = binId;
  document.getElementById('edit-bin-label').textContent = `Editing ${binId}`;
  document.getElementById('assign-item').disabled = !operator.previewed.has(binId);
};

async function refreshAudit() {
  if (!state.operatorToken) return;
  const payload = await api('/api/audit?limit=20');
  document.getElementById('audit-list').replaceChildren(
    ...payload.entries.map((entry) => {
      const row = document.createElement('li');
      const when = new Date(entry.at);
      const clock = isNaN(when) ? entry.at : when.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
      const said = {
        assign: 'put',
        move: 'moved',
        clear: 'marked empty',
        mark_unknown: 'marked unknown',
        set_quantity: 'recounted',
        set_availability: 'changed availability of',
        upsert_item: 'edited',
        csv_import: 'imported a CSV',
      }[entry.action] || entry.action;
      row.textContent = `${clock} — ${entry.actor} ${said}${entry.target ? ' ' + entry.target : ''}`;
      return row;
    })
  );
}

async function sendUpdate(update) {
  setOperatorError('');
  try {
    await api('/api/inventory/update', { method: 'POST', body: JSON.stringify(update) });
    await refreshSnapshot();
    await runSearch();
    await refreshAudit();
  } catch (error) {
    setOperatorError(error.message);
  }
}

function locationOf(binId) {
  return `${state.snapshot.rack.rack_id}/${binId}`;
}

function startOperator() {
  document.getElementById('operator-unlock').addEventListener('click', async () => {
    state.operatorToken = document.getElementById('operator-token').value.trim();
    document.getElementById('operator-token').value = '';
    try {
      await refreshAudit();
      document.getElementById('operator-body').hidden = false;
      renderItemOptions();
      setOperatorError('');
    } catch (error) {
      state.operatorToken = null;
      setOperatorError(error.message);
    }
  });

  document.getElementById('preview-bin').addEventListener('click', async () => {
    if (!operator.bin) return setOperatorError('Pick a bin in the grid first.');
    try {
      await api('/api/preview', { method: 'POST', body: JSON.stringify({ bin_id: operator.bin }) });
      operator.previewed.add(operator.bin);
      document.getElementById('assign-item').disabled = false;
      await refreshSnapshot();
    } catch (error) {
      setOperatorError(error.message);
    }
  });

  document.getElementById('assign-item').addEventListener('click', () => {
    if (!operator.bin) return setOperatorError('Pick a bin in the grid first.');
    sendUpdate({
      action: 'assign',
      item_id: document.getElementById('edit-item').value,
      location: locationOf(operator.bin),
    });
  });
  document.getElementById('clear-bin').addEventListener('click', () => {
    if (!operator.bin) return setOperatorError('Pick a bin in the grid first.');
    sendUpdate({ action: 'clear', location: locationOf(operator.bin) });
  });
  document.getElementById('mark-unknown').addEventListener('click', () => {
    if (!operator.bin) return setOperatorError('Pick a bin in the grid first.');
    sendUpdate({ action: 'mark_unknown', location: locationOf(operator.bin) });
  });

  document.getElementById('csv-export').addEventListener('click', async () => {
    try {
      document.getElementById('csv-export-text').value = await api('/api/inventory/export.csv');
    } catch (error) {
      setOperatorError(error.message);
    }
  });

  async function runImport(apply) {
    const text = document.getElementById('csv-import-text').value;
    try {
      const result = await api('/api/inventory/import', {
        method: 'POST',
        body: JSON.stringify({ csv: text, apply }),
      });
      const lines = [
        `added: ${result.added.join(', ') || 'none'}`,
        `removed: ${result.removed.join(', ') || 'none'}`,
        `changed: ${
          result.changed.map((entry) => `${entry.item_id} (${entry.fields.join(', ')})`).join('; ') || 'none'
        }`,
        result.applied ? 'applied' : 'not applied — click Apply import to save',
      ];
      document.getElementById('csv-diff').textContent = lines.join('\n');
      if (apply) {
        await refreshSnapshot();
        await runSearch();
        await refreshAudit();
      }
    } catch (error) {
      document.getElementById('csv-diff').textContent = '';
      setOperatorError(error.message);
    }
  }

  document.getElementById('csv-import-preview').addEventListener('click', () => runImport(false));
  document.getElementById('csv-import-apply').addEventListener('click', () => runImport(true));
}

function wireGridKeys() {
  const grid = document.getElementById('rack-grid');
  grid.addEventListener('keydown', (event) => {
    const moves = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 0, ArrowUp: 0 };
    if (!(event.key in moves)) return;
    const cells = [...grid.querySelectorAll('.bin-cell')];
    const index = cells.indexOf(document.activeElement);
    if (index < 0) return;
    const columns = state.snapshot ? state.snapshot.rack.columns : 1;
    const step = event.key === 'ArrowDown' ? columns : event.key === 'ArrowUp' ? -columns : moves[event.key];
    const next = cells[index + step];
    if (!next) return;
    event.preventDefault();
    next.focus();
  });
}

function start() {
  document.getElementById('search-input').addEventListener('input', debounce(runSearch, 200));
  document.getElementById('category-filter').addEventListener('change', runSearch);
  document.getElementById('availability-filter').addEventListener('change', runSearch);
  document.getElementById('locate-selected').addEventListener('click', locateSelected);
  document.getElementById('clear-highlight').addEventListener('click', async () => {
    delete document.getElementById('highlight-banner').dataset.sticky;
    await api('/api/locate/clear', { method: 'POST' });
    await refreshSnapshot();
  });
  refreshSnapshot();
  runSearch();
  startOperator();
  wireGridKeys();
  setInterval(refreshSnapshot, 2000);
}

start();
