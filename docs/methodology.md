# How CompanyBench measures company search

CompanyBench evaluates **company list building for a stated query, requested list size,
reference time, and system configuration**. Avina / Mesh Interactive created it and has a
commercial interest in the results. The catalogue, adapters, acceptance rules, evidence,
errors, and scoring code are inspectable so that claims can be challenged and reproduced.

The 250 public queries are an authored breadth catalogue, not a representative sample of GTM
search volume. The 74-source [research ledger](sources.md) records inspiration and the
authoritative sources for finite reference cohorts. It does not establish how frequently
people run each query. Twelve separate development cases support adapter development.
The [dataset guide](dataset.md) describes the version, taxonomy, selection, and retired cases.

## What a comparison means

An evaluated system is an endpoint, model, effort setting, prompt, result limit, tool budget,
timeout, and pricing configuration. A claim about one configuration is not a claim about
every product from that vendor. Record these settings before running; report changed
configurations separately. Use the same queries, requested size, reference time, acceptance
rules, and declared trial count for each compared configuration.

The runner randomizes configuration order within query/trial blocks using the recorded
seed. Global and provider concurrency are explicit; configurations sharing an API key
environment variable share a concurrency group. Remote queueing and account limits can
affect latency. Concurrent requests are not a controlled isolated throughput experiment.

Choose a subset or sample before inspecting results. Record its IDs, filters, seed, and
size. Predeclare the main comparison and useful slices; publish unfavorable slices and
failures too. The defaults support a small exploratory run. Repeated trials are configurable
and should be used when making claims about stochastic stability. Rerunning only poor
results and substituting the best answer changes the experiment.

Public reports are labeled **automated preliminary results**. A completed automated
comparison can be published without human review. Human review is an optional way to
measure or correct judging errors; its extent and selection method must be disclosed.
An offline demo contains fabricated fixtures and must never be presented as a provider result.

## Identity, evidence, and judgment

Each query specifies the company unit and authored acceptance conditions. Preserve OR,
exclusions, numerical thresholds, geography basis, event sequence, and time windows.
Original compound queries retain a complete semantic acceptance condition where splitting
would change the meaning. No runtime LLM rewrites the catalogue's acceptance rules.

Returned positions are retained in order. Identity normalization uses business names and
hostnames conservatively; a shared parent domain does not establish the same operating
company. Ambiguous same-hostname identities need a documented identity override to be
resolved. Until then they receive no valid-company credit and remain visible as unresolved.
Human identity corrections record a reason, source URLs, and an explicit reviewed flag.
Apply them through a new evidence revision, not by editing an existing report.

After all searches in the declared cohort have terminated, research pools each query/company
across configurations and trials. Provider citations are leads, not proof. The researcher
can find additional public sources, fetch their content, and construct one bounded evidence
packet shared by every judge. HTML text, selected script markers, and bounded PDF text are
supported; inaccessible or unsupported evidence is recorded as a gap. Excerpts and packet
limits can omit relevant material. Neither model-generated prose nor an inaccessible URL
proves a fact. A fetched page's date does not establish when the claimed event occurred.

Judges see the company, query, acceptance conditions, reference time, and frozen evidence.
They do not see the search configuration, rank, search cost, or reference answers.
The shared rubric instructs them to disregard instructions inside source material and to
distinguish the exact company, parent/subsidiary, headquarters/office, product use/integration,
and announcement/completion. Blinding reduces direct provider favoritism; shared model
families, evidence-selection bias, and recognition of a company's public identity remain.

Every condition receives one of three factual labels:

| Label | Meaning |
|---|---|
| `met` | Evidence affirmatively supports the condition. |
| `failed` | Evidence affirmatively contradicts the condition. |
| `unknown` | Evidence is missing, stale, ambiguous, inaccessible, or conflicting. |

