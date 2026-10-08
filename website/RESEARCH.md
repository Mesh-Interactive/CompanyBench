# Benchmark interface research

Reviewed October 6, 2026. These observations informed the CompanyBench interface; they are not endorsements or borrowed benchmark findings. All numbers in the initial CompanyBench site are synthetic. No competitor scores, text, charts, or source code were copied.

## Primary sites reviewed

| Site | Useful observed pattern | CompanyBench design choice |
| --- | --- | --- |
| [ARC Prize leaderboard](https://arcprize.org/leaderboard) | Cost-versus-performance plot, benchmark-version choices, model filters, and verification notes. | Keep quality and cost visible together. Identify the dataset and release; distinguish preliminary findings from verified findings. |
| [LiveBench](https://livebench.ai/#/) | Release picker; sortable overall and category scores; expandable subtasks; configurable columns; cost per successful task beside scores. Its insights page compares quality with cost and has selectable models. The JavaScript interface was inspected in the browser. | Put meaningful counts and costs beside the ranking. Make the jump from overview to a specific query short. Keep the metric formula next to its interpretation. |
| [Artificial Analysis](https://artificialanalysis.ai/models) | Separate quality, speed, latency, and cost views; explicit metric direction; model detail links; comparison plots and methodology explanations. | Use four result groups, a single metric selector, and quality-versus-cost/time scatter plots. Avoid making a composite score the only story. |
| [BenchLM](https://benchlm.ai/) | Capability navigation; source/evidence status; CSV access; explicit snapshot dates and methodology; shareable filter state. | Preserve selections in the URL. Show query counts and incomplete coverage. Keep download and methodology controls close to the results. |
| [Vals AI](https://www.vals.ai/home) | Model-specific and benchmark-specific detail; domain-specific examples; dated benchmark pages and changelogs described on its release feed. | Let visitors explore industries and query types, while retaining exact provider settings and the version being viewed. |
| [LLM Stats](https://llm-stats.com/) | Compact highlights for different strengths, followed by a sortable, paginated table and links to detailed methodology. | Use a concise overview, then an explorable ranking. Label each highlight by its metric instead of implying one universal winner. |
| [Exa evals](https://exa.ai/evals) | Research-led explanations link evaluation claims to dated technical articles. The landing page is a research index rather than an interactive leaderboard. | Put findings, definitions, and reproducible artifacts together. A persuasive claim needs a route to the underlying companies and checks. |
| [Arena](https://arena.ai/leaderboard) | Separate use-case leaderboards and a quality-versus-cost frontier. | Show where tradeoffs change by use case. A provider that performs well overall may not lead on a particular segment. |
| [Princeton HAL](https://hal.cs.princeton.edu/) | Cost-aware benchmark highlights and separate benchmark leaderboards, with reliability analysis linked alongside performance. | Keep useful-list quality beside cost, retain the query group being compared, and make errors and completion visible. |
| [Epoch AI](https://epoch.ai/benchmarks) | Data-selection controls, separate internal/external provenance, a raw data table, CSV downloads, and per-run log viewers. Its explanation separates uncertainty, settings, and limitations. | Make individual results inspectable, keep the source of a score visible, and disclose gaps. Retain exact settings with every result rather than treating a vendor name as a complete experiment description. |

## What would make CompanyBench compelling

The question a GTM visitor brings is usually practical: “Which tool gives me the best list for this kind of search?” The interface should let them answer that without understanding the entire benchmark first.

The overview should open with **Valid companies found**, then show **Precision**, **Cost per valid company**, **Search time**, and **Completion**. These metrics tell different stories. A cheap tool that finds very few companies, or a precise tool that returns only two, should not look best because the visitor saw one attractive number. Every figure should state its query count and whether it is a mean or a ratio of totals.

Use a horizontal ranking for one metric. Bars are easier to compare than radar areas and remain legible with fourteen configurations. Provider colors should remain stable throughout the site. Clicking a provider can emphasize it; unselected providers should stay visible at low opacity so the comparison universe remains clear. The UI should separate provider emphasis from query filtering: changing emphasis must not quietly change the population being evaluated.

Use scatter plots for quality versus cost and quality versus time. Both axes need units, direction, and accessible tooltips. The plot should show the same filtered queries as the ranking. Keep an accompanying table, because nearby points and longer model names can become difficult to read. A frontier is useful when backed by real data, but it should not become a decorative claim of superiority in a synthetic preview.

Show a matrix by complexity or query category before offering every taxonomy field. This makes specialization visible: simple searches, compound filters, public signals, and technology searches may behave differently. Each segment should show its sample size. A tiny subgroup must not visually imply the same confidence as the full catalogue.

## Filtering and detail

Show a few common filters first, with additional fields behind “More filters.” Multiple selections within a field use **OR**; separate fields use **AND**. State that rule plainly. Display removable active-filter chips, the remaining query count, and a reset control. Include an explicit zero-results state rather than empty chart frames. Search text and stable query IDs should both work in the query explorer.

The raw-data view should have one query per row and one provider configuration per column, with a pinned query column. Cells show the chosen metric and a compact result summary. A click opens a wide side drawer. This keeps the original query/provider comparison visible behind the detail, supports moving to another provider, and avoids stacking a modal inside another modal.

The drawer should present, in order:

1. The complete search text and acceptance criteria.
2. Counts, cost, time, status, and exact provider settings.
3. A searchable company table preserving returned order, duplicate positions, rejected companies, unknown judgments, and errors.
4. An expanded company row with the condition-by-condition judge results, identity decision, dated evidence excerpts, source links, and any judge disagreement.

This is the strongest credibility feature. A visitor should be able to trace a headline score to a returned company, then see why it received credit. Unknown evidence must remain visibly different from a failed condition. An error or missing cost should not render as a measured zero.

## Synthetic preview rules

The initial UI must show **Synthetic data** and state that the results are invented in its persistent header. Result drawers and downloads preserve that context. Fictional company names and reserved `.example` domains avoid creating invented factual claims about real businesses. Evidence excerpts and judge results must say that they are synthetic.

Do not add fabricated confidence intervals, verification badges, publication dates, provider quotes, or a declaration that Avina wins. Real provider names can illustrate the intended comparison, but their synthetic values are not predictions. The eventual measured release needs a separate release identifier and an explicit switch from synthetic to measured data.

## Brand and accessibility

Use Avina's light product surface for dense analysis: restrained purple accents, white cards, thin neutral dividers, and compact controls. Use Geist or a system font for display text and Inter or a system font for the interface; the supplied ABC Diatype files are excluded because their redistribution license was not supplied. The attached design system separates its light app theme from the dark marketing theme; an analytical dashboard benefits from the light theme's table contrast. A restrained coral/lilac accent can connect the page to the marketing brand without turning every chart into a gradient.

Keep labels and values readable when providers are faded. Color cannot be the only signal: use names, numeric values, selected states, and judgment text. Ensure keyboard access to chart points and cells, visible focus, Escape-to-close, focus restoration, and a usable mobile drawer. Keep exports available without requiring interaction with the charts.

## Later measured releases

Before promoting actual results, surface completion coverage, the selected judge and evidence version, cost source, formula definitions, trials, and exact provider settings. Add paired uncertainty only when calculated from measured query results, with its limitations. Keep disappointing results and errors in the raw explorer. Provide a correction link and immutable downloadable artifacts. Disclose Avina's authorship and commercial interest in the methodology view and footer.
