# Benchmark design references

The following public repositories informed CompanyBench's organization and contribution
interfaces. These are design references, not evidence about company-search performance.
The code and catalogue here are authored for CompanyBench.

| Reference | Practice used here |
|---|---|
| [Exa search benchmarks](https://github.com/exa-labs/benchmarks) | Separate datasets, system configurations, adapters, evaluation, tests, and saved runs. Explicit costs and resumable execution. |
| [LiveBench](https://github.com/LiveBench/LiveBench) | Select a dataset release and subset explicitly. Preserve generation and scoring stages and category breakdowns. |
| [LiveCodeBench](https://github.com/LiveCodeBench/LiveCodeBench) | Retain model outputs for separate evaluation. Treat dates, release versions, timeouts, and repeated samples as experiment settings. |
| [HumanEval](https://github.com/openai/human-eval) | Small JSONL data contracts, stable task IDs, runnable examples, and result records with explicit failures. |
| [tau-bench](https://github.com/sierra-research/tau-bench) | Explicit model/provider configuration, task selection, concurrency, trials, and saved trajectories. |
| [ARC-AGI](https://github.com/fchollet/ARC-AGI) | Simple inspectable data and an interface for examining individual tasks. |
| [Language Model Evaluation Harness](https://github.com/EleutherAI/lm-evaluation-harness) | A shared provider interface, task discovery, configuration guides, cached results, and optional dependencies. |

Company search has additional limits: most eligible-company universes are unknown, public
evidence is uneven, identities can be ambiguous, and paid asynchronous submissions can
outlive a local process. The [methodology](methodology.md) and [architecture](architecture.md)
describe the corresponding recall restrictions, evidence controls, and execution journal.

References reviewed on 2026-10-06. No rankings from these projects are reproduced here.
