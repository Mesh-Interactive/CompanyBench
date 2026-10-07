"""A deadline does not turn an unconfirmed paid acceptance into a scored empty list."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from companybench.client import SearchClient
from companybench.datasets import load_dataset
from companybench.models import CompanyCandidate, Cost, SearchJob, SearchRequest, SearchResult
from companybench.providers.base import ImmediateProvider
from companybench.storage import RunStore
from companybench.transport import AcceptanceUnknown, CallContext, ReplayUnavailable


class SlowPaidProvider(ImmediateProvider):
    async def search(self, request, context):
        await context.post("https://example.org/paid", operation="submit", billable=True)
        pytest.fail("The simulated paid response should still be pending at the deadline")


@pytest.mark.asyncio
async def test_deadline_during_a_paid_submission_preserves_unknown_acceptance(tmp_path):
    async def respond(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    store = RunStore(tmp_path)
    request = SearchRequest(
        query=load_dataset("development")[0], reference_time=datetime.now(UTC), task_id="test"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        context = CallContext(store, "test", client=http)
        with pytest.raises(AcceptanceUnknown, match="deadline"):
            await SearchClient(SlowPaidProvider(), context=context, timeout=0.02).asearch(request)
    assert store.read(store.operation_file("test", "submit"))["status"] == "acceptance_unknown"
    assert not context.load("search_result")


class CachedPaidProvider(ImmediateProvider):
    async def search(self, request, context):
        response = await context.post("https://example.org/paid", operation="submit", billable=True)
        return SearchResult(
            provider=self.name,
            candidates=[CompanyCandidate(position=1, name=response["company"])],
            cost=Cost(public_usd=1),
        )


def expire(context):
    earlier = datetime.now(UTC) - timedelta(hours=3)
    context.checkpoint(
        "search_timing",
        {
            "started_at": earlier.isoformat(),
            "deadline_at": (earlier + timedelta(hours=2)).isoformat(),
        },
    )


@pytest.mark.asyncio
async def test_completed_paid_response_recovers_after_deadline_without_network(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"company": "Saved company"})

    store = RunStore(tmp_path)
    request = SearchRequest(
        query=load_dataset("development")[0], reference_time=datetime.now(UTC), task_id="test"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        context = CallContext(store, "test", client=http)
        provider = CachedPaidProvider()
        await provider.search(request, context)  # Crash before saving the completed job.
        expire(context)
        result = await SearchClient(provider, context=context).asearch(request)
    assert len(calls) == 1
    assert result.status == "completed"
    assert result.candidates[0].name == "Saved company"
    assert result.cost.public_usd == 1
    assert result.timing_censored
    assert context.load("search_result")["status"] == "completed"


@pytest.mark.asyncio
async def test_saved_terminal_job_survives_elapsed_deadline(tmp_path):
    context = CallContext(RunStore(tmp_path), "test")
    expire(context)
    job = SearchJob(
        id="saved",
        status="completed",
        result=SearchResult(
            provider="saved", candidates=[CompanyCandidate(position=1, name="Saved company")]
        ),
    )
    context.checkpoint("job", job.model_dump(mode="json"))
    request = SearchRequest(
        query=load_dataset("development")[0], reference_time=datetime.now(UTC), task_id="test"
    )
    result = await SearchClient(SlowPaidProvider(), context=context).asearch(request)
    assert result.status == "completed"
    assert len(result.candidates) == 1
    assert result.timing_censored


class MissingContinuation(CachedPaidProvider):
    async def search(self, request, context):
        result = await super().search(request, context)
        await context.post("https://example.org/next", operation="continue", billable=True)
        return result


@pytest.mark.asyncio
async def test_deadline_replay_cannot_send_unsaved_continuation(tmp_path):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"company": "Saved company"})

    store = RunStore(tmp_path)
    request = SearchRequest(
        query=load_dataset("development")[0], reference_time=datetime.now(UTC), task_id="test"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        context = CallContext(store, "test", client=http)
        await CachedPaidProvider().search(request, context)
        expire(context)
        with pytest.raises(ReplayUnavailable, match="unsaved response"):
            await SearchClient(MissingContinuation(), context=context).asearch(request)
    assert len(calls) == 1
    assert not context.load("search_result")
    assert not store.read(store.operation_file("test", "continue"))
    assert not context.replay_only


@pytest.mark.asyncio
async def test_expired_unconfirmed_submission_is_not_converted_to_a_completed_empty_list(tmp_path):
    store = RunStore(tmp_path)
    context = CallContext(store, "test")
    earlier = datetime.now(UTC) - timedelta(hours=3)
    context.checkpoint(
        "search_timing",
        {
            "started_at": earlier.isoformat(),
            "deadline_at": (earlier + timedelta(hours=2)).isoformat(),
        },
    )
    store.write(store.operation_file("test", "submit"), {"status": "acceptance_unknown"})
    request = SearchRequest(
        query=load_dataset("development")[0], reference_time=datetime.now(UTC), task_id="test"
    )
    with pytest.raises(AcceptanceUnknown, match="deadline"):
        await SearchClient(SlowPaidProvider(), context=context).asearch(request)
    assert not context.load("search_result")
