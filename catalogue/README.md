# Catalogue exports

These files contain all **250 active public queries** from catalogue version 2.0.0.
They are generated from the canonical
[queries.jsonl](../src/companybench/data/queries.jsonl). JSON preserves structured fields;
CSV and XLSX store nested lists and objects as JSON inside cells. All text is authored,
with source provenance recorded per query and in the [source ledger](../docs/sources.md).

- [CSV](queries.csv)
- [JSON](queries.json)
- [XLSX](queries.xlsx)

Regenerate from the repository root after a versioned dataset change:

```bash
companybench export catalogue/queries.csv
companybench export catalogue/queries.json
companybench export catalogue/queries.xlsx
```

The XLSX export needs the optional Excel dependency (`pip install -e '.[excel]'`).
Use the canonical JSONL for benchmark execution. Export formats do not change IDs,
one-based release indexes, acceptance rules, or reference-list scope.
