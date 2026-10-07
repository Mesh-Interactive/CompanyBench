# Development workflow

CompanyBench is a standalone Python package. Do not import private Mesh application code.

1. Read the relevant documentation and contracts. Plan and critique substantive changes.
2. Install development dependencies with `uv sync --extra dev` or `pip install -e '.[dev]'`.
3. Write focused tests for important behavior, then implement the change.
4. Review the code for correctness and simplify unnecessary abstractions.
5. Run affected tests while developing. Mock external calls; CI must remain offline.
6. Update documentation after the behavior is stable.
7. Run the full test suite serially, lint, type checks, and `git diff --check` before committing.
8. Inspect the staged diff and commit. Push only when the task authorizes it.

Paid calls must save request intent before submission. Do not silently retry a request whose
acceptance is uncertain. Keep API keys in environment variables, never committed artifacts.
Use dedicated test accounts for paid smoke tests. Do not run a full benchmark implicitly.

Published results retain their frozen queries, settings, evidence, costs, and errors. Corrections
create a new version. Automated judgments must be labeled as preliminary.
