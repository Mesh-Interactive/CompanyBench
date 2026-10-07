import {METRICS, FACET_LABELS, loadData, normalizeDataset, filterQueries, facetOptions, getMetrics, summarize, getCompanies, getRow, exportRows} from './data.js';

// Every view uses the same query selection. Provider choices only change emphasis.
let data;
const state = {page: 'overview', metric: 'mean_valid_companies', judge: 'llm', providers: new Set(), facets: {}, search: '', group: 'complexity', plot: 'cost', pageIndex: 0, pageSize: 20, detail: null, companySearch: '', companyVerdict: 'all', trial: 1};
let filtered = [], summaries = [], openedFacet = null, lastFocused = null, detailFocusTarget = null, toastTimer;
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));
const finite = value => typeof value === 'number' && Number.isFinite(value);
const color = value => /^#[0-9a-f]{6}$/i.test(value ?? '') ? value : '#805AD5';
const metric = () => METRICS.find(item => item.id === state.metric) ?? METRICS[0];
const provider = id => data.providers.find(item => item.id === id);
const dot = item => '<span class="color-dot" style="background:' + color(item.color) + '"></span>';
const synthetic = () => Boolean(data.meta.synthetic);
const stamp = () => synthetic() ? 'Synthetic illustration · not measured results' : 'Automated preliminary results';
const safeUrl = value => { try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; } };
const active = id => state.providers.has(id);

function format(value, item = metric()) {
  if (!finite(value)) return 'Not available';
  if (item.unit === 'percent') return (value * 100).toFixed(1) + '%';
  if (item.unit === 'currency') return '$' + value.toFixed(value < 1 ? 3 : 2);
  if (item.unit === 'seconds') return value >= 60 ? (value / 60).toFixed(1) + ' min' : value.toFixed(1) + ' s';
  return value.toLocaleString('en-US', {maximumFractionDigits: 1, minimumFractionDigits: 1});
}
const byId = id => METRICS.find(item => item.id === id);
const display = (value, id) => format(value, byId(id));
const count = value => finite(value) ? Math.round(value).toLocaleString('en-US') : 'Not available';
const direction = item => item.direction === 'lower' ? 'Lower is better' : 'Higher is better';
const sorted = (rows, item = metric()) => [...rows].sort((a, b) => {
  if (!finite(a[item.id])) return finite(b[item.id]) ? 1 : a.label.localeCompare(b.label);
  if (!finite(b[item.id])) return -1;
  return (item.direction === 'lower' ? a[item.id] - b[item.id] : b[item.id] - a[item.id]) || a.label.localeCompare(b.label);
});

function readLocation() {
  state.providers = new Set(data.providers.map(item => item.id));
  state.metric = 'mean_valid_companies';
  state.judge = data.judges.some(item => item.id === 'llm') ? 'llm' : data.judges[0].id;
  state.group = 'complexity';
  state.plot = 'cost';
  state.trial = 1;
  if (location.hash.length > 16000) return;
  const [page, query = ''] = location.hash.slice(1).split('?');
  if (['overview', 'compare', 'queries', 'methodology'].includes(page)) state.page = page;
  const params = new URLSearchParams(query);
  if (METRICS.some(item => item.id === params.get('metric'))) state.metric = params.get('metric');
  if (data.judges.some(item => item.id === params.get('judge'))) state.judge = params.get('judge');
  if (params.has('providers')) state.providers = new Set(params.get('providers').split(',').filter(id => provider(id)));
  state.search = (params.get('search') ?? '').slice(0, 500);
  if (['complexity', 'industry', 'primary_category'].includes(params.get('group'))) state.group = params.get('group');
  if (params.get('plot') === 'time') state.plot = 'time';
  const trial = Number(params.get('trial'));
  if (Number.isInteger(trial) && trial >= 1 && trial <= (data.meta.trials ?? 1)) state.trial = trial;
  try {
    const facets = JSON.parse(params.get('filters') ?? '{}');
    const available = facetOptions(data);
    state.facets = Object.fromEntries(Object.entries(facets).filter(([key, values]) => available[key] && Array.isArray(values)).map(([key, values]) => [key, values.filter(value => available[key].includes(value))]));
  } catch { state.facets = {}; }
}

function saveLocation(push = false) {
  const params = new URLSearchParams({metric: state.metric, judge: state.judge});
  if (state.providers.size !== data.providers.length) params.set('providers', [...state.providers].join(','));
  if (Object.values(state.facets).some(values => values.length)) params.set('filters', JSON.stringify(state.facets));
  if (state.search) params.set('search', state.search);
  if (state.group !== 'complexity') params.set('group', state.group);
  if (state.plot !== 'cost') params.set('plot', state.plot);
  if (state.trial !== 1) params.set('trial', String(state.trial));
  const hash = '#' + state.page + '?' + params.toString();
  if (location.hash !== hash) history[push ? 'pushState' : 'replaceState'](null, '', hash);
}

function announce(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 3500);
}

function renderControls() {
  $('metric-select').innerHTML = METRICS.map(item => '<option value="' + esc(item.id) + '"' + (item.id === state.metric ? ' selected' : '') + '>' + esc(item.label) + '</option>').join('');
  $('judge-select').innerHTML = data.judges.map(item => '<option value="' + esc(item.id) + '"' + (item.id === state.judge ? ' selected' : '') + '>' + esc(item.label) + '</option>').join('');
  $('metric-note').textContent = direction(metric()) + (state.metric === 'recall' ? ' · Exhaustive reference queries only' : ' · Same filtered queries in every view');
  const options = facetOptions(data);
  const common = ['complexity', 'industry', 'primary_category', 'signal_tags', 'technology', 'geography'];
  $('filter-buttons').innerHTML = common.filter(key => options[key]).map(key => {
    const n = (state.facets[key] ?? []).length;
    return '<button class="filter-pill' + (n ? ' selected' : '') + '" data-facet="' + esc(key) + '" aria-expanded="' + (openedFacet === key) + '">' + esc(FACET_LABELS[key]) + (n ? ' <span>' + n + '</span>' : '') + ' <span aria-hidden="true">⌄</span></button>';
  }).join('');
  const selectedCount = Object.values(state.facets).reduce((total, values) => total + values.length, 0);
  $('filter-count').textContent = selectedCount ? '· ' + selectedCount : '';
  $('query-count').innerHTML = '<strong>' + filtered.length + '</strong> of ' + data.queries.length + ' queries';
  $('active-filters').innerHTML = Object.entries(state.facets).flatMap(([key, values]) => values.map(value => '<button class="chip-remove" data-remove-facet="' + esc(key) + '" data-value="' + esc(JSON.stringify(value)) + '" aria-label="Remove ' + esc(FACET_LABELS[key] + ': ' + value) + '">' + esc(FACET_LABELS[key]) + ': ' + esc(value) + ' <span aria-hidden="true">×</span></button>')).join('') + (state.search ? '<button class="chip-remove" data-action="clear-search">Search: ' + esc(state.search) + ' ×</button>' : '');
  $('provider-chips').innerHTML = data.providers.map(item => '<button class="provider-chip" data-provider="' + esc(item.id) + '" aria-pressed="' + active(item.id) + '" title="' + esc(item.id + (item.effort ? ' · effort: ' + item.effort : '')) + '">' + dot(item) + esc(item.label) + '</button>').join('');
}

