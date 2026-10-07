"""Minimal provider contracts; orchestration belongs to the client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from companybench.models import SearchJob, SearchRequest, SearchResult

if TYPE_CHECKING:
    from companybench.transport import CallContext


class ImmediateProvider:
    name = "custom"
    api_key_env: str | None = None
    readiness = "contract-tested"

    async def search(self, request: SearchRequest, context: CallContext) -> SearchResult:
        raise NotImplementedError


class JobProvider:
    name = "custom"
    api_key_env: str | None = None
    readiness = "contract-tested"

    async def submit(self, request: SearchRequest, context: CallContext) -> SearchJob:
        raise NotImplementedError

    async def poll(self, request: SearchRequest, job: SearchJob, context: CallContext) -> SearchJob:
        raise NotImplementedError

    async def cancel(self, job: SearchJob, context: CallContext) -> bool:
        return False

    async def cleanup(self, job: SearchJob, context: CallContext) -> bool:
        return False


class SyncProvider(ImmediateProvider):
    def __init__(self, name: str, function: Callable[[SearchRequest], SearchResult]) -> None:
        self.name = name
        self.function = function

    async def search(self, request: SearchRequest, context: CallContext) -> SearchResult:
        return await asyncio.to_thread(self.function, request)


Provider = ImmediateProvider | JobProvider


def objective(request: SearchRequest) -> str:
    """Pass the same objective and acceptance conditions to each search system."""
    conditions = "\n".join(f"- {c.id}: {c.description}" for c in request.query.conditions)
    rule: Any = request.query.rule.model_dump() if request.query.rule else "all conditions"
    return (
        f"Find up to {request.target_count} distinct companies.\n{request.query.query}\n"
        f"Reference time (UTC): {request.reference_time.isoformat()}\n"
        f"Company unit: {request.query.company_unit}\nConditions:\n{conditions}\n"
        f"Acceptance logic: {rule}\nReturn company names, websites, and source URLs."
    )
