import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeDataset, getMetrics, getCompanies, summarize, exportRows } from '../dist/data.js';

// Minimal public fixtures use the exact Python report field names. Company
// identities and evidence are fictional; no live outputs or credentials appear.
const query = (id, index, complexity = 'L1') => ({
  id, index, query: `Find fictional companies for ${id}.`, complexity,
  industry: 'Software', conditions: [{ id: 'requirements', description: 'Company meets the explicit query requirements.' }],
  rule: { op: 'condition', condition_id: 'requirements', children: [] },
  geography: 'United States', filter_tags: ['headquarters'], signal_tags: [],
});

function row(query_id, provider, trial, judge, valid, returned, cost, time) {
  return {
    query_id, provider, trial, judge, requested_count: 5, attainable_count: 5,
    valid_companies: valid, returned_companies: returned, unique_companies: returned,
    precision: returned ? valid / returned : null,
    requested_count_fraction: valid / 5,
    quality_score: returned ? 200 * valid / (returned + 5) : 0,
    cost_usd: cost, cost_per_valid_company: valid && cost !== null ? cost / valid : null,
    valid_companies_per_dollar: cost ? valid / cost : null,
    latency_seconds: time, latency_per_valid_company: valid && time !== null ? time / valid : null,
    grading_complete: true, grading_errors: 0, invalid_companies: returned - valid,
    unknown_companies: 0, unresolved_identities: 0, duplicates: 0, malformed: 0,
    search_status: 'completed', timing_censored: time === null, recall: null,
    slots: [], undefined_reasons: {},
  };
}

function report(rows, extras = {}) {
  return {
    synthetic: false, status: 'Automated preliminary results', comparison_complete: true,
    selected_queries: [query('q1', 1), query('q2', 2, 'L3')], rows,
    configurations: { p: { preset: { description: 'Provider P', options: { model: 'test-model', effort: 'medium' } }, overrides: {} } },
    calculation_settings: { target_count: 5, trials: 2, prefix: null },
    reference_time: '2026-10-01T00:00:00Z', evidence_version: 'test-evidence-version',
    dataset_hash: 'dataset-hash', settings_hash: 'settings-hash', analysis_code_hash: 'code-hash',
    cost_view: 'account', prefix: null,
    evaluation_cost_scope: 'Whole run, including all evidence revisions and judges; not restricted by report filters.',
    summaries: [], evidence: [], ...extras,
  };
}

const fourTrials = [
  row('q1', 'p', 1, 'llm', 3, 5, 2, 10),
  row('q1', 'p', 2, 'llm', 1, 2, 1, 6),
  row('q2', 'p', 1, 'llm', 2, 4, 3, 12),
  row('q2', 'p', 2, 'llm', 2, 2, 1, 8),
];

test('multiple judge rows merge by search task without blending judgments or doubling search cost', () => {
  const secondJudge = fourTrials.map(r => ({ ...r, judge: 'decisions', valid_companies: r.query_id === 'q1' ? 1 : 0,
    precision: r.query_id === 'q1' ? 1 / r.returned_companies : 0,
    quality_score: r.query_id === 'q1' ? 200 / (r.returned_companies + 5) : 0 }));
  const original = report([...fourTrials, ...secondJudge]);
  const imported = normalizeDataset(original);
  assert.equal(imported.rows.length, 4);
  assert.deepEqual(imported.judges.map(j => j.id).sort(), ['decisions', 'llm']);
  const first = imported.rows.find(r => r.query_id === 'q1' && r.trial === 1);
  assert.equal(getMetrics(first, 'llm').valid_companies, 3);
  assert.equal(getMetrics(first, 'decisions').valid_companies, 1);
  assert.equal(summarize(imported, { judge: 'llm' })[0].cost_usd, 7);
  assert.equal(summarize(imported, { judge: 'decisions' })[0].cost_usd, 7);
  assert.equal(summarize(imported, { judge: 'decisions' })[0].cost_per_valid_company, 3.5);
});

