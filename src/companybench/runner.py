"""Search, research, and grade stages backed by immutable local artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
import re
import subprocess
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from companybench import __version__
from companybench.client import SearchClient
from companybench.config import Settings
from companybench.models import (
    CompanyIdentity,
    CompanyJudgment,
    EvidencePacket,
    Query,
    SearchRequest,
    SearchResult,
)
from companybench.storage import RunStore, fingerprint, now_iso
from companybench.transport import AcceptanceUnknown, Budget, BudgetExceeded, CallContext


def task_id(query_id: str, provider: str, trial: int) -> str:
    return f"{query_id}:{provider}:{trial}"


def judge_directory(name: str) -> str:
    """Keep ordinary names readable and arbitrary plugin references inside one directory."""
    return (
        name
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name)
        else "custom-" + fingerprint(name)[:24]
    )


def package_code_hash() -> str:
    source_root = Path(__file__).parent
    return fingerprint(
        {
            str(path.relative_to(source_root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(source_root.rglob("*.py"))
        }
    )


def manifest_for(
    queries: list[Query],
    settings: Settings,
    *,
    reference_time: datetime | None = None,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from companybench.providers.registry import PRESETS

    if not queries:
        raise ValueError("Query selection is empty")
    if not settings.providers:
        raise ValueError("Select at least one provider with --provider or a configuration file")
    reference_time = reference_time or datetime.now(UTC)
    if reference_time.tzinfo is None:
        raise ValueError("Reference time must include a timezone")
    serialized = [query.model_dump(mode="json") for query in queries]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).parents[2],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = None
    tasks = [
        {
            "task_id": task_id(query.id, provider, trial),
            "query_id": query.id,
            "provider": provider,
            "trial": trial,
        }
        for trial in range(1, settings.trials + 1)
        for query in queries
        for provider in settings.providers
    ]
    # Randomizing within query/trial blocks reduces provider-order effects without changing selection.
    rng = random.Random(settings.seed)
    ordered: list[dict[str, Any]] = []
    for start in range(0, len(tasks), len(settings.providers)):
        block = tasks[start : start + len(settings.providers)]
        rng.shuffle(block)
        ordered.extend(block)
    return {
        "format_version": 1,
        "created_at": now_iso(),
        "reference_time": reference_time.astimezone(UTC).isoformat(),
        "package_version": __version__,
        "code_commit": commit,
        "code_hash": package_code_hash(),
        "dataset_hash": fingerprint(serialized),
        "settings": settings.model_dump(mode="json"),
        "settings_hash": fingerprint(settings.model_dump(mode="json")),
        "queries": serialized,
        "provider_presets": {
            name: PRESETS.get(name, {"custom": name}) for name in settings.providers
        },
        "selection": selection or {},
        "tasks": ordered,
        "evaluation_status": "automated preliminary",
        "stage": "planned",
    }


def preflight(settings: Settings, *, evaluation: bool = True) -> list[dict[str, Any]]:
    from companybench.evidence import OpenAIResearcher
    from companybench.judges import get_judge
    from companybench.providers.registry import resolve

    checks = []
    for name in settings.providers:
        provider = resolve(name, settings.provider_options.get(name))
        variable = provider.api_key_env
        checks.append(
            {
                "component": name,
                "key_environment_variable": variable,
                "configured": not variable or bool(os.environ.get(variable)),
                "readiness": provider.readiness,
            }
        )
    if evaluation:
        for name in settings.judges:
            judge = get_judge(name, settings.judge_options.get(name))
            variable = judge.api_key_env
            checks.append(
                {
                    "component": f"judge:{name}",
                    "key_environment_variable": variable,
                    "configured": not variable
                    or bool(os.environ.get(variable))
                    or (name == "jev" and bool(os.environ.get("TYPESAFE_API_KEY"))),
                }
            )
        researcher = OpenAIResearcher(settings.research)
        variable = researcher.api_key_env
        checks.append(
            {
                "component": "research",
                "key_environment_variable": variable,
                "configured": variable is None or bool(os.environ.get(variable)),
            }
        )
    return checks


def cost_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    from companybench.pricing import estimate_max_cost

    settings = Settings.model_validate(manifest["settings"])
    estimates = {}
    for name in settings.providers:
        value = estimate_max_cost(name, settings.target_count, settings.provider_options.get(name))
        estimates[name] = {
            "estimated_max_search_usd": value * len(manifest["queries"]) * settings.trials
            if value is not None
            else None,
            "per_query_usd": value,
        }
    return {
        "queries": len(manifest["queries"]),
        "configurations": len(settings.providers),
        "trials": settings.trials,
        "search_tasks": len(manifest["tasks"]),
        "possible_company_positions": len(manifest["tasks"]) * settings.target_count,
        "search_estimates": estimates,
        "known_search_estimate_usd": sum(
            value["estimated_max_search_usd"] or 0 for value in estimates.values()
        ),
        "unpriced_configurations": [
            name for name, value in estimates.items() if value["per_query_usd"] is None
        ],
        "research_and_judging": "Additional; depends on unique companies, evidence, and selected judges",
        "budget": "Soft estimate; unknown charges and accepted jobs can exceed it",
    }


def _budget(store: RunStore, settings: Settings) -> Budget:
    budget = Budget(settings.budget_usd)
    manifest = store.read("manifest.json")
    search_folders = set()
    for task in manifest["tasks"]:
        state = store.task(task["task_id"])
        search_folders.add(fingerprint(task["task_id"])[:24])
        cost = state.get("result", {}).get("cost", {}).get("public_usd")
        budget.reserved += cost if cost is not None else state.get("reserved_usd") or 0
        if (
            (state.get("result") or "reserved_usd" in state)
            and cost is None
            and state.get("reserved_usd") is None
        ):
            budget.unknown_operations += 1
    settled_folders = set()
    settled_unknown_without_reservation = set()
    for ledger in evaluation_costs(store):
        folder = fingerprint(ledger["task_id"])[:24]
        amount = ledger["cost"].get("public_usd")
        budget.reserved += amount if amount is not None else ledger.get("reserved_usd") or 0
        settled_folders.add(folder)
        if amount is None:
            budget.unknown_operations += 1
            if "reserved_usd" not in ledger:
                settled_unknown_without_reservation.add(folder)
    # A paid response can be saved before its stage output/ledger. Restore that exposure
    # instead of making crashes look like free research or judging.
    for path in store.artifact("operations").glob("*/*.json"):
        operation = store.read(str(path.relative_to(store.path)))
        if operation.get("status") == "rejected":
            continue
        folder = path.parent.name
        if folder in settled_unknown_without_reservation:
            budget.reserved += operation.get("estimated_usd") or 0
        if folder not in search_folders and folder not in settled_folders:
            estimate = operation.get("estimated_usd")
            budget.reserved += estimate or 0
            budget.unknown_operations += int(estimate is None)
        elif folder in search_folders and operation.get("status") in {
            "intent",
            "acceptance_unknown",
        }:
            budget.unknown_operations += 1
    return budget


def evaluation_costs(store: RunStore) -> list[dict[str, Any]]:
    """The append-only journal covers crashes before the convenient JSON snapshot."""
    rows = {item["task_id"]: item for item in store.read("evaluation_costs.json", [])}
    for item in store.read_lines("evaluation_costs.jsonl"):
        rows[item["task_id"]] = item
    return list(rows.values())


def _save_evaluation_cost(
    store: RunStore,
    key: str,
    row: dict[str, Any],
    ledger: list[dict[str, Any]],
    settled: dict[str, dict[str, Any]],
    budget: Budget,
) -> None:
    previous = settled.get(key)
    operations = [
        store.read(str(path.relative_to(store.path)))
        for path in store.artifact("operations/" + fingerprint(key)[:24]).glob("*.json")
    ]
    operation_reservation = sum(
        item.get("estimated_usd") or 0 for item in operations if item.get("status") != "rejected"
    )
    prior_reserved = operation_reservation
    if previous:
        previous_amount = previous["cost"].get("public_usd")
        prior_reserved = (
            previous_amount
            if previous_amount is not None
            else previous.get("reserved_usd", operation_reservation)
        )
    amount = row["cost"].get("public_usd")
    if amount is None and prior_reserved:
        row = {**row, "reserved_usd": prior_reserved}
    if previous == row:
        return
    if amount is not None:
        budget.reserved += amount - prior_reserved
        unknown = (
            int(previous["cost"].get("public_usd") is None)
            if previous
            else sum(item.get("estimated_usd") is None for item in operations)
        )
        budget.unknown_operations = max(0, budget.unknown_operations - unknown)
    store.append("evaluation_costs.jsonl", row)
    ledger.append(row)
    settled[key] = row


async def search_run(
    store: RunStore, *, progress: Callable[[dict[str, Any]], None] | None = None
) -> None:
    from companybench.pricing import estimate_cost, estimate_max_cost
    from companybench.providers.registry import resolve

    manifest = store.read("manifest.json")
    settings = Settings.model_validate(manifest["settings"])
    queries = {row["id"]: Query.model_validate(row) for row in manifest["queries"]}
    budget = _budget(store, settings)
    providers = {
        name: resolve(name, settings.provider_options.get(name)) for name in settings.providers
    }
    global_limit = asyncio.Semaphore(settings.concurrency)
    changed = False
    provider_limits: dict[str, asyncio.Semaphore] = {}
    for name, provider in providers.items():
        group = provider.api_key_env or name
        provider_limits.setdefault(
            group, asyncio.Semaphore(1 if name == "avina" else settings.provider_concurrency)
        )

    async def execute(task: dict[str, Any]) -> None:
        nonlocal changed
        identifier, name = task["task_id"], task["provider"]
        state = store.task(identifier)
        if state.get("result"):
            return
        provider = providers[name]
        group = provider.api_key_env or name
        async with global_limit, provider_limits[group]:
            changed = True
            state = store.task(identifier)
            estimate = estimate_max_cost(
                name, settings.target_count, settings.provider_options.get(name)
            )
            try:
                if "reserved_usd" not in state:
                    budget.reserve(estimate)
                    store.save_task(identifier, reserved_usd=estimate)
                    state["reserved_usd"] = estimate
            except BudgetExceeded as error:
                store.save_task(identifier, status="not_started", error=str(error))
                return
            request = SearchRequest(
                query=queries[task["query_id"]],
                target_count=settings.target_count,
                reference_time=datetime.fromisoformat(manifest["reference_time"]),
                task_id=identifier,
                trial=task["trial"],
            )
            store.save_task(identifier, request=request.model_dump(mode="json"), status="searching")
            context = CallContext(store, identifier)
            client = SearchClient(
                provider,
                context=context,
                poll_interval=settings.poll_interval,
                timeout=settings.timeout,
            )
            try:
                result = await client.asearch(request)
                result.provider = name
                if result.cost.public_usd is None and not result.metadata.get("cost_unsettled"):
                    priced = estimate_cost(
                        name,
                        result.usage,
                        model=result.model,
                        options=settings.provider_options.get(name),
                    )
                    result.cost = priced.model_copy(
                        update={
                            "account_usd": result.cost.account_usd
                            if result.cost.account_usd is not None
                            else priced.account_usd,
                            "confirmed_usd": result.cost.confirmed_usd
                            if result.cost.confirmed_usd is not None
                            else priced.confirmed_usd,
                        }
                    )
                if result.metadata.get("resumable"):
                    store.save_task(
                        identifier, status="interrupted", last_result=result.model_dump(mode="json")
                    )
                else:
                    if result.cost.public_usd is not None:
                        budget.reserved += result.cost.public_usd - (state.get("reserved_usd") or 0)
                        if estimate is None:
                            budget.unknown_operations = max(0, budget.unknown_operations - 1)
                    elif estimate is not None:
                        budget.unknown_operations += 1
                    store.save_task(
                        identifier, status="searched", result=result.model_dump(mode="json")
                    )
            except AcceptanceUnknown as error:
                store.save_task(identifier, status="submission_unknown", error=str(error))
            except asyncio.CancelledError:
                store.save_task(identifier, status="interrupted")
                raise
            except Exception as error:
                result = SearchResult(
                    provider=name, status="failed", error=f"{type(error).__name__}: {error}"
                )
                store.save_task(
                    identifier, status="searched", result=result.model_dump(mode="json")
                )
            store.event(
                "search_finished", task_id=identifier, status=store.task(identifier).get("status")
            )
            if progress:
                progress(
                    {
                        "task_id": identifier,
                        "status": store.task(identifier).get("status"),
                        "estimated_spend_usd": budget.reserved,
                        "unknown_cost_tasks": budget.unknown_operations,
                    }
                )

    await asyncio.gather(*(execute(task) for task in manifest["tasks"]))
    if changed:
        manifest["stage"] = "searched"
        manifest["search_finished_at"] = now_iso()
        store.write("manifest.json", manifest)


def _search_records(store: RunStore) -> list[tuple[dict[str, Any], SearchRequest, SearchResult]]:
    records = []
    for task in store.read("manifest.json")["tasks"]:
        state = store.task(task["task_id"])
        if state.get("result"):
            records.append(
                (
                    task,
                    SearchRequest.model_validate(state["request"]),
                    SearchResult.model_validate(state["result"]),
                )
            )
    return records


async def research_run(
    store: RunStore,
    *,
    new_revision: bool = False,
    researcher: Any = None,
    research_options: dict[str, Any] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    from companybench.evidence import OpenAIResearcher, normalize_identity

    manifest = store.read("manifest.json")
    settings = Settings.model_validate(manifest["settings"])
    records = _search_records(store)
    if len(records) != len(manifest["tasks"]):
        raise ValueError(
            "Finish the selected search cohort before freezing evidence; unresolved or unattempted searches remain"
        )
    latest = store.read("evidence/current.json")
    if latest and not new_revision:
        return latest["version"]
    revision = (latest["revision"] + 1) if latest else 1
    research_settings = (
        research_options
        if research_options is not None
        else latest["research_settings"]
        if latest
        else settings.research
    )
    researcher = researcher or OpenAIResearcher(research_settings)
    pooled: dict[tuple[str, str], dict[str, Any]] = {}
    mapping: dict[str, dict[str, Any]] = {}
    from companybench.review import load_identity_overrides

    override_path = store.artifact("identity_overrides.json")
    overrides = load_identity_overrides(override_path) if override_path.exists() else {}
    for task, request, result in records:
        positions = {}
        for ordinal, candidate in enumerate(result.candidates[: request.target_count], 1):
            if (
                candidate.position != ordinal
                or candidate.malformed
                or not (candidate.name or candidate.domain or candidate.website)
            ):
                continue
            identity = normalize_identity(
                candidate, request.query.id, unique_key=f"{task['task_id']}:{candidate.position}"
            )
            if identity.id in overrides:
                identity = overrides[identity.id].model_copy(deep=True)
            positions[str(ordinal)] = identity.model_dump(mode="json")
            key = (request.query.id, identity.id)
            pooled.setdefault(key, {"query": request.query, "company": identity, "leads": set()})
            pooled[key]["leads"].update(candidate.citations)
            if candidate.website:
                pooled[key]["leads"].add(candidate.website)
            elif candidate.domain:
                pooled[key]["leads"].add(f"https://{candidate.domain}")
        mapping[task["task_id"]] = positions
    # Different business names sharing a hostname can be aliases or separate subsidiaries.
    # Do not grant multiple unique-company credits until the relationship is reviewed.
    by_host: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (query_id, _), entry in pooled.items():
        if entry["company"].domain:
            by_host[query_id, entry["company"].domain].append(entry)
    reviewed_ids = {identity.id for identity in overrides.values()}
    for entries in by_host.values():
        if len(entries) > 1:
            for entry in entries:
                identity = entry["company"]
                if identity.id not in reviewed_ids:
                    identity.resolved = False
                    identity.notes += "; same hostname appears under different company names; identity review required"
    pooled_identities = {
        entry["company"].id: entry["company"].model_dump(mode="json") for entry in pooled.values()
    }
    for positions in mapping.values():
        for position, value in positions.items():
            positions[position] = pooled_identities[value["id"]]
    budget = _budget(store, settings)
    packets = []
    ledger = evaluation_costs(store)
    settled = {item["task_id"]: item for item in ledger}

    async def research_one(item: tuple[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
        (query_id, entity_id), entry = item
        key = f"research:{revision}:{query_id}:{entity_id}"
        packet = EvidencePacket(
            query=entry["query"],
            company=entry["company"],
            reference_time=datetime.fromisoformat(manifest["reference_time"]),
        )
        context = CallContext(store, key, budget=budget)
        cached = context.load("research_output")
        if cached:
            packet = EvidencePacket.model_validate(cached["packet"])
            from companybench.models import Cost, Usage

            usage, cost = Usage.model_validate(cached["usage"]), Cost.model_validate(cached["cost"])
        else:
            packet, usage, cost = await researcher.research(packet, sorted(entry["leads"]), context)
            context.checkpoint(
                "research_output",
                {
                    "packet": packet.model_dump(mode="json"),
                    "usage": usage.model_dump(mode="json"),
                    "cost": cost.model_dump(mode="json"),
                },
            )
        _save_evaluation_cost(
            store,
            key,
            {
                "task_id": key,
                "stage": "research",
                "usage": usage.model_dump(mode="json"),
                "cost": cost.model_dump(mode="json"),
            },
            ledger,
            settled,
            budget,
        )
        packets.append(packet.model_dump(mode="json"))
        if progress:
            progress({"research": len(packets), "companies": len(pooled), "query_id": query_id})
        return packet.model_dump(mode="json")

    limit = asyncio.Semaphore(settings.concurrency)

    async def limited(item: tuple[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
        async with limit:
            return await research_one(item)

    await asyncio.gather(*(limited(item) for item in sorted(pooled.items())))
    store.write("evaluation_costs.json", ledger)
    packets.sort(key=lambda packet: (packet["query"]["id"], packet["company"]["id"]))
    version = fingerprint(
        {
            "packets": [
                {key: value for key, value in packet.items() if key != "evidence_version"}
                for packet in packets
            ],
            "identities": mapping,
            "cohort": manifest["tasks"],
            "researcher": research_settings,
            "reference_time": manifest["reference_time"],
            "capture_revision": revision,
        }
    )
    for packet in packets:
        packet["evidence_version"] = version
    store.write(f"evidence/{version}/packets.json", packets)
    store.write(f"evidence/{version}/identities.json", mapping)
    revision_metadata = {
        "version": version,
        "revision": revision,
        "frozen_at": now_iso(),
        "research_settings": research_settings,
        "reference_time": manifest["reference_time"],
        "search_hash": fingerprint(
            [
                {
                    "task_id": task["task_id"],
                    "request": request.model_dump(mode="json"),
                    "result": result.model_dump(mode="json"),
                }
                for task, request, result in records
            ]
        ),
        "packet_count": len(packets),
        "cohort_hash": fingerprint(manifest["tasks"]),
    }
    store.write(f"evidence/{version}/metadata.json", revision_metadata)
    store.write("evidence/current.json", revision_metadata)
    manifest["stage"] = "researched"
    manifest["evidence_version"] = version
    store.write("manifest.json", manifest)
    return version


def frozen_evidence(store: RunStore) -> tuple[str, list[EvidencePacket]]:
    current = store.read("evidence/current.json")
    if not current:
        raise ValueError("Research and freeze evidence before grading")
    manifest = store.read("manifest.json")
    if (
        fingerprint(manifest["queries"]) != manifest["dataset_hash"]
        or fingerprint(manifest["settings"]) != manifest["settings_hash"]
    ):
        raise ValueError("Frozen dataset or configuration changed; create a new run")
    packets = store.read(f"evidence/{current['version']}/packets.json")
    identities = store.read(f"evidence/{current['version']}/identities.json")
    calculated = fingerprint(
        {
            "packets": [
                {key: value for key, value in packet.items() if key != "evidence_version"}
                for packet in packets
            ],
            "identities": identities,
            "cohort": manifest["tasks"],
            "researcher": current["research_settings"],
            "reference_time": current["reference_time"],
            "capture_revision": current["revision"],
        }
    )
    if calculated != current["version"] or current["cohort_hash"] != fingerprint(manifest["tasks"]):
        raise ValueError(
            "Frozen evidence or provider cohort changed; create a new evidence revision"
        )
    if current["reference_time"] != manifest["reference_time"]:
        raise ValueError("Frozen reference time changed; create a new run")
    if any(packet.get("evidence_version") != current["version"] for packet in packets):
        raise ValueError("Evidence packets do not share their frozen revision")
    search_hash = fingerprint(
        [
            {
                "task_id": task["task_id"],
                "request": request.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            }
            for task, request, result in _search_records(store)
        ]
    )
    if current.get("search_hash") and current["search_hash"] != search_hash:
        raise ValueError(
            "Frozen search results changed; create a new evidence revision and regrade"
        )
    return current["version"], [EvidencePacket.model_validate(value) for value in packets]


async def grade_run(
    store: RunStore,
    *,
    judges: list[str] | None = None,
    judge_options: dict[str, dict[str, Any]] | None = None,
    judge_instances: dict[str, Any] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    from companybench.evidence import packet_hash
    from companybench.extensions import implementation_identity, load_factory
    from companybench.judges import RUBRIC_HASH, get_judge

    manifest = store.read("manifest.json")
    settings = Settings.model_validate(manifest["settings"])
    version, packets = frozen_evidence(store)
    selected = judges or settings.judges
    options = judge_options if judge_options is not None else settings.judge_options
    ledger = evaluation_costs(store)
    settled = {item["task_id"]: item for item in ledger}
    budget = _budget(store, settings)

    async def grade_system(name: str) -> bool:
        judge = (
            judge_instances[name]
            if judge_instances and name in judge_instances
            else get_judge(name, options.get(name))
        )
        implementation = {"judge": implementation_identity(judge)}
        if ":" in name:
            factory = load_factory(name)
            implementation["factory"] = getattr(
                factory, "__companybench_source_identity__", implementation_identity(factory)
            )
        config_hash = fingerprint(
            {
                "name": name,
                "options": options.get(name, {}),
                "model": getattr(judge, "model", None),
                "implementation": implementation,
                "version": version,
                "rubric": RUBRIC_HASH,
                "schema": CompanyJudgment.model_json_schema(),
            }
        )
        directory = f"judgments/{version}/{judge_directory(name)}"
        relative = f"{directory}/{config_hash}.json"
        saved = store.read(relative, [])
        existing = store.read(f"{directory}/current.json")
        if (
            existing
            and existing["config_hash"] == config_hash
            and existing.get("content_hash") != fingerprint(saved)
        ):
            raise ValueError("Frozen judgments changed; import reviewed corrections instead")
        individual = f"{directory}/{config_hash}/companies"
        recovered = [
            store.read(str(path.relative_to(store.path)))
            for path in store.artifact(individual).glob("*.json")
        ]
        saved_successes = {
            (item["query_id"], item["entity_id"]): item
            for item in saved
            if item["verdict"] != "error"
        }
        for item in recovered:
            key = (item["query_id"], item["entity_id"])
            if key in saved_successes and item != saved_successes[key]:
                raise ValueError(
                    "Individual judgment conflicts with frozen results; import reviewed corrections instead"
                )
        judgments = {
            (item["query_id"], item["entity_id"]): item
            for item in [*saved, *recovered]
            if item["verdict"] != "error"
        }
        limit = asyncio.Semaphore(settings.concurrency)

        async def grade_one(packet: EvidencePacket) -> None:
            key = (packet.query.id, packet.company.id)
            context_key = f"judge:{config_hash}:{key[0]}:{key[1]}"
            if key in judgments:
                judgment = CompanyJudgment.model_validate(judgments[key])
                if judgment.packet_hash and judgment.packet_hash != packet_hash(packet):
                    raise ValueError(
                        "Saved judgment refers to different evidence; create a new grading configuration"
                    )
            else:
                async with limit:
                    context = CallContext(store, context_key, budget=budget)
                    judgment = await judge.grade(packet, context)
                    judgments[key] = judgment.model_dump(mode="json")
                    # One atomic file per company avoids repeatedly writing an entire large cohort.
                    store.write(f"{individual}/{fingerprint(key)[:24]}.json", judgments[key])
            _save_evaluation_cost(
                store,
                context_key,
                {
                    "task_id": context_key,
                    "stage": "judge",
                    "judge": name,
                    "usage": judgment.usage.model_dump(mode="json"),
                    "cost": judgment.cost.model_dump(mode="json"),
                },
                ledger,
                settled,
                budget,
            )
            if progress:
                progress({"judge": name, "graded": len(judgments), "companies": len(packets)})

        await asyncio.gather(*(grade_one(packet) for packet in packets))
        serialized = [judgments[key] for key in sorted(judgments)]
        index_path = f"{directory}/current.json"
        existing = store.read(index_path)
        if saved == serialized and existing and existing["config_hash"] == config_hash:
            return False
        store.write(relative, serialized)
        store.write(
            index_path,
            {
                "path": relative,
                "config_hash": config_hash,
                "judge": name,
                "content_hash": fingerprint(serialized),
                "options": options.get(name, {}),
                "graded_at": now_iso(),
                "model": getattr(judge, "model", None),
                "implementation": implementation,
            },
        )
        return True

    changes = [await grade_system(name) for name in selected]
    store.write("evaluation_costs.json", ledger)
    declared = sorted(set(manifest.get("declared_judges", settings.judges)) | set(selected))
    if any(changes) or manifest.get("declared_judges") != declared:
        manifest["declared_judges"] = declared
        manifest["stage"] = "graded"
        manifest["graded_at"] = now_iso()
        store.write("manifest.json", manifest)


def metric_rows(
    store: RunStore, *, cost_view: str = "public", prefix: int | None = None
) -> list[dict[str, Any]]:
    from companybench.evidence import packet_hash
    from companybench.metrics import calculate_metrics

    version, packets = frozen_evidence(store)
    packet_hashes = {
        (packet.query.id, packet.company.id): packet_hash(packet) for packet in packets
    }
    identities = store.read(f"evidence/{version}/identities.json")
    index_paths = sorted(store.artifact(f"judgments/{version}").glob("*/current.json"))
    if not index_paths:
        raise ValueError("No judgments exist; run grade first")
    rows = []
    for path in index_paths:
        index = store.read(str(path.relative_to(store.path)))
        serialized = store.read(index["path"])
        if index.get("content_hash") and index["content_hash"] != fingerprint(serialized):
            raise ValueError(
                "Saved judgments changed; import reviewed corrections instead of editing artifacts"
            )
        judgments = [CompanyJudgment.model_validate(item) for item in serialized]
        by_query: dict[str, dict[str, CompanyJudgment]] = defaultdict(dict)
        for judgment in judgments:
            if judgment.packet_hash and judgment.packet_hash != packet_hashes.get(
                (judgment.query_id, judgment.entity_id)
            ):
                raise ValueError("Judgment does not match frozen evidence")
            by_query[judgment.query_id][judgment.entity_id] = judgment
        for task, request, result in _search_records(store):
            task_identities = {
                int(position): CompanyIdentity.model_validate(value)
                for position, value in identities.get(task["task_id"], {}).items()
            }
            row = calculate_metrics(
                request,
                result,
                task_identities,
                by_query[request.query.id],
                cost_view=cost_view,
                prefix=prefix,
            )
            row["judge"] = index.get("judge", path.parent.name)
            row["judge_config_hash"] = index["config_hash"]
            row["evidence_version"] = version
            rows.append(row)
    return rows
