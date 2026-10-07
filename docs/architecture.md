# Architecture

CompanyBench is a Python package with four explicit stages: **search → research → grade →
report**. Each stage writes inspectable local artifacts. Reporting is offline, and changing
a judge does not rerun company search. Pydantic models provide a small shared vocabulary;
there is no workflow server or database to operate.

```text
src/companybench/
  models.py          Queries, results, jobs, identities, evidence, judgments, costs
  datasets.py        Loading, validation, selection, and export
  data/              Versioned queries, development cases, sources, metadata, retirements
  providers/         Thin API adapters and named configuration registry
  client.py          Sync, async, and submit/poll provider facade
  transport.py       Paid-call intent, checkpoints, HTTP, and soft budget tracking
  runner.py          Stage orchestration, task cohort, shared evidence, cached grading
  evidence.py        Identity normalization, bounded research and public source capture
  extensions.py      Shared trusted module/file factory loading and source hashes
  judges/            Shared rubric, deterministic rules, LLM/Decisions/Jev adapters
  pricing.py         Explicit price assumptions and nullable estimates
  metrics.py         Pure accounting and aggregation
  report.py          JSON, CSV, Markdown, HTML, and immutable publication bundles
  review.py          Optional human judgments and reviewed identity corrections
  storage.py         Atomic JSON writes, locks, hashes, and event records
  cli.py             Commands over the same Python interfaces
  demo.py            Fully synthetic offline example using the real pipeline
```

## Provider boundary

An `ImmediateProvider` implements `async search(request, context) -> SearchResult`.
A `JobProvider` implements `async submit(request, context) -> SearchJob` and
`async poll(request, job, context) -> SearchJob`. Job cancellation and cleanup are optional;
their default return value is false. `SyncProvider(name, function)` wraps a blocking function
that accepts a `SearchRequest` and returns a `SearchResult`.

`SearchClient` exposes `search`, `asearch`, `submit`, and `poll`. The synchronous `search`
facade cannot run inside an active event loop; use `await asearch` there. The client owns
poll intervals, deadlines, saved job handles, and censored timing. Adapters own wire
formats and native pagination. Keep every result position, malformed item, native usage,
partial result, and terminal error; do not silently filter or repair a provider's answer.

`objective(request)` formats the shared query and acceptance rules for search. Reference
answers and dataset provenance are deliberately excluded. An adapter should not receive
extra benchmark answer keys or use them to enrich its output. Cap results consistently and
preserve native ordering; the metric layer evaluates a prefix without backfilling.

Provider registration uses a preset, installed `companybench.providers` entry point, or
explicit trusted `module:factory` / `/path/provider.py:factory`. The factory receives
`**options` and returns an `ImmediateProvider` or `JobProvider`. Names are discovered without
loading plugins. A local plugin is executable Python, not a sandboxed data file.

## Durable execution

The manifest freezes selected queries, provider configurations, trials, reference time,
seed, randomized task order, and hashes. Tasks are query × configuration × trial.
`RunStore` uses atomic replacements for JSON, an append-only event log, and a process lock.
Task IDs are hashed into filenames; artifact paths cannot escape the run directory.
Evaluation costs use an append-only journal and a convenient JSON snapshot at stage
completion. Each company judgment is checkpointed as its own atomic file before the
consolidated configuration file is written. This preserves completed work after an
interruption without rewriting an entire large cohort for every response.

`CallContext` records paid-request intent before submission. A request whose acceptance is
uncertain must not be blindly repeated: reuse the saved response/job or reconcile the
operation. Read-only polling can be resumed. Saved search bindings reject a different
request or provider configuration using the same checkpoint. The client keeps remote
handles even when its local deadline is reached. Soft budget tracking cannot guarantee
the final invoice, particularly for unknown-price or already accepted work.
After an expired deadline, normalization may replay completed paid responses already on
disk. This recovery mode blocks network requests and new request intents; a missing
continuation response requires reconciliation rather than a fresh paid call.

## Shared evaluation

Research starts after the full search cohort is accounted for. It resolves identities
conservatively, pools candidate leads per query/company, and researches one packet per
pooled identity. Reviewed overrides can disambiguate subsidiaries or aliases. Source fetches
enforce public HTTP(S) destinations, bounded redirects/content, and DNS checks; evidence
text remains untrusted input. Captured sources and excerpt metadata support auditing.

Packets are bounded before judging so all selected judges receive the same material.
The evidence revision hashes the packets, identity mapping, search cohort, fixed reference
time, capture revision, and research settings. Its immutable metadata also records the
normalized search-result hash. Each judgment separately records its packet and rubric hashes. Grading checks
these artifacts and caches successful judgments by evidence/configuration version. Errors
remain incomplete grading. Resuming uses the same paid-operation journal: it reuses saved
responses and only resumes or reconciles operations when safe. A cached rejected or malformed
response requires a new grading configuration or evidence revision to make another paid
attempt; resuming does not silently resend it.
Grading configuration hashes include the judge implementation and local factory source
identity, so changing a file-based judge does not reuse its earlier verdicts.

Judge adapters share three-valued acceptance logic. They return `CompanyJudgment` objects;
metrics do not call an LLM. New judges can be registered locally or installed through
`companybench.judges`; see [judge documentation](judges.md) for the exact factory interface.
The demo injects deterministic researchers and judges while exercising the same stages.

## Artifact boundary

```text
run/
  manifest.json
  events.jsonl
  tasks/<task-hash>.json
  operations/<task-hash>/<operation-hash>.json
  evidence/current.json
  evidence/<revision>/{metadata,packets,identities}.json
  judgments/<revision>/<judge>/<configuration-hash>.json
  judgments/<revision>/<judge>/<configuration-hash>/companies/<company-hash>.json
  evaluation_costs.jsonl
  evaluation_costs.json
  reports/<revision-and-view>/<prefix>/
```

Raw local artifacts support diagnosis and can contain restricted provider content. Public
bundles remove native payloads and known secret/contact fields, omit source text without
recorded redistribution permission, and include checksums. They are a separate export,
not permission to publish the entire run directory. Corrections create new evidence,
judgment, dataset, or publication versions as appropriate. See
[methodology](methodology.md) for scoring and claim boundaries.

`report --evidence-version FULL_HASH` reads a retained evidence revision without changing
the current pointer. Reports record the code hash used for analysis. Research and judging
costs are labeled as whole-run overhead, including other revisions and judges; report
filters do not reduce that ledger.