test('repeated trials use query means and efficiency ratios from summed search costs', () => {
  const imported = normalizeDataset(report(fourTrials));
  const summary = summarize(imported)[0];
  assert.equal(summary.expected_tasks, 4);
  assert.equal(summary.completed_tasks, 4);
  assert.equal(summary.comparison_complete, true);
  assert.equal(summary.valid_companies, 8);
  assert.equal(summary.mean_valid_companies, 2);
  assert.equal(summary.precision, (.6 + .5 + .5 + 1) / 4);
  assert.equal(summary.cost_per_valid_company, 7 / 8);
  assert.equal(summary.latency_per_valid_company, 36 / 8);
  assert.equal(exportRows(imported).length, 4);
});

test('declared provider and judge groups with no result rows stay visible as incomplete', () => {
  const imported = normalizeDataset(report(fourTrials, {
    comparison_complete: false,
    configurations: {
      p: { preset: { description: 'Provider P' } },
      missing: { preset: { description: 'Provider with no search outputs' } },
    },
    summaries: [
      { provider: 'p', judge: 'llm', expected_tasks: 4, completed_tasks: 4 },
      { provider: 'missing', judge: 'llm', expected_tasks: 4, completed_tasks: 0 },
      { provider: 'p', judge: 'jev', expected_tasks: 4, completed_tasks: 0 },
      { provider: 'missing', judge: 'jev', expected_tasks: 4, completed_tasks: 0 },
    ],
  }));
  assert.deepEqual(imported.providers.map(p => p.id).sort(), ['missing', 'p']);
  assert.deepEqual(imported.judges.map(j => j.id).sort(), ['jev', 'llm']);
  const missing = summarize(imported).find(s => s.provider === 'missing');
  assert.equal(missing.expected_tasks, 4);
  assert.equal(missing.completed_tasks, 0);
  assert.equal(missing.comparison_complete, false);
  assert.equal(missing.quality_score, null);
  assert.equal(missing.cost_per_valid_company, null);
  assert.ok(summarize(imported, { judge: 'jev' }).every(s => !s.comparison_complete && s.quality_score === null));
});

test('unknown prices and censored completion times do not become measured zeroes', () => {
  const rows = structuredClone(fourTrials);
  Object.assign(rows[0], { cost_usd: null, latency_seconds: null, timing_censored: true,
    observed_elapsed_seconds: 40,
    undefined_reasons: { cost: 'Account price unknown', latency: 'Job completion was not observed' } });
  const imported = normalizeDataset(report(rows));
  const metric = getMetrics(imported.rows[0]);
  assert.equal(metric.cost_usd, null);
  assert.equal(metric.latency_seconds, null);
  assert.equal(metric.observed_elapsed_seconds, 40);
  assert.equal(metric.undefined_reasons.cost, 'Account price unknown');
  const summary = summarize(imported)[0];
  assert.equal(summary.known_cost_usd, 5);
  assert.equal(summary.cost_usd, null);
  assert.equal(summary.price_coverage, .75);
  assert.equal(summary.timing_coverage, .75);
  assert.equal(summary.cost_per_valid_company, null);
  assert.equal(summary.valid_companies_per_dollar, null);
  assert.equal(summary.latency_per_valid_company, null);
});

test('incomplete grading remains incomplete even when some valid companies are known', () => {
  const rows = structuredClone(fourTrials);
  Object.assign(rows[0], { grading_complete: false, grading_errors: 1,
    quality_score: null, precision: null, requested_count_fraction: null });
  const imported = normalizeDataset(report(rows, { comparison_complete: false }));
  const metric = getMetrics(imported.rows[0]);
  assert.equal(metric.valid_companies, 3);
  assert.equal(metric.grading_complete, false);
  assert.equal(metric.quality_score, null);
  const summary = summarize(imported)[0];
  assert.equal(summary.comparison_complete, false);
  assert.equal(summary.mean_valid_companies, null);
  assert.equal(summary.quality_score, null);
  assert.equal(summary.precision, null);
  assert.equal(summary.cost_per_valid_company, null);
  assert.equal(summary.grading_errors, 1);
});

