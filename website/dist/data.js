/** Pure data operations shared by the synthetic preview and saved benchmark reports.
 * No operation in this module starts a search, fetches company evidence or calls a judge.
 */

export const FACET_LABELS = {
  complexity: 'Complexity', industry: 'Industry', primary_category: 'Search type',
  family: 'Query family', geography: 'Geography', geography_basis: 'Location requirement',
  size_band: 'Company size', business_model: 'Business model', filter_tags: 'Filters',
  signal_tags: 'Signals', technology: 'Technology', tags: 'Query tags',
  time_window: 'Time window', verification_methods: 'Evidence type', company_unit: 'Company definition',
  logic: 'Logic', source_basis: 'Query authorship', track: 'Data track',
  expected_count: 'Expected company count', satisfiability: 'Reference review',
};

export const METRICS = [
  {id:'mean_valid_companies',label:'Valid companies found',unit:'companies',direction:'higher',formula:'Mean distinct valid companies per query',description:'Each company receives credit once. Duplicates, malformed results and missing evidence receive no credit.'},
  {id:'quality_score',label:'Quality score',unit:'score',direction:'higher',formula:'200 × valid companies / (returned positions + requested count)',description:'Balances the number of valid companies with precision. Uses the eligible count for smaller exhaustive reference lists. Known empty cases are reported separately.'},
  {id:'precision',label:'Precision',unit:'percent',direction:'higher',formula:'Valid companies / returned positions',description:'Equal-query mean. The denominator includes duplicate and malformed positions. An unknown company receives no credit.'},
  {id:'requested_count_fraction',label:'Valid companies (% of requested)',unit:'percent',direction:'higher',formula:'Valid companies / requested count',description:'Shows how much of the requested list was filled with distinct, valid companies. This is not recall.'},
  {id:'cost_per_valid_company',label:'Cost per valid company',unit:'currency',direction:'lower',formula:'Sum of search costs / sum of valid companies',description:'Includes search operations and continuations. Research and judging costs are separate. Missing prices make the aggregate undefined.'},
  {id:'valid_companies_per_dollar',label:'Valid companies per dollar',unit:'companies_per_dollar',direction:'higher',formula:'Sum of valid companies / sum of search costs',description:'Counts valid companies per search dollar. It is undefined when cost is unknown or zero.'},
  {id:'quality_per_dollar',label:'Quality score per dollar',unit:'score_per_dollar',direction:'higher',formula:'Sum of defined quality scores / matching sum of search costs',description:'Only rows with defined quality scores are included in both sums. Known-empty cases are excluded. This is an efficiency measure, not accuracy.'},
  {id:'mean_latency_seconds',label:'Search time',unit:'seconds',direction:'lower',formula:'Mean full search time',description:'Includes provider queues, native web search, continuations, retrieval and normalization. Excludes local scheduling, independent research and judging.'},
  {id:'latency_per_valid_company',label:'Search time per valid company',unit:'seconds',direction:'lower',formula:'Sum of search times / sum of valid companies',description:'Divides full search time by valid count. It does not measure each company’s arrival time. Unknown completion times make this ratio undefined.'},
  {id:'recall',label:'Recall',unit:'percent',direction:'higher',formula:'Valid reference companies found / complete eligible reference list',description:'Available only for queries with an exhaustive, nonempty public reference list. Always inspect the reference-query count before comparing.'},
  {id:'unknown_fraction',label:'Missing evidence',unit:'percent',direction:'lower',formula:'(Not enough evidence + unresolved identities) / returned positions',description:'Shows results that cannot be established as valid or invalid from the available evidence. It is separate from incomplete judge execution.'},
];

const categoryNames = {V:'valid_companies',I:'invalid_companies',U:'unknown_companies',N:'unresolved_identities',E:'grading_errors',D:'duplicates',M:'malformed'};
const verdicts = {V:'valid',I:'invalid',U:'unknown',N:'unknown',E:'error',D:'duplicate',M:'malformed'};
const ignoredFacets = new Set(['id','index','query','conditions','rule','acceptance','reference','evidence','pitfalls','gtm_use_case','source_urls','source_ids','fixture','logic','verification_limitations','facets']);
const providerColors = {'Avina':'#805AD5','Exa':'#8D6FC3','Parallel':'#E49489','OpenAI':'#718096','Anthropic':'#DC8DAE','Google':'#B37DC7','xAI':'#4A5568'};
const technologyNames = ['HubSpot','Salesforce','Salesforce Sales Cloud','Snowflake','Databricks','dbt','BigQuery','Google BigQuery','Shopify','Shopify Plus','WooCommerce','Magento','Adobe Commerce','BigCommerce','Stripe','Cloudflare','AWS','Azure','Microsoft Azure','Google Cloud','Workday','NetSuite','SAP','SAP Business One','SAP ECC','SAP S/4HANA','Oracle','Oracle OPERA Cloud','Zendesk','Intercom','Segment','Vercel','Kubernetes','Docker','PostgreSQL','MongoDB','React','Next.js','Webflow','WordPress','Marketo','Adobe Marketo Engage','Pardot','Braze','Amplitude','Mixpanel','Okta','Auth0','ServiceNow','Gong','Outreach','Klaviyo','Bullhorn','Epic','MyChart','VMware','NVIDIA','Contentful','Toast','Elasticsearch','Meta advertising pixel'];
// Technology facets describe named tools, so substrings in unrelated words are
// not matches: React is not a reactor, and AWS is not part of a lawsuit.
const technologyPatterns = technologyNames.map(name=>[name,new RegExp(
  `(?:^|[^a-z0-9])${name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}(?=$|[^a-z0-9])`,'i')]);