function render() {
  filtered = filterQueries(data, {search: state.search, facets: state.facets});
  summaries = summarize(data, {queries: filtered, judge: state.judge});
  const titles = {
    overview: ['Company search, measured.', 'Find the right search provider for the list you need.<br> Explore quality, cost, and speed, then inspect every company.'],
    compare: ['See the trade-offs.', 'Compare providers on the same queries.<br> Change the metric, narrow the use case, and follow the evidence.'],
    queries: ['Every query. Every result.', 'Inspect the companies behind each score.<br> Open a provider cell to see the returned list, judgments, and sources.'],
    methodology: ['A score you can inspect.', 'Plain metrics. Shared evidence. Visible limitations.<br> Understand what the benchmark measures and what it cannot establish.']
  };
  $('page-title').textContent = titles[state.page][0];
  $('page-description').innerHTML = titles[state.page][1];
  document.querySelectorAll('[data-page]').forEach(button => {
    button.classList.toggle('active', button.dataset.page === state.page);
    if (button.dataset.page === state.page) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  $('release-label').textContent = synthetic() ? 'Synthetic preview' : 'Imported report';
  $('release-summary').innerHTML = esc(data.queries.length + ' queries · ' + data.providers.length + ' provider setups') + '<br> ' + esc((data.meta.requested_count ?? 'Variable') + ' requested companies · ' + (data.meta.trials ?? 1) + ' trial' + ((data.meta.trials ?? 1) > 1 ? 's' : ''));
  $('data-notice').innerHTML = '<span class="notice-dot"></span><strong>' + (synthetic() ? 'Synthetic data' : 'Automated preliminary results') + '</strong><span>' + (synthetic() ? 'Interface preview. These are invented results, not provider measurements.' : 'Imported report. Automated judgments are not independently verified ground truth.') + '</span>';
  renderControls();
  document.documentElement.style.setProperty('--header-height', document.querySelector('.site-header').offsetHeight + 'px');
  $('headline-results').innerHTML = state.page === 'overview' && filtered.length ? highlights() : '';
  if (!filtered.length && !['methodology','queries'].includes(state.page)) {
    $('content').innerHTML = '<div class="empty-state"><h2>No queries match these filters.</h2><p class="muted">Try removing a selection or clearing the search.</p><button class="button primary" data-action="reset-filters">Reset query filters</button></div>';
  } else if (state.page === 'overview') $('content').innerHTML = coverage() + charts() + heatmap() + overviewBottom();
  else if (state.page === 'compare') $('content').innerHTML = coverage() + charts() + comparisonTable() + heatmap();
  else if (state.page === 'queries') $('content').innerHTML = queryTable();
  else $('content').innerHTML = methodology();
  if ($('filter-dialog').open) { $('filter-match-count').textContent = filtered.length + ' matching queries'; }
  saveLocation();
}

function highlights() {
  const groups = [
    ['mean_valid_companies', 'Most valid companies', 'companies / query'],
    ['precision', 'Highest precision', 'equal-query mean'],
    ['cost_per_valid_company', 'Lowest cost per valid company', 'search cost only'],
    ['mean_latency_seconds', 'Fastest full search', 'mean per query']
  ];
  return '<div class="highlight-grid">' + groups.map(([id, title, note]) => {
    const best = sorted(summaries.filter(row => active(row.provider) && finite(row[id])), byId(id))[0];
    return '<article class="highlight-card"><div class="highlight-label"><span>' + esc(title) + '</span><button class="text-button" data-help-metric="' + id + '" aria-label="Explain ' + esc(title) + '">ⓘ</button></div><div class="highlight-value">' + (best ? display(best[id], id) : '—') + '</div><div class="highlight-provider">' + (best ? dot(best) + esc(best.label) : 'Select a provider with available results') + '</div><p class="annotation">' + esc(note) + ' · ' + (synthetic() ? 'example highlight' : 'among emphasized providers') + '</p></article>';
  }).join('') + '</div>';
}

function coverage() {
  const selected = summaries.filter(row => active(row.provider));
  const tasks = selected.reduce((sum, row) => sum + row.completed_tasks, 0);
  const expected = selected.reduce((sum, row) => sum + row.expected_tasks, 0);
  const errors = selected.reduce((sum, row) => sum + row.search_errors, 0);
  const missing = selected.reduce((sum, row) => sum + row.unknown_companies + row.unresolved_identities, 0);
  const incomplete = selected.reduce((sum, row) => sum + row.grading_errors, 0);
  return '<div class="coverage-line"><span><strong>' + state.providers.size + '</strong> emphasized setups</span><span><strong>' + count(tasks) + ' / ' + count(expected) + '</strong> task results</span><span><strong>' + count(errors) + '</strong> search errors</span><span><strong>' + count(missing) + '</strong> companies lacking evidence or identity</span><span><strong>' + count(incomplete) + '</strong> incomplete judgments</span></div>';
}

function charts() {
  const item = metric();
  const ranked = sorted(summaries);
  const max = Math.max(0, ...ranked.map(row => finite(row[item.id]) ? row[item.id] : 0));
  const ranking = ranked.map((row, index) => '<button class="rank-row' + (active(row.provider) ? '' : ' faded') + '" data-provider="' + esc(row.provider) + '" aria-pressed="' + active(row.provider) + '" title="' + esc(row.label + ': ' + format(row[item.id]) + '. ' + (active(row.provider) ? 'Click to fade.' : 'Click to emphasize.')) + '"><span class="rank-no">' + (index + 1) + '</span><span class="rank-name">' + esc(row.label) + '</span><span class="rank-track"><span class="rank-bar" style="display:block;width:' + (max && finite(row[item.id]) ? Math.max(1, row[item.id] / max * 100) : 0) + '%;background:' + color(row.color) + '"></span></span><span class="rank-value">' + format(row[item.id]) + '</span></button>').join('');
  return '<div class="two-up"><section class="panel"><header class="panel-head"><div><h2>' + esc(item.label) + '</h2><p class="panel-subtitle">' + direction(item) + ' · ' + filtered.length + ' queries · ' + esc(data.judges.find(j => j.id === state.judge)?.label ?? state.judge) + '</p></div><span class="badge">' + (synthetic() ? 'Illustrative' : 'Preliminary') + '</span></header><div class="ranking">' + ranking + '</div><p class="chart-caption">' + esc(item.formula) + (item.id === 'recall' ? '. Only exhaustive-reference query scores contribute; see query counts in Compare.' : '.') + ' ' + esc(stamp()) + '.</p></section><section class="panel"><header class="panel-head"><div><h2>Quality, at what ' + (state.plot === 'cost' ? 'cost' : 'speed') + '?</h2><p class="panel-subtitle">Higher quality · ' + (state.plot === 'cost' ? 'lower cost per valid company' : 'less full search time') + '</p></div><div class="toggle-group" aria-label="Trade-off axis"><button data-plot="cost" class="' + (state.plot === 'cost' ? 'active' : '') + '">Cost</button><button data-plot="time" class="' + (state.plot === 'time' ? 'active' : '') + '">Time</button></div></header>' + scatter() + '<div class="chart-tooltip" id="chart-tooltip">Hover or focus a point to inspect its exact provider settings and values.</div><p class="chart-caption">Each point uses the same ' + filtered.length + ' queries. Providers with unavailable axis values are omitted from this plot. ' + esc(stamp()) + '.</p></section></div>';
}

function scatter() {
  const xId = state.plot === 'cost' ? 'cost_per_valid_company' : 'mean_latency_seconds';
  const xMetric = byId(xId);
  const points = summaries.filter(row => finite(row[xId]) && finite(row.quality_score));
  const width = 560, height = 355, left = 56, right = 24, top = 32, bottom = 64;
  const plotW = width - left - right, plotH = height - top - bottom;
  const maxX = Math.max(0.01, ...points.map(row => row[xId])) * 1.12;
  const x = value => left + value / maxX * plotW;
  const y = value => top + (100 - value) / 100 * plotH;
  let graphic = '<svg class="tradeoff-chart" viewBox="0 0 ' + width + ' ' + height + '" role="group" aria-label="Quality score versus ' + esc(xMetric.label) + '"><text x="' + left + '" y="14" class="axis-label">Quality score ↑</text>';
  for (let value = 0; value <= 100; value += 25) graphic += '<line x1="' + left + '" y1="' + y(value) + '" x2="' + (width - right) + '" y2="' + y(value) + '" stroke="#edf2f7"/><text x="' + (left - 12) + '" y="' + (y(value) + 4) + '" text-anchor="end">' + value + '</text>';
  for (let i = 0; i <= 4; i++) {
    const value = maxX * i / 4;
    graphic += '<text x="' + x(value) + '" y="' + (height - bottom + 25) + '" text-anchor="middle">' + (state.plot === 'cost' ? '$' + value.toFixed(2) : Math.round(value) + 's') + '</text>';
  }
  graphic += '<text x="' + (left + plotW / 2) + '" y="' + (height - 13) + '" text-anchor="middle">' + esc(xMetric.label) + ' →</text><text x="' + (left + 10) + '" y="' + (top + 18) + '" fill="#805ad5">Higher quality / lower ' + (state.plot === 'cost' ? 'cost' : 'time') + '</text>';
  points.forEach((row, index) => {
    const label = row.label + '. Quality score ' + row.quality_score.toFixed(1) + '. ' + xMetric.label + ' ' + format(row[xId], xMetric) + '. Click to change emphasis.';
    const attrs = ' class="chart-point" data-provider="' + esc(row.provider) + '" data-point="' + esc(row.provider) + '" role="button" tabindex="0" aria-pressed="' + active(row.provider) + '" aria-label="' + esc(label) + '" fill="' + color(row.color) + '" stroke="white" stroke-width="1.5" opacity="' + (active(row.provider) ? 1 : .16) + '"';
    if (index % 3 === 0) graphic += '<rect x="' + (x(row[xId]) - 5) + '" y="' + (y(row.quality_score) - 5) + '" width="10" height="10" rx="1.5"' + attrs + '><title>' + esc(label) + '</title></rect>';
    else graphic += '<circle cx="' + x(row[xId]) + '" cy="' + y(row.quality_score) + '" r="' + (index % 3 === 1 ? 6 : 4.5) + '"' + attrs + '><title>' + esc(label) + '</title></circle>';
  });
  if (!points.length) graphic += '<text x="280" y="170" text-anchor="middle">No complete quality/' + (state.plot === 'cost' ? 'cost' : 'time') + ' pair is available.</text>';
  return graphic + '</svg>';
}

function comparisonTable() {
  const rows = sorted(summaries);
  const ids = ['mean_valid_companies', 'precision', 'quality_score', 'cost_per_valid_company', 'mean_latency_seconds'];
  return '<section class="panel"><header class="panel-head"><div><h2>All providers, side by side</h2><p class="panel-subtitle">Exact settings remain separate. Sorted by ' + esc(metric().label.toLowerCase()) + '.</p></div><button class="button small" data-action="export-csv">Download this cut ↓</button></header><div class="table-scroll"><table><thead><tr><th>Provider and settings</th>' + ids.map(id => '<th><button class="text-button" data-metric="' + id + '">' + esc(byId(id).label) + (state.metric === id ? (byId(id).direction === 'lower' ? ' ↑' : ' ↓') : '') + '</button></th>').join('') + '<th>Price coverage</th><th>Grading</th><th>Recall queries</th></tr></thead><tbody>' + rows.map(row => '<tr style="' + (active(row.provider) ? '' : 'opacity:.3') + '"><td><button class="text-button" data-provider="' + esc(row.provider) + '" aria-pressed="' + active(row.provider) + '">' + esc(row.label) + '</button><div class="muted small-copy">' + esc(provider(row.provider).effort ?? row.provider) + '</div></td>' + ids.map(id => '<td>' + display(row[id], id) + '</td>').join('') + '<td>' + display(row.price_coverage, 'precision') + '</td><td>' + (row.comparison_complete ? 'Complete' : 'Incomplete') + '</td><td>' + row.recall_queries + '</td></tr>').join('') + '</tbody></table></div><p class="chart-caption">Equal-query means for valid count, precision, and quality. Cost efficiency uses summed costs and valid companies. Full search costs are attributed even when fewer companies qualify.</p></section>';
}

function heatmap() {
  const all = facetOptions(data, filtered)[state.group] ?? [];
  const groups = all.map(value => ({value, queries: filtered.filter(query => (query.facets[state.group] ?? query[state.group]) === value)}));
  if (state.group !== 'complexity') groups.sort((a, b) => b.queries.length - a.queries.length);
  return '<section class="panel"><header class="panel-head"><div><h2>Where performance changes</h2><p class="panel-subtitle">' + esc(metric().label) + ' by subgroup. Click a cell to inspect those queries.</p></div><label class="control-label">Group by <select id="group-select"><option value="complexity"' + (state.group === 'complexity' ? ' selected' : '') + '>Complexity</option><option value="industry"' + (state.group === 'industry' ? ' selected' : '') + '>Industry</option><option value="primary_category"' + (state.group === 'primary_category' ? ' selected' : '') + '>Search type</option></select></label></header><div class="table-scroll"><table class="segment-heatmap"><thead><tr><th>' + esc(FACET_LABELS[state.group]) + '</th>' + data.providers.map(item => '<th class="provider-heading' + (active(item.id) ? '' : ' faded') + '">' + esc(item.label) + '</th>').join('') + '</tr></thead><tbody>' + groups.map(group => {
    const metrics = summarize(data, {queries: group.queries, judge: state.judge});
    return '<tr><td class="heat-label">' + esc(group.value) + '<small>' + group.queries.length + ' queries</small></td>' + metrics.map(row => '<td class="heat-cell' + (active(row.provider) ? '' : ' faded') + '"><button data-group-value="' + esc(JSON.stringify(group.value)) + '" data-group-provider="' + esc(row.provider) + '" title="Inspect ' + esc(group.value + ' · ' + row.label) + '"><span class="heat-value" style="background:rgba(128,90,213,' + (finite(row[state.metric]) ? Math.min(.2, .04 + (metric().unit === 'percent' ? row[state.metric] : metric().unit === 'score' ? row[state.metric] / 100 : .5) * .15) : .02) + ')">' + format(row[state.metric]) + '</span></button></td>').join('') + '</tr>';
  }).join('') + '</tbody></table></div><p class="chart-caption">' + esc(stamp()) + '. Small groups are descriptive examples, not equally certain comparisons. Missing metrics stay unavailable.</p></section>';
}

function overviewBottom() {
  return '<section class="panel section-line"><div><h2>Follow a score to its source.</h2><p class="panel-subtitle">Explore all ' + filtered.length + ' queries, then inspect each company and its condition judgments.</p></div><button class="button primary" data-page="queries">Inspect query results →</button></section>';
}

function queryTable() {
  const pages = Math.max(1, Math.ceil(filtered.length / state.pageSize));
  state.pageIndex = Math.min(state.pageIndex, pages - 1);
  const start = state.pageIndex * state.pageSize;
  const queries = filtered.slice(start, start + state.pageSize);
  return '<section class="panel"><header class="panel-head"><div><h2>Query results</h2><p class="panel-subtitle">One query per row. One provider setup per column. Click a cell to inspect its companies.</p></div><span class="badge">' + (synthetic() ? 'Synthetic rows' : 'Saved results') + '</span></header><div class="explorer-toolbar"><label class="search-input"><input id="query-search" type="search" value="' + esc(state.search) + '" placeholder="Search query text, ID, or industry…" aria-label="Search queries"></label><div class="plot-controls"><label>Trial <select id="trial-select" aria-label="Trial">' + Array.from({length: data.meta.trials ?? 1}, (_, index) => '<option value="' + (index + 1) + '"' + (index + 1 === state.trial ? ' selected' : '') + '>' + (index + 1) + '</option>').join('') + '</select></label><button class="button small" data-action="export-csv">Download rows ↓</button></div></div><div class="table-scroll query-grid"><table><thead><tr><th>Query / acceptance context</th>' + data.providers.map(item => '<th class="provider-heading' + (active(item.id) ? '' : ' faded') + '">' + esc(item.label) + '</th>').join('') + '</tr></thead><tbody>' + queries.map(query => '<tr><td><div class="query-title" title="' + esc(query.query) + '">' + esc(query.query) + '</div><div class="query-meta"><span>' + esc(query.id) + '</span><span class="badge purple">' + esc(query.complexity) + '</span><span>' + esc(query.industry) + '</span></div></td>' + data.providers.map(item => {
    const row = getRow(data, query.id, item.id, state.trial);
    const values = getMetrics(row, state.judge, query);
    const label = values ? format(values[state.metric]) : row ? 'Incomplete grading' : 'Not attempted';
    const context = values ? count(values.valid_companies) + ' valid · ' + display(values.precision, 'precision') + ' precision' : row ? 'Search result saved; no score from this judge' : 'No saved task result';
    return '<td class="heat-cell' + (active(item.id) ? '' : ' faded') + '"><button data-query="' + esc(query.id) + '" data-result-provider="' + esc(item.id) + '" aria-label="' + esc(query.id + ', ' + item.label + ': ' + label + '. ' + context + '. Open company results.') + '" title="' + esc(context + (values?.search_status !== 'completed' ? ' · ' + (values?.search_status ?? row?.search_status ?? 'Unattempted') : '')) + '"><span class="cell-value">' + esc(label) + '</span><span class="cell-detail">' + esc(context) + '</span></button></td>';
  }).join('') + '</tr>').join('') + (queries.length ? '' : '<tr><td colspan="' + (data.providers.length + 1) + '">No queries match the current selection. Clear the search or remove a filter.</td></tr>') + '</tbody></table></div><div class="pagination"><span>Queries ' + (filtered.length ? start + 1 : 0) + '–' + Math.min(start + state.pageSize, filtered.length) + ' of ' + filtered.length + ' · ' + esc(metric().label) + '</span><div class="page-buttons"><button class="button small" data-action="previous-page"' + (state.pageIndex === 0 ? ' disabled' : '') + '>← Previous</button><button class="button small" data-action="next-page"' + (state.pageIndex + 1 === pages ? ' disabled' : '') + '>Next →</button></div></div><p class="chart-caption">' + esc(stamp()) + '. Returned positions include duplicates, malformed entries, and companies with insufficient evidence. Positions beyond the requested limit do not refill rejected results.</p></section>';
}

function importedReportDetails() {
  if (!data.original_report) return '';
  const report = data.original_report;
  const details = {
    status: report.status,
    synthetic: Boolean(report.synthetic),
    comparison_complete: report.comparison_complete,
    fixed_reference_time_utc: report.reference_time,
    cost_view: report.cost_view,
    evaluated_result_prefix: report.prefix ?? 'Full requested search',
    original_requested_count: report.calculation_settings?.target_count,
    selected_queries: report.selected_queries?.length,
    evidence_version: report.evidence_version,
    dataset_hash: report.dataset_hash,
    settings_hash: report.settings_hash,
    analysis_code_hash: report.analysis_code_hash,
    calculation_settings: report.calculation_settings,
    evaluation_costs: report.evaluation_costs,
    evaluation_cost_scope: report.evaluation_cost_scope,
    limitations: report.limitations ?? [],
    outstanding_jobs: report.outstanding_jobs ?? []
  };
  return '<section class="panel"><details class="method-metric"><summary><strong>Imported report details and limitations</strong><span class="muted"> · settings, evidence version, and cost scope ⌄</span></summary><p class="panel-subtitle">Date windows use the saved fixed UTC reference. A shorter result prefix retains the full original search cost. Research and judging charges have their own disclosed scope.</p><pre>' + esc(JSON.stringify(details, null, 2)) + '</pre></details></section>';
}

function methodology() {
  return '<div class="methodology-grid"><section class="panel prose"><p class="eyebrow">HOW TO READ THE RESULTS</p><h2>Quality has more than one dimension.</h2><p>A good list needs enough distinct companies that satisfy the exact query. Precision shows how much filler was returned. Cost and search time show what the list takes to produce.</p><p>Metrics use the same selected queries and the selected judge. Each provider/model/effort combination is a separate setup. A high-effort label does not mean the same thing across vendors.</p><p>Query means give each query equal weight. Cost per valid company uses total search cost divided by total valid companies. Research and judging costs are separate.</p><p>Unknown facts receive no credit. Failed searches receive zero quality. Missing searches and incomplete grading make the comparison incomplete; they do not silently become good results.</p></section><section class="panel prose"><p class="eyebrow">DATA STATUS</p><h2>' + (synthetic() ? 'A working interface, with invented results.' : 'Imported, automated preliminary results.') + '</h2><p>' + esc(data.meta.disclosure ?? stamp()) + '</p><p>' + (synthetic() ? 'The 250 query definitions and provider settings are real. Counts, costs, search times, returned companies, evidence, and judge outcomes are synthetic fixtures for exploring the interface. They are not predictions or measured claims about any provider.' : 'This file is processed locally in your browser. Its scores reflect its saved evidence and judgments. This interface does not rerun searches, fetch evidence, or independently certify the imported report.') + '</p><p>No fabricated confidence intervals or verification badges are shown. One trial cannot establish repeatability. Authored complexity labels are not measured difficulty.</p><p>Avina / Mesh Interactive created CompanyBench and has a commercial interest in its results. The benchmark should retain disappointing results and publish corrections as new versions.</p><a href="https://github.com/Mesh-Interactive/CompanyBench/blob/main/docs/methodology.md" target="_blank" rel="noopener">Read the full methodology ↗</a></section></div>' + importedReportDetails() + '<section class="panel"><header class="panel-head"><div><h2>Metric definitions</h2><p class="panel-subtitle">The requested result limit applies to original positions. Each valid company receives one credit.</p></div></header>' + METRICS.map(item => '<details class="method-metric"><summary><strong>' + esc(item.label) + '</strong><span class="muted"> · ' + direction(item) + ' ⌄</span></summary><code class="formula">' + esc(item.formula) + '</code><p>' + esc(item.description) + '</p></details>').join('') + '</section><section class="panel"><header class="panel-head"><div><h2>Providers and exact settings</h2><p class="panel-subtitle">Configurations are inspectable. No live compatibility is implied by this synthetic preview.</p></div></header><div class="table-scroll"><table><thead><tr><th>Provider</th><th>CLI name</th><th>Model / endpoint</th><th>Effort</th><th>Full settings</th></tr></thead><tbody>' + data.providers.map(item => '<tr><td>' + esc(item.label) + '</td><td>' + esc(item.id) + '</td><td>' + esc(item.model ?? item.vendor) + '</td><td>' + esc(item.effort ?? 'Native') + '</td><td><details><summary>Inspect ⌄</summary><pre>' + esc(JSON.stringify(item.configuration ?? item, null, 2)) + '</pre></details></td></tr>').join('') + '</tbody></table></div></section><section class="panel prose"><h2>Inspect, reproduce, and challenge.</h2><p>Download the data, inspect the company-level condition judgments, and follow source links in measured reports. A missing source is not proof that a requirement failed. Recall is defined only for exhaustive eligible reference lists; open-ended company searches do not have a known complete denominator.</p><p>Shorter result prefixes are views of the original search, with its full cost. Captured source text keeps its original redistribution rules. The published JSON report is the source of truth for the saved calculation inputs and limitations.</p><p><a href="https://github.com/Mesh-Interactive/CompanyBench/blob/main/docs/quickstart.md" target="_blank" rel="noopener">Run the benchmark ↗</a> · <a href="https://github.com/Mesh-Interactive/CompanyBench/blob/main/docs/corrections.md" target="_blank" rel="noopener">Challenge a result ↗</a> · <button class="text-button" data-action="export-json">Download this dataset ↓</button></p></section>';
}

function facetChoices(key, term = '') {
  const options = facetOptions(data)[key] ?? [];
  const other = {...state.facets, [key]: []};
  const pool = filterQueries(data, {search: state.search, facets: other});
  return options.filter(value => String(value).toLowerCase().includes(term.toLowerCase())).map(value => {
    const n = pool.filter(query => {
      const field = query.facets?.[key] ?? query[key];
      return (Array.isArray(field) ? field : [field]).includes(value);
    }).length;
    return '<label class="facet-option"><input type="checkbox" data-facet-check="' + esc(key) + '" data-value="' + esc(JSON.stringify(value)) + '"' + ((state.facets[key] ?? []).includes(value) ? ' checked' : '') + '><span>' + esc(value) + '</span><span class="facet-option-count">' + n + '</span></label>';
  }).join('');
}

function closePopover(restoreFocus = false) {
  const trigger = openedFacet && [...document.querySelectorAll('[data-facet]')].find(button => button.dataset.facet === openedFacet);
  openedFacet = null;
  $('popover-root').innerHTML = '';
  document.querySelectorAll('[data-facet]').forEach(button => button.setAttribute('aria-expanded', 'false'));
  if (restoreFocus) trigger?.focus();
}

function openPopover(key, term = '', focusSearch = false) {
  openedFacet = key;
  const anchor = document.querySelector('[data-facet="' + key + '"]');
  if (!anchor) return;
  const rect = anchor.getBoundingClientRect();
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - 317));
  const chromeBottom = document.querySelector('.site-header').offsetHeight + $('data-notice').offsetHeight + 12;
  const below = window.innerHeight - rect.bottom - 20;
  const above = rect.top - chromeBottom - 12;
  const opensBelow = below >= 240 || below >= above;
  const room = Math.max(160, opensBelow ? below : above);
  const height = Math.min(400, room);
  const top = opensBelow ? rect.bottom + 8 : Math.max(chromeBottom, rect.top - height - 8);
  anchor.setAttribute('aria-expanded', 'true');
  $('popover-root').innerHTML = '<section class="popover" role="dialog" aria-label="' + esc(FACET_LABELS[key]) + ' filter" style="left:' + left + 'px;top:' + top + 'px;max-height:' + height + 'px"><div class="popover-heading"><strong>' + esc(FACET_LABELS[key]) + '</strong><button class="text-button" data-clear-facet="' + key + '">Clear</button></div><input type="search" id="facet-search" value="' + esc(term) + '" placeholder="Find a value…" aria-label="Search ' + esc(FACET_LABELS[key]) + ' values"><div class="facet-options" id="popover-options" style="max-height:' + Math.max(60, height - 170) + 'px">' + facetChoices(key, term) + '</div><p class="popover-footer">Any selected value matches (OR). Counts use the other active filters.</p></section>';
  if (focusSearch) $('facet-search').focus();
}

