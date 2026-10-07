"""Independent Exa Websets and Agent products, with separate public presets."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlencode

from companybench.models import SearchJob, SearchRequest, SearchResult, Usage
from companybench.pricing import EXA_FIXED_RATES, EXA_METERED_CAPS, estimate_cost, exa_run_cap
from companybench.providers.base import JobProvider, objective
from companybench.providers.common import (
    COMPANY_SCHEMA,
    CREDIT_OPTIONS,
    HTTPProvider,
    JobStatus,
    ResultStatus,
    candidates_from_payload,
    citation_urls,
    parsed_result,
)


class ExaWebsetsProvider(HTTPProvider, JobProvider):
    name = "exa-websets"
    api_key_env = "EXA_API_KEY"
    default_base_url = "https://api.exa.ai"
    allowed_options = CREDIT_OPTIONS

    async def submit(self, request: SearchRequest, context: Any) -> SearchJob:
        body = {
            "search": {
                "query": objective(request),
                "count": request.target_count,
                "entity": {"type": "company"},
            }
        }
        # Criteria stay in the full query to preserve nested AND/OR/NOT logic and
        # avoid converting every leaf into a required native criterion.
        raw = await context.request(
            "POST",
            f"{self.base_url}/websets/v0/websets",
            operation="exa.websets.submit",
            billable=True,
            headers=self.headers("x-api-key"),
            json=body,
        )
        return SearchJob(id=raw["id"], status="queued", handles={"owned": True}, progress=raw)

    async def poll(self, request: SearchRequest, job: SearchJob, context: Any) -> SearchJob:
        base = f"{self.base_url}/websets/v0/websets/{quote(job.id, safe='')}"
        headers = self.headers("x-api-key")
        raw = await context.request("GET", base, operation="exa.websets.poll", headers=headers)
        searches = raw.get("searches", [])
        states = [s.get("status") for s in searches]
        terminal = bool(states) and all(s in {"completed", "canceled"} for s in states)
        cancelled = terminal and any(s == "canceled" for s in states)
        rows: list[dict[str, Any]] = []
        cursor = None
        seen: set[str] = set()
        while True:
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = await context.request(
                "GET",
                f"{base}/items?{urlencode(params)}",
                operation="exa.websets.items",
                headers=headers,
            )
            rows.extend(page.get("data", []))
            if not page.get("hasMore"):
                break
            cursor = page.get("nextCursor")
            if not cursor or cursor in seen:
                raise ValueError("Exa Websets pagination did not advance")
            seen.add(cursor)
        items = []
        for row in rows:
            properties = row.get("properties", {})
            items.append(
                {
                    "name": (properties.get("company") or {}).get("name"),
                    "url": properties.get("url"),
                    "id": row.get("id"),
                    "citations": citation_urls(row.get("evaluations")),
                    "description": properties.get("description"),
                    "evaluations": row.get("evaluations", []),
                }
            )
        # The Webset contains native results, not the rejected internal search pool.
        billed_matches = sum(
            all(v.get("satisfied") == "yes" for v in row.get("evaluations", [])) for row in rows
        )
        evaluations_reported = all(isinstance(row.get("evaluations"), list) for row in rows)
        usage = Usage(
            units={"credits": billed_matches * 10} if evaluations_reported else {},
            metadata={
                "credit_basis": "inferred native result tariff; excludes unreported operations"
            },
        )
        result = SearchResult(
            provider=self.name,
            candidates=candidates_from_payload(items),
            status="completed" if terminal and not cancelled else "partial",
            usage=usage,
            raw={"webset": raw, "items": rows},
            metadata={"native_status": raw.get("status")},
        )
        result.cost = estimate_cost(self.name, usage, options=self.options)
        result.cost.assumptions.append(
            "10 credits per native result satisfying all returned native evaluations; no previews, recall-estimation requests or enrichments requested."
        )
        state: JobStatus = "cancelled" if cancelled else "completed" if terminal else "running"
        return SearchJob(id=job.id, status=state, handles=job.handles, progress=raw, result=result)

    async def cancel(self, job: SearchJob, context: Any) -> bool:
        await context.request(
            "POST",
            f"{self.base_url}/websets/v0/websets/{quote(job.id, safe='')}/cancel",
            operation="exa.websets.cancel",
            headers=self.headers("x-api-key"),
        )
        return True

    async def cleanup(self, job: SearchJob, context: Any) -> bool:
        if not job.handles.get("owned") or job.status not in {"completed", "cancelled", "failed"}:
            return False
        await context.request(
            "DELETE",
            f"{self.base_url}/websets/v0/websets/{quote(job.id, safe='')}",
            operation="exa.websets.delete",
            headers=self.headers("x-api-key"),
        )
        return True


class ExaAgentProvider(HTTPProvider, JobProvider):
    name = "exa-agent-high"
    api_key_env = "EXA_API_KEY"
    default_base_url = "https://api.exa.ai"
    allowed_options = {"effort", "max_cost_usd", "max_duration_seconds"}
    allowed_efforts = set(EXA_FIXED_RATES) | set(EXA_METERED_CAPS)

    def __init__(self, name: str | None = None, **options: Any) -> None:
        super().__init__(name=name, **options)
        effort = options.get("effort", "high")
        exa_run_cap(effort, options)
        duration = options.get("max_duration_seconds")
        if duration is not None and (
            effort != "ultra" or not isinstance(duration, int) or not 300 <= duration <= 10800
        ):
            raise ValueError("max_duration_seconds accepts integers 300–10800 for Exa ultra only")

    async def submit(self, request: SearchRequest, context: Any) -> SearchJob:
        body: dict[str, Any] = {
            "query": objective(request),
            "outputSchema": COMPANY_SCHEMA,
            "effort": self.options.get("effort", "high"),
        }
        budget = {
            native: self.options[key]
            for key, native in (
                ("max_cost_usd", "maxCostDollars"),
                ("max_duration_seconds", "maxDurationSeconds"),
            )
            if key in self.options
        }
        if budget:
            body["budget"] = budget
        raw = await context.request(
            "POST",
            f"{self.base_url}/agent/runs",
            operation="exa.agent.submit",
            billable=True,
            headers=self.headers("x-api-key"),
            json=body,
        )
        return SearchJob(id=raw["id"], status="queued", handles={"owned": True}, progress=raw)

    async def poll(self, request: SearchRequest, job: SearchJob, context: Any) -> SearchJob:
        raw = await context.request(
            "GET",
            f"{self.base_url}/agent/runs/{quote(job.id, safe='')}",
            operation="exa.agent.poll",
            headers=self.headers("x-api-key"),
        )
        state = raw.get("status")
        if state not in {"queued", "running", "completed", "failed", "cancelled"}:
            raise ValueError(f"Unknown Exa Agent status: {state!r}")
        native_usage = raw.get("usage") or {}
        cost = raw.get("costDollars") or {}
        units = (
            {"reported_usd": cost["total"]} if isinstance(cost.get("total"), (int, float)) else {}
        )
        for native_key, key in (
            ("agentComputeUnits", "agent_compute_units"),
            ("emails", "emails"),
            ("phoneNumbers", "phone_numbers"),
        ):
            if isinstance(native_usage.get(native_key), (int, float)):
                units[key] = native_usage[native_key]
        usage = Usage(
            search_requests=native_usage.get("searches", 0),
            units=units,
            metadata={
                "native_usage": native_usage,
                "searches_reported": "searches" in native_usage,
                "native_status": state,
            },
        )
        output = raw.get("output") or {}
        reason = raw.get("stopReason")
        result_status: ResultStatus = (
            "completed"
            if state == "completed" and reason in {None, "schema_satisfied"}
            else "partial"
        )
        if state == "failed":
            result_status = "failed"
        result = parsed_result(
            self.name,
            output.get("structured") or {"companies": []},
            usage=usage,
            status=result_status,
            raw=raw,
        )
        # Grounding uses field paths. Attach only citations belonging to that row;
        # preserve unmatched global grounding in raw instead of assigning it to all.
        for grounding in output.get("grounding", []):
            field = str(grounding.get("field", ""))
            match = re.search(r"companies(?:\[(\d+)\]|[./](\d+))", field)
            if match:
                index = int(match.group(1) or match.group(2))
                if index < len(result.candidates):
                    result.candidates[index].citations = list(
                        dict.fromkeys(
                            result.candidates[index].citations
                            + citation_urls(grounding.get("citations"))
                        )
                    )
        result.cost = estimate_cost(self.name, usage, options=self.options)
        result.metadata["stop_reason"] = reason
        if raw.get("error"):
            result.error = str(raw["error"])
        return SearchJob(
            id=job.id,
            status=state,
            handles=job.handles,
            progress={"stop_reason": reason},
            result=result,
            error=result.error,
        )

    async def cancel(self, job: SearchJob, context: Any) -> bool:
        await context.request(
            "POST",
            f"{self.base_url}/agent/runs/{quote(job.id, safe='')}/cancel",
            operation="exa.agent.cancel",
            headers=self.headers("x-api-key"),
        )
        return True

    async def cleanup(self, job: SearchJob, context: Any) -> bool:
        if not job.handles.get("owned") or job.status not in {"completed", "cancelled", "failed"}:
            return False
        await context.request(
            "DELETE",
            f"{self.base_url}/agent/runs/{quote(job.id, safe='')}",
            operation="exa.agent.delete",
            headers=self.headers("x-api-key"),
        )
        return True
