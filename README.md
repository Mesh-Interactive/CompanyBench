# CompanyBench

An open benchmark for **company list building**: given a search query, which companies
does a system return, how many satisfy the requirements, and what does the useful list
cost in money and time?

CompanyBench includes **250 public-web queries**, **14 named search configurations**, and
**three judging backends**. It runs locally with a Python CLI, saves resumable artifacts,
and produces inspectable HTML, JSON, CSV, and Markdown reports.

**Status:** this repository contains the catalogue and benchmark implementation, not measured
provider rankings. Adapters have offline contract tests; they have not been live-verified.
Avina's adapter follows a draft API contract whose deployment still needs verification.

## Try it without API keys

Requires Python 3.11 or later.

```bash
git clone https://github.com/Mesh-Interactive/CompanyBench.git
cd CompanyBench
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
companybench validate
companybench demo --output runs/demo
```

Open the printed `report.html` path. The demo uses invented companies, evidence, prices,
and timings; it makes no API calls. With [uv](https://docs.astral.sh/uv/), use
`uv sync --extra dev`, then prefix commands with `uv run`.

Inspect a real run before paying for it:

```bash
companybench queries --segment manufacturing --sample 5 --seed 42
companybench run --provider exa-agent-high --provider parallel-core \
  --segment manufacturing --sample 5 --seed 42 --dry-run
```

The [quickstart](docs/quickstart.md) covers credentials, a paid pilot, all 250 queries,
selection, resume, regrading, and publication. Choose providers explicitly; removing
query selectors selects the entire catalogue.

## What it measures

Every returned position is retained, including duplicates and malformed entries. Search
results are pooled by query and company, researched independently, and graded against the
authored acceptance conditions using the same frozen evidence packet for every judge.
Missing evidence stays unknown. Judges do not see provider names, ranks, costs, or answer keys.

Reports include valid companies found, precision, valid companies as a percentage of the
requested count, cost per valid company, search time, and search time per valid company. The quality score rewards
valid yield while penalizing filler; a separate cost efficiency score includes search cost.
**True recall is reported only for the ten exhaustive reference cohorts.** Open-ended searches
have no known complete denominator. See the [methodology](docs/methodology.md) for formulas,
uncertainty, failed runs, and aggregation.

Filter results by complexity, industry, geography, query family, filters, signals, or custom
metadata. Selection supports stable IDs, inclusive index ranges, explicit index lists, safe
filter expressions, and seeded sampling. The [catalogue guide](docs/dataset.md) explains
the taxonomy and [research ledger](docs/sources.md) records the sources of query ideas.
Download the complete [CSV, JSON, or XLSX catalogue](catalogue/README.md).

## Search configurations

| Preset | System |
|---|---|
| `avina` | Avina company signal search |
| `exa-websets` | Exa Websets |
| `exa-agent-high` | Exa Agent, high effort |
| `exa-agent-auto` | Exa Agent, auto effort |
| `parallel-core` | Parallel FindAll, core |
| `parallel-pro` | Parallel FindAll, pro |
| `openai-sol-medium` | GPT-6.1 Sol, medium |
| `openai-astra-max` | GPT-6 Astra, max |
| `claude-sonnet-medium` | Claude Sonnet 5.5, medium |
| `claude-opus-max` | Claude Opus 5.5, max |
| `gemini-flash-high` | Gemini 3.8 Flash, high |
| `gemini-pro-high` | Gemini 3.1 Pro Preview, high |
| `grok-low` | Grok 4.7, low |
| `grok-xhigh` | Grok 4.7, xhigh |

Foundational-model configurations use native web search. Effort names are vendor-specific;
these presets are starting points, not proven optimal settings. The [provider guide](docs/providers.md)
documents exact contracts, native behavior, keys, and extension interfaces. Public price
assumptions and unknown-cost handling are in [pricing](docs/pricing.md).

The default judge is an evidence-grounded LLM (`llm`). OpenAI Decisions (`decisions`) and
Jev / TypeSafe (`jev`) are optional; choose any combination and inspect each judge's results
separately. Research uses OpenAI independently of the chosen search provider. See
[judges and review](docs/judges.md) for prompts, limitations, and optional human corrections.

## Contribute

Add queries as JSONL or a small Python dataset factory. Add providers through an immediate
search adapter or a submit/poll adapter; the shared client supplies synchronous and
asynchronous search. Providers and judges can also be installed as entry-point plugins.
No server or database is required.

Start with [CONTRIBUTING.md](CONTRIBUTING.md), [extension examples](examples/README.md),
[architecture](docs/architecture.md), [benchmark design references](docs/benchmark-design.md), and
the [development workflow](CLAUDE.md). Tests use mocks and synthetic fixtures, so contributors
can work without API keys. Reports and frozen bundles retain errors and provenance to make
corrections reviewable.
The [corrections policy](docs/corrections.md) explains how disputed results are retained and revised.

## Ownership and limits

Avina / Mesh Interactive created CompanyBench and has a commercial interest in the results.
The catalogue is an **authored breadth sample**, not a measurement of which searches customers
run most often. Automated results are preliminary: evidence retrieval and judges can be wrong,
and agreement between judges is not proof of correctness. Published comparisons should name
the exact configurations, dataset, selection, date, judge, and cost assumptions, and include
unfavorable results and failures.

Code and authored catalogue are available under the [MIT license](LICENSE). Third-party
source material retains its own rights; publication omits captured source text without
recorded redistribution permission.