The benchmark applies authored AND/OR/NOT rules deterministically with three-valued logic.
For example, `met OR unknown` is met; `met AND unknown` is unknown; `NOT unknown` remains
unknown. Absence of a mention never proves a negative. Company identity is judged separately.
API errors, refusals, malformed judge responses, and missing judgments are **grading errors**,
not factual unknowns. They prevent a complete score for the affected comparison.

The LLM judge, OpenAI Decisions, and Jev/TypeSafe consume the same packet and rubric, but
their response formats differ. Typed choice APIs may supply probabilities without rationale
or citation attribution; the benchmark preserves that limitation rather than inventing them.
Each judge has its own results. Agreement is reported only over successfully judged pairs,
with coverage, and is not evidence of correctness or independence. The default LLM and
Decisions judges can share an OpenAI model family. See [judges](judges.md) for contracts.

## Per-query metrics

For the evaluated prefix, let **K** be the requested count, **R** the number of returned
positions, and **V** the number of distinct, resolved, valid companies. No backfilling from
positions beyond K is allowed. Duplicates, malformed positions, invalid companies, and
unknowns remain in R. At most one position receives credit for a company.

| Metric | Definition and interpretation |
|---|---|
| Companies returned | R, including bad and repeated positions. |
| Unique companies returned | Distinct resolved identities; also inspect duplicates and unresolved identities. |
| Valid companies found | V; the main useful-yield measure. |
| Precision | V / R. Empty output has undefined precision, not 100%. |
| Valid companies found (% of requested) | V / K; the fraction of the requested count. It is not recall. |
| Cost per valid company | Search cost C / V. Undefined when V is zero or C is unknown. |
| Valid companies per dollar | V / C. Undefined when C is unknown or zero. |
| Search time | Elapsed query wall time through search, polling, and retrieval of results. |
| Search time per valid company | L / V; amortized elapsed time, not time to each discovery. |
| Quality score | Q = 200V / (R + K), on a 0–100 scale. |
| Quality score per dollar | Q / C; cost efficiency in quality-score points per dollar. |

Q is the harmonic mean of precision and valid fraction of requested, multiplied by 100.
It rewards useful yield and penalizes filler. For K=50, ten valid results alone score 33.3;
ten valid results mixed into fifty positions score 20. A failed empty open-ended search has
V=0 and Q=0. A grading error makes Q undefined instead of disguising evaluation failure as
poor search quality. Raw statuses distinguish completed, partial, truncated, failed, and
refused searches. A completed comparison can contain search failures: retaining those
failures is necessary for a fair denominator.

Diagnostic precision among decided or resolved companies is also available. It is not the
headline precision: excluding unknowns or bad slots can make a weak result look strong.

### Recall and finite reference cohorts

True recall needs a known eligible universe. CompanyBench reports it only for an explicitly
exhaustive, dated, authoritative reference set of size G. The ten shipped finite cohorts
are historical award, designation, approval, or ranking lists, with scope documented in
each query. They cover easier-to-verify membership questions; they do not validate recall
for the open-ended catalogue.

Recall is distinct valid reference members found / G. Its attainable ceiling under a cap
is min(K,G) / G. For these queries, Q substitutes **min(K,G)** for K, so finding all three
eligible companies is not penalized for failing to return fifty. The requested-count
fraction still uses K and is labeled accordingly.

A valid automated judgment must also match a reference name or curated alias after limited
case/punctuation/legal-suffix normalization. A shared domain alone does not establish
membership: a parent and subsidiary may share it. Unmatched positive judgments become
visible reference conflicts with no credit. Duplicate aliases count once. A genuine missing
alias or incorrect reference requires a documented dataset correction and new version.
An exhaustive empty reference has no defined recall or Q; a separate correct-empty indicator
is meaningful only for successful completed searches. No empty cohort is shipped as a
shortcut to high scores.

### Unknowns and uncertainty

Unknown and unresolved positions receive zero valid credit in the primary score. Optimistic
bounds add those positions to V, capped by the attainable count, while retaining R. These
are sensitivity bounds, not confidence intervals: unresolved positions might be duplicates,
and unknown conditions may still be false. Grading errors do not get an optimistic score.
Report unknown rates alongside quality, especially when comparing industries with uneven
public evidence. Missing evidence is not proof that a provider returned an invalid company.

