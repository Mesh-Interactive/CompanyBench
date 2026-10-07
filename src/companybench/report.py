"""Offline reports and redacted, hashed publication bundles."""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from companybench.metrics import aggregate, paired_interval, query_facets
from companybench.runner import evaluation_costs, metric_rows, package_code_hash
from companybench.storage import RunStore, canonical_json, fingerprint

SECRET_FIELDS = {
    "api_key",
    "authorization",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "workspace_id",
    "workspaceId",
    "account_id",
    "user_id",
    "idempotency_key",
    "contacts",
    "email",
    "phone",
    "headers",
    "persona_ids",
    "artifact_directory",
}

_PRIVATE_KEYS = {re.sub(r"[^a-z0-9]", "", key.casefold()) for key in SECRET_FIELDS} | {
    "raw",
    "rawresponse",
    "rawrequest",
    "requestbody",
    "responsebody",
    "bodybase64",
    "apikeys",
    "secret",
    "clientsecret",
    "credentials",
    "cookies",
    "setcookie",
    "accountnumber",
    "accountname",
    "workspacename",
    "organizationid",
    "organizationname",
    "customerid",
    "billingid",
    "billingaccountid",
    "billingaccount",
    "subscriptionid",
    "userid",
    "username",
    "personaid",
    "contactid",
    "emailaddress",
    "phonenumber",
    "signalid",
    "runid",
    "jobid",
    "providerjobid",
}


def _private_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return normalized in _PRIVATE_KEYS or normalized.endswith(("apikey", "apikeys", "clientsecret"))