function renderAllFilters() {
  const openKeys = new Set([...$('all-filter-fields').querySelectorAll('details[open]')].map(element => element.dataset.key));
  const term = $('filter-search').value.trim().toLowerCase();
  const options = facetOptions(data);
  $('all-filter-fields').innerHTML = Object.keys(options).map((key, index) => {
    const labelMatches = FACET_LABELS[key].toLowerCase().includes(term);
    if (term && !labelMatches && !options[key].some(value => String(value).toLowerCase().includes(term))) return '';
    return '<details class="facet-section" data-key="' + esc(key) + '"' + (openKeys.has(key) || (state.facets[key] ?? []).length || term || index < 2 ? ' open' : '') + '><summary>' + esc(FACET_LABELS[key]) + '<span class="muted">' + ((state.facets[key] ?? []).length ? (state.facets[key] ?? []).length + ' selected · ' : '') + '⌄</span></summary><div class="facet-options">' + facetChoices(key, labelMatches ? '' : term) + '</div></details>';
  }).join('');
  $('filter-match-count').textContent = filtered.length + ' matching queries';
}

function toggleFacet(key, value) {
  const values = new Set(state.facets[key] ?? []);
  if (values.has(value)) values.delete(value); else values.add(value);
  state.facets[key] = [...values];
  state.pageIndex = 0;
}

