# CompanyBench results site

A static results explorer for company list building. Its initial release uses the
250 real catalogue queries and 14 real provider configurations, with **invented
performance data, company identities, evidence, costs and judgments**. It is an
interface preview, not a provider comparison. No searches or paid API calls run
when you open this site.

## Open the site

There is no frontend dependency installation or build step for the static site.
From the CompanyBench repository root:

```bash
python3 -m http.server 8000 --directory website/dist --bind 127.0.0.1
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). A local server is needed for
the source site's JavaScript modules and JSON fixture.

For one portable file that opens directly without a server:

```bash
python3 website/scripts/build-standalone.py
```

Open `website/preview.html`. The builder embeds the fixture, styles, scripts and
Avina mark. Optional Google Fonts use system fallbacks when the network is
unavailable. The generated preview is an artifact; edit the source files under
`website/dist`, then rebuild it.

## Explore a comparison

- **Overview** starts with quality score versus cost per valid company, beside
  headline valid counts, precision, cost and search time. Provider rankings are
  expandable. The query-group table shows results by complexity, industry or
  search type.
- **Providers** compares every provider configuration on the selected metric.
  Expand **Providers** to select or deselect configurations; unselected providers
  stay visible, faded. Select a chart point or ranking row to change its emphasis.
  These choices do not change the queries being evaluated.
- **Query results** shows one query per row and provider results in columns.
  Select a cell to open its result drawer, then expand a company to inspect its
  acceptance conditions, identity, judge reasons and captured sources.
- **Methodology** explains formulas, coverage, settings and limitations. Avina's
  authorship and commercial interest remain disclosed.

Select several values in a filter to combine them with **OR**. Different filter
fields combine with **AND**. Active filter chips and the query count show what is
included. Reset clears query filters. Metric and judge controls apply to the
same selected query group throughout the interface.

Complexity classes use plain names throughout the interface:

| Class | Requirements | Saved dataset code |
|---|---|---|
| Basic filters | Straightforward industry, location or company-type requirements. | L1 |
| Multiple requirements | Several filters or a specific business description. | L2 |
| Specific evidence | Dated events, numeric thresholds, specialist facts or relationships. | L3 |
| Complex conditions | Combined relationships, event sequences or difficult exclusions. | L4 |

Saved query codes and URL filter values remain unchanged. CSV downloads include
both the code and its plain name. On small screens, **All filters** opens every
query filter, and provider choices stay collapsed until expanded.

**Quality score** measures list completeness and precision, from 0 to 100:
`200 × valid companies / (returned positions + requested count)`. With 50
requested, 25 valid out of 50 returned scores 50; 25 valid out of 25 scores 66.7.
Cost is separate: **Valid companies per dollar** divides valid companies by search
cost. Multiplying that ratio by cost returns the valid company count. Use the
chart's help button for the score definition and examples.

The download menu exports the filtered results as CSV or the current dataset as
JSON. The share control copies a URL containing the current view selections.
Use a hosted URL when sharing with other people; a local or file URL still points
to your own computer.

## Open measured results

Use **Download data → Open a report** and choose a JSON report produced by
`companybench report`. The file is read in the browser. Nothing is uploaded and
no additional research or judging happens. The original synthetic fixture can
be restored from the same menu.

Measured files receive the **Automated preliminary results** disclosure. Missing
costs, incomplete grading and uncertain times remain visible; they are not
converted into measured zeroes. The imported report supplies its own selected
queries, exact provider settings, judgments, evidence and evidence version.

Only import files you intend to inspect locally. Publication bundles should use
CompanyBench's publication export to remove secrets and account information
before redistribution. The site cannot establish that an imported file is a
correct or independently reviewed measurement.

## Development

```bash
node --test website/tests/*.test.mjs
python3 website/scripts/build-demo-data.py
python3 website/scripts/build-standalone.py
```

The synthetic fixture generator is deterministic and reads the catalogue and
provider presets. It does not call any provider. Its values are chosen to
exercise interface states, not to predict provider performance.

`website/package.json` also exposes `npm test` and `npm run build` from the
`website` directory. No `npm install` is needed. Python 3.11+ runs the portable
builder with its standard library; a modern Node.js runs the offline data tests.

The interface uses styled dropdowns matching Avina's dashboard, visible keyboard
focus, a skip link, keyboard-accessible comparisons and native dialogs. Dropdowns
support arrow keys, Home, End, Enter, Escape and Tab. Result drawers support
Escape and focus restoration. Small screens can scroll provider columns while
retaining the query column, and drawers expand to the available screen width.
Color accompanies names and numbers; it is not the only source of meaning.

See [the research notes](RESEARCH.md) for the benchmark sites that informed the
layout and the reasoning behind the comparison views.

## Design and licensing

The site follows the supplied Avina design system: a light canvas, white panels,
purple accents, restrained weights, fine borders and compact controls. The
provided Avina logo and brand styling identify Avina / Mesh Interactive; they
do not grant contributors rights to present their own products as Avina.

No proprietary ABC Diatype font files are included. Dinamo's
[font license](https://abcdinamo.com/licenses) prohibits putting those files in
public repositories. The site uses Inter and system font stacks instead;
the optional Google Fonts delivery has a system fallback. Third-party fonts and
the Avina brand assets are not relicensed by CompanyBench's MIT license.
CompanyBench's authored application code remains under the repository's
[MIT license](../LICENSE).

## Deployment

`website/dist` is the complete static site. Copy its contents to any static host,
including its `data`, `assets` and `tokens` directories. The application uses
relative paths and URL hash state, so no server routing or backend is required.
The portable preview is useful for review; the separate static files are the
recommended deployment form.

Do not describe the synthetic preview as published benchmark findings. Replace
the fixture with an explicit measured release and retain the report's audit
artifacts before presenting actual provider performance.
