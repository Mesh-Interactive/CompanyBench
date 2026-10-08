import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {filterQueries, facetOptions, getMetrics, summarize, getCompanies, normalizeDataset, COMPLEXITY_LEVELS, complexityLabel} from '../dist/data.js';

const data = normalizeDataset(JSON.parse(fs.readFileSync(new URL('../dist/data/demo.json', import.meta.url))));

test('demo data contains the frozen catalogue and all search configurations', () => {
  assert.equal(data.queries.length, 250);
  assert.equal(data.providers.length, 14);
  assert.equal(data.rows.length, 3500);
  assert.equal(data.meta.synthetic, true);
  assert.equal(new Set(data.rows.map(r => `${r.query_id}:${r.provider}:${r.trial}`)).size, 3500);
});

test('filters OR selected values within a facet and AND across facets', () => {
  const result = filterQueries(data, {facets: {complexity: ['L1', 'L2'], industry: ['Software']}});
  assert.ok(result.length > 0);
  assert.ok(result.every(q => ['L1', 'L2'].includes(q.complexity) && q.industry === 'Software'));
  assert.deepEqual(filterQueries(data, {facets: {industry: ['Unlisted industry']}}), []);
  assert.equal(filterQueries(data, {search: 'CSB-001'}).length, 1);
  assert.ok(facetOptions(data).signal_tags.length > 1);
});

test('plain complexity names preserve canonical query selections and unknown contributor values', () => {
  assert.deepEqual(Object.values(COMPLEXITY_LEVELS).map(level=>level.label),
    ['Basic filters','Multiple requirements','Specific evidence','Complex conditions']);
  const expected = [27,71,118,34];
  Object.keys(COMPLEXITY_LEVELS).forEach((code,index)=>{
    assert.equal(filterQueries(data,{facets:{complexity:[code]}}).length,expected[index]);
    assert.equal(complexityLabel(code),COMPLEXITY_LEVELS[code].label);
    assert.ok(COMPLEXITY_LEVELS[code].description.length>20);
  });
  assert.equal(complexityLabel('Contributor-defined complexity'),'Contributor-defined complexity');
});

test('quality score measures list completeness and precision independently of cost', () => {
  const row = {requested_count:50,returned_companies:50,categories:{llm:'V'.repeat(50)},cost_usd:5};
  assert.equal(getMetrics(row).quality_score,100);
  const half = {...row,categories:{llm:'V'.repeat(25)+'I'.repeat(25)}};
  assert.equal(getMetrics(half).quality_score,50);
  assert.ok(Math.abs(getMetrics({...half,returned_companies:25,categories:{llm:'V'.repeat(25)}}).quality_score-66.6666666667)<1e-8);
  assert.equal(getMetrics({...row,cost_usd:500}).quality_score,100);
  assert.equal(getMetrics(row).cost_usd*getMetrics(row).valid_companies_per_dollar,50);
});

test('derived technology facets match complete names and preserve explicit facets', () => {
  const fixture = normalizeDataset({meta:{synthetic:true},providers:[{id:'p',label:'Provider'}],
    judges:[{id:'llm'}],rows:[],queries:[
      {id:'false',query:'Find nuclear reactors and companies involved in lawsuits.'},
      {id:'true',query:'Find companies using React, AWS, dbt, Google BigQuery, VMware, NVIDIA, Shopify Plus and Next.js.'},
      {id:'escaped',query:'Find websites using NextXjs.'},
      {id:'explicit',query:'Find companies using AWS.',technology:['Custom technology']},
      {id:'explicit-facet',query:'Find companies using AWS.',facets:{technology:['Another tool']}},
    ]});
  assert.deepEqual(fixture.queries[0].facets.technology,[]);
  for (const name of ['React','AWS','dbt','BigQuery','VMware','NVIDIA','Shopify Plus','Next.js']) {
    assert.ok(fixture.queries[1].facets.technology.includes(name),`${name} should be recognized`);
  }
  assert.deepEqual(fixture.queries[2].facets.technology,[]);
  assert.deepEqual(fixture.queries[3].facets.technology,['Custom technology']);
  assert.deepEqual(fixture.queries[4].facets.technology,['Another tool']);
  assert.ok(data.queries.find(q=>q.id==='CSB-129').facets.technology.includes('dbt'));
});