def _redact_text(value: str) -> str:
    """Catch credential-bearing URLs and common token forms inside free-form errors."""

    def url(match: re.Match[str]) -> str:
        original = match.group(0)
        try:
            parsed = urlsplit(original)
            if not parsed.hostname:
                return original
            authority = parsed.hostname
            if ":" in authority:
                authority = "[" + authority + "]"
            if parsed.port:
                authority += ":" + str(parsed.port)
            sensitive = {
                "sig",
                "signature",
                "key",
                "xamzsignature",
                "xamzcredential",
                "xamzsecuritytoken",
                "googleaccessid",
                "xgoogsignature",
                "xgoogcredential",
            }
            query = []
            changed = bool(parsed.username or parsed.password)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True):
                normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
                if _private_key(key) or normalized in sensitive:
                    item = "[REDACTED]"
                    changed = True
                query.append((key, item))
            return (
                urlunsplit(
                    (parsed.scheme, authority, parsed.path, urlencode(query), parsed.fragment)
                )
                if changed
                else original
            )
        except ValueError:
            return "[REDACTED INVALID URL]"

    value = re.sub(r'https?://[^\s<>"\']+', url, value)
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", value)
    return re.sub(r"\bsk-(?:proj-|ant-[a-z0-9-]*-)?[A-Za-z0-9_-]{20,}", "[REDACTED API KEY]", value)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        output = {key: redact(item) for key, item in value.items() if not _private_key(str(key))}
        # Preserve the typed identity assessment, never its enclosing raw API response.
        raw = value.get("raw")
        if (
            {"judge", "conditions", "packet_hash"}.issubset(value)
            and isinstance(raw, dict)
            and "identity_judgment" in raw
        ):
            output["identity_judgment"] = redact(raw["identity_judgment"])
        return output
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def build_report(
    store: RunStore,
    *,
    cost_view: str = "public",
    prefix: int | None = None,
    query_ids: set[str] | None = None,
) -> dict[str, Any]:
    manifest = store.read("manifest.json")
    rows = metric_rows(store, cost_view=cost_view, prefix=prefix)
    if query_ids is not None:
        rows = [row for row in rows if row["query_id"] in query_ids]
    tasks = [
        task for task in manifest["tasks"] if query_ids is None or task["query_id"] in query_ids
    ]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["provider"], row["judge"]].append(row)
    declared_judges = set(
        manifest.get("declared_judges", manifest["settings"].get("judges", ["llm"]))
    )
    declared_judges.update(row["judge"] for row in rows)
    for provider in {task["provider"] for task in tasks}:
        for judge in declared_judges:
            groups.setdefault((provider, judge), [])
    query_by_id = {query["id"]: query for query in manifest["queries"]}
    facet_query_ids: dict[tuple[str, Any], set[str]] = defaultdict(set)
    for task in tasks:
        for facet, values in query_facets(query_by_id[task["query_id"]]).items():
            for value in values if isinstance(values, list) else [values]:
                facet_query_ids[facet, value].add(task["query_id"])
    summaries = []
    segments = []
    for (provider, judge), group in sorted(groups.items()):
        expected = sum(task["provider"] == provider for task in tasks)
        summaries.append(
            {"provider": provider, "judge": judge, **aggregate(group, expected_tasks=expected)}
        )
        facet_groups: dict[tuple[str, Any], list[dict[str, Any]]] = defaultdict(list)
        for row in group:
            for facet, values in row["facets"].items():
                for value in values if isinstance(values, list) else [values]:
                    facet_groups[facet, value].append(row)
        for facet, value in sorted(facet_query_ids, key=lambda pair: (pair[0], str(pair[1]))):
            selected = facet_groups[facet, value]
            expected_segment = sum(
                task["provider"] == provider and task["query_id"] in facet_query_ids[facet, value]
                for task in tasks
            )
            segments.append(
                {
                    "provider": provider,
                    "judge": judge,
                    "facet": facet,
                    "value": value,
                    **aggregate(selected, expected_tasks=expected_segment),
                }
            )
    comparisons = []
    judges = sorted({judge for _, judge in groups})
    for judge in judges:
        for left, right in itertools.combinations(
            sorted(provider for provider, j in groups if j == judge), 2
        ):
            left_summary = next(
                item for item in summaries if item["provider"] == left and item["judge"] == judge
            )
            right_summary = next(
                item for item in summaries if item["provider"] == right and item["judge"] == judge
            )
            if left_summary["comparison_complete"] and right_summary["comparison_complete"]:
                comparisons.append(
                    {
                        "left": left,
                        "right": right,
                        "judge": judge,
                        **paired_interval(
                            groups[left, judge],
                            groups[right, judge],
                            seed=manifest["settings"]["seed"],
                        ),
                    }
                )
    agreements = []
    all_verdicts: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for row in rows:
        for slot in row["slots"]:
            judgment = slot.get("judgment")
            if judgment:
                all_verdicts[row["query_id"], judgment["entity_id"]][row["judge"]] = judgment[
                    "verdict"
                ]
    for left, right in itertools.combinations(judges, 2):
        attempted = [
            values for values in all_verdicts.values() if left in values and right in values
        ]
        paired = [
            values for values in attempted if values[left] != "error" and values[right] != "error"
        ]
        agreements.append(
            {
                "left": left,
                "right": right,
                "companies": len(paired),
                "attempted_company_pairs": len(attempted),
                "judged_coverage": len(paired) / len(attempted) if attempted else None,
                "agreement": sum(values[left] == values[right] for values in paired) / len(paired)
                if paired
                else None,
            }
        )
    current = store.read("evidence/current.json")
    packets = store.read(f"evidence/{current['version']}/packets.json")
    if query_ids is not None:
        packets = [packet for packet in packets if packet["query"]["id"] in query_ids]
    return {
        "title": "CompanyBench",
        "status": "Automated preliminary results",
        "comparison_complete": bool(summaries)
        and all(item["comparison_complete"] for item in summaries),
        "synthetic": manifest.get("synthetic", False),
        "cost_view": cost_view,
        "prefix": prefix,
        "dataset_hash": manifest["dataset_hash"],
        "settings_hash": manifest["settings_hash"],
        "analysis_code_hash": package_code_hash(),
        "evidence_version": current["version"],
        "reference_time": manifest["reference_time"],
        "selected_queries": [
            query for query in manifest["queries"] if query_ids is None or query["id"] in query_ids
        ],
        "configurations": {
            name: {
                "preset": manifest.get("provider_presets", {}).get(name, {}),
                "overrides": manifest["settings"].get("provider_options", {}).get(name, {}),
                "returned_models": sorted(
                    {row["model"] for row in rows if row["provider"] == name and row.get("model")}
                ),
            }
            for name in {task["provider"] for task in tasks}
        },
        "calculation_settings": {
            key: manifest["settings"].get(key)
            for key in [
                "target_count",
                "trials",
                "timeout",
                "concurrency",
                "provider_concurrency",
                "seed",
            ]
        }
        | {"cost_view": cost_view, "prefix": prefix},
        "summaries": summaries,
        "segments": segments,
        "paired_comparisons": comparisons,
        "judge_agreement": agreements,
        "rows": rows,
        "evidence": packets,
        "evaluation_costs": evaluation_costs(store),
        "evaluation_cost_scope": "Whole run, including all evidence revisions and judges; not restricted by report filters.",
        "outstanding_jobs": [
            {
                "task_id": task["task_id"],
                "provider": task["provider"],
                "status": store.task(task["task_id"])
                .get("checkpoints", {})
                .get("job", {})
                .get("status"),
            }
            for task in tasks
            if store.task(task["task_id"]).get("checkpoints", {}).get("job", {}).get("status")
            in {"queued", "running"}
        ],
        "limitations": [
            "This is an authored breadth catalogue, not a sample of GTM search volume.",
            "Validity is an automated judgment; independent human accuracy is not established.",
            "Unknown facts receive no credit; evidence retrieval can affect the result.",
            "Intervals condition on the selected queries and judgments; they exclude annotation error and API drift.",
            "Effort, model, endpoint, limits, and pricing assumptions define the system being compared.",
            "Missing prices and uncertain completion times remain unknown.",
            "Research and judging overhead covers the whole run, including retained revisions, rather than only the displayed selection.",
        ],
    }