const finite = value => typeof value === 'number' && Number.isFinite(value);
const ratio = (a,b) => finite(a) && finite(b) && b > 0 ? a/b : null;
const sum = values => values.reduce((a,b) => a+b,0);
const mean = values => values.length ? sum(values)/values.length : null;
const defined = values => values.filter(finite);
const keyOf = (row) => `${row.query_id}\u0000${row.provider}\u0000${row.trial ?? 1}`;
const record = value => value && typeof value === 'object' && !Array.isArray(value);

function requireRecord(value, label) {
  if (!record(value)) throw new Error(`${label} must be a JSON object.`);
}

function requireString(value, label, optional=false) {
  if (optional && value == null) return;
  if (typeof value !== 'string' || !value.trim() || value.includes('\u0000')) {
    throw new Error(`${label} must be a nonempty string.`);
  }
}

function optionalString(value, label) {
  if (value != null && typeof value !== 'string') throw new Error(`${label} must be a string or null.`);
}

function numberField(value, label, {integer=false,min=0,max=Infinity}={}) {
  if (value == null) return;
  if (!finite(value) || value<min || value>max || integer && !Number.isInteger(value)) {
    throw new Error(`${label} must be a finite ${integer ? 'integer' : 'number'} from ${min} to ${max===Infinity ? 'an unbounded maximum' : max}, or null.`);
  }
}

function validateFacetValue(value, label) {
  const primitive = item => ['string','boolean'].includes(typeof item) || finite(item);
  if (!primitive(value) && !(Array.isArray(value) && value.every(primitive))) {
    throw new Error(`${label} must contain a primitive value or an array of primitive values.`);
  }
}

function validateConditions(conditions, label, criteria=false) {
  if (conditions == null) return;
  if (!Array.isArray(conditions)) throw new Error(`${label} must be an array.`);
  for (const [index,condition] of conditions.entries()) {
    requireRecord(condition,`${label} ${index+1}`);
    requireString(criteria ? condition.id : condition.criterion_id ?? condition.condition_id ?? condition.id,`${label} ${index+1} ID`);
    optionalString(condition.description,`${label} ${index+1} description`);
    optionalString(condition.reason,`${label} ${index+1} reason`);
    const status = condition.verdict ?? condition.status;
    if (!criteria && status != null && !['met','failed','unknown'].includes(status)) throw new Error(`${label} ${index+1} verdict must be met, failed, or unknown.`);
    numberField(condition.confidence,`${label} ${index+1} confidence`,{max:1});
    if (condition.probabilities != null) {
      requireRecord(condition.probabilities,`${label} ${index+1} probabilities`);
      for (const [key,value] of Object.entries(condition.probabilities)) numberField(value,`${label} ${index+1} probability ${key}`,{max:1});
    }
    for (const field of ['evidence_ids','evidence_refs','evidence_types']) {
      if (condition[field] != null && (!Array.isArray(condition[field]) || condition[field].some(x=>typeof x!=='string'))) throw new Error(`${label} ${index+1} ${field} must be an array of strings.`);
    }
  }
}

const countFields = ['requested_count','attainable_count','returned_companies','unique_companies',
  'valid_companies','invalid_companies','unknown_companies','unresolved_identities','grading_errors',
  'duplicates','malformed','reference_count','reference_conflicts','valid_companies_upper_bound'];
const ratioFields = ['precision','requested_count_fraction','valid_fraction_among_resolved',
  'precision_among_decided','unknown_fraction','recall','recall_ceiling'];
const measuredFields = ['cost_usd','cost_per_valid_company','valid_companies_per_dollar',
  'latency_seconds','observed_elapsed_seconds','latency_per_valid_company','quality_per_dollar'];