test('duplicates, malformed positions and unknowns retain denominator without credit', () => {
  const row = {query_id: 'x', provider: 'p', requested_count: 5, returned_companies: 5,
    categories: {llm: 'VVDMU'}, cost_usd: 2, latency_seconds: 10, search_status: 'completed'};
  const score = getMetrics(row);
  assert.equal(score.valid_companies, 2);
  assert.equal(score.precision, .4);
  assert.equal(score.quality_score, 40);
  assert.equal(score.cost_per_valid_company, 1);
  assert.equal(score.latency_per_valid_company, 5);
  assert.equal(score.duplicates, 1);
  assert.equal(score.malformed, 1);
  assert.equal(score.unknown_companies, 1);
});

test('reference scores use attainable count, and empty results are separate', () => {
  const row = {requested_count: 50, returned_companies: 2, categories: {llm: 'VV'}, cost_usd: 1, latency_seconds: 4, search_status: 'completed'};
  const query = {reference: {exhaustive: true, companies: [{name:'A'}, {name:'B'}]}};
  assert.equal(getMetrics(row, 'llm', query).quality_score, 100);
  assert.equal(getMetrics(row, 'llm', query).recall, 1);
  assert.equal(getMetrics(row).recall, null);
  const empty = {...row, returned_companies: 0, categories: {llm: ''}};
  const emptyQuery = {reference: {exhaustive: true, companies: []}};
  assert.equal(getMetrics(empty, 'llm', emptyQuery).correct_empty_result, true);
  assert.equal(getMetrics(empty, 'llm', emptyQuery).quality_score, null);
  assert.equal(getMetrics({...empty, search_status: 'failed'}, 'llm', emptyQuery).quality_score, 0);
  assert.equal(getMetrics({...empty, search_status: 'failed'}, 'llm', emptyQuery).correct_empty_result, false);
});

test('incomplete judgments and unknown costs do not invent scores', () => {
  const row = {requested_count: 5, returned_companies: 3, categories: {llm: 'VEU'}, cost_usd: null, latency_seconds: 4};
  const score = getMetrics(row);
  assert.equal(score.grading_complete, false);
  assert.equal(score.precision, null);
  assert.equal(score.quality_score, null);
  assert.equal(score.cost_per_valid_company, null);
  assert.equal(score.valid_companies_per_dollar, null);
});

test('aggregate query means and cost ratios match the Python methodology', () => {
  const fixture = normalizeDataset({meta:{synthetic:true},queries:[{id:'a',complexity:'L1'},{id:'b',complexity:'L3'}], providers:[{id:'p',label:'P'}],judges:[{id:'llm'}],rows:[
    {query_id:'a',provider:'p',trial:1,requested_count:5,returned_companies:5,categories:{llm:'VVVII'},cost_usd:2,latency_seconds:10},
    {query_id:'b',provider:'p',trial:1,requested_count:5,returned_companies:2,categories:{llm:'VI'},cost_usd:1,latency_seconds:5},
  ]});
  const summary = summarize(fixture)[0];
  assert.equal(summary.valid_companies, 4);
  assert.equal(summary.mean_valid_companies, 2);
  assert.equal(summary.precision, .55);
  assert.equal(summary.cost_per_valid_company, .75);
  assert.equal(summary.valid_companies_per_dollar, 4/3);
  assert.equal(summary.expected_tasks, 2);
  assert.equal(summary.price_coverage, 1);
  const missing = {...fixture,rows:fixture.rows.slice(0,1)};
  assert.equal(summarize(missing)[0].comparison_complete, false);
  assert.equal(summarize(missing)[0].quality_score, null);
});

