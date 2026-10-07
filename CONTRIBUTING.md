# Contributing

Useful contributions include search adapters, query cases, evidence fixes, pricing sources,
judge adapters, and reproducible bug reports. The goal is an inspectable comparison, including
results unfavorable to Avina. Read [CLAUDE.md](CLAUDE.md), [architecture](docs/architecture.md),
and [methodology](docs/methodology.md) before changing evaluation behavior.

## Development

Use Python 3.11 or newer in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
ruff check .
mypy src/companybench
git diff --check
```

Tests must run offline with mocked provider responses. Add focused behavioral tests for
changes to adapters, accounting, identity, checkpointing, or acceptance logic. Test edge
cases that can change a comparison: duplicates, incomplete pagination, ambiguous submit
responses, interrupted jobs, missing usage, unknown evidence, and malformed judgments.
Run the full suite serially before submitting. CI checks tests, lint, and typing without
credentials. A contract test is not a live API validation claim.

Describe the problem, resulting behavior, validation, and any scoring consequences in your
pull request. Do not commit keys, private account/workspace IDs, local `.env` files, third-party
source dumps, or paid-run artifacts. Run paid smoke tests only deliberately with your own
authorized credentials; record endpoint, date, and outcome when claiming live compatibility.

## Add queries

For a local catalogue, return `Query` objects or dictionaries from a trusted Python factory,
or use JSON/JSONL. Load it with `--dataset module:factory` or `--dataset ./queries.jsonl`.
The [dataset guide](docs/dataset.md) includes selection and export examples. A minimal case is:

```python
from companybench.models import Criterion, Query


def queries():
    return [
        Query(
            id="CUSTOM-001",
            index=1,
            query="Find companies headquartered in Kenya that export roasted coffee.",
            family="firmographic",
            complexity="L2",
            industry="Food and beverage",
            geography="Kenya",
            geography_basis="headquarters",
            conditions=[
                Criterion(id="hq", description="The company's headquarters are in Kenya."),
                Criterion(id="export", description="The company exports roasted coffee."),
            ],
            acceptance="Both conditions require public evidence; exporters of only green coffee do not qualify.",
            source_basis="original",
        )
    ]
```

For the shared catalogue, propose stable IDs and source-ledger additions. Specify the company
unit, exact acceptance criteria, evidence requirements, time/geography basis, tags, and any
observability limitations. Preserve exclusions and alternatives with an authored `Rule`
where needed. An interesting but hard-to-verify query is allowed; private data unavailable
to all providers is not part of the default public track. Do not assert an exhaustive
reference list without a dated authoritative finite source and a checked complete list.
Keep development fixtures separate from the public catalogue and version semantic changes.

## Add search providers

Subclass `ImmediateProvider` or `JobProvider` from `companybench.providers.base`. Implement
the asynchronous methods documented in [architecture](docs/architecture.md), and use the
shared `SearchRequest`, `SearchResult`, and `SearchJob` contracts. A synchronous integration
can be wrapped with `SyncProvider`.

Use `context` for HTTP calls and checkpoints so paid submissions have durable intent and
ambiguous acceptance is not retried silently. Read secrets from a named environment variable.
Keep company positions and malformed entries; report unknown cost as null. Return real
citations, native usage, and explicit errors. Document endpoint, model/effort, native limits,
pagination, timeouts, cleanup support, pricing basis, and readiness.

A trusted local factory receives keyword options directly:

```python
def create_provider(**options):
    return MyCompanyProvider(**options)
```

Use `--provider my_package:create_provider`, or register
an installed plugin in its `pyproject.toml`:

```toml
[project.entry-points."companybench.providers"]
my-search = "my_package:create_provider"
```

Provider names must not collide with built-ins. Query and provider factories can also be
loaded from a trusted Python file. Importing either executes local code. Keep contribution
adapters small; orchestration and metrics belong in the shared core. Add mocked contract
tests before a paid smoke test and include a minimal documented configuration.

## Add judges or propose corrections

The judge boundary is separate from search. Follow [judges](docs/judges.md) for
`register_judge`, installed `companybench.judges` entry points, local factories, typed
responses, and optional human review. Preserve the common evidence packet and rule logic.
A new judge must not browse independently, inspect search-provider labels, or read reference
answers while scoring. Never manufacture a rationale or citation field that its API does
not return.

For a disputed result, include the query ID, run/evidence version, company identity, exact
condition, and public evidence with its applicable date. A pricing correction should cite
the provider's published schedule and distinguish public, account, and confirmed charges.
An identity correction needs explicit source-backed review. Corrections produce a new
version; preserve the old bundle and explain the change. Report validation limits openly,
and never rewrite unfavorable results into apparently clean successes.
