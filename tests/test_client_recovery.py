"""Client isolation and deadlines survive multiple queries and process restarts."""

from datetime import UTC, datetime, timedelta

import pytest

from companybench.client import SearchClient
from companybench.models import (
    CompanyCandidate,
    Cost,
    Criterion,
    Query,
    SearchJob,
    SearchRequest,
    SearchResult,
)
from companybench.providers.base import ImmediateProvider, JobProvider
from companybench.storage import RunStore
from companybench.transport import CallContext, ProviderRequestError


def make_request(identifier="one"):
    return SearchRequest(
        task_id=identifier,
        reference_time=datetime(2026, 10, 6, tzinfo=UTC),
        query=Query(
            id=identifier,
            index=1,
            query=f"Find {identifier}",
            complexity="L1",
            industry="Software",
            family="firmographic",
            conditions=[Criterion(id="fit", description="Matches the query")],
        ),
    )


class Echo(ImmediateProvider):
    name = "echo"

    def __init__(self):
        self.calls = []

    async def search(self, request, context):
        self.calls.append(request.query.id)
        return SearchResult(provider=self.name, metadata={"query_id": request.query.id})


@pytest.mark.asyncio
async def test_one_client_isolates_distinct_queries_and_replays_same_query(tmp_path):
    provider = Echo()
    client = SearchClient(provider, run_dir=str(tmp_path))
    first = await client.asearch(make_request("one"))
    second = await client.asearch(make_request("two"))
    replayed = await client.asearch(make_request("one"))
    assert first.metadata["query_id"] == replayed.metadata["query_id"] == "one"
    assert second.metadata["query_id"] == "two"
    assert provider.calls == ["one", "two"]


@pytest.mark.asyncio
async def test_reused_task_id_with_changed_query_rejects_before_returning_cache(tmp_path):
    provider = Echo()
    context = CallContext(RunStore(tmp_path), "same-task")
    client = SearchClient(provider, context=context)
    await client.asearch(make_request())
    changed = make_request().model_copy(update={"target_count": 12})
    with pytest.raises(ValueError, match="different request"):
        await client.asearch(changed)
    assert provider.calls == ["one"]


@pytest.mark.asyncio
async def test_expired_remote_job_deadline_does_not_reset_on_resume(tmp_path):
    class Remote(JobProvider):
        name = "remote"

        async def submit(self, request, context):
            raise AssertionError("An expired run must not submit again")

        async def poll(self, request, job, context):
            raise AssertionError("An expired run must not receive a fresh polling budget")

    context = CallContext(RunStore(tmp_path), "one")
    started = datetime.now(UTC) - timedelta(seconds=20)
    context.checkpoint("first_submission_at", started.isoformat())
    partial = SearchResult(
        provider="remote", cost=Cost(public_usd=1, account_usd=0.8, confirmed_usd=0.7)
    )
    context.checkpoint(
        "job", SearchJob(id="remote-id", status="running", result=partial).model_dump(mode="json")
    )
    result = await SearchClient(Remote(), context=context, timeout=10).asearch(make_request())
    assert result.status == "partial"
    assert result.metadata["deadline_reached"]
    assert result.metadata["remote_job"]["id"] == "remote-id"
    assert result.timing_censored
    assert result.latency_seconds >= 20
    assert result.metadata["cost_unsettled"]
    assert result.metadata["observed_cost_at_deadline"]["public_usd"] == 1
    assert result.cost.public_usd is result.cost.account_usd is result.cost.confirmed_usd is None


@pytest.mark.asyncio
async def test_persisted_deadline_cannot_be_extended_with_new_client_timeout(tmp_path):
    context = CallContext(RunStore(tmp_path), "one")
    started = datetime.now(UTC) - timedelta(seconds=20)
    context.checkpoint(
        "search_timing",
        {
            "started_at": started.isoformat(),
            "deadline_at": (started + timedelta(seconds=10)).isoformat(),
            "timeout_seconds": 10,
        },
    )
    provider = Echo()
    result = await SearchClient(provider, context=context, timeout=7200).asearch(make_request())
    assert result.metadata["deadline_reached"]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_provider_timeout_is_not_relabelled_as_harness_deadline(tmp_path):
    class ProviderTimeout(ImmediateProvider):
        async def search(self, request, context):
            raise TimeoutError("provider-specific timeout")

    with pytest.raises(TimeoutError, match="provider-specific"):
        await SearchClient(ProviderTimeout(), run_dir=str(tmp_path), timeout=7200).asearch(
            make_request()
        )


@pytest.mark.asyncio
async def test_transient_poll_error_keeps_partial_results_and_native_job_resumable(tmp_path):
    class Polling(JobProvider):
        name = "remote"

        def __init__(self):
            self.polls = 0
            self.submissions = 0

        async def submit(self, request, context):
            self.submissions += 1
            return SearchJob(id="remote-id", status="running")

        async def poll(self, request, job, context):
            self.polls += 1
            if self.polls == 2:
                raise ProviderRequestError("temporarily unavailable", 503)
            result = SearchResult(
                provider=self.name,
                candidates=[CompanyCandidate(position=1, name="A")],
                cost=Cost(public_usd=1.5),
            )
            return SearchJob(
                id=job.id, status="running" if self.polls == 1 else "completed", result=result
            )

    context = CallContext(RunStore(tmp_path), "one")
    provider = Polling()
    client = SearchClient(provider, context=context, poll_interval=0)
    partial = await client.asearch(make_request())
    assert partial.metadata["resumable"]
    assert partial.candidates[0].name == "A"
    assert partial.cost.public_usd == 1.5
    assert context.load("search_result") is None
    completed = await client.asearch(make_request())
    assert completed.status == "completed"
    assert provider.submissions == 1
    assert provider.polls == 3
