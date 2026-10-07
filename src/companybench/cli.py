"""Command-line search, evaluation, inspection, and contribution tools."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Annotated, Any

import typer

from companybench.config import Settings, load_settings
from companybench.datasets import (
    SEGMENTS,
    export_dataset,
    load_dataset,
    select_queries,
    validate_dataset,
)
from companybench.models import Query, SearchJob, SearchRequest
from companybench.report import build_report, write_report
from companybench.runner import (
    cost_plan,
    grade_run,
    manifest_for,
    preflight,
    research_run,
    search_run,
)
from companybench.storage import RunStore

app = typer.Typer(
    help="Company list building: search, check, compare, and inspect.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def guarded(function: Any) -> Any:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except (ValueError, RuntimeError, KeyError, OSError) as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(1) from error
        except KeyboardInterrupt:
            typer.echo(
                "Interrupted. Saved work remains in the run directory; remote jobs may still be billable.",
                err=True,
            )
            raise typer.Exit(130) from None

    return wrapped


def emit(value: Any) -> None:
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def require_keys(checks: list[dict[str, Any]]) -> None:
    missing = [item for item in checks if not item["configured"]]
    if missing:
        raise ValueError(
            "Missing credentials: "
            + ", ".join(
                f"{item['component']} ({item['key_environment_variable']})" for item in missing
            )
        )


def selected_queries(
    dataset: str | None,
    *,
    ids: list[str] | None = None,
    indices: str | None = None,
    index_range: str | None = None,
    where: list[str] | None = None,
    segments: list[str] | None = None,
    sample: int | None = None,
    seed: int = 42,
) -> list[Query]:
    parsed_indices = json.loads(indices) if indices is not None else None
    if parsed_indices is not None and not isinstance(parsed_indices, list):
        raise ValueError("--indices must be a JSON list, for example '[12,101,142]'")
    return select_queries(
        load_dataset(dataset),
        ids=ids,
        indices=parsed_indices,
        index_range=index_range,
        where=where,
        segments=segments,
        sample=sample,
        seed=seed,
    )


def progress(value: dict[str, Any]) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False), err=True)


def run_directory(output: Path | None) -> Path:
    return output or Path("runs") / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    )


@app.command("run")
@app.command("search")
@guarded
def execute(
    ctx: typer.Context,
    config: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    provider: Annotated[list[str] | None, typer.Option("--provider", "-p")] = None,
    judge: Annotated[list[str] | None, typer.Option("--judge")] = None,
    dataset: Annotated[str | None, typer.Option("--dataset")] = None,
    ids: Annotated[list[str] | None, typer.Option("--id")] = None,
    indices: Annotated[str | None, typer.Option("--indices")] = None,
    index_range: Annotated[str | None, typer.Option("--range")] = None,
    where: Annotated[list[str] | None, typer.Option("--where")] = None,
    segment: Annotated[list[str] | None, typer.Option("--segment")] = None,
    sample: Annotated[int | None, typer.Option("--sample")] = None,
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    target_count: Annotated[int | None, typer.Option("--target-count")] = None,
    trials: Annotated[int | None, typer.Option("--trials")] = None,
    budget_usd: Annotated[
        float | None,
        typer.Option("--budget-usd", help="Soft estimated budget, not a guaranteed charge limit."),
    ] = None,
    reference_time: Annotated[str | None, typer.Option("--reference-time")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """Run all stages, or use 'search' to save provider results without evaluation."""
    settings = load_settings(config)
    overrides = {
        key: value
        for key, value in {
            "providers": provider,
            "judges": judge,
            "seed": seed,
            "target_count": target_count,
            "trials": trials,
            "budget_usd": budget_usd,
        }.items()
        if value is not None
    }
    settings = Settings.model_validate(settings.model_dump() | overrides)
    selection: dict[str, Any] = dict(
        ids=ids,
        indices=indices,
        index_range=index_range,
        where=where,
        segments=segment,
        sample=sample,
        seed=settings.seed,
    )
    queries = selected_queries(dataset, **selection)
    manifest = manifest_for(
        queries,
        settings,
        reference_time=datetime.fromisoformat(reference_time) if reference_time else None,
        selection={"dataset": dataset or "default", **selection},
    )
    search_only = ctx.command.name == "search"
    if not search_only and not settings.judges:
        raise ValueError("Select at least one judge, or use 'search' to run without evaluation")
    checks = preflight(settings, evaluation=not search_only)
    if dry_run:
        emit(
            {
                "plan": cost_plan(manifest),
                "query_ids": [query.id for query in queries],
                "credentials": checks,
                "reference_time": manifest["reference_time"],
            }
        )
        return
    require_keys(checks)
    store = RunStore(run_directory(output))
    if store.read("manifest.json"):
        raise ValueError("Run directory already contains a run; use resume or a new directory")
    with store.lock():
        store.write("manifest.json", manifest)
        typer.echo(f"Run directory: {store.path}", err=True)

        async def pipeline() -> None:
            await search_run(store, progress=None if quiet else progress)
            if not search_only:
                await research_run(store, progress=None if quiet else progress)
                await grade_run(store, progress=None if quiet else progress)

        asyncio.run(pipeline())
        if not search_only:
            typer.echo(str(write_report(store) / "report.html"))
        else:
            typer.echo(str(store.path))


@app.command()
@guarded
def resume(
    run: Path,
    search_only: Annotated[bool, typer.Option("--search-only")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """Reuse saved results and poll accepted jobs; uncertain submissions are not repeated."""
    store = RunStore(run)
    manifest = store.read("manifest.json")
    if not manifest:
        raise ValueError("Run manifest not found")
    plan = []
    for task in manifest["tasks"]:
        state = store.task(task["task_id"])
        action = (
            "reuse"
            if state.get("result")
            else "needs reconciliation"
            if state.get("status") == "submission_unknown"
            else "poll/checkpointed work"
            if state.get("checkpoints", {}).get("job")
            else "submit (journal acceptance checks apply)"
        )
        plan.append({"task_id": task["task_id"], "action": action})
    if dry_run:
        emit(plan)
        return
    settings = Settings.model_validate(manifest["settings"])
    if all(store.task(task["task_id"]).get("result") for task in manifest["tasks"]):
        with store.lock():
            if search_only:
                typer.echo(str(store.path))
                return
            if store.read("evidence/current.json"):
                try:
                    complete = build_report(store)["comparison_complete"]
                except ValueError as error:
                    if "No judgments exist" not in str(error):
                        raise
                    complete = False
                if complete:
                    typer.echo(str(write_report(store) / "report.html"))
                    return
    require_keys(preflight(settings, evaluation=not search_only))
    with store.lock():

        async def pipeline() -> None:
            await search_run(store, progress=None if quiet else progress)
            if not search_only:
                await research_run(store, progress=None if quiet else progress)
                await grade_run(store, progress=None if quiet else progress)

        asyncio.run(pipeline())
        typer.echo(str(store.path if search_only else write_report(store) / "report.html"))


@app.command()
@guarded
def research(
    run: Path,
    new_revision: Annotated[bool, typer.Option("--new-revision")] = False,
    identity_map: Annotated[Path | None, typer.Option("--identity-map")] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """Research saved results; changed evidence is a new revision requiring regrading."""
    from companybench.evidence import OpenAIResearcher

    store = RunStore(run)
    manifest = store.read("manifest.json")
    settings = Settings.model_validate(manifest["settings"])
    options = load_settings(config).research if config else settings.research
    researcher = OpenAIResearcher(options)
    require_keys(
        [
            {
                "component": "research",
                "key_environment_variable": researcher.api_key_env,
                "configured": bool(os.environ.get(researcher.api_key_env)),
            }
        ]
    )
    with store.lock():
        if config:
            store.event("research_configuration_changed", research_settings=options)
            new_revision = True
        if identity_map:
            from companybench.review import load_identity_overrides

            load_identity_overrides(identity_map)
            overrides = json.loads(identity_map.read_text())
            store.write("identity_overrides.json", overrides)
            new_revision = True
        version = asyncio.run(
            research_run(
                store,
                new_revision=new_revision,
                researcher=researcher,
                research_options=options,
                progress=None if quiet else progress,
            )
        )
        typer.echo(version)


@app.command()
@guarded
def grade(
    run: Path,
    judge: Annotated[list[str] | None, typer.Option("--judge")] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
    human_labels: Annotated[Path | None, typer.Option("--human-labels")] = None,
    reviewer: Annotated[str, typer.Option("--reviewer")] = "human",
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None:
    """Grade frozen evidence without repeating search or research."""
    from companybench.judges import get_judge

    store = RunStore(run)
    settings = Settings.model_validate(store.read("manifest.json")["settings"])
    options = load_settings(config).judge_options if config else settings.judge_options
    selected = judge or settings.judges
    with store.lock():
        if human_labels:
            from companybench.review import import_review

            emit(import_review(store, human_labels, reviewer=reviewer))
            return
        checks = []
        for name in selected:
            implementation = get_judge(name, options.get(name))
            variable = implementation.api_key_env
            configured = not variable or bool(os.environ.get(variable))
            if name == "jev":
                configured = configured or bool(os.environ.get("TYPESAFE_API_KEY"))
            checks.append(
                {"component": name, "key_environment_variable": variable, "configured": configured}
            )
        require_keys(checks)
        asyncio.run(
            grade_run(
                store, judges=selected, judge_options=options, progress=None if quiet else progress
            )
        )
        typer.echo(str(store.path))


@app.command()
@guarded
def report(
    run: Path,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    cost_view: Annotated[str, typer.Option("--cost-view")] = "public",
    prefix: Annotated[int | None, typer.Option("--prefix")] = None,
    where: Annotated[list[str] | None, typer.Option("--where")] = None,
    segment: Annotated[list[str] | None, typer.Option("--segment")] = None,
    publication: Annotated[bool, typer.Option("--publication")] = False,
    evidence_version: Annotated[
        str | None,
        typer.Option("--evidence-version", help="Report a retained full evidence SHA-256 version."),
    ] = None,
) -> None:
    """Create offline reports and an optional redacted publication bundle."""
    if cost_view not in {"public", "account", "confirmed"}:
        raise ValueError("Cost view must be public, account, or confirmed")
    if prefix is not None and prefix <= 0:
        raise ValueError("Prefix must be positive")
    store = RunStore(run)
    if evidence_version:
        store = store.evidence_view(evidence_version)
    selected = None
    if where or segment:
        queries = [Query.model_validate(value) for value in store.read("manifest.json")["queries"]]
        selected = {query.id for query in select_queries(queries, where=where, segments=segment)}
    with store.lock():
        target = write_report(
            store,
            output=output,
            cost_view=cost_view,
            prefix=prefix,
            query_ids=selected,
            publication=publication,
        )
    typer.echo(str(target / "report.html"))


@app.command()
@guarded
def queries(
    dataset: Annotated[str | None, typer.Option("--dataset")] = None,
    where: Annotated[list[str] | None, typer.Option("--where")] = None,
    segment: Annotated[list[str] | None, typer.Option("--segment")] = None,
    sample: Annotated[int | None, typer.Option("--sample")] = None,
    seed: Annotated[int, typer.Option("--seed")] = 42,
    indices: Annotated[str | None, typer.Option("--indices")] = None,
    index_range: Annotated[str | None, typer.Option("--range")] = None,
    ids: Annotated[list[str] | None, typer.Option("--id")] = None,
    full: Annotated[bool, typer.Option("--full")] = False,
) -> None:
    """Inspect query selection and acceptance conditions without paid calls."""
    values = selected_queries(
        dataset,
        ids=ids,
        indices=indices,
        index_range=index_range,
        where=where,
        segments=segment,
        sample=sample,
        seed=seed,
    )
    emit(
        [
            value.model_dump(mode="json")
            if full
            else {
                "id": value.id,
                "index": value.index,
                "query": value.query,
                "complexity": value.complexity,
                "industry": value.industry,
            }
            for value in values
        ]
    )


@app.command("validate")
@guarded
def validate(dataset: Annotated[str | None, typer.Option("--dataset")] = None) -> None:
    """Validate a bundled, JSONL, or Python dataset."""
    values = load_dataset(dataset)
    emit({"queries": len(values), "errors": validate_dataset(values), "segments": SEGMENTS})


@app.command()
@guarded
def providers() -> None:
    """Show named configurations, required key names, and verification status."""
    from companybench.providers.registry import PRESETS, provider_names, resolve

    emit(
        [
            {
                "name": name,
                **PRESETS.get(name, {"source": "package entry point"}),
                "readiness": resolve(name).readiness if name in PRESETS else "not loaded",
            }
            for name in provider_names()
        ]
    )


@app.command()
@guarded
def judges() -> None:
    """Show judging backends and required key names."""
    from companybench.judges import JUDGES, judge_names

    emit(
        [
            {
                "name": name,
                **(
                    {"model": JUDGES[name].default_model, "api_key_env": JUDGES[name].api_key_env}
                    if name in JUDGES
                    else {"source": "package entry point", "readiness": "not loaded"}
                ),
            }
            for name in judge_names()
        ]
    )


@app.command()
@guarded
def doctor(
    config: Annotated[Path | None, typer.Option("--config")] = None,
    provider: Annotated[list[str] | None, typer.Option("--provider")] = None,
    search_only: Annotated[bool, typer.Option("--search-only")] = False,
) -> None:
    """Check local configuration and key presence; never send provider requests."""
    settings = load_settings(config)
    if provider is not None:
        settings.providers = provider
    emit({"checks": preflight(settings, evaluation=not search_only), "paid_calls": 0})


@app.command()
@guarded
def demo(output: Annotated[Path | None, typer.Option("--output", "-o")] = None) -> None:
    """Run synthetic providers, evidence, and judgments entirely offline."""
    from companybench.demo import run_demo

    target = asyncio.run(run_demo(run_directory(output)))
    typer.echo(str(target / "report.html"))


@app.command()
@guarded
def smoke(
    provider: Annotated[list[str], typer.Option("--provider", "-p")],
    config: Annotated[Path | None, typer.Option("--config")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """PAID: run one development query, up to three companies, for selected providers."""
    settings = load_settings(config)
    settings.providers = provider
    settings.target_count = 3
    settings.trials = 1
    require_keys(preflight(settings, evaluation=False))
    store = RunStore(run_directory(output))
    if store.read("manifest.json"):
        raise ValueError("Use a new directory for smoke tests")
    manifest = manifest_for(
        load_dataset("development")[:1],
        settings,
        selection={"dataset": "development", "purpose": "paid live smoke"},
    )
    with store.lock():
        store.write("manifest.json", manifest)
        asyncio.run(search_run(store, progress=progress))
    emit(
        {
            "run": str(store.path),
            "states": [
                {
                    "provider": task["provider"],
                    "status": store.task(task["task_id"]).get("status"),
                    "result_status": store.task(task["task_id"]).get("result", {}).get("status"),
                }
                for task in manifest["tasks"]
            ],
        }
    )


@app.command()
@guarded
def cleanup(
    run: Path,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Perform cleanup; otherwise show owned resources only."),
    ] = False,
) -> None:
    """Inspect or archive benchmark-owned remote resources; cancellation is not implied."""
    from companybench.client import SearchClient
    from companybench.providers.registry import resolve
    from companybench.transport import CallContext

    store = RunStore(run)
    manifest = store.read("manifest.json")
    settings = Settings.model_validate(manifest["settings"])
    owned = []
    for task in manifest["tasks"]:
        state = store.task(task["task_id"])
        job = state.get("checkpoints", {}).get("job")
        if job:
            owned.append({"task_id": task["task_id"], "provider": task["provider"], "job": job})
    if not execute:
        emit({"owned_resources": owned, "executed": False})
        return
    with store.lock():

        async def clean() -> list[dict[str, Any]]:
            values = []
            for item in owned:
                state = store.task(item["task_id"])
                provider = resolve(
                    item["provider"], settings.provider_options.get(item["provider"])
                )
                context = CallContext(store, item["task_id"])
                cleaned = await SearchClient(provider, context=context).cleanup(
                    SearchRequest.model_validate(state["request"]),
                    SearchJob.model_validate(item["job"]),
                )
                context.checkpoint("cleanup_requested", cleaned)
                values.append(
                    {
                        "task_id": item["task_id"],
                        "cleanup_requested": cleaned,
                        "cancellation_confirmed": False,
                    }
                )
            return values

        emit(asyncio.run(clean()))


@app.command()
@guarded
def export(
    output: Path,
    dataset: Annotated[str | None, typer.Option("--dataset")] = None,
    where: Annotated[list[str] | None, typer.Option("--where")] = None,
    segment: Annotated[list[str] | None, typer.Option("--segment")] = None,
    review: Annotated[
        Path | None,
        typer.Option(
            "--review", help="Export blinded review rows from this run instead of queries."
        ),
    ] = None,
    identity_review: Annotated[
        Path | None, typer.Option("--identity-review", help="Export identity proposals for a run.")
    ] = None,
) -> None:
    """Export a dataset to JSONL/JSON/CSV/XLSX, or a run to blinded review JSONL."""
    if review and identity_review:
        raise ValueError("Select one of --review or --identity-review")
    if identity_review:
        from companybench.review import export_identity_proposals

        emit(export_identity_proposals(RunStore(identity_review), output))
    elif review:
        from companybench.review import export_review

        export_review(RunStore(review), output)
        typer.echo(str(output))
    else:
        typer.echo(
            str(export_dataset(selected_queries(dataset, where=where, segments=segment), output))
        )


if __name__ == "__main__":
    app()