function showMetricHelp(id = state.metric) {
  const item = byId(id) ?? metric();
  $('metric-help-content').innerHTML = '<header class="drawer-header"><h2 id="metric-help-title">' + esc(item.label) + '</h2><button class="icon-button" data-action="close-metric" aria-label="Close metric definition">×</button></header><div class="drawer-body"><span class="badge purple">' + direction(item) + '</span><code class="formula">' + esc(item.formula) + '</code><p>' + esc(item.description) + '</p><p>Query averages give each query equal weight. Trials are averaged within the query before averaging across queries. Cost efficiency uses sums, rather than averaging per-query cost ratios.</p><p>' + esc(stamp()) + '. Undefined values are shown as “Not available”; they are never replaced with zero.</p></div>';
  $('metric-dialog').showModal();
}

const verdictLabels = {valid_companies:'Valid', invalid_companies:'Invalid', unknown_companies:'Not enough evidence', unresolved_identities:'Identity unresolved', grading_errors:'Incomplete grading', duplicates:'Duplicate', malformed:'Malformed', valid:'Valid', invalid:'Invalid', unknown:'Not enough evidence', error:'Incomplete grading', duplicate:'Duplicate'};
function verdictLabel(slot) { return verdictLabels[slot.category] ?? verdictLabels[slot.verdict] ?? String(slot.verdict ?? slot.category ?? 'Not enough evidence'); }
function verdictClass(slot) {
  if (slot.category) return slot.category === 'valid_companies' ? 'green' : slot.category === 'invalid_companies' ? 'red' : 'amber';
  return slot.verdict === 'valid' ? 'green' : slot.verdict === 'invalid' ? 'red' : 'amber';
}

