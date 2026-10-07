"""Offline, explicitly synthetic end-to-end demonstration.

The invented companies use reserved ``.example`` domains. The evidence, verdicts,
prices, and chart timings are fixtures, not claims about real providers or firms.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from companybench.client import SearchClient
from companybench.config import Settings
from companybench.datasets import load_dataset
from companybench.evidence import packet_hash
from companybench.models import (
    CompanyCandidate,
    CompanyJudgment,
    Cost,
    CriterionJudgment,
    EvidencePacket,
    EvidenceSource,
    SearchJob,
    SearchRequest,
    SearchResult,
    Usage,
)
from companybench.providers.base import ImmediateProvider, JobProvider, Provider
from companybench.report import write_report
from companybench.runner import grade_run, manifest_for, research_run
from companybench.storage import RunStore
from companybench.transport import CallContext

DEMO_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _company(request: SearchRequest, position: int, label: str) -> CompanyCandidate:
    domain = f"{request.query.id.lower()}-{label.lower()}.example"
    return CompanyCandidate(
        position=position,
        name=f"Synthetic {request.query.id} {label}",
        domain=domain,
        website=f"https://{domain}",
        citations=[f"https://{domain}/synthetic-evidence"],
        claims={"synthetic": True},
    )


def _cost(amount: float) -> Cost:
    return Cost(
        public_usd=amount,
        account_usd=amount,
        confirmed_usd=amount,
        basis="Invented demonstration amount; no money was charged",
        assumptions=["Synthetic fixture; never use this amount as a provider price"],
    )


class DemoCleanProvider(ImmediateProvider):
    """Return three complete synthetic matches immediately."""

    name = "demo-clean"
    readiness = "synthetic"

    async def search(self, request: SearchRequest, context: CallContext) -> SearchResult:
        context.checkpoint("demo_submissions", context.load("demo_submissions", 0) + 1)
        return SearchResult(
            provider=self.name,
            candidates=[
                _company(request, position, name)
                for position, name in enumerate(["Alpha", "Beta", "Gamma"], 1)
            ],
            cost=_cost(0.30),
            metadata={"synthetic": True},
        )


class DemoNoisyProvider(JobProvider):
    """Exercise a remote-job lifecycle with duplicate, invalid, unknown, and bad rows."""

    name = "demo-noisy"
    readiness = "synthetic"

    async def submit(self, request: SearchRequest, context: CallContext) -> SearchJob:
        context.checkpoint("demo_submissions", context.load("demo_submissions", 0) + 1)
        return SearchJob(id=f"synthetic-{request.task_id}", status="queued")

    async def poll(self, request: SearchRequest, job: SearchJob, context: CallContext) -> SearchJob:
        calls = context.load("demo_polls", 0) + 1
        context.checkpoint("demo_polls", calls)
        if calls == 1:
            return job.model_copy(update={"status": "running"})
        return SearchJob(
            id=job.id,
            status="completed",
            result=SearchResult(
                provider=self.name,
                candidates=[
                    _company(request, 1, "Alpha"),
                    _company(request, 2, "Alpha"),
                    _company(request, 3, "Unknown"),
                    _company(request, 4, "Invalid"),
                    CompanyCandidate(position=5, malformed=True, raw="not a company"),
                ],
                cost=_cost(0.05),
                metadata={"synthetic": True},
            ),
        )


class DemoResearcher:
    """Provide invented evidence without fetching any URL."""

    async def research(
        self, packet: EvidencePacket, leads: list[str], context: CallContext
    ) -> tuple[EvidencePacket, Usage, Cost]:
        result = packet.model_copy(deep=True)
        if packet.company.resolved:
            fixture = (
                "unknown"
                if packet.company.name.endswith("Unknown")
                else "failed"
                if packet.company.name.endswith("Invalid")
                else "met"
            )
            result.sources = [
                EvidenceSource(
                    id="synthetic-source",
                    url=f"https://{packet.company.domain}/synthetic-evidence",
                    title="Invented company evidence for an offline software demonstration",
                    text=json.dumps({"synthetic": True, "fixture_verdict": fixture}),
                    fetched_at=DEMO_TIME,
                    method="synthetic",
                    redistributable=True,
                )
            ]
        else:
            result.gaps = ["Malformed synthetic row has no company identity"]
        return result, Usage(metadata={"synthetic": True}), _cost(0)


class DemoJudge:
    """Read a fixture label; this does not validate an LLM or grading rubric."""

    name = "demo"
    model = "synthetic-fixture-v1"
    api_key_env = None

    async def grade(self, packet: EvidencePacket, context: CallContext) -> CompanyJudgment:
        verdict = (
            json.loads(packet.sources[0].text)["fixture_verdict"] if packet.sources else "unknown"
        )
        if verdict not in {"met", "failed", "unknown"}:
            raise ValueError("Invalid synthetic verdict fixture")
        criterion_verdict = cast(Literal["met", "failed", "unknown"], verdict)
        company_verdict: Literal["valid", "invalid", "unknown"] = (
            "valid" if verdict == "met" else "invalid" if verdict == "failed" else "unknown"
        )
        return CompanyJudgment(
            query_id=packet.query.id,
            entity_id=packet.company.id,
            judge=self.name,
            model=self.model,
            verdict=company_verdict,
            conditions=[
                CriterionJudgment(
                    criterion_id=criterion.id,
                    verdict=criterion_verdict,
                    reason="Invented deterministic fixture label; no real-world judgment occurred.",
                    evidence_ids=[source.id for source in packet.sources],
                )
                for criterion in packet.query.conditions
            ],
            packet_hash=packet_hash(packet),
            rubric_hash="synthetic-fixture-v1",
            usage=Usage(metadata={"synthetic": True}),
            cost=_cost(0),
        )


async def run_demo(path: Path) -> Path:
    """Run all real pipeline stages with fake components; return the report directory.

    Repeating this function resumes cached work. An existing nonsynthetic run is
    never overwritten. Call from a synchronous program with ``asyncio.run``.
    """
    queries = load_dataset("development")[:3]
    settings = Settings(
        providers=["demo-clean", "demo-noisy"],
        judges=["demo"],
        target_count=5,
        poll_interval=0,
        timeout=5,
        research={"mode": "synthetic"},
    )
    manifest = manifest_for(queries, settings, reference_time=DEMO_TIME)
    manifest["synthetic"] = True
    manifest["evaluation_status"] = "synthetic demonstration"
    store = RunStore(path)
    providers: dict[str, Provider] = {
        "demo-clean": DemoCleanProvider(),
        "demo-noisy": DemoNoisyProvider(),
    }
    with store.lock():
        existing = store.read("manifest.json")
        if existing:
            if (
                not existing.get("synthetic")
                or existing["dataset_hash"] != manifest["dataset_hash"]
                or existing["settings_hash"] != manifest["settings_hash"]
            ):
                raise ValueError(
                    "Demo destination contains a different run; choose a new directory"
                )
            manifest = existing
        else:
            store.write("manifest.json", manifest)
        query_by_id = {query.id: query for query in queries}
        for task in manifest["tasks"]:
            request = SearchRequest(
                query=query_by_id[task["query_id"]],
                target_count=5,
                reference_time=DEMO_TIME,
                task_id=task["task_id"],
                trial=task["trial"],
            )
            context = CallContext(store, request.task_id)
            result = await SearchClient(
                providers[task["provider"]], context=context, poll_interval=0, timeout=5
            ).asearch(request)
            result.metadata["offline_execution_seconds"] = result.latency_seconds
            result.metadata["chart_time_basis"] = "Invented timing for a synthetic illustration"
            result.latency_seconds = 3.2 if task["provider"] == "demo-clean" else 0.8
            store.save_task(
                request.task_id,
                status="searched",
                request=request.model_dump(mode="json"),
                result=result.model_dump(mode="json"),
            )
        await research_run(store, researcher=DemoResearcher())
        await grade_run(store, judge_instances={"demo": DemoJudge()})
        return write_report(store, publication=True)