def _number(value: Any, *, percent: bool = False) -> str:
    if value is None:
        return "Unknown"
    return (
        f"{value * 100:.1f}%"
        if percent
        else f"{value:,.3f}"
        if isinstance(value, float)
        else str(value)
    )


def markdown(report: dict[str, Any]) -> str:
    lines = ["# CompanyBench", "", "**Automated preliminary results.**", ""]
    if report["synthetic"]:
        lines += ["**Synthetic offline demonstration. These are not provider measurements.**", ""]
    if not report["comparison_complete"]:
        lines += ["**Comparison incomplete: do not use these results for headline rankings.**", ""]
    lines += [
        "Created by Avina / Mesh Interactive. Commercial interest is disclosed; all configurations and errors are inspectable.",
        "",
        f"Reference time: {report['reference_time']}. Cost view: {report['cost_view']}.",
        "",
        "| Configuration | Judge | Queries | Mean valid companies | Precision | Quality score | Search cost ($) | Cost per valid company ($) | Complete |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["summaries"]:
        values = [
            item["provider"],
            item["judge"],
            str(item["queries"]),
            _number(item["mean_valid_companies"]),
            _number(item["precision"], percent=True),
            _number(item["quality_score"]),
            _number(item["cost_usd"]),
            _number(item["cost_per_valid_company"]),
            str(item["comparison_complete"]),
        ]
        lines.append(
            "| "
            + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in values)
            + " |"
        )
    lines += [
        "",
        "Precision = distinct valid companies / returned positions (duplicates and malformed positions included).",
        "Quality score = 200 × valid companies / (returned positions + requested count).",
        "For exhaustive smaller reference lists, quality uses the eligible count instead of requested count.",
        "Research and judging are separate evaluation costs. Search time per valid company divides full search time by valid count.",
        "",
        *[f"- {value}" for value in report["limitations"]],
        "",
    ]
    return "\n".join(lines)