function openDetail(queryId, providerId) {
  lastFocused = document.activeElement;
  detailFocusTarget = {queryId, providerId};
  state.detail = {queryId, providerId};
  state.companySearch = '';
  state.companyVerdict = 'all';
  closePopover();
  renderDetail();
  $('detail-dialog').showModal();
}

function renderDetail() {
  if (!state.detail) return;
  const query = data.queries.find(item => item.id === state.detail.queryId);
  const item = provider(state.detail.providerId);
  const row = getRow(data, query.id, item.id, state.trial);
  const values = getMetrics(row, state.judge, query);
  const conditions = query.conditions ?? [];
  $('drawer-content').innerHTML = '<header class="drawer-header"><div><p class="eyebrow">' + esc(query.id) + ' · ' + esc(item.label) + '</p><h2 id="drawer-title">Companies and evidence</h2><span class="badge purple">' + esc(synthetic() ? 'Synthetic data' : 'Preliminary results') + '</span></div><button class="icon-button" data-action="close-detail" aria-label="Close company results">×</button></header><div class="drawer-body"><span class="badge purple">' + esc(stamp()) + '</span><p class="drawer-query">' + esc(query.query) + '</p><div class="drawer-context"><span class="badge">' + esc(query.complexity) + '</span><span class="badge">' + esc(query.industry) + '</span><span class="muted small-copy">' + esc(query.company_unit ?? '') + '</span></div><div class="drawer-controls"><label class="control-label">Provider <select id="detail-provider" aria-label="Detail provider">' + data.providers.map(p => '<option value="' + esc(p.id) + '"' + (p.id === item.id ? ' selected' : '') + '>' + esc(p.label) + '</option>').join('') + '</select></label><label class="control-label">Judge <select id="detail-judge" aria-label="Detail judge">' + data.judges.map(j => '<option value="' + esc(j.id) + '"' + (j.id === state.judge ? ' selected' : '') + '>' + esc(j.label) + '</option>').join('') + '</select></label></div>' + (values ? '<div class="drawer-metrics">' + [['Valid companies',count(values.valid_companies)],['Precision',display(values.precision,'precision')],['Search cost',finite(values.cost_usd) ? '$'+values.cost_usd.toFixed(3) : 'Unknown'],['Search time',display(values.latency_seconds,'mean_latency_seconds')]].map(([label,value]) => '<div class="mini-stat">' + esc(label) + '<strong>' + esc(value) + '</strong></div>').join('') + '</div><p class="muted small-copy">' + count(values.returned_companies) + ' returned positions · ' + count(values.requested_count) + ' requested · ' + esc(values.search_status) + (values.search_error ? ' · ' + esc(values.search_error) : '') + '</p>' : '<div class="acceptance">No saved result exists for this query, provider, trial, and judge. This is unattempted or incomplete work, not a scored empty list.</div>') + '<details class="acceptance"><summary>Acceptance criteria and verification limits <span>⌄</span></summary><p>' + esc(query.acceptance ?? '') + '</p><ul>' + conditions.map(condition => '<li><strong>' + esc(condition.id) + '</strong> · ' + esc(condition.description) + '</li>').join('') + '</ul><p><strong>Logic:</strong> ' + esc(JSON.stringify(query.rule ?? query.logic ?? 'AND')) + '</p><p><strong>Evidence:</strong> ' + esc(query.evidence ?? '') + '</p><p><strong>Known limitations:</strong> ' + esc(query.observability ?? query.verification_limitations ?? query.pitfalls ?? 'Consult the query definition.') + '</p><p><strong>Fixed reference time:</strong> ' + esc(data.meta.reference_time ?? 'Not recorded') + '</p>' + (query.reference ? '<p><strong>Reference list:</strong> ' + (query.reference.exhaustive ? 'Exhaustive' : 'Examples only') + ' · ' + (query.reference.companies ?? []).length + ' companies. ' + (synthetic() ? 'The synthetic returned companies below are placeholders, not these reference companies.' : '') + '</p>' : '') + '<details><summary>Complete query definition ⌄</summary><pre>' + esc(JSON.stringify(query, null, 2)) + '</pre></details></details><details class="acceptance"><summary>Exact provider settings <span>⌄</span></summary><pre>' + esc(JSON.stringify(item.configuration ?? item, null, 2)) + '</pre></details><div class="company-toolbar"><label class="search-input"><input id="company-search" type="search" value="' + esc(state.companySearch) + '" placeholder="Find a company…" aria-label="Search companies"></label><select id="company-verdict" aria-label="Company result status"><option value="all">All company results</option>' + Object.entries(verdictLabels).filter(([key]) => key.endsWith('companies') || ['unresolved_identities','grading_errors','duplicates','malformed'].includes(key)).map(([key,label]) => '<option value="' + key + '"' + (state.companyVerdict === key ? ' selected' : '') + '>' + esc(label) + '</option>').join('') + '</select></div><div id="company-results"></div></div><div class="drawer-footer"><span>' + esc(item.label) + ' · trial ' + state.trial + ' · ' + esc(state.judge) + '</span><button class="button small" data-action="export-detail">Download company rows ↓</button></div>';
  renderCompanies();
}