## Costs, timing, and aggregation

Three cost views are separate: public list-price estimate, account-specific estimate, and
confirmed charge. A missing price or usage quantity stays null. Known subtotal and price
coverage remain visible; unknown costs must not silently become zero. Search costs belong
to the evaluated system. Shared research and grading have their own evaluation-cost ledger
and are not charged to one provider's cost-per-valid-company score.

The budget is a **soft estimate**. Unknown prices and already accepted asynchronous work
can exceed it. Check the plan and pricing sources before a paid run. A timeout may leave
remote work active; cancellation/cleanup are provider capabilities, not guaranteed refunds.

Search timing starts around submission and includes polling waits. Research and judging
are excluded. On interrupted/resumed work, the exact completion time may be unobservable;
the timer is marked censored, the observed elapsed time is retained, and the exact latency
and latency-per-valid-company remain null. Mean displayed latency uses available uncensored
timings; inspect censoring and failures before claiming speed superiority.

Per-configuration headline quality and precision average trials within each query, then
weight queries equally. Undefined precision for empty results is excluded from that
precision mean; quality still penalizes their empty yield. Equal-complexity quality is a
separate sensitivity summary, not evidence that the catalogue reflects market prevalence.
Segment reports cover scalar and list-valued facets, including custom fields; overlapping
segments must not be summed into a population estimate. Incomplete declared coverage is
shown and is ineligible for publication as a complete comparison.

Aggregate cost per valid company is sum(C) / sum(V); aggregate quality per dollar is
sum(Q) / sum(C), not the mean of per-query ratios. For a common set of trials this equals
mean(Q) / mean(C). Duplicating every trial leaves these efficiency ratios unchanged.
Known-empty reference cases have no quality score and are excluded from both sums in
quality score per dollar; `quality_tasks` and `quality_cost_usd` identify that calculation's scope.
Unequal trial counts change their weighting. Different query selections, target counts,
cost views, or reference mixes describe different experiments and are not directly
comparable. Never sum quality scores to imply a larger study is better.

Paired comparisons first average trials per query, then bootstrap query differences using
the recorded seed. At least five paired queries are required for the displayed 95% interval.
The interval conditions on this authored query set and its judgments; it does not capture
annotation error, the GTM query population, rate-limit variation, or future API changes.
Many exploratory slices increase the chance of apparently favorable differences. A small
interval or a high judge agreement rate is not a universal superiority claim.

## Reproducibility and public claims

Runs retain query/settings hashes, code revision, task order, original normalized positions,
usage/cost assumptions, errors, frozen evidence, judge/rubric versions, and event records.
The report also records the hash of the code used to calculate it. Retained evidence
revisions can be reported with `companybench report RUN --evidence-version FULL_SHA256`
without moving the active revision pointer or making network calls.
Adding a provider after research changes the pooled cohort: create a new evidence revision
and regrade everyone. Publication creates a redacted bundle with checksums; changed content
requires a new output directory. Third-party source text without recorded redistribution
permission is omitted, retaining URLs and content hashes. Private local run files can hold
more data than the public bundle and should not be committed indiscriminately.

A defensible claim names the dataset version, selected queries, K, reference date, system
configurations, judge, unknown/error rates, cost view, and uncertainty. For example:
“On this declared manufacturing subset, configuration A found more evidence-supported
companies than configuration B under judge J.” Report every compared configuration and
link the frozen bundle. Do not turn that into “the best company search” without defining
the much broader population and validating the evidence.

Methodological context includes [NIST's statistical evaluation guidance](https://www.nist.gov/publications/expanding-ai-evaluation-toolbox-statistical-models),
[research on LLM judge biases](https://arxiv.org/abs/2306.05685), and
[pooling bias in retrieval evaluation](https://arxiv.org/abs/2405.05600).
These motivate the disclosures and controls here; they do not certify the benchmark.
