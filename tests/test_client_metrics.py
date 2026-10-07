"""Provider orchestration and metric arithmetic."""

from datetime import UTC, datetime

import pytest

from companybench.client import SearchClient
from companybench.metrics import calculate_metrics
from companybench.models import (
    CompanyCandidate,
    CompanyIdentity,
    CompanyJudgment,
    Cost,
    Criterion,
    Query,
    SearchJob,
    SearchRequest,
    SearchResult,
)
from companybench.providers.base import JobProvider
from companybench.storage import RunStore
from companybench.transport import CallContext


def request():
    query = Query(
        id="T1",
        index=1,
        query="Find US manufacturers",
        family="profile",
        complexity="L1",
        industry="Manufacturing",
        conditions=[Criterion(id="fit", description="US manufacturer")],
    )
    return SearchRequest(
        query=query, target_count=5, reference_time=datetime.now(UTC), task_id="T1:fake:1"
    )


class PollingFake(JobProvider):
    name = "fake"

    async def submit(self, request, context):
        context.checkpoint("submit_count", (context.load("submit_count") or 0) + 1)
        return SearchJob(id="remote", status="running")

    async def poll(self, request, job, context):
        return SearchJob(
            id=job.id,
            status="completed",
            result=SearchResult(
                provider=self.name,
                candidates=[CompanyCandidate(position=1, name="Example", domain="example.com")],
            ),
        )


@pytest.mark.asyncio
async def test_polling_result_and_completed_resume(tmp_path):
    """Resuming completed work must not submit the remote job again."""
    context = CallContext(RunStore(tmp_path), "T1:fake:1")
    client = SearchClient(PollingFake(), context=context, poll_interval=0)
    query_request = request()
    first = await client.asearch(query_request)
    second = await SearchClient(PollingFake(), context=context, poll_interval=0).asearch(
        query_request
    )
    assert first.candidates == second.candidates
    assert context.load("submit_count") == 1


def test_duplicate_and_malformed_results_reduce_precision():
    """Returned slots cannot disappear from the precision denominator."""
    result = SearchResult(
        provider="fake",
        candidates=[
            CompanyCandidate(position=1, name="A"),
            CompanyCandidate(position=2, name="A"),
            CompanyCandidate(position=3, malformed=True),
            CompanyCandidate(position=4, name="B"),
        ],
        cost=Cost(public_usd=2),
        latency_seconds=10,
    )
    identities = {
        1: CompanyIdentity(id="a", name="A"),
        2: CompanyIdentity(id="a", name="A"),
        4: CompanyIdentity(id="b", name="B"),
    }
    judgments = {
        "a": CompanyJudgment(query_id="T1", entity_id="a", judge="llm", verdict="valid"),
        "b": CompanyJudgment(query_id="T1", entity_id="b", judge="llm", verdict="unknown"),
    }
    values = calculate_metrics(request(), result, identities, judgments)
    assert values["valid_companies"] == 1
    assert values["precision"] == 0.25
    assert values["quality_score"] == pytest.approx(200 / 9)
    assert values["cost_per_valid_company"] == 2
    assert values["duplicates"] == 1
    assert values["malformed"] == 1


def test_judge_error_is_incomplete_not_invalid():
    """Judge transport failures cannot be counted as factual company errors."""
    result = SearchResult(provider="fake", candidates=[CompanyCandidate(position=1, name="A")])
    identity = CompanyIdentity(id="a", name="A")
    judgment = CompanyJudgment(
        query_id="T1", entity_id="a", judge="llm", verdict="error", error="refusal"
    )
    values = calculate_metrics(request(), result, {1: identity}, {"a": judgment})
    assert not values["grading_complete"]
    assert values["invalid_companies"] == 0
    assert values["quality_score"] is None