function validateRow(row, index, isReport) {
  const label = `Result row ${index+1}`;
  requireRecord(row,label);
  requireString(row.query_id,`${label} query_id`);
  requireString(row.provider,`${label} provider`);
  if (isReport) requireString(row.judge,`${label} judge`);
  numberField(row.trial,`${label} trial`,{integer:true,min:1});
  for (const field of countFields) numberField(row[field],`${label} ${field}`,{integer:true});
  numberField(row.requested_count,`${label} requested_count`,{integer:true,min:1,max:100000});
  for (const field of ratioFields) numberField(row[field],`${label} ${field}`,{max:1});
  for (const field of measuredFields) numberField(row[field],`${label} ${field}`);
  for (const field of ['quality_score','quality_upper_bound']) numberField(row[field],`${label} ${field}`,{max:100});
  for (const field of ['grading_complete','timing_censored','synthetic','correct_empty_result']) {
    if (row[field] != null && typeof row[field]!=='boolean') throw new Error(`${label} ${field} must be a boolean or null.`);
  }
  optionalString(row.search_status,`${label} search_status`);
  optionalString(row.search_error,`${label} search_error`);
  if (row.valid_companies != null && row.returned_companies != null && row.valid_companies>row.returned_companies) {
    throw new Error(`${label} valid companies cannot exceed returned positions.`);
  }
  if (row.categories != null) {
    requireRecord(row.categories,`${label} categories`);
    numberField(row.returned_companies,`${label} returned_companies`,{integer:true,max:row.requested_count ?? 50});
    if (row.returned_companies == null) throw new Error(`${label} categories require a returned_companies count.`);
    for (const [judge,value] of Object.entries(row.categories)) {
      requireString(judge,`${label} judge key`);
      if (typeof value!=='string' || value.length!==row.returned_companies || !/^[VIUNEDM]*$/.test(value)) {
        throw new Error(`${label} categories for ${judge} must match the returned positions and use recognized judgment categories.`);
      }
    }
  }
  if (row.slots != null && !Array.isArray(row.slots)) throw new Error(`${label} slots must be an array.`);
  for (const [slotIndex,slot] of (row.slots ?? []).entries()) {
    requireRecord(slot,`${label} position ${slotIndex+1}`);
    numberField(slot.position,`${label} position`,{integer:true,min:1});
    optionalString(slot.name,`${label} company name`);
    optionalString(slot.domain,`${label} company domain`);
    optionalString(slot.entity_id,`${label} company ID`);
    if (slot.judgment != null) {
      requireRecord(slot.judgment,`${label} judgment`);
      validateConditions(slot.judgment.conditions,`${label} judgment conditions`);
    }
  }
}

function validateInput(input, isReport) {
  const queries = isReport ? input.selected_queries : input.queries;
  for (const [index,query] of queries.entries()) {
    requireRecord(query,`Query ${index+1}`);
    requireString(query.id,`Query ${index+1} ID`);
    optionalString(query.query,`Query ${query.id} text`);
    numberField(query.index,`Query ${query.id} index`,{integer:true,min:1});
    if (query.facets != null) {
      requireRecord(query.facets,`Query ${query.id} facets`);
      for (const [key,value] of Object.entries(query.facets)) validateFacetValue(value,`Query ${query.id} facet ${key}`);
    }
    for (const key of Object.keys(FACET_LABELS)) if (query[key]!=null) validateFacetValue(query[key],`Query ${query.id} ${key}`);
    if (query.reference != null) {
      requireRecord(query.reference,`Query ${query.id} reference`);
      if (!Array.isArray(query.reference.companies)) throw new Error(`Query ${query.id} reference companies must be an array.`);
      if (typeof query.reference.exhaustive!=='boolean') throw new Error(`Query ${query.id} reference exhaustive flag must be a boolean.`);
    }
    validateConditions(query.conditions,`Query ${query.id} conditions`,true);
  }
  input.rows.forEach((row,index)=>validateRow(row,index,isReport));
  if (input.meta != null) {
    requireRecord(input.meta,'Dataset metadata');
    if (input.meta.synthetic != null && typeof input.meta.synthetic!=='boolean') throw new Error('Dataset metadata synthetic flag must be a boolean.');
    numberField(input.meta.trials,'Dataset trial count',{integer:true,min:1});
    numberField(input.meta.requested_count,'Dataset requested count',{integer:true,min:1,max:100000});
  }
  if (!isReport && input.rows.some(row=>row.categories != null) && input.meta?.synthetic!==true) {
    throw new Error('Category-only data generates invented company fixtures and must be explicitly labeled synthetic.');
  }
  if (isReport) {
    if (input.configurations != null) requireRecord(input.configurations,'Report configurations');
    if (input.summaries != null && !Array.isArray(input.summaries)) throw new Error('Report summaries must be an array.');
    if (input.calculation_settings != null) requireRecord(input.calculation_settings,'Report calculation settings');
    numberField(input.calculation_settings?.trials,'Report trial count',{integer:true,min:1});
    numberField(input.calculation_settings?.target_count,'Report requested count',{integer:true,min:1,max:100000});
    numberField(input.prefix,'Report prefix',{integer:true,min:1});
    if (input.synthetic != null && typeof input.synthetic!=='boolean') throw new Error('Report synthetic flag must be a boolean.');
    for (const [name,configuration] of Object.entries(input.configurations ?? {})) {
      requireString(name,'Provider configuration ID');
      requireRecord(configuration,`Configuration ${name}`);
      if (configuration.preset != null) requireRecord(configuration.preset,`Configuration ${name} preset`);
      if (configuration.preset?.options != null) requireRecord(configuration.preset.options,`Configuration ${name} preset options`);
      if (configuration.overrides != null) requireRecord(configuration.overrides,`Configuration ${name} overrides`);
      optionalString(configuration.preset?.description,`Configuration ${name} description`);
    }
    for (const summary of input.summaries ?? []) {
      requireRecord(summary,'Report summary');
      requireString(summary.provider,'Report summary provider');
      requireString(summary.judge,'Report summary judge');
    }
  }
  if (input.evidence != null && !Array.isArray(input.evidence)) throw new Error('Evidence packets must be an array.');
  for (const packet of input.evidence ?? []) {
    requireRecord(packet,'Evidence packet');
    requireRecord(packet.query,'Evidence query');
    requireString(packet.query.id,'Evidence query ID');
    requireRecord(packet.company,'Evidence company');
    requireString(packet.company.id,'Evidence company ID');
    optionalString(packet.company.name,'Evidence company name');
    optionalString(packet.company.domain,'Evidence company domain');
    optionalString(packet.company.notes,'Evidence company notes');
    if (packet.company.aliases != null && (!Array.isArray(packet.company.aliases) || packet.company.aliases.some(value=>typeof value!=='string'))) throw new Error('Evidence company aliases must be an array of strings.');
    for (const field of ['gaps','omitted']) {
      if (packet[field] != null && (!Array.isArray(packet[field]) || packet[field].some(value=>typeof value!=='string'))) throw new Error(`Evidence ${field} must be an array of strings.`);
    }
    if (!Array.isArray(packet.sources)) throw new Error('Evidence sources must be an array.');
    for (const source of packet.sources) {
      requireRecord(source,'Evidence source');
      optionalString(source.url,'Evidence source URL');
      optionalString(source.text,'Evidence source text');
      optionalString(source.title,'Evidence source title');
    }
  }
}

