"""Avina public v1 saved-signal workflow (draft contract; opt-in live verification)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from companybench.models import SearchJob, SearchRequest, SearchResult, Usage
from companybench.pricing import estimate_cost
from companybench.providers.base import JobProvider, objective
from companybench.providers.common import CREDIT_OPTIONS, HTTPProvider, candidates_from_payload


class AvinaProvider(HTTPProvider, JobProvider):
    name = "avina"
    api_key_env = "AVINA_API_KEY"
    default_base_url = "https://api.avina.io/v1"
    readiness = "draft-contract-tested"
    allowed_options = CREDIT_OPTIONS

    async def submit(self, request: SearchRequest, context: Any) -> SearchJob:
        if request.target_count > 50:
            raise ValueError("Avina public v1 accepts at most 50 companies per run")
        query = objective(request)
        if len(query) > 2000:
            raise ValueError("Avina's plain-language query is limited to 2000 characters")
        headers = self.headers()
        signal = context.load("avina_signal")
        if signal is None:
            signal = await context.request(
                "POST",
                f"{self.base_url}/signals",
                operation="avina.create_signal",
                billable=True,
                headers=headers,
                idempotency_header="Idempotency-Key",
                json={
                    "query": query,
                    "name": f"CompanyBench {request.task_id}"[:200],
                    "persona_ids": [],
                    "icp_criteria": [request.query.acceptance or request.query.query],
                },
            )
            context.checkpoint("avina_signal", signal)
        if (signal.get("monitor") or {}).get("enabled"):
            raise ValueError("Benchmark signals must have monitoring disabled")
        signal_id = signal["id"]
        run = await context.request(
            "POST",
            f"{self.base_url}/signals/{quote(signal_id, safe='')}/runs",
            operation="avina.create_run",
            billable=True,
            headers=headers,
            idempotency_header="Idempotency-Key",
            json={"count": request.target_count},
        )
        return SearchJob(
            id=run["id"],
            status="queued",
            handles={"signal_id": signal_id, "signal_type": signal.get("type"), "owned": True},
            progress=run,
        )

    async def poll(self, request: SearchRequest, job: SearchJob, context: Any) -> SearchJob:
        signal_id = quote(job.handles["signal_id"], safe="")
        headers = self.headers()
        run = await context.request(
            "GET",
            f"{self.base_url}/signals/{signal_id}/runs/{quote(job.id, safe='')}",
            operation="avina.poll",
            headers=headers,
        )
        state = run.get("status")
        if state not in {"queued", "running", "completed", "failed"}:
            raise ValueError(f"Unknown Avina run status: {state!r}")
        rows: list[dict[str, Any]] = []
        cursor = None
        seen: set[str] = set()
        while True:
            params = {"limit": 100}
            if cursor:
                params["after"] = cursor
            page = await context.request(
                "GET",
                f"{self.base_url}/signals/{signal_id}/results?{urlencode(params)}",
                operation="avina.results",
                headers=headers,
            )
            rows.extend(page.get("data", []))
            if not page.get("has_more"):
                break
            cursor = page.get("next_cursor")
            if not cursor or cursor in seen:
                raise ValueError("Avina pagination did not advance")
            seen.add(cursor)
        payload = [
            {
                **row.get("company", {}),
                "id": row.get("id"),
                "headline": row.get("headline"),
                "summary": row.get("summary"),
                "occurred_at": row.get("occurred_at"),
                "created_at": row.get("created_at"),
                "matched_terms": row.get("matched_terms"),
                "icp": row.get("icp"),
            }
            for row in rows
        ]
        # Credits inferred from the public per-result tariff, not a workspace-wide
        # balance delta that unrelated activity could contaminate.
        credits = sum(
            2
            if row.get("signal_type", job.handles.get("signal_type"))
            in {"custom_ai", "technographic"}
            else 1
            for row in rows
        )
        known_types = all(
            row.get("signal_type", job.handles.get("signal_type"))
            in {"custom_ai", "technographic", "new_hire", "job_listing", "social_post"}
            for row in rows
        )
        usage = Usage(
            units={"credits": credits} if known_types else {},
            metadata={"credit_basis": "inferred from returned results; not measured account delta"},
        )
        result = SearchResult(
            provider=self.name,
            candidates=candidates_from_payload(payload),
            usage=usage,
            status="completed"
            if state == "completed"
            else "failed"
            if state == "failed"
            else "partial",
            error=str(run.get("error")) if run.get("error") else None,
            raw={"run": run, "results": rows},
            metadata={"signal_id": job.handles["signal_id"], "public_api_contract": "draft"},
        )
        result.cost = estimate_cost(self.name, usage, options=self.options)
        result.cost.assumptions.append(
            "Credits inferred from returned signals; not invoice-confirmed. Contact enrichment disabled."
        )
        return SearchJob(
            id=job.id,
            status=state,
            handles=job.handles,
            progress=run,
            result=result,
            error=result.error,
        )

    async def cleanup(self, job: SearchJob, context: Any) -> bool:
        if not job.handles.get("owned") or job.status not in {"completed", "failed", "cancelled"}:
            return False
        await context.request(
            "DELETE",
            f"{self.base_url}/signals/{quote(job.handles['signal_id'], safe='')}",
            operation="avina.archive",
            headers=self.headers(),
        )
        return True