function detailSlots() {
  if (!state.detail) return [];
  const query = data.queries.find(item => item.id === state.detail.queryId);
  return getCompanies(getRow(data, query.id, state.detail.providerId, state.trial), state.judge, query, data);
}

function renderCompanies() {
  const all = detailSlots();
  const visible = all.filter(slot => (state.companyVerdict === 'all' || slot.category === state.companyVerdict) && [slot.name,slot.domain,slot.company?.name,slot.company?.domain,slot.position].join(' ').toLowerCase().includes(state.companySearch.toLowerCase()));
  $('company-results').innerHTML = '<p class="muted small-copy" style="margin-bottom:12px">' + visible.length + ' of ' + all.length + ' returned positions shown. Original ordering is preserved.</p><div class="table-scroll"><table class="company-table"><thead><tr><th>Position</th><th>Company</th><th>Judge result</th><th>Details</th></tr></thead><tbody>' + visible.map(slot => '<tr><td>' + esc(slot.position) + '</td><td><div class="company-name">' + esc(slot.name ?? slot.company?.name ?? 'Unresolved company') + '</div><div class="company-domain">' + esc(slot.domain ?? slot.company?.domain ?? 'No usable company domain') + '</div></td><td><span class="badge ' + verdictClass(slot) + '">' + esc(verdictLabel(slot)) + '</span></td><td><button class="text-button" data-company-position="' + esc(slot.position) + '" aria-expanded="false">Evidence ⌄</button></td></tr><tr data-company-details="' + esc(slot.position) + '" hidden><td colspan="4">' + companyDetails(slot) + '</td></tr>').join('') + (visible.length ? '' : '<tr><td colspan="4">No company positions match this filter.</td></tr>') + '</tbody></table></div>';
}