function queryFacets(query) {
  const result = Object.fromEntries(Object.entries(query).filter(([key,value]) => !ignoredFacets.has(key) &&
    (['string','number','boolean'].includes(typeof value) || Array.isArray(value) && value.every(x => ['string','number','boolean'].includes(typeof x)))));
  const technology = technologyPatterns.filter(([,pattern])=>pattern.test(query.query ?? '')).map(([name])=>name);
  return {...result,...query.facets,technology:query.technology ?? query.facets?.technology ?? technology};
}

function vendorFor(id) {
  return id.startsWith('exa') ? 'Exa' : id.startsWith('parallel') ? 'Parallel' :
    id.startsWith('openai') ? 'OpenAI' : id.startsWith('claude') ? 'Anthropic' :
    id.startsWith('gemini') ? 'Google' : id.startsWith('grok') ? 'xAI' : id === 'avina' ? 'Avina' : 'Other';
}

/** Accept the lightweight fixture or a JSON report produced by `companybench report`. */
export function normalizeDataset(input) {
  if (!record(input)) throw new Error('A benchmark dataset must be a JSON object.');
  const isReport = Array.isArray(input.selected_queries);
  if (!Array.isArray(isReport ? input.selected_queries : input.queries) || !Array.isArray(input.rows)) {
    throw new Error('The file must contain queries and normalized result rows, or a CompanyBench JSON report.');
  }
  validateInput(input,isReport);
  const queries = (isReport ? input.selected_queries : input.queries).map(q => ({...q,facets:queryFacets(q)}));
  if (new Set(queries.map(q=>q.id)).size !== queries.length) throw new Error('Query IDs must be unique.');
  let rows = input.rows;
  let providers = input.providers;
  let judges = input.judges;
  if (isReport) {
    const grouped = new Map();
    for (const row of input.rows) {
      const key = keyOf(row);
      if (!grouped.has(key)) grouped.set(key,{...row,metrics_by_judge:Object.create(null),synthetic:Boolean(input.synthetic)});
      const merged = grouped.get(key);
      for (const field of ['requested_count','returned_companies','cost_usd','latency_seconds','timing_censored','observed_elapsed_seconds','search_status']) {
        if (Object.hasOwn(merged,field) && Object.hasOwn(row,field) && merged[field]!==row[field]) {
          throw new Error(`Conflicting search facts (${field}) across judges for the same task ${row.query_id}/${row.provider}/${row.trial ?? 1}.`);
        }
      }
      if (merged.metrics_by_judge[row.judge]) throw new Error('Duplicate query/provider/trial/judge rows in report.');
      merged.metrics_by_judge[row.judge] = row;
    }
    rows = [...grouped.values()];
    providers = [...new Set([...Object.keys(input.configurations ?? {}),...(input.summaries ?? []).map(s=>s.provider),...rows.map(r=>r.provider)])].map(id => {
      const configuration = input.configurations?.[id] ?? {};
      const options = {...configuration.preset?.options,...configuration.overrides};
      const vendor = vendorFor(id);
      return {id,label:configuration.preset?.description ?? id,vendor,color:providerColors[vendor] ?? '#718096',model:options.model,effort:options.effort ?? options.generator,configuration};
    });
    judges = [...new Set([...(input.summaries ?? []).map(s=>s.judge),...input.rows.map(r=>r.judge)])].map(id=>({id,label:id}));
  }
  if (!Array.isArray(providers) || !Array.isArray(judges) || !judges.length) throw new Error('A dataset must declare providers and judges.');
  for (const [index,provider] of providers.entries()) {
    requireRecord(provider,`Provider ${index+1}`);
    requireString(provider.id,`Provider ${index+1} ID`);
    requireString(provider.label,`Provider ${provider.id} label`);
    optionalString(provider.vendor,`Provider ${provider.id} vendor`);
    optionalString(provider.model,`Provider ${provider.id} model`);
    optionalString(provider.effort,`Provider ${provider.id} effort`);
    if (provider.color != null && (typeof provider.color!=='string' || !/^#[0-9a-f]{6}$/i.test(provider.color))) throw new Error(`Provider ${provider.id} color must be a six-digit hex color.`);
  }
  for (const [index,judge] of judges.entries()) {
    requireRecord(judge,`Judge ${index+1}`);
    requireString(judge.id,`Judge ${index+1} ID`);
    optionalString(judge.label,`Judge ${judge.id} label`);
  }
  if (new Set(judges.map(j=>j.id)).size!==judges.length) throw new Error('Judge IDs must be unique.');
  const queryIds = new Set(queries.map(q=>q.id));
  const providerIds = new Set(providers.map(p=>p.id));
  const judgeIds = new Set(judges.map(j=>j.id));
  if (providerIds.size !== providers.length) throw new Error('Provider IDs must be unique.');
  if (new Set(rows.map(keyOf)).size !== rows.length) throw new Error('Search task IDs must be unique.');
  for (const row of rows) {
    if (!queryIds.has(row.query_id) || !providerIds.has(row.provider)) throw new Error('Every result must refer to a declared query and provider.');
    if (row.categories && Object.keys(row.categories).some(judge=>!judgeIds.has(judge))) throw new Error('Every position category set must refer to a declared judge.');
  }
  const meta = isReport ? {schema_version:'1.0',synthetic:Boolean(input.synthetic),title:'CompanyBench results',
    reference_time:input.reference_time,dataset_hash:input.dataset_hash,evidence_version:input.evidence_version,
    requested_count:input.prefix ?? input.calculation_settings?.target_count ?? 50,trials:input.calculation_settings?.trials ?? 1,
    search_target_count:input.calculation_settings?.target_count ?? 50,
    cost_view:input.cost_view,prefix:input.prefix,analysis_code_hash:input.analysis_code_hash,
    settings_hash:input.settings_hash,status:input.status,evaluation_cost_scope:input.evaluation_cost_scope,
    calculation_settings:input.calculation_settings,
    comparison_complete:input.comparison_complete,limitations:input.limitations,evaluation_costs:input.evaluation_costs,
    disclosure:input.synthetic ? 'Synthetic demonstration. These are not provider measurements.' : 'Automated preliminary results. Judge judgments are not independently established ground truth.'} : {...input.meta,synthetic:Boolean(input.meta?.synthetic)};
  return {meta,queries,providers,judges,rows,evidence:input.evidence ?? [],original_report:isReport ? input : null};
}

export async function loadData(url = './data/demo.json') {
  if (globalThis.COMPANYBENCH_DATA) return normalizeDataset(globalThis.COMPANYBENCH_DATA);
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Unable to load benchmark data (HTTP ${response.status}).`);
  return normalizeDataset(await response.json());
}

/** OR within one facet; AND across facets. Empty selections impose no condition. */
export function filterQueries(data, {search='',facets={},ids=null} = {}) {
  const searchTerms = search.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const selectedIds = ids && new Set(ids);
  const active = Object.entries(facets).filter(([,values])=>values && (values.size ?? values.length));
  return data.queries.filter(query => {
    if (selectedIds && !selectedIds.has(query.id)) return false;
    if (searchTerms.length) {
      const text = [query.id,query.index,query.query,query.industry,query.family,query.gtm_use_case].join(' ').toLocaleLowerCase();
      if (!searchTerms.every(term=>text.includes(term))) return false;
    }
    return active.every(([facet,values])=>{
      const allowed = values instanceof Set ? values : new Set(values);
      const value = query.facets?.[facet] ?? query[facet];
      return (Array.isArray(value) ? value : [value]).some(x=>allowed.has(x));
    });
  });
}

export function facetOptions(data, queries = data.queries) {
  const result = {};
  for (const key of Object.keys(FACET_LABELS)) {
    const values = new Set(queries.flatMap(q=>{
      const value = q.facets?.[key] ?? q[key];
      return Array.isArray(value) ? value : value == null ? [] : [value];
    }));
    if (values.size) result[key] = [...values].sort((a,b)=>String(a).localeCompare(String(b),undefined,{numeric:true}));
  }
  return result;
}

export function getRow(data, queryId, providerId, trial = 1) {
  return data.rows.find(row=>row.query_id===queryId && row.provider===providerId && (row.trial ?? 1)===trial) ?? null;
}

/** Exact row arithmetic, with null ratios and explicit reasons preserved. */
export function getMetrics(row, judge='llm', query=null) {
  if (!row) return null;
  if (row.metrics_by_judge) {
    const metric = row.metrics_by_judge[judge];
    return metric ? {...metric,mean_valid_companies:metric.valid_companies,mean_latency_seconds:metric.latency_seconds} : null;
  }
  if (!row.categories) return {...row,mean_valid_companies:row.valid_companies,mean_latency_seconds:row.latency_seconds};
  const k = row.requested_count ?? 50;
  const r = Math.min(k,row.returned_companies);
  const categories = row.categories[judge];
  if (categories === undefined) return null;
  if (categories.length !== row.returned_companies || [...categories].some(value=>!categoryNames[value])) {
    throw new Error('Synthetic position categories must match the returned list exactly.');
  }
  const counts = Object.fromEntries(Object.values(categoryNames).map(category=>[category,0]));
  for (const letter of categories.slice(0,k)) counts[categoryNames[letter]]++;
  const v = counts.valid_companies;
  const reference = query?.reference;
  const referenceCount = reference?.exhaustive ? reference.companies.length : null;
  const attainable = referenceCount === null ? k : Math.min(k,referenceCount);
  const empty = referenceCount === 0;
  const complete = counts.grading_errors === 0;
  const cost = finite(row.cost_usd) ? row.cost_usd : null;
  const latency = !row.timing_censored && finite(row.latency_seconds) ? row.latency_seconds : null;
  let quality = complete && r + attainable > 0 && !empty ? 200*v/(r+attainable) : null;
  if (['failed','refused'].includes(row.search_status)) quality = 0;
  return {query_id:row.query_id,provider:row.provider,judge,trial:row.trial ?? 1,
    requested_count:k,attainable_count:attainable,returned_companies:r,
    unique_companies:counts.valid_companies+counts.invalid_companies+counts.unknown_companies+counts.grading_errors,
    ...counts,precision:complete ? ratio(v,r) : null,requested_count_fraction:complete ? ratio(v,k) : null,
    cost_usd:cost,cost_per_valid_company:complete ? ratio(cost,v) : null,
    valid_companies_per_dollar:complete ? ratio(v,cost) : null,
    latency_seconds:latency,mean_latency_seconds:latency,latency_per_valid_company:complete ? ratio(latency,v) : null,
    quality_score:quality,quality_per_dollar:ratio(quality,cost),
    recall:complete && referenceCount !== null ? ratio(v,referenceCount) : null,
    reference_count:referenceCount,recall_ceiling:referenceCount !== null ? ratio(Math.min(k,referenceCount),referenceCount) : null,
    correct_empty_result:empty && complete ? r===0 && row.search_status==='completed' : null,
    grading_complete:complete,search_status:row.search_status ?? 'completed',search_error:row.search_error ?? null,
    unknown_fraction:ratio(counts.unknown_companies+counts.unresolved_identities,r),
    mean_valid_companies:complete ? v : null,timing_censored:Boolean(row.timing_censored),
    observed_elapsed_seconds:row.latency_seconds,
    undefined_reasons:{precision:!complete ? 'Incomplete grading' : !r ? 'No returned companies' : null,
      cost:cost===null ? 'Price or usage is unknown' : null,
      recall:referenceCount===null || !referenceCount ? 'No exhaustive nonempty reference list' : !complete ? 'Incomplete grading' : null,
      quality_score:empty ? 'Known empty reference list; inspect Correct empty results' : !complete ? 'Incomplete grading' : null,
      cost_per_valid_company:!complete ? 'Incomplete grading' : cost===null ? 'Search cost is unknown' : !v ? 'No valid companies' : null,
      latency_seconds:latency===null ? 'Completion time is uncertain' : null}};
}

/** Equal-query averages, separately labeled strata, and ratios from summed costs. */
export function summarize(data, {queryIds=null,queries=null,judge='llm',providerIds=null} = {}) {
  const selectedQueries = queries ?? (queryIds ? data.queries.filter(q=>new Set(queryIds).has(q.id)) : data.queries);
  const queryMap = new Map(selectedQueries.map(q=>[q.id,q]));
  const providers = providerIds ? data.providers.filter(p=>new Set(providerIds).has(p.id)) : data.providers;
  const trialCount = data.meta.trials ?? 1;
  return providers.map(provider=>{
    const tasks = data.rows.filter(row=>row.provider===provider.id && queryMap.has(row.query_id));
    const rows = tasks.map(row=>getMetrics(row,judge,queryMap.get(row.query_id))).filter(Boolean);
    const expected = selectedQueries.length*trialCount;
    const complete = expected>0 && rows.length===expected && rows.every(row=>row.grading_complete);
    const byQuery = new Map();
    for (const row of rows) {
      if (!byQuery.has(row.query_id)) byQuery.set(row.query_id,[]);
      byQuery.get(row.query_id).push(row);
    }
    const queryMean = metric => complete ? mean([...byQuery.values()].map(group=>mean(defined(group.map(r=>r[metric])))).filter(finite)) : null;
    const costs = defined(rows.map(r=>r.cost_usd));
    const cost = costs.length===rows.length && rows.length ? sum(costs) : null;
    const valid = sum(rows.map(r=>r.valid_companies ?? 0));
    const latency = defined(rows.map(r=>r.latency_seconds));
    const qualityRows = rows.filter(r=>finite(r.quality_score));
    const qualityCost = qualityRows.length && qualityRows.every(r=>finite(r.cost_usd)) ? sum(qualityRows.map(r=>r.cost_usd)) : null;
    const strata = new Map();
    for (const [id,group] of byQuery) {
      const key = queryMap.get(id).complexity;
      if (!strata.has(key)) strata.set(key,[]);
      const value = mean(defined(group.map(r=>r.quality_score)));
      if (value!==null) strata.get(key).push(value);
    }
    const returned = sum(rows.map(r=>r.returned_companies ?? 0));
    const missing = sum(rows.map(r=>(r.unknown_companies ?? 0)+(r.unresolved_identities ?? 0)));
    return {provider:provider.id,label:provider.label,vendor:provider.vendor,color:provider.color,
      judge,queries:byQuery.size,completed_tasks:rows.length,expected_tasks:expected,
      comparison_complete:complete,completion_coverage:ratio(rows.length,expected),
      valid_companies:valid,mean_valid_companies:queryMean('valid_companies'),
      returned_companies:returned,mean_returned_companies:queryMean('returned_companies'),
      unique_companies:sum(rows.map(r=>r.unique_companies ?? 0)),requested_count:queryMean('requested_count'),
      precision:queryMean('precision'),quality_score:queryMean('quality_score'),
      quality_by_equal_complexity:complete ? mean([...strata.values()].map(mean).filter(finite)) : null,
      requested_count_fraction:queryMean('requested_count_fraction'),
      cost_usd:cost,known_cost_usd:sum(costs),price_coverage:ratio(costs.length,rows.length),
      cost_per_valid_company:complete ? ratio(cost,valid) : null,
      valid_companies_per_dollar:complete ? ratio(valid,cost) : null,
      quality_per_dollar:complete ? ratio(sum(qualityRows.map(r=>r.quality_score)),qualityCost) : null,
      quality_cost_usd:qualityCost,quality_tasks:qualityRows.length,
      mean_latency_seconds:mean(latency),latency_seconds:mean(latency),
      latency_per_valid_company:complete && latency.length===rows.length ? ratio(sum(latency),valid) : null,
      timing_coverage:ratio(latency.length,rows.length),
      unknown_companies:sum(rows.map(r=>r.unknown_companies ?? 0)),unknown_fraction:ratio(missing,returned),
      unresolved_identities:sum(rows.map(r=>r.unresolved_identities ?? 0)),
      invalid_companies:sum(rows.map(r=>r.invalid_companies ?? 0)),
      duplicates:sum(rows.map(r=>r.duplicates ?? 0)),malformed:sum(rows.map(r=>r.malformed ?? 0)),
      grading_errors:sum(rows.map(r=>r.grading_errors ?? 0)),
      search_errors:rows.filter(r=>['failed','refused'].includes(r.search_status)).length,
      partial_searches:rows.filter(r=>['partial','truncated'].includes(r.search_status)).length,
      known_empty_tasks:rows.filter(r=>r.correct_empty_result!=null).length,
      correct_empty_results:rows.filter(r=>r.correct_empty_result===true).length,
      recall:queryMean('recall'),recall_queries:new Set(rows.filter(r=>r.recall!=null).map(r=>r.query_id)).size,
      recall_coverage:ratio(rows.filter(r=>r.recall!=null).length,rows.length),
      total_search_time:latency.length===rows.length && rows.length ? sum(latency) : null};
  });
}

function stringHash(value) {
  let hash = 2166136261;
  for (const char of value) hash = Math.imul(hash ^ char.charCodeAt(0),16777619);
  return hash>>>0;
}

function conditionStates(rule, desired, output={}) {
  if (!rule) return output;
  if (rule.op==='condition') {output[rule.condition_id]=desired;return output;}
  const children = rule.children ?? rule.rules ?? [];
  if (rule.op==='not') return conditionStates(rule.child ?? children[0],desired==='met' ? 'failed' : desired==='failed' ? 'met' : 'unknown',output);
  const all = rule.op==='all';
  children.forEach((child,index)=>conditionStates(child,desired==='unknown' ? 'unknown' :
    all ? desired==='met' ? 'met' : index===0 ? 'failed' : 'met' :
    desired==='failed' ? 'failed' : index===0 ? 'met' : 'failed',output));
  return output;
}

/** Lazy drilldown: detail is generated only for an opened query/provider cell. */
export function getCompanies(row, judge='llm', query=null, data=null) {
  if (!row) return [];
  if (row.metrics_by_judge || !row.categories) {
    const metric = getMetrics(row,judge,query);
    return (metric?.slots ?? []).map(slot=>{
      const packet = data?.evidence?.find(p=>p.query?.id===row.query_id && p.company?.id===slot.entity_id);
      const judgment = slot.judgment;
      return {...slot,synthetic:Boolean(data?.meta.synthetic),verdict:judgment?.verdict ?? slot.category,
        company:packet?.company ?? {id:slot.entity_id,name:slot.name,domain:slot.domain},
        evidence:packet?.sources ?? [],conditions:(judgment?.conditions ?? []).map(condition=>({
          ...condition,id:condition.criterion_id ?? condition.id,
          condition_id:condition.criterion_id ?? condition.condition_id,
          description:query?.conditions?.find(c=>c.id===(condition.criterion_id ?? condition.condition_id))?.description ?? condition.description,
          status:condition.verdict ?? condition.status,evidence_refs:condition.evidence_ids ?? condition.evidence_refs ?? []})),judgment,
        explanation:judgment?.explanation ?? judgment?.reason ?? slot.reference_check ?? slot.contract_error ?? '',
        identity:packet?.company ?? null,
        identity_judgment:judgment?.identity_judgment ?? judgment?.raw?.identity_judgment ?? null,
        gaps:packet?.gaps ?? [],omitted:packet?.omitted ?? [],
        packet_metadata:packet ? {reference_time:packet.reference_time,evidence_version:packet.evidence_version,gaps:packet.gaps ?? [],omitted:packet.omitted ?? []} : null};
    });
  }
  const colors = row.categories[judge] ?? '';
  const nouns = ['Cedar','Aster','Harbor','Juniper','Willow','Orchard','Summit','Elm','Maple','Linden','Meadow','Birch'];
  const endings = ['Works','Systems','Labs','Group','Industries','Solutions','Partners','Technologies'];
  const seed = stringHash(`${row.query_id}:${row.provider}`);
  const duplicateIndex = Math.max(0,[...colors].findIndex(letter=>['V','I','U','E'].includes(letter)));
  const duplicatePosition = duplicateIndex+1;
  const firstIdentity = {id:`synthetic-${row.query_id}-${row.provider}-${duplicatePosition}`,
    name:`${nouns[(seed+duplicateIndex)%nouns.length]} ${endings[((seed>>>4)+duplicateIndex*3)%endings.length]} ${String(duplicatePosition).padStart(2,'0')}`,
    domain:`fixture-${row.query_id.toLowerCase()}-${row.provider}-${duplicatePosition}.example`,resolved:true};
  return [...colors].map((letter,index)=>{
    const position = index+1;
    const identity = letter==='D' ? firstIdentity : {id:`synthetic-${row.query_id}-${row.provider}-${position}`,
      name:`${nouns[(seed+index)%nouns.length]} ${endings[((seed>>>4)+index*3)%endings.length]} ${String(position).padStart(2,'0')}`,
      domain:`fixture-${row.query_id.toLowerCase()}-${row.provider}-${position}.example`,resolved:letter!=='N'};
    const sourceId = `fixture-${position}`;
    const target = letter==='V' ? 'met' : letter==='I' ? 'failed' : 'unknown';
    const states = conditionStates(query?.rule,target);
    const conditions = (query?.conditions ?? []).map(condition=>({id:condition.id,condition_id:condition.id,criterion_id:condition.id,
      description:condition.description,status:states[condition.id] ?? target,
      verdict:states[condition.id] ?? target,
      reason:(states[condition.id] ?? target)==='met' ? 'Synthetic fixture: this condition is assigned Met for interface testing.' :
        (states[condition.id] ?? target)==='failed' ? 'Synthetic fixture: this condition is assigned Failed for interface testing.' :
        'Synthetic fixture: there is not enough evidence to establish this condition.',
      evidence_refs:letter==='U' || letter==='N' ? [] : [sourceId],
      evidence_ids:letter==='U' || letter==='N' ? [] : [sourceId]}));
    const evidence = letter==='M' || letter==='D' ? [] : [{id:sourceId,title:'Synthetic fixture · company source',
      url:`https://${identity.domain}/synthetic-fixture`,synthetic:true,
      text:'Invented example for interface development. This excerpt represents the place where captured company facts, event dates and exact condition evidence will appear. It makes no factual claim about an existing company.',
      fetched_at:'2026-10-06T00:00:00Z',published_at:null,method:'synthetic_fixture',
      content_hash:`fixture-${stringHash(identity.id).toString(16)}`}];
    const explanation = letter==='D' ? `Duplicate of company position ${duplicatePosition}. The position stays in the precision denominator and receives no additional credit.` :
      letter==='M' ? 'Malformed result. The original position stays in the precision denominator and receives no credit.' :
      letter==='N' ? 'Company identity cannot be established in this invented fixture. No credit is given.' :
      letter==='U' ? 'Not enough evidence. Missing evidence receives no credit and is separate from a confirmed mismatch.' :
      letter==='I' ? 'The acceptance rule is not satisfied in this invented fixture. No credit is given.' :
      letter==='E' ? 'Incomplete judgment. The query cannot receive a complete score.' :
      'The acceptance rule is satisfied in this invented fixture. This distinct company receives one credit.';
    return {position,synthetic:true,entity_id:letter==='M' ? null : identity.id,
      name:letter==='M' ? 'Malformed result' : identity.name,domain:letter==='M' ? null : identity.domain,
      company:letter==='M' ? null : {...identity,aliases:[],notes:'Invented company identity; not a real company.'},
      category:categoryNames[letter],verdict:verdicts[letter],explanation,evidence,conditions,
      judgment:{judge,entity_id:identity.id,verdict:verdicts[letter],conditions,explanation,
        identity_judgment:{status:identity.resolved ? 'met' : 'unknown',reason:'Synthetic company identity fixture.'}},
      duplicate_of:letter==='D' ? duplicatePosition : null,identity:{...identity,notes:'Synthetic identity fixture.'},
      identity_judgment:{status:identity.resolved ? 'met' : 'unknown',reason:'Synthetic company identity fixture.'},
      gaps:letter==='U' || letter==='N' ? ['Synthetic fixture: available evidence is incomplete.'] : [],
      omitted:[],packet_metadata:{reference_time:'2026-10-06T00:00:00Z',evidence_version:'synthetic-fixture-v1',omitted:[]}};
  });
}

/** CSV-ready rows. Callers control file downloads and CSV formula escaping. */
export function exportRows(data, {queryIds=null,judge='llm',providerIds=null}={}) {
  const selected = queryIds && new Set(queryIds);
  const providers = providerIds && new Set(providerIds);
  const queryMap = new Map(data.queries.map(q=>[q.id,q]));
  return data.rows.filter(row=>(!selected || selected.has(row.query_id)) && (!providers || providers.has(row.provider)))
    .map(row=>{
      const query = queryMap.get(row.query_id);
      const metric = getMetrics(row,judge,query);
      return {query_id:query.id,query_index:query.index,query:query.query,complexity:query.complexity,
        industry:query.industry,provider:row.provider,judge,trial:row.trial ?? 1,synthetic:data.meta.synthetic,
        ...Object.fromEntries(Object.entries(metric ?? {}).filter(([,value])=>!record(value) && !Array.isArray(value)))};
    });
}