test('imported company details preserve aliases, original positions and actual condition/source fields', () => {
  const base = row('q1', 'p', 1, 'llm', 1, 2, 2, 10);
  base.slots = [
    { position: 1, native_position: 4, entity_id: 'company-1', name: 'Fictional One Inc.', domain: 'one.example',
      category: 'valid_companies', judgment: { verdict: 'valid', model: 'judge-model',
        raw: { identity_judgment: { verdict: 'met', reason: 'Reviewed legal entity matches the dated source.', evidence_ids: ['source-1'] } },
        conditions: [{ criterion_id: 'requirements', verdict: 'met', reason: 'Illustrative captured evidence meets the condition.',
          evidence_ids: ['source-1'], probabilities: { met: .9, failed: .02, unknown: .08 }, confidence: .9 }] } },
    { position: 2, native_position: 7, entity_id: 'company-1', name: 'Fictional One', domain: 'one.example', category: 'duplicates' },
  ];
  const input = report([base], {
    selected_queries: [query('q1', 1)], calculation_settings: { target_count: 5, trials: 1 },
    evidence: [{ query: { id: 'q1' }, company: { id: 'company-1', name: 'Fictional One Inc.', domain: 'one.example',
      aliases: ['Fictional One'], resolved: true, notes: 'Reviewed legal name matches the source.' },
      reference_time: '2026-10-01T00:00:00Z', evidence_version: 'evidence-revision-1',
      gaps: ['Revenue has not been verified'], omitted: ['One source was omitted from the bounded judge packet.'],
      sources: [{ id: 'source-1', url: 'https://one.example/about', title: 'Captured example source',
        text: 'Illustrative evidence text.', fetched_at: '2026-10-01T01:00:00Z', published_at: '2026-09-28T00:00:00Z',
        method: 'public_text', error: null, redistributable: true, metadata: { content_hash: 'captured-hash' } }] }],
  });
  const before = JSON.stringify(input);
  const imported = normalizeDataset(input);
  const companies = getCompanies(imported.rows[0], 'llm', imported.queries[0], imported);
  assert.equal(companies.length, 2);
  assert.deepEqual(companies[0].company.aliases, ['Fictional One']);
  assert.equal(companies[0].native_position, 4);
  assert.equal(companies[0].conditions[0].condition_id, 'requirements');
  assert.equal(companies[0].conditions[0].status, 'met');
  assert.deepEqual(companies[0].conditions[0].evidence_refs, ['source-1']);
  assert.equal(companies[0].conditions[0].probabilities.met, .9);
  assert.equal(companies[0].evidence[0].text, 'Illustrative evidence text.');
  assert.equal(companies[0].evidence[0].metadata.content_hash, 'captured-hash');
  assert.deepEqual(companies[0].gaps, ['Revenue has not been verified']);
  assert.deepEqual(companies[0].omitted, ['One source was omitted from the bounded judge packet.']);
  assert.equal(companies[0].packet_metadata.evidence_version, 'evidence-revision-1');
  assert.equal(companies[0].identity_judgment.reason, 'Reviewed legal entity matches the dated source.');
  assert.equal(companies[0].synthetic, false);
  assert.equal(companies[1].category, 'duplicates');
  assert.equal(companies[1].entity_id, companies[0].entity_id);
  assert.equal(JSON.stringify(input), before, 'Import and display transforms must not mutate the audit artifact');
});