test('synthetic company drilldown counts reconcile exactly for every judge', () => {
  for (const row of data.rows.filter((_, i) => i % 47 === 0)) {
    const query = data.queries.find(q => q.id === row.query_id);
    for (const judge of data.judges) {
      const slots = getCompanies(row, judge.id, query, data);
      assert.equal(slots.length, row.returned_companies);
      assert.equal(slots.filter(s => s.category === 'valid_companies').length, getMetrics(row, judge.id, query).valid_companies);
      assert.ok(slots.every(s => s.synthetic === true));
      assert.ok(slots.filter(s => s.company?.domain).every(s => s.company.domain.endsWith('.example')));
      assert.ok(slots.every(s => s.evidence.every(e => e.synthetic === true)));
      for (const duplicate of slots.filter(s=>s.category==='duplicates')) {
        assert.ok(duplicate.duplicate_of < duplicate.position);
        assert.equal(duplicate.entity_id,slots[duplicate.duplicate_of-1].entity_id);
        assert.ok(!['unresolved_identities','malformed'].includes(slots[duplicate.duplicate_of-1].category));
      }
    }
  }
});

test('all generated row bounds and reference counts are valid', () => {
  const queries = new Map(data.queries.map(q=>[q.id,q]));
  for (const row of data.rows) {
    assert.ok(row.returned_companies>=0 && row.returned_companies<=50);
    assert.ok(row.cost_usd>=0 && row.latency_seconds>=0);
    for (const judge of data.judges) {
      const metric = getMetrics(row,judge.id,queries.get(row.query_id));
      assert.ok(metric.valid_companies<=metric.attainable_count);
      assert.ok(metric.valid_companies<=metric.returned_companies);
      assert.ok(metric.quality_score===null || metric.quality_score>=0 && metric.quality_score<=100);
      assert.ok(metric.precision===null || metric.precision>=0 && metric.precision<=1);
    }
  }
  const ranking = summarize(data).sort((a,b)=>b.quality_score-a.quality_score);
  assert.notEqual(ranking[0].provider,'avina');
});

test('condition drilldowns respect the actual query logic', () => {
  const evaluate = (rule, conditions) => {
    if (rule.op==='condition') return conditions.find(c=>c.condition_id===rule.condition_id).status;
    const values = rule.children.map(child=>evaluate(child,conditions));
    if (rule.op==='not') return values[0]==='met' ? 'failed' : values[0]==='failed' ? 'met' : 'unknown';
    if (rule.op==='all') return values.includes('failed') ? 'failed' : values.includes('unknown') ? 'unknown' : 'met';
    return values.includes('met') ? 'met' : values.includes('unknown') ? 'unknown' : 'failed';
  };
  for (const query of data.queries.filter(q=>q.rule.op!=='condition')) {
    const row = data.rows.find(r=>r.query_id===query.id && r.returned_companies);
    for (const slot of getCompanies(row,'llm',query,data)) {
      if (slot.verdict==='valid') assert.equal(evaluate(query.rule,slot.conditions),'met');
      if (slot.verdict==='invalid') assert.equal(evaluate(query.rule,slot.conditions),'failed');
      if (slot.verdict==='unknown') assert.equal(evaluate(query.rule,slot.conditions),'unknown');
    }
  }
});

test('OR and NOT acceptance logic remains consistent in synthetic drilldowns', () => {
  const query = {id:'logic',conditions:[{id:'c1',description:'Condition one'},{id:'c2',description:'Exclusion'}],
    rule:{op:'all',children:[{op:'any',children:[{op:'condition',condition_id:'c1'}]},
      {op:'not',children:[{op:'condition',condition_id:'c2'}]}]}};
  const row = {query_id:'logic',provider:'p',returned_companies:3,categories:{llm:'VIU'}};
  const slots = getCompanies(row,'llm',query);
  assert.deepEqual(slots[0].conditions.map(c=>c.status),['met','failed']);
  assert.deepEqual(slots[1].conditions.map(c=>c.status),['failed','failed']);
  assert.deepEqual(slots[2].conditions.map(c=>c.status),['unknown','unknown']);
});

