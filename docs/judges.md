# Evidence and judges

CompanyBench's first results are **automated, preliminary assessments**, not human-certified ground truth. All selected judges receive one frozen, provider-blind evidence packet for each query/company. A provider's response, claimed matches, price, name, rank, and any reference answer set are excluded from judge context.

## Evidence collection

`OpenAIResearcher(options=None).research(packet, lead_urls, context)` returns `(EvidencePacket, Usage, Cost)`. It uses `gpt-6-astra` at high effort with hosted web search to find source URLs, then fetches those public pages. Generated research prose never becomes factual evidence. Pooled provider URLs are untrusted research leads; they do not count as proof until fetched.

The default requests a maximum of five hosted tool calls and fetches at most eight source URLs. Visible web-search calls and token usage are recorded; a hosted call's internal work is controlled by OpenAI. Deterministic query-term matching chooses relevant windows from the complete captured text, including content late in a document; offsets and full-text hashes are retained. A shared packet retains at most eight 2,400-character excerpts and is further bounded to 24,000 ASCII-serialized characters, with additional space reserved for the longest typed question. The same omissions and excerpts are visible to every judge. Missing, inaccessible, unsupported, or conflicting evidence remains visible as a gap.

HTTP fetches allow public HTTP(S) on ports 80/443 only, reject credentials and non-global IP addresses, pin the validated address for the connection, verify TLS against the original hostname, and validate each redirect. Transfers are capped at 2 MB; redirects, time and content types are bounded. HTML extraction preserves script-source URLs, footer addresses and a small set of literal tracking markers. PDFs use selectable-text extraction, capped at 100 pages and one million extracted characters. Scanned PDFs requiring OCR and encrypted PDFs are unsupported. The fetcher does **not** execute JavaScript, render pages, bypass authentication or capture browser network traffic. A tracking script does not establish CRM use. Cases requiring unavailable evidence can therefore remain unknown.

Fetched pages are cached by URL, reference time and evidence revision inside the run. Concurrent requests share an in-flight fetch. Private cache artifacts retain the bounded original bytes, extracted text and content hashes; a new evidence revision fetches fresh sources. Raw captures are not included in public publication bundles. Source redistribution still requires permission; the MIT license does not grant rights to third-party website content.

Identity pooling requires the same normalized business name and hostname. Only trailing legal suffixes such as Inc., LLC, Ltd., Corporation and GmbH are removed; original reported names remain aliases. Names alone never merge companies; different business names sharing a domain remain separate. Parent/subsidiary relationships are not inferred. Name-only or domain-only occurrences remain unresolved rather than acquiring a guessed company identity. An integration may supply reviewed identity overrides through `normalize_identity(..., aliases=...)`. This conservative method can leave duplicates unresolved; its notes remain auditable.

Packets are content-hashed and checkpointed. Regrading reuses the packet; changing evidence produces a different hash. Research and judge usage/costs are separate from company-search cost. Missing usage or pricing produces an unknown dollar cost, never zero.

## Grading contract

Every backend evaluates identity and all authored acceptance conditions:

- **met**: affirmative evidence establishes the condition.
- **failed**: affirmative evidence contradicts the condition.
- **unknown**: the available evidence does not decide it.

The harness evaluates the authored AND/OR/NOT tree using three-valued logic. A false AND child decides failure; a true OR child decides success; NOT preserves unknown. Identity must be established before a company can be valid. Missing questions, invalid JSON, refusals, unknown evidence IDs and API failures are grading errors. They are not company-invalid judgments.

The shared prompt enforces headquarters versus operations, company versus customer, parent versus subsidiary, date/sequence/threshold requirements, and evidence-based negative predicates. Source text is explicitly untrusted input. Prompt instructions reduce injection risk but do not prove LLM reliability.

## Backends

| Name | Default | Credential | Output |
| --- | --- | --- | --- |
| `llm` | `gpt-6-astra`, high effort | `OPENAI_API_KEY` | Strict structured judgments, concise reasons and validated evidence IDs |
| `decisions` | `gpt-6-luna` | `OPENAI_API_KEY` | Typed choices and probability distributions |
| `jev` | `jev-1.13.0` | `JEV_API_KEY` (`TYPESAFE_API_KEY` also works directly) | Typed choices and probability distributions |

[OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions) and [TypeSafe/Jev](https://docs.typesafe.ai/api) do not emit free-form rationales or evidence attribution in these typed-choice requests. Their reason/evidence-ID fields remain empty; the shared source packet remains available for inspection. We do not manufacture explanations. Probabilities/confidence are reported as returned, not presented as calibrated correctness probabilities.

The identity answer is stored separately in `CompanyJudgment.raw.identity_judgment`. Original response, packet hash, rubric hash, requested model, usage and costs are retained. Judge disagreement is useful diagnostic information; consensus is not proof of truth.

## Extension interface

```python
from companybench.judges import Judge, register_judge


class MyJudge(Judge):
    name = "my-judge"
    default_model = "my-versioned-model"
    api_key_env = "MY_JUDGE_API_KEY"

    # Implement request(packet, context), account(response), and parse(packet, response).
    # parse returns (identity CriterionJudgment, list[CriterionJudgment]).


register_judge("my-judge", MyJudge)
```

Alternatively implement an object with `async grade(packet, context) -> CompanyJudgment`, `model`, and `api_key_env` for a runner integration. Do not independently refetch or truncate evidence inside a judge. Test structured failures and missing evidence with mocked HTTP before any paid smoke test. No paid validation is claimed by the offline test suite.

Use a trusted local module with `--judge my_package.judges:create` or a file with
`--judge /absolute/path/judge.py:create`. The factory is called as
`create(options={...})` and returns the judge object; it receives one options
dictionary, whereas provider factories receive `**options` and dataset factories
receive no arguments. Installed packages register the same judge factory under
`[project.entry-points."companybench.judges"]` in `pyproject.toml`. Entry-point names
use lowercase letters, digits, underscores or hyphens. Built-in, registered, and
plugin name collisions fail explicitly. `judge_names()` and `companybench judges`
discover plugin names without executing their code. Selecting a local module,
file, or installed plugin executes Python and requires trusted code. File modules
include a source-content hash in their identity and support dataclasses.

## Human review and identity maps

`export_review(store, path)` writes provider-blind JSONL with the evidence version, packet hash, query/company identity, evidence context, and editable identity/condition answers. Reviewers set `reviewed: true` only on completed rows. `import_review(store, path, reviewer="auditor-one")` verifies the frozen evidence, exact condition IDs, source IDs and reasons, then computes the overall verdict from the acceptance logic. It rejects stale/tampered packets and contradictory supplied overall verdicts. Unreviewed rows are skipped; an entirely unreviewed template is rejected. Imports make no API calls and record API cost as zero with human labor explicitly unpriced. Each import is retained by content hash; only that reviewer's current pointer changes. Partial reviews do not become complete benchmarks.

`export_identity_proposals(store, path)` writes unresolved identities and hostname collisions as unreviewed proposals. It never guesses corrected identities. Review the records, retain only the intended changes, and create an identity map:

```json
{"overrides":[{"original_id":"the-exported-identity-id","reviewed":true,
  "reason":"Official corporate page identifies this operating entity",
  "source_urls":["https://example.com/about"],
  "company":{"id":"reviewed-example","name":"Example Ltd","domain":"example.com",
             "resolved":true,"aliases":["Example"]}}]}
```

`load_identity_overrides(path)` validates this map. Root keys are original identity IDs from the proposals; several original IDs may map to one identical reviewed company. An ID cannot describe conflicting companies. Applying changes requires a new evidence revision, since identities affect pooling and the metric denominators. Human acceptance labels do not silently promote unresolved identities.

The CLI uses the same `{"overrides": [...]}` wrapper, not a bare ID-to-company dictionary:

```sh
companybench export identities.json --identity-review runs/pilot
# Review identities.json, remove proposals you are not approving, and supply sources/reasons.
companybench research runs/pilot --identity-map identities.json
companybench export review.jsonl --review runs/pilot
# Complete the per-condition labels and set reviewed: true on finished rows.
companybench grade runs/pilot --human-labels review.jsonl --reviewer auditor-one
companybench report runs/pilot
```

Applying an identity map starts a new research revision and can incur research API costs. Exporting and importing human labels do not call APIs.