function companyDetails(slot) {
  const identity = slot.judgment?.identity_judgment ?? slot.judgment?.raw?.identity_judgment ?? slot.identity ?? slot.company;
  const statusLabel = {met:'Met',failed:'Failed',unknown:'Not enough evidence',error:'Incomplete grading'};
  const conditions = slot.conditions ?? [];
  const sources = slot.evidence ?? [];
  const check = slot.reference_check ?? slot.contract_error;
  const gaps = slot.gaps ?? slot.packet_metadata?.gaps ?? [];
  const omissions = slot.omitted ?? slot.packet_metadata?.omitted ?? [];
  return '<div class="evidence-box"><span class="badge purple">' + esc(synthetic() ? 'Synthetic identity, evidence, and judgment' : 'Saved judgment and evidence') + '</span>' + (check ? '<p class="company-reason"><strong>Final credit decision:</strong> ' + esc(typeof check === 'object' ? JSON.stringify(check) : check) + '</p>' : '') + '<p class="company-reason"><strong>Original judge explanation:</strong> ' + esc(slot.explanation || 'No explanation was saved for this position.') + '</p><div class="condition-row"><strong>Identity decision</strong>' + esc(identity?.reason ?? identity?.notes ?? (identity?.resolved ? 'Resolved in the saved evidence packet.' : 'No resolved identity decision is available.')) + (identity?.aliases?.length ? '<p>Aliases: ' + esc(identity.aliases.join(', ')) + '</p>' : '') + '</div>' + (gaps.length ? '<div class="condition-row"><strong>Research gaps</strong><p>' + esc(gaps.join(' · ')) + '</p></div>' : '') + (omissions.length ? '<div class="condition-row"><strong>Omitted or truncated evidence</strong><p>' + esc(omissions.join(' · ')) + '</p></div>' : '') + (slot.packet_metadata?.evidence_version ? '<p class="muted small-copy">Evidence version: ' + esc(slot.packet_metadata.evidence_version) + '</p>' : '') + '<h3 style="margin:16px 0 4px">Condition judgments</h3>' + (conditions.length ? conditions.map(condition => {
    const status = condition.status ?? condition.verdict ?? 'unknown';
    const id = condition.condition_id ?? condition.criterion_id ?? condition.id;
    const query = data.queries.find(item => item.id === state.detail.queryId);
    const authored = query.conditions?.find(item => item.id === id);
    return '<div class="condition-row"><strong>' + esc(id) + ' · ' + esc(condition.description ?? authored?.description ?? '') + '</strong><span class="badge ' + (status === 'met' ? 'green' : status === 'failed' ? 'red' : 'amber') + '">' + esc(statusLabel[status] ?? status) + '</span><p class="company-reason">' + esc(condition.reason ?? condition.explanation ?? 'No reason saved.') + '</p><p class="muted small-copy">Evidence references: ' + esc((condition.evidence_refs ?? condition.evidence_ids ?? []).join(', ') || 'None') + '</p></div>';
  }).join('') : '<p class="muted small-copy">No condition judgments were saved for this position.</p>') + '<details class="acceptance"><summary>Complete saved judgment ⌄</summary><pre>' + esc(JSON.stringify(slot.judgment ?? {category: slot.category, explanation: slot.explanation}, null, 2)) + '</pre></details><h3 style="margin:16px 0 4px">Evidence sources</h3>' + (sources.length ? sources.map(source => {
    const url = safeUrl(source.url);
    return '<div class="condition-row"><strong>' + esc(source.title ?? source.id) + '</strong><p class="small-copy">' + esc(source.id) + ' · ' + esc(source.method ?? 'Saved source') + '</p><blockquote class="source-quote">' + esc(source.text || 'Source text is missing or omitted from the published report. No fact is inferred from its absence.') + '</blockquote>' + (source.synthetic || synthetic() ? '<p class="muted small-copy">Reserved example URL: ' + esc(source.url) + ' · This is not a live company source.</p>' : url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">Open captured source ↗</a>' : '<p class="muted small-copy">A safe HTTP(S) source link is not available.</p>') + '<p class="muted small-copy">Fetched: ' + esc(source.fetched_at ?? 'Unknown') + ' · Published: ' + esc(source.published_at ?? 'Unknown') + '</p>' + (source.content_hash ? '<p class="muted small-copy">Source hash: ' + esc(source.content_hash) + '</p>' : '') + '</div>';
  }).join('') : '<p class="muted small-copy">No evidence sources are attached. Missing evidence does not establish a failed condition.</p>') + '</div>';
}

function csv(rows) {
  if (!rows.length) return 'No matching rows\n';
  const fields = [...new Set(rows.flatMap(row => Object.keys(row)))];
  const quote = value => {
    let text = typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value ?? '');
    if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  };
  return fields.map(quote).join(',') + '\n' + rows.map(row => fields.map(field => quote(row[field])).join(',')).join('\n') + '\n';
}

function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  document.querySelectorAll('.download-menu').forEach(element => element.open = false);
}

async function share() {
  try { await navigator.clipboard.writeText(location.href); announce('View link copied. Imported files are not included in the link.'); }
  catch { announce('Copy this view’s address from your browser. Its filters are saved in the URL.'); }
}

function changePage(page) {
  if (!['overview','compare','queries','methodology'].includes(page)) return;
  state.page = page;
  closePopover();
  saveLocation(true);
  render();
}