test('real imports without source packets do not manufacture companies or evidence', () => {
  const imported = normalizeDataset(report(fourTrials));
  assert.deepEqual(getCompanies(imported.rows[0], 'llm', imported.queries[0], imported), []);
  const rows = structuredClone(fourTrials);
  rows[0].slots = [{ position: 1, entity_id: 'no-packet', name: 'Company without captured packet', category: 'unknown_companies' }];
  const withoutSource = normalizeDataset(report(rows));
  const company = getCompanies(withoutSource.rows[0], 'llm', withoutSource.queries[0], withoutSource)[0];
  assert.deepEqual(company.evidence, []);
  assert.equal(company.synthetic, false);
});

test('an exported prefix and cost view are retained as experiment provenance', () => {
  const imported = normalizeDataset(report(fourTrials, { prefix: 10,
    calculation_settings: { target_count: 50, trials: 2, prefix: 10 } }));
  assert.equal(imported.meta.prefix, 10);
  assert.equal(imported.meta.cost_view, 'account');
  assert.equal(imported.meta.settings_hash, 'settings-hash');
  assert.equal(imported.meta.analysis_code_hash, 'code-hash');
  assert.equal(imported.meta.status, 'Automated preliminary results');
  assert.match(imported.meta.evaluation_cost_scope, /Whole run/);
});

test('query filtering reduces expected trial coverage without losing missing-query declarations', () => {
  const imported = normalizeDataset(report(fourTrials.slice(0, 2), { comparison_complete: false }));
  const full = summarize(imported)[0];
  assert.equal(full.expected_tasks, 4);
  assert.equal(full.comparison_complete, false);
  const filtered = summarize(imported, { queryIds: ['q1'] })[0];
  assert.equal(filtered.expected_tasks, 2);
  assert.equal(filtered.comparison_complete, true);
  assert.equal(filtered.mean_valid_companies, 2);
  assert.equal(filtered.cost_per_valid_company, .75);
});

test('category-only fixtures cannot be imported as measured company evidence', () => {
  assert.throws(() => normalizeDataset({ meta: { synthetic: false, trials: 1 },
    queries: [query('q1', 1)], providers: [{ id: 'p', label: 'Provider P' }], judges: [{ id: 'llm', label: 'LLM judge' }],
    rows: [{ query_id: 'q1', provider: 'p', trial: 1, requested_count: 5, returned_companies: 2,
      categories: { llm: 'VV' }, cost_usd: 1, latency_seconds: 2 }],
  }), /synthetic/i);
});

test('custom judge names that match JavaScript object members still remain separate', () => {
  const rows = [row('q1', 'p', 1, 'constructor', 1, 2, 1, 2), row('q1', 'p', 1, 'toString', 2, 2, 1, 2)];
  const imported = normalizeDataset(report(rows, { selected_queries: [query('q1', 1)],
    calculation_settings: { target_count: 5, trials: 1 } }));
  assert.equal(imported.rows.length, 1);
  assert.equal(getMetrics(imported.rows[0], 'constructor').valid_companies, 1);
  assert.equal(getMetrics(imported.rows[0], 'toString').valid_companies, 2);
  assert.equal(getMetrics(imported.rows[0], 'hasOwnProperty'), null);
});

test('different judges cannot silently replace the underlying search facts for one task', () => {
  const rows = [row('q1', 'p', 1, 'llm', 1, 2, 1, 2), row('q1', 'p', 1, 'jev', 1, 3, 1, 2)];
  assert.throws(() => normalizeDataset(report(rows, { selected_queries: [query('q1', 1)],
    calculation_settings: { target_count: 5, trials: 1 } })), /search|conflict|same task/i);
});

test('invalid nonfinite metrics and impossible valid counts fail import', () => {
  for (const update of [{ cost_usd: Infinity }, { latency_seconds: -1 }, { precision: 1.1 }, { valid_companies: 6, returned_companies: 5 }]) {
    const rows = structuredClone(fourTrials);
    Object.assign(rows[0], update);
    assert.throws(() => normalizeDataset(report(rows)), /finite|valid|returned|number/i);
  }
});