def html_report(report: dict[str, Any]) -> str:
    # Keep the companion report/artifacts intact. HTML needs one query definition
    # per catalogue entry and typed judgments, not repeated native API payloads.
    display = dict(report)
    if "evidence" in report:
        display["evidence"] = [
            {**packet, "query": {"id": packet.get("query", {}).get("id")}}
            for packet in report["evidence"]
        ]
    if "rows" in report:
        display["rows"] = []
        for row in report["rows"]:
            slots = []
            for slot in row.get("slots", []):
                normalized = dict(slot)
                judgment = slot.get("judgment")
                if isinstance(judgment, dict):
                    normalized["judgment"] = {
                        key: value for key, value in judgment.items() if key != "raw"
                    }
                    raw = judgment.get("raw")
                    if isinstance(raw, dict) and "identity_judgment" in raw:
                        normalized["judgment"].setdefault(
                            "identity_judgment", raw["identity_judgment"]
                        )
                slots.append(normalized)
            display["rows"].append({**row, "slots": slots})
    # Data is inert JSON. Escaping '<' prevents payloads from closing the script element.
    data = (
        json.dumps(display, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return (
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CompanyBench results</title><style>
body{font:15px system-ui,sans-serif;color:#17252d;background:#f5f7fa;margin:0}main{max-width:1400px;margin:auto;padding:32px}h1{font-size:34px;margin:0}h2{margin-top:30px}p{line-height:1.55}.notice{padding:14px;background:#fff3d6;border-radius:8px;margin:18px 0}.card{background:white;border:1px solid #dce3ea;border-radius:10px;padding:18px;margin:18px 0;overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:10px;border-bottom:1px solid #edf0f4;vertical-align:top}th{background:#f5f7fa}select,input,button{padding:9px;border:1px solid #cad4df;border-radius:6px;margin:4px;font:inherit}button{background:white;cursor:pointer}button:disabled{opacity:.45;cursor:default}details{margin:10px 0}summary{cursor:pointer;line-height:1.6}pre{white-space:pre-wrap;word-break:break-word;font:12px ui-monospace,monospace}a{color:#175f9e}svg{width:100%;height:300px}.plots{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(400px,100%),1fr));gap:20px}small{color:#526472}.pill{padding:3px 7px;background:#eaf0f7;border-radius:5px}.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}.filters label{display:flex;flex-direction:column;font-size:13px}.filters select{min-width:0;max-width:100%}.pagination{display:flex;gap:8px;align-items:center;flex-wrap:wrap;position:sticky;top:0;background:#f5f7fa;padding:8px 0;z-index:1}.query-preview{color:#526472;margin:8px 0 0}.task-content{border-top:1px solid #edf0f4;margin-top:14px}.metric-definitions{display:grid;grid-template-columns:max-content 1fr;gap:5px 16px}.metric-definitions dt{font-weight:600}.metric-definitions dd{margin:0}noscript{display:block;padding:18px;background:#fff3d6}
</style><main><h1>CompanyBench</h1><p>Company list building · <strong>Automated preliminary results</strong></p>
<p>Created by <a href="https://www.avina.io">Avina / Mesh Interactive</a>. Authorship and commercial interest are disclosed.</p>
<div id="notice" class="notice"></div><p id="runinfo"></p><div class="card"><table id="summary"></table></div>
<div class="plots"><div class="card"><h2>Quality and cost</h2><svg id="costplot"></svg></div><div class="card"><h2>Quality and search time</h2><svg id="timeplot"></svg></div></div>
<p>Precision = distinct valid companies / all returned positions. Quality score = 200 × valid companies / (returned positions + requested count). Smaller exhaustive reference lists use their eligible count.</p>
<details class="card"><summary>Configurations and calculation settings</summary><pre id="configurations"></pre></details>
<h2>Inspect queries and companies</h2><p>Inspection filters narrow the cards below. Summary statistics and plots above describe the complete saved report.</p><p><small>Normalized HTML inspection data. The companion report.json retains full report detail, subject to publication redaction.</small></p>
<label>Configuration <select id="provider"></select></label><label>Judge <select id="judge"></select></label><label>Find a query <input id="filter" type="search" placeholder="Query, industry, signal, or ID"></label><button id="reset-filters" type="button">Reset filters</button>
<details class="card"><summary>Segment filters · all selections intersect (AND)</summary><p>Combine any facets. Counts beside values refer to catalogue queries in this report.</p><div id="facet-filters" class="filters"></div></details>
<p id="result-count" role="status" aria-live="polite"></p>
<nav class="pagination" aria-label="Task result pages"><button id="previous-page" type="button">Previous page</button><span id="page-status" role="status" aria-live="polite"></span><button id="next-page" type="button">Next page</button><label>Cards per page <select id="page-size"><option>10</option><option selected>20</option><option>50</option></select></label></nav>
<div id="details"></div><noscript>Enable JavaScript to inspect this standalone offline report. The companion report.json contains all results.</noscript>
<h2>Limits of these results</h2><ul id="limits"></ul><p>Intervals condition on these authored queries and automated judgments. Judge agreement does not establish human accuracy.</p>
<details><summary>Paired comparisons and judge agreement</summary><pre id="comparisons"></pre></details>
<details><summary>Research and judging cost ledger</summary><pre id="overhead"></pre></details>
</main><script type="application/json" id="data">"""
        + data
        + """</script><script>
const d=JSON.parse(document.getElementById('data').textContent);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=v=>v==null?'Unknown':Number(v).toLocaleString(undefined,{maximumFractionDigits:3});
const pct=v=>v==null?'Unknown':(v*100).toFixed(1)+'%';
const safe=v=>{try{const u=new URL(v);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:''}catch{return ''}};
document.getElementById('notice').textContent=d.synthetic?'Synthetic demonstration: no provider was called.':d.comparison_complete?'Complete declared comparison. Results use automated judgments.':'Comparison incomplete. Do not use it for headline rankings.';
document.getElementById('runinfo').textContent=`Reference time: ${d.reference_time}. Cost view: ${d.cost_view}. Queries: ${d.selected_queries.length}. Evidence: ${d.evidence_version.slice(0,12)}.`;
document.getElementById('summary').innerHTML='<tr><th>Configuration / Judge</th><th>Mean valid companies</th><th>Precision</th><th>Quality</th><th>Search cost</th><th>$/valid company</th><th>Mean seconds</th><th>Missing evidence</th><th>Price coverage</th><th>Completed tasks</th></tr>'+d.summaries.map(s=>`<tr><td>${esc(s.provider)}<br><small>${esc(s.judge)}</small></td><td>${num(s.mean_valid_companies)}</td><td>${pct(s.precision)}</td><td>${num(s.quality_score)}</td><td>${num(s.cost_usd)}</td><td>${num(s.cost_per_valid_company)}</td><td>${num(s.mean_latency_seconds)}</td><td>${pct(s.unknown_fraction)}</td><td>${pct(s.price_coverage)}</td><td>${esc(s.completed_tasks)}/${esc(s.expected_tasks)}</td></tr>`).join('');
function plot(id,field,label){const points=d.summaries.filter(s=>s.quality_score!=null&&s[field]!=null),svg=document.getElementById(id);svg.setAttribute('viewBox','0 0 620 300');if(!points.length){svg.innerHTML='<text x="20" y="50">No complete priced/timed results</text>';return}const max=Math.max(1,...points.map(s=>s[field]));svg.innerHTML=`<line x1="55" y1="255" x2="600" y2="255" stroke="#667"/><line x1="55" y1="255" x2="55" y2="15" stroke="#667"/><text x="260" y="290">${esc(label)}</text><text x="5" y="20">100</text><text x="10" y="255">0</text>`+points.map((s,i)=>{let x=55+520*s[field]/max,y=255-230*s.quality_score/100;return `<circle cx="${x}" cy="${y}" r="6" fill="hsl(${i*53%360} 55% 43%)"><title>${esc(s.provider+' / '+s.judge)}: quality ${num(s.quality_score)}, ${esc(label)} ${num(s[field])}</title></circle><text x="${Math.min(x+8,490)}" y="${y-8}" font-size="10">${esc(s.provider)}</text>`}).join('')}
plot('costplot','cost_per_valid_company','USD per valid company');plot('timeplot','mean_latency_seconds','Mean search seconds');
document.getElementById('configurations').textContent=JSON.stringify({configurations:d.configurations||{},calculation_settings:d.calculation_settings||{}},null,2);
const q=new Map(d.selected_queries.map(x=>[x.id,x])),e=new Map(d.evidence.map(x=>[JSON.stringify([x.query.id,x.company.id]),x]));
const excludedFacets=new Set(['id','index','query','conditions','rule','acceptance','reference','evidence','pitfalls','gtm_use_case','source_urls','source_ids','fixture','logic','verification_limitations']);
const scalar=v=>['string','number','boolean'].includes(typeof v),values=v=>Array.isArray(v)?v:[v];
const facets=new Map(d.selected_queries.map(x=>[x.id,Object.fromEntries(Object.entries(x).filter(([k,v])=>!excludedFacets.has(k)&&(scalar(v)||(Array.isArray(v)&&v.every(scalar)))))]));
for(const r of d.rows)facets.set(r.query_id,{...(facets.get(r.query_id)||{}),...(r.facets||{})});
const textIndex=new Map(d.selected_queries.map(x=>[x.id,JSON.stringify([x,facets.get(x.id)]).toLowerCase()]));
const facetValues=new Map();
for(const [id,record] of facets)for(const [key,value] of Object.entries(record))for(const v of values(value)){if(!scalar(v)||v==='')continue;if(!facetValues.has(key))facetValues.set(key,new Map());const encoded=JSON.stringify(v),catalogue=facetValues.get(key);if(!catalogue.has(encoded))catalogue.set(encoded,{value:v,queries:new Set()});catalogue.get(encoded).queries.add(id)}
const facetControls=[];let page=0;const DEFAULT_PAGE_SIZE=20;
for(const [key,options] of [...facetValues].sort(([a],[b])=>a.localeCompare(b))){const label=document.createElement('label');label.appendChild(document.createTextNode(key.replaceAll('_',' ')));const select=document.createElement('select');select.dataset.facet=key;select.innerHTML='<option value="">All</option>'+[...options].sort((a,b)=>String(a[1].value).localeCompare(String(b[1].value))).map(([value,item])=>`<option value="${esc(value)}">${esc(item.value)} (${item.queries.size})</option>`).join('');select.addEventListener('change',resetPage);label.appendChild(select);document.getElementById('facet-filters').appendChild(label);facetControls.push(select)}
for(const [id,key] of [['provider','provider'],['judge','judge']]){document.getElementById(id).innerHTML='<option value="">All</option>'+[...new Set(d.rows.map(r=>r[key]))].sort().map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');document.getElementById(id).addEventListener('change',resetPage)}
document.getElementById('filter').addEventListener('input',resetPage);document.getElementById('page-size').addEventListener('change',resetPage);
document.getElementById('previous-page').addEventListener('click',()=>{page=Math.max(0,page-1);render()});document.getElementById('next-page').addEventListener('click',()=>{page++;render()});
document.getElementById('reset-filters').addEventListener('click',()=>{for(const id of ['provider','judge','filter'])document.getElementById(id).value='';for(const control of facetControls)control.value='';resetPage()});
function resetPage(){page=0;render()}
function matchingQueries(){const term=document.getElementById('filter').value.trim().toLowerCase(),selected=facetControls.filter(c=>c.value);return new Set([...q.keys()].filter(id=>(textIndex.get(id)||'').includes(term)&&selected.every(c=>values(facets.get(id)?.[c.dataset.facet]).some(v=>JSON.stringify(v)===c.value))))}
function taskKey(r){return JSON.stringify([r.query_id,r.provider,r.trial])}
function render(){const p=document.getElementById('provider').value,j=document.getElementById('judge').value,matching=matchingQueries();const rows=d.rows.map((row,index)=>({row,index})).filter(({row:r})=>matching.has(r.query_id)&&(!p||r.provider===p)&&(!j||r.judge===j));const size=Number(document.getElementById('page-size').value)||DEFAULT_PAGE_SIZE,pages=Math.max(1,Math.ceil(rows.length/size));page=Math.min(page,pages-1);const start=page*size,visibleRows=rows.slice(start,start+size),taskCount=new Set(rows.map(({row})=>taskKey(row))).size,queryCount=new Set(rows.map(({row})=>row.query_id)).size;
document.getElementById('result-count').textContent=`${matching.size} of ${q.size} catalogue queries match; ${queryCount} have displayed results. ${taskCount} search tasks · ${rows.length} task judgments of ${d.rows.length} saved.`;
document.getElementById('page-status').textContent=`Page ${rows.length?page+1:0} of ${rows.length?pages:0} · showing ${rows.length?start+1:0}–${Math.min(start+size,rows.length)} task judgments`;
document.getElementById('previous-page').disabled=page===0;document.getElementById('next-page').disabled=page>=pages-1;
document.getElementById('details').innerHTML=visibleRows.map(({row:r,index})=>`<details class="card" data-task-row="${index}"><summary><strong>${esc(r.query_id)} · ${esc(r.provider)} · ${esc(r.judge)} · trial ${esc(r.trial??1)}</strong> — ${num(r.valid_companies)} valid, ${pct(r.precision)} precision, quality ${num(r.quality_score)}</summary><p class="query-preview">${esc(q.get(r.query_id)?.query||'Query unavailable')}</p><div class="task-content"></div></details>`).join('')||'<p>No matching task results. Reset filters to see the saved cohort.</p>'}
function taskContent(r){const query=q.get(r.query_id)||{},definition={acceptance:query.acceptance||'',conditions:query.conditions||[],acceptance_logic:query.rule||query.logic||'All conditions',company_unit:query.company_unit,reference:query.reference},denominators={requested_count:r.requested_count,returned_positions:r.returned_companies,unique_companies:r.unique_companies,valid_companies:r.valid_companies,exhaustive_reference_count:r.reference_count??null,scored_prefix:d.prefix??null};return `<p>Status: ${esc(r.search_status)}. ${esc(r.search_error||'')} ${r.grading_complete?'':'Grading incomplete.'}</p><dl class="metric-definitions"><dt>Unknown / unresolved</dt><dd>${num(r.unknown_companies)} / ${num(r.unresolved_identities)}</dd><dt>Duplicates / malformed</dt><dd>${num(r.duplicates)} / ${num(r.malformed)}</dd><dt>Search cost / time</dt><dd>${num(r.cost_usd)} USD / ${num(r.latency_seconds)} seconds</dd><dt>Observed elapsed time</dt><dd>${num(r.observed_elapsed_seconds)} seconds${r.timing_censored?' (timing censored)':''}</dd></dl><details><summary>Exact query conditions and acceptance logic</summary><pre>${esc(JSON.stringify(definition,null,2))}</pre></details><details><summary>Result denominators and undefined metrics</summary><pre>${esc(JSON.stringify({denominators,undefined_reasons:r.undefined_reasons||{},returned_model:r.model,usage:r.usage,cost_details:r.cost_details},null,2))}</pre></details><details><summary>Query facets</summary><pre>${esc(JSON.stringify(facets.get(r.query_id)||{},null,2))}</pre></details><h3>Company positions (${num((r.slots||[]).length)})</h3>${(r.slots||[]).map((s,index)=>`<details data-company-slot="${index}"><summary>#${esc(s.position)} ${esc(s.name||s.domain||'Malformed')} <span class="pill">${esc(s.category)}</span></summary><div class="company-content"></div></details>`).join('')||'<p>No company positions returned.</p>'}`}
function companyContent(r,s){const packet=e.get(JSON.stringify([r.query_id,s.entity_id]));return `<pre>${esc(JSON.stringify(s.judgment||{category:s.category},null,2))}</pre><p>${esc(packet?.company.notes||'')}</p>${(packet?.sources||[]).map(src=>`<details><summary>${safe(src.url)?`<a href="${esc(safe(src.url))}" target="_blank" rel="noopener noreferrer">${esc(src.url)}</a>`:esc(src.url)} · ${esc(src.id)}</summary><pre>${esc(src.text||src.error||'Excerpt omitted from publication')}</pre><small>Fetched ${esc(src.fetched_at)}; published ${esc(src.published_at||'unknown')}; method ${esc(src.method)}</small></details>`).join('')||'<p>No evidence sources in this packet.</p>'}`}
document.getElementById('details').addEventListener('toggle',event=>{const target=event.target;if(!target.open||target.dataset.loaded)return;if(target.hasAttribute('data-task-row')){const row=d.rows[Number(target.dataset.taskRow)];target.querySelector('.task-content').innerHTML=taskContent(row);target.dataset.loaded='true'}else if(target.hasAttribute('data-company-slot')){const task=target.closest('[data-task-row]'),row=d.rows[Number(task.dataset.taskRow)],slot=row.slots[Number(target.dataset.companySlot)];target.querySelector('.company-content').innerHTML=companyContent(row,slot);target.dataset.loaded='true'}},true);
render();document.getElementById('limits').innerHTML=d.limitations.map(x=>'<li>'+esc(x)+'</li>').join('');document.getElementById('comparisons').textContent=JSON.stringify({paired:d.paired_comparisons,agreement:d.judge_agreement},null,2);document.getElementById('overhead').textContent=JSON.stringify(d.evaluation_costs,null,2);
</script></html>"""
    )


def write_report(
    store: RunStore,
    *,
    output: Path | None = None,
    cost_view: str = "public",
    prefix: int | None = None,
    query_ids: set[str] | None = None,
    publication: bool = False,
) -> Path:
    report = build_report(store, cost_view=cost_view, prefix=prefix, query_ids=query_ids)
    target = output or store.path / "reports" / f"{report['evidence_version'][:12]}-{cost_view}" / (
        str(prefix) if prefix else "full"
    )
    target.mkdir(parents=True, exist_ok=True)
    (target / "report.json").write_text(canonical_json(report) + "\n")
    (target / "report.md").write_text(markdown(report))
    (target / "report.html").write_text(html_report(report))
    fields = (
        [key for key in report["rows"][0] if key not in {"slots", "facets", "undefined_reasons"}]
        if report["rows"]
        else ["query_id", "provider"]
    )
    with (target / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(
            {
                key: canonical_json(value) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            }
            for row in report["rows"]
        )
    if publication:
        publish_bundle(store, report, target / "publication")
    return target


def publish_bundle(store: RunStore, report: dict[str, Any], target: Path) -> None:
    if not report["comparison_complete"]:
        raise ValueError(
            "Publication requires complete searches and grading for the declared comparison"
        )
    clean = redact(report)
    for packet in clean["evidence"]:
        for source in packet["sources"]:
            if not source.get("redistributable"):
                source["text_sha256"] = hashlib.sha256(source.get("text", "").encode()).hexdigest()
                source["text"] = ""
                source["publication_omission"] = (
                    "No redistribution permission recorded; source URL and content hash retained"
                )
    manifest = redact(store.read("manifest.json"))
    if manifest.get("synthetic"):
        manifest["evaluation_status"] = "synthetic demonstration"
    results = []
    selected = {query["id"] for query in report["selected_queries"]}
    for task in manifest["tasks"]:
        if task["query_id"] in selected:
            state = store.task(task["task_id"])
            value = redact(state.get("result", {}))
            value.pop(
                "raw", None
            )  # Native payloads can contain contacts or restricted provider data.
            for candidate in value.get("candidates", []):
                candidate.pop("raw", None)
            results.append({"task": task, "result": value})
    payload = {"manifest.json": manifest, "report.json": clean, "normalized_results.json": results}
    bundle_hash = fingerprint(payload)
    if target.exists():
        prior = (
            json.loads((target / "checksums.json").read_text())
            if (target / "checksums.json").exists()
            else {}
        )
        if prior.get("bundle_hash") != bundle_hash:
            raise ValueError(
                "Publication bundle exists with different contents; use a new output directory"
            )
        if set(prior.get("files", {})) != {*payload, "report.html"}:
            raise ValueError("Publication checksums do not describe the expected files")
        for name, digest in prior["files"].items():
            path = target / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(
                    "Publication files changed or are missing; export to a new directory"
                )
        return
    target.mkdir(parents=True)
    hashes = {}
    for name, value in payload.items():
        text = canonical_json(value) + "\n"
        (target / name).write_text(text)
        hashes[name] = hashlib.sha256(text.encode()).hexdigest()
    (target / "report.html").write_text(html_report(clean))
    hashes["report.html"] = hashlib.sha256((target / "report.html").read_bytes()).hexdigest()
    (target / "checksums.json").write_text(
        canonical_json({"bundle_hash": bundle_hash, "files": hashes}) + "\n"
    )
