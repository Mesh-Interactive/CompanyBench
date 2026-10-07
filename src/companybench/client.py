"""One sync/async/polling client for both immediate and remote-job providers."""

from __future__ import annotations

import asyncio
import math
import tempfile
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from companybench.models import SearchJob, SearchRequest, SearchResult
from companybench.providers.base import ImmediateProvider, JobProvider, Provider
from companybench.storage import RunStore, fingerprint
from companybench.transport import AcceptanceUnknown, CallContext, ProviderRequestError


class SearchClient:
    def __init__(
        self,
        provider: Provider,
        *,
        context: CallContext | None = None,
        run_dir: str | None = None,
        poll_interval: float = 5,
        timeout: float = 7200,
    ) -> None:
        if (
            not math.isfinite(poll_interval)
            or not math.isfinite(timeout)
            or poll_interval < 0
            or timeout <= 0
        ):
            raise ValueError("Polling interval must be nonnegative and timeout positive")
        if not isinstance(provider, (ImmediateProvider, JobProvider)):
            raise TypeError("Provider must implement ImmediateProvider or JobProvider")
        self.provider = provider
        self.context = context
        self.run_dir = run_dir
        self.poll_interval = poll_interval
        self.timeout = timeout
        self._contexts: dict[str, CallContext] = {}
        self._store: RunStore | None = None

    def _context(self, request: SearchRequest) -> CallContext:
        options = {
            key: value
            for key, value in getattr(self.provider, "options", {}).items()
            if key.casefold() not in {"api_key", "token", "password", "authorization"}
        }
        binding = fingerprint(
            {
                "request": request.model_dump(mode="json"),
                "provider": self.provider.name,
                "adapter": f"{type(self.provider).__module__}.{type(self.provider).__qualname__}",
                "options": options,
            }
        )
        if self.context is not None:
            context = self.context
        else:
            if self._store is None:
                self._store = RunStore(self.run_dir or tempfile.mkdtemp(prefix="companybench-"))
            task_id = request.task_id or f"search:{binding}"
            if task_id not in self._contexts:
                self._contexts[task_id] = CallContext(self._store, task_id)
            context = self._contexts[task_id]
        previous = context.load("search_binding")
        if previous is not None and previous != binding:
            raise ValueError(
                "Saved search has a different request or provider configuration; use a new task ID or context"
            )
        if previous is None:
            # Legacy task records still provide a request to validate before binding.
            recorded = context.store.task(context.task_id).get("request")
            if recorded is not None and fingerprint(recorded) != fingerprint(
                request.model_dump(mode="json")
            ):
                raise ValueError("Saved task has a different request; use a new task ID or context")
            context.checkpoint("search_binding", binding)
        return context

    def _timing(self, context: CallContext) -> dict[str, Any]:
        timing = context.load("search_timing")
        if timing is None:
            first_submission = context.load("first_submission_at")
            started = (
                datetime.fromisoformat(first_submission) if first_submission else datetime.now(UTC)
            )
            timing = {
                "started_at": started.isoformat(),
                "deadline_at": (started + timedelta(seconds=self.timeout)).isoformat(),
                "timeout_seconds": self.timeout,
            }
            context.checkpoint("search_timing", timing)
        return timing

    @staticmethod
    def _unknown_submission(context: CallContext) -> bool:
        operations = context.store.artifact("operations/" + fingerprint(context.task_id)[:24])
        return any(
            context.store.read(str(path.relative_to(context.store.path))).get("status")
            in {"intent", "acceptance_unknown"}
            for path in operations.glob("*.json")
        )

    @staticmethod
    def _completed_operation(context: CallContext) -> bool:
        operations = context.store.artifact("operations/" + fingerprint(context.task_id)[:24])
        return any(
            context.store.read(str(path.relative_to(context.store.path))).get("status")
            == "completed"
            for path in operations.glob("*.json")
        )

    async def submit(self, request: SearchRequest) -> SearchJob:
        context = self._context(request)
        self._timing(context)
        saved = context.load("job")
        if saved:
            return SearchJob.model_validate(saved)
        if isinstance(self.provider, JobProvider):
            job = await self.provider.submit(request, context)
        else:
            result = await self.provider.search(request, context)
            job = SearchJob(id=request.task_id or "immediate", status="completed", result=result)
        context.checkpoint("job", job.model_dump(mode="json"))
        return job

    async def poll(self, request: SearchRequest, job: SearchJob) -> SearchJob:
        if job.status in {"completed", "failed", "cancelled"}:
            return job
        if not isinstance(self.provider, JobProvider):
            raise ValueError("Immediate providers do not have pending remote jobs")
        context = self._context(request)
        updated = await self.provider.poll(request, job, context)
        context.checkpoint("job", updated.model_dump(mode="json"))
        return updated

    async def asearch(self, request: SearchRequest) -> SearchResult:
        context = self._context(request)
        saved_result = context.load("search_result")
        if saved_result:
            return SearchResult.model_validate(saved_result)
        resumed = (
            context.load("search_timing") is not None
            or context.load("first_submission_at") is not None
            or context.load("job") is not None
        )
        timing = self._timing(context)
        remaining = (
            datetime.fromisoformat(timing["deadline_at"]) - datetime.now(UTC)
        ).total_seconds()
        if remaining <= 0 and not context.load("job"):
            if self._unknown_submission(context):
                raise AcceptanceUnknown(
                    "Submission status unknown and search deadline elapsed; reconcile the accepted request before reporting a final result"
                )
        started = time.monotonic()
        saved_job = context.load("job")
        job = SearchJob.model_validate(saved_job) if saved_job else None
        deadline: asyncio.Timeout | None = None
        try:
            # A crash can fall between a durable HTTP response/job and the final
            # result checkpoint. Recover that paid work without granting a new deadline.
            if remaining <= 0 and job is None and self._completed_operation(context):
                previous_replay_only = context.replay_only
                context.replay_only = True
                try:
                    job = await self.submit(request)
                finally:
                    context.replay_only = previous_replay_only
            if job is not None and job.status in {"completed", "failed", "cancelled"}:
                result = job.result or SearchResult(
                    provider=self.provider.name,
                    status="failed",
                    error=job.error or f"Job ended with status {job.status}",
                )
                return self._finish(context, result, timing, resumed, started)
            if remaining <= 0:
                raise TimeoutError
            deadline = asyncio.timeout(remaining)
            async with deadline:
                job = await self.submit(request)
                while job.status in {"queued", "running"}:
                    await asyncio.sleep(self.poll_interval)
                    job = await self.poll(request, job)
                if job.result:
                    result = job.result
                else:
                    result = SearchResult(
                        provider=self.provider.name,
                        status="failed",
                        error=job.error or f"Job ended with status {job.status}",
                    )
        except TimeoutError:
            if deadline is not None and not deadline.expired():
                raise  # A provider's own timeout is not evidence that our deadline elapsed.
            saved_job = context.load("job")
            if saved_job:
                job = SearchJob.model_validate(saved_job)
            if job is None and self._unknown_submission(context):
                raise AcceptanceUnknown(
                    "Submission status unknown at the search deadline; paid work may have been accepted"
                ) from None
            result = (
                job.result.model_copy(deep=True)
                if job and job.result
                else SearchResult(provider=self.provider.name)
            )
            result.status = "partial"
            result.error = "Search deadline reached; remote work may still be active"
            result.metadata["deadline_reached"] = True
            result.metadata["remote_job"] = job.model_dump(mode="json") if job else None
            if job is not None and job.status in {"queued", "running"}:
                result.metadata["cost_unsettled"] = True
                result.metadata["observed_cost_at_deadline"] = result.cost.model_dump(mode="json")
                result.cost = result.cost.model_copy(
                    update={
                        "public_usd": None,
                        "account_usd": None,
                        "confirmed_usd": None,
                        "basis": "unknown",
                        "assumptions": [
                            *result.cost.assumptions,
                            "Remote work may continue after the deadline; last observed cost is not a final charge.",
                        ],
                    }
                )
        except (ProviderRequestError, httpx.TransportError) as error:
            if job is None or job.status not in {"queued", "running"}:
                raise
            result = (
                job.result.model_copy(deep=True)
                if job.result
                else SearchResult(provider=self.provider.name)
            )
            result.status = "partial"
            result.error = f"Polling interrupted: {type(error).__name__}: {error}"
            result.metadata.update({"resumable": True, "remote_job": job.model_dump(mode="json")})
        return self._finish(context, result, timing, resumed, started)

    @staticmethod
    def _finish(
        context: CallContext,
        result: SearchResult,
        timing: dict[str, Any],
        resumed: bool,
        started: float,
    ) -> SearchResult:
        # A restarted process cannot know the exact completion time of work completed while absent.
        first_submission = context.load("first_submission_at") or timing["started_at"]
        result.latency_seconds = (
            max(0, (datetime.now(UTC) - datetime.fromisoformat(first_submission)).total_seconds())
            if resumed
            else time.monotonic() - started
        )
        result.timing_censored = resumed
        result.metadata["artifact_directory"] = str(context.store.path)
        if not result.metadata.get("resumable"):
            context.checkpoint("search_result", result.model_dump(mode="json"))
        return result

    def search(self, request: SearchRequest) -> SearchResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.asearch(request))
        raise RuntimeError("Use 'await client.asearch(request)' inside an active event loop")

    async def cancel(self, request: SearchRequest, job: SearchJob) -> bool:
        if isinstance(self.provider, JobProvider):
            return await self.provider.cancel(job, self._context(request))
        return False

    async def cleanup(self, request: SearchRequest, job: SearchJob) -> bool:
        if isinstance(self.provider, JobProvider):
            return await self.provider.cleanup(job, self._context(request))
        return False
