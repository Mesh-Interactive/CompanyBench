# Extension examples

These examples use the real public interfaces and run without API keys. The provider
returns an invented company. The judge always returns **Not enough evidence**. Neither
measures real search quality.

From the repository root:

```bash
companybench validate --dataset examples/queries.py:create_queries
companybench search --provider examples/provider.py:create --dataset examples/queries.py:create_queries --output runs/example
companybench queries --dataset examples/queries.py:create_queries --full
```

Use `ImmediateProvider.search` for one asynchronous operation, `JobProvider.submit` and
`poll` for jobs, or `SyncProvider` for a blocking function. Route paid HTTP operations
through `CallContext`, using stable operation names and documented idempotency support.
Assign positions `1, 2, ...` in original list order. Keep duplicate and malformed rows.
See the [provider guide](../docs/providers.md) and [judge guide](../docs/judges.md).

Copy one exported JSONL query, assign a new stable ID, and name its acceptance conditions
to contribute data without writing Python. Do not use existing or retired IDs.