document.addEventListener('click', event => {
  const target = event.target.closest('[data-page],[data-action],[data-provider],[data-facet],[data-remove-facet],[data-clear-facet],[data-help-metric],[data-metric],[data-plot],[data-group-provider],[data-result-provider],[data-company-position]');
  if (!target) { if (openedFacet && !event.target.closest('.popover')) closePopover(); return; }
  if (target.dataset.page) { changePage(target.dataset.page); return; }
  if (target.dataset.provider) {
    const id = target.dataset.provider;
    if (active(id)) state.providers.delete(id); else state.providers.add(id);
    closePopover(); render(); return;
  }
  if (target.dataset.facet) {
    if (openedFacet === target.dataset.facet) closePopover(); else openPopover(target.dataset.facet, '', true);
    return;
  }
  if (target.dataset.removeFacet || target.dataset.clearFacet) {
    const key = target.dataset.removeFacet ?? target.dataset.clearFacet;
    if (target.dataset.removeFacet) toggleFacet(key, JSON.parse(target.dataset.value)); else state.facets[key] = [];
    const opened = openedFacet;
    render();
    if (opened) openPopover(opened);
    if ($('filter-dialog').open) renderAllFilters();
    return;
  }
  if (target.dataset.helpMetric) { showMetricHelp(target.dataset.helpMetric); return; }
  if (target.dataset.metric) { state.metric = target.dataset.metric; render(); return; }
  if (target.dataset.plot) { state.plot = target.dataset.plot; render(); return; }
  if (target.dataset.groupProvider) {
    state.facets[state.group] = [JSON.parse(target.dataset.groupValue)];
    state.providers = new Set([target.dataset.groupProvider]);
    state.pageIndex = 0;
    changePage('queries'); return;
  }
  if (target.dataset.resultProvider) { openDetail(target.dataset.query, target.dataset.resultProvider); return; }
  if (target.dataset.companyPosition) {
    const row = [...$('company-results').querySelectorAll('[data-company-details]')].find(item => item.dataset.companyDetails === target.dataset.companyPosition);
    row.hidden = !row.hidden;
    target.setAttribute('aria-expanded', String(!row.hidden)); return;
  }
  const action = target.dataset.action;
  if (action === 'select-all') { state.providers = new Set(data.providers.map(item => item.id)); render(); }
  else if (action === 'select-none') { state.providers.clear(); render(); }
  else if (action === 'reset-filters') { state.facets = {}; state.search = ''; state.pageIndex = 0; closePopover(); render(); }
  else if (action === 'clear-search') { state.search = ''; state.pageIndex = 0; render(); }
  else if (action === 'all-filters') { closePopover(); renderAllFilters(); $('filter-dialog').showModal(); }
  else if (action === 'close-filters') $('filter-dialog').close();
  else if (action === 'metric-help') showMetricHelp();
  else if (action === 'close-metric') $('metric-dialog').close();
  else if (action === 'close-detail') $('detail-dialog').close();
  else if (action === 'share') share();
  else if (action === 'previous-page') { state.pageIndex--; render(); }
  else if (action === 'next-page') { state.pageIndex++; render(); }
  else if (action === 'export-csv') download('companybench-' + (synthetic() ? 'synthetic-' : '') + 'filtered-results.csv', csv(exportRows(data, {queryIds: filtered.map(query => query.id), judge: state.judge})), 'text/csv;charset=utf-8');
  else if (action === 'export-json') {
    const payload = data.original_report ?? {...data, original_report: undefined};
    download('companybench-' + (synthetic() ? 'synthetic-preview' : 'imported-results') + '.json', JSON.stringify(payload, null, 2), 'application/json');
  }
  else if (action === 'export-detail') download('companybench-' + (synthetic() ? 'synthetic-' : '') + state.detail.queryId + '-' + state.detail.providerId + '-companies.json', JSON.stringify({synthetic: synthetic(),disclosure: stamp(),query_id: state.detail.queryId,provider: state.detail.providerId,judge: state.judge,trial: state.trial,companies: detailSlots()}, null, 2), 'application/json');
  else if (action === 'demo') {
    loadData().then(result => { data = result; initialize(true); announce('Synthetic preview restored.'); }).catch(error => announce(error.message));
  }
});

document.addEventListener('change', event => {
  const target = event.target;
  if (target.dataset.facetCheck) {
    const key = target.dataset.facetCheck, value = JSON.parse(target.dataset.value);
    const term = $('facet-search')?.value ?? '';
    toggleFacet(key, value);
    render();
    if (openedFacet) {
      openPopover(key, term);
      [...$('popover-root').querySelectorAll('[data-facet-check]')].find(input => input.dataset.value === target.dataset.value)?.focus();
    }
    if ($('filter-dialog').open) {
      renderAllFilters();
      [...$('all-filter-fields').querySelectorAll('[data-facet-check]')].find(input => input.dataset.facetCheck === key && input.dataset.value === target.dataset.value)?.focus();
    }
  } else if (target.id === 'metric-select') { state.metric = target.value; render(); }
  else if (target.id === 'judge-select' || target.id === 'detail-judge') { state.judge = target.value; render(); if (state.detail) renderDetail(); }
  else if (target.id === 'group-select') { state.group = target.value; render(); }
  else if (target.id === 'trial-select') { state.trial = Number(target.value); render(); }
  else if (target.id === 'detail-provider') { state.detail.providerId = target.value; renderDetail(); }
  else if (target.id === 'company-verdict') { state.companyVerdict = target.value; renderCompanies(); }
  else if (target.id === 'load-report') importReport(target.files[0]);
});

let searchTimer;
document.addEventListener('input', event => {
  if (event.target.id === 'facet-search') $('popover-options').innerHTML = facetChoices(openedFacet, event.target.value);
  else if (event.target.id === 'filter-search') renderAllFilters();
  else if (event.target.id === 'company-search') { state.companySearch = event.target.value; renderCompanies(); }
  else if (event.target.id === 'query-search') {
    state.search = event.target.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.pageIndex = 0;
      render();
      $('query-search')?.focus();
    }, 220);
  }
});

function pointDetails(event) {
  const point = event.target.closest('[data-point]');
  if (!point || !$('chart-tooltip')) return;
  const row = summaries.find(item => item.provider === point.dataset.point);
  const item = provider(row.provider);
  $('chart-tooltip').innerHTML = '<strong>' + esc(item.label) + '</strong> · quality ' + display(row.quality_score,'quality_score') + ' · cost/valid ' + display(row.cost_per_valid_company,'cost_per_valid_company') + ' · full search ' + display(row.mean_latency_seconds,'mean_latency_seconds') + '<br><span class="muted">' + esc(item.model ?? item.id) + (item.effort ? ' · effort ' + esc(item.effort) : '') + ' · ' + row.queries + ' queries · ' + esc(stamp()) + '</span>';
}
document.addEventListener('pointerover', pointDetails);
document.addEventListener('focusin', pointDetails);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && openedFacet) { event.preventDefault(); closePopover(true); }
  if (['Enter',' '].includes(event.key) && event.target.matches('.chart-point')) { event.preventDefault(); event.target.dispatchEvent(new MouseEvent('click', {bubbles:true})); }
});
window.addEventListener('resize', () => { closePopover(); document.documentElement.style.setProperty('--header-height', document.querySelector('.site-header').offsetHeight + 'px'); });
window.addEventListener('scroll', () => closePopover(), {passive:true});
window.addEventListener('popstate', () => { readLocation(); closePopover(); render(); });
$('detail-dialog').addEventListener('close', () => {
  state.detail = null;
  const cell = detailFocusTarget && [...document.querySelectorAll('[data-result-provider]')].find(button => button.dataset.query === detailFocusTarget.queryId && button.dataset.resultProvider === detailFocusTarget.providerId);
  if (lastFocused?.isConnected) lastFocused.focus();
  else if (cell) cell.focus();
  else $('main').focus();
  detailFocusTarget = null;
});

async function importReport(file) {
  if (!file) return;
  if (file.size > 100 * 1024 * 1024) { announce('This file exceeds the 100 MB browser import limit. Export a smaller query subset.'); return; }
  try {
    const loaded = normalizeDataset(JSON.parse(await file.text()));
    data = loaded;
    initialize(true);
    document.querySelectorAll('.download-menu').forEach(element => element.open = false);
    announce('Report loaded locally. No file contents were uploaded.');
  } catch (error) { announce('Could not load this report: ' + error.message); }
  $('load-report').value = '';
}

function initialize(resetView = false) {
  state.providers = new Set(data.providers.map(item => item.id));
  state.judge = data.judges.some(item => item.id === 'llm') ? 'llm' : data.judges[0].id;
  state.facets = {}; state.search = ''; state.pageIndex = 0; state.trial = 1;
  if (resetView) { state.page = 'overview'; state.metric = 'mean_valid_companies'; state.group = 'complexity'; state.plot = 'cost'; }
  else readLocation();
  render();
}

loadData().then(result => { data = result; initialize(); }).catch(error => {
  $('content').innerHTML = '<div class="empty-state"><h2>The dataset could not be loaded.</h2><p>' + esc(error.message) + '</p><p class="muted">Serve this directory over HTTP, or open the self-contained preview.html.</p></div>';
});
