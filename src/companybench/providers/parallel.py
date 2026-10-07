"""Parallel FindAll: only native matched entities form the returned company list."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from companybench.models import SearchJob, SearchRequest, SearchResult, Usage
from companybench.pricing import estimate_cost
from companybench.providers.base import JobProvider, objective
from companybench.providers.common import (
    HTTPProvider,
    JobStatus,
    ResultStatus,
    candidates_from_payload,
    citation_urls,
)


class ParallelProvider(HTTPProvider, JobProvider):
    name = "parallel-core"
    api_key_env = "PARALLEL_API_KEY"
    default_base_url = "https://api.parallel.ai"
    allowed_options = {"generator"}

    def __init__(self, name: str | None = None, **options: Any) -> None:
        super().__init__(name=name, **options)
        if options.get("generator", "core") not in {"base", "core", "pro"}:
            raise ValueError("FindAll generator must be base, core, or pro")

    async def submit(self, request: SearchRequest, context: Any) -> SearchJob:
        # One full logical condition preserves OR/NOT. Splitting all leaves into
        # native match_conditions would silently turn disjunctions into AND.
        body = {
            "objective": objective(request),
            "entity_type": "companies",
            "match_conditions": [{"name": "benchmark_criteria", "description": objective(request)}],
            "generator": self.options.get("generator", "core"),
            "match_limit": request.target_count,
        }
        run = await context.request(
            "POST",
            f"{self.base_url}/v1beta/findall/runs",
            operation="parallel.submit",
            billable=True,
            headers=self.headers("x-api-key"),
            json=body,
        )
        return SearchJob(id=run["findall_id"], status="queued", progress=run)

    async def poll(self, request: SearchRequest, job: SearchJob, context: Any) -> SearchJob:
        raw = await context.request(
            "GET",
            f"{self.base_url}/v1beta/findall/runs/{quote(job.id, safe='')}/result",
            operation="parallel.poll",
            headers=self.headers("x-api-key"),
        )
        run = raw.get("run", {})
        status = run.get("status", {})
        state = status.get("status")
        mapping: dict[str, JobStatus] = {
            "queued": "queued",
            "running": "running",
            "cancelling": "running",
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
            "action_required": "failed",
        }
        if state not in mapping:
            raise ValueError(f"Unknown FindAll status: {state!r}")
        matches = [row for row in raw.get("candidates", []) if row.get("match_status") == "matched"]
        rows = [
            {
                "id": r.get("candidate_id"),
                "name": r.get("name"),
                "url": r.get("url"),
                "citations": citation_urls(r.get("basis")),
                "output": r.get("output"),
            }
            for r in matches
        ]
        usage = Usage(
            units={"native_matches": len(matches)},
            metadata={"generator": self.options.get("generator", "core")},
        )
        final_status: ResultStatus = (
            "completed"
            if state == "completed"
            else "failed"
            if state in {"failed", "action_required"}
            else "partial"
        )
        result = SearchResult(
            provider=self.name,
            candidates=candidates_from_payload(rows),
            status=final_status,
            usage=usage,
            raw=raw,
            metadata={"termination_reason": status.get("termination_reason")},
        )
        if state in {"failed", "action_required"}:
            result.error = str(status.get("termination_reason") or state)
        result.cost = estimate_cost(self.name, usage, options=self.options)
        return SearchJob(
            id=job.id, status=mapping[state], result=result, progress=status, error=result.error
        )

    async def cancel(self, job: SearchJob, context: Any) -> bool:
        await context.request(
            "POST",
            f"{self.base_url}/v1beta/findall/runs/{quote(job.id, safe='')}/cancel",
            operation="parallel.cancel",
            headers=self.headers("x-api-key"),
        )
        return True