test('real report import preserves exact normalized metrics and evidence', () => {
  const report = {synthetic:false,reference_time:'2026-10-01T00:00:00Z',selected_queries:[{id:'q',index:1,query:'Find companies',complexity:'L1'}],configurations:{p:{preset:{description:'Provider',options:{model:'m'}}}},rows:[{query_id:'q',provider:'p',trial:1,judge:'llm',valid_companies:1,returned_companies:2,precision:.5,quality_score:40,cost_usd:2,latency_seconds:12,grading_complete:true,slots:[{position:1,name:'Company',entity_id:'c',category:'valid_companies',judgment:{verdict:'valid',conditions:[{condition_id:'x',status:'met',reason:'Verified'}]}}]}],evidence:[{query:{id:'q'},company:{id:'c',name:'Company'},sources:[{id:'s',url:'https://example.com',text:'An actual captured excerpt'}]}]};
  const imported = normalizeDataset(report);
  assert.equal(imported.meta.synthetic, false);
  assert.equal(getMetrics(imported.rows[0]).quality_score, 40);
  assert.equal(getCompanies(imported.rows[0], 'llm', imported.queries[0], imported)[0].evidence[0].text, 'An actual captured excerpt');
  assert.equal(getCompanies(imported.rows[0], 'llm', imported.queries[0], imported)[0].synthetic, false);
});

test('malformed imports fail with descriptive validation errors', () => {
  const fixture = () => ({meta:{synthetic:true},queries:[{id:'q',index:1,query:'Example',facets:{complexity:'L1'}}],
    providers:[{id:'p',label:'Provider',color:'#805AD5'}],judges:[{id:'llm',label:'LLM'}],
    rows:[{query_id:'q',provider:'p',requested_count:5,returned_companies:2,categories:{llm:'VI'},cost_usd:1,latency_seconds:10}]});
  const cases = [
    [input=>input.providers[0]=null,/Provider 1 must be a JSON object/],
    [input=>input.providers[0].id=123,/Provider 1 ID must be a nonempty string/],
    [input=>input.providers[0].label={},/label must be a nonempty string/],
    [input=>input.providers[0].color='red;opacity:0',/six-digit hex/],
    [input=>input.queries[0].id=[],/ID must be a nonempty string/],
    [input=>input.queries[0].facets.complexity={a:'L1'},/primitive/],
    [input=>input.queries[0].index=.5,/finite integer/],
    [input=>input.rows[0]=null,/Result row 1 must be a JSON object/],
    [input=>input.rows[0].cost_usd=-1,/cost_usd must be a finite number/],
    [input=>input.rows[0].cost_usd='0.5',/cost_usd must be a finite number/],
    [input=>input.rows[0].latency_seconds=Infinity,/latency_seconds must be a finite number/],
    [input=>input.rows[0].returned_companies=6,/returned_companies must be a finite integer/],
    [input=>input.rows[0].precision=1.1,/precision must be a finite number/],
    [input=>input.rows[0].categories.llm='VA',/recognized judgment categories/],
    [input=>input.rows[0].grading_complete='true',/grading_complete must be a boolean/],
    [input=>input.meta.synthetic='false',/synthetic flag must be a boolean/],
    [input=>input.judges.push({id:'llm'}),/Judge IDs must be unique/],
  ];
  for (const [mutate,error] of cases) {
    const input = fixture(); mutate(input); assert.throws(()=>normalizeDataset(input),error);
  }
  const unknown = fixture(); unknown.rows[0].cost_usd=null; unknown.rows[0].latency_seconds=null;
  assert.equal(getMetrics(normalizeDataset(unknown).rows[0]).cost_usd,null);
  assert.equal(getMetrics(normalizeDataset(unknown).rows[0]).latency_seconds,null);
});
