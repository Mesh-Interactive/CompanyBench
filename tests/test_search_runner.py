"""Search-stage accounting and recovery through real orchestration, without network calls."""

import asyncio

import httpx
import pytest

from companybench.config import Settings
from companybench.datasets import load_dataset
from companybench.demo import DEMO_TIME, DemoJudge, DemoResearcher
from companybench.models import CompanyCandidate, Cost, SearchJob, SearchResult
from companybench.providers.base import ImmediateProvider, JobProvider
from companybench.runner import (
    _budget,
    grade_run,
    manifest_for,
    metric_rows,
    research_run,
    search_run,
)
from companybench.storage import RunStore
from companybench.transport import ProviderRequestError


def make_store(tmp_path, provider, *, count=3, budget=None, concurrency=1, target=4):
    settings = Settings(
        providers=[provider.name],
        judges=["demo"],
        budget_usd=budget,
        concurrency=concurrency,
        provider_concurrency=concurrency,
        poll_interval=0,
        timeout=10,
        target_count=target,
    )
    store = RunStore(tmp_path)
    store.write(
        "manifest.json",
        manifest_for(load_dataset("development")[:count], settings, reference_time=DEMO_TIME),
    )
    return store, settings


def install_provider(monkeypatch, provider, *, estimate):
    monkeypatch.setattr(
        "companybench.providers.registry.resolve", lambda name, options=None: provider
    )
    monkeypatch.setattr("companybench.pricing.estimate_max_cost", lambda *args, **kwargs: estimate)
    monkeypatch.setattr("companybench.pricing.estimate_cost", lambda *args, **kwargs: Cost())


def states(store):
    return [store.task(task["task_id"]) for task in store.read("manifest.json")["tasks"]]


class ImmediateFixture(ImmediateProvider):
    name = "runner-fixture"

    def __init__(self, cost, *, yield_control=False, candidates=None):
        self.cost = cost
        self.yield_control = yield_control
        self.candidates = candidates or []
        self.calls = []

    async def search(self, request, context):
        self.calls.append(request.task_id)
        if self.yield_control:
            await asyncio.sleep(0)
        return SearchResult(
            provider=self.name,
            cost=Cost(public_usd=self.cost),
            candidates=self.candidates,
        )


@pytest.mark.asyncio
async def test_known_search_cost_replaces_reservation_and_resume_does_not_double_charge(
    tmp_path, monkeypatch
):
    provider = ImmediateFixture(1)
    install_provider(monkeypatch, provider, estimate=2)
    store, settings = make_store(tmp_path, provider, budget=4)
    progress = []

    await search_run(store, progress=progress.append)

    assert len(provider.calls) == 3  # Settled costs release enough room for the next query.
    assert all(state["result"]["cost"]["public_usd"] == 1 for state in states(store))
    assert [row["estimated_spend_usd"] for row in progress] == [1, 2, 3]
    assert _budget(store, settings).reserved == 3
    await search_run(store)
    assert len(provider.calls) == 3
    assert _budget(store, settings).reserved == 3


@pytest.mark.asyncio
async def test_parallel_reservations_block_new_work_until_settlement_then_resume(
    tmp_path, monkeypatch
):
    provider = ImmediateFixture(1, yield_control=True)
    install_provider(monkeypatch, provider, estimate=2)
    store, settings = make_store(tmp_path, provider, budget=4, concurrency=3)

    await search_run(store)

    assert len(provider.calls) == 2
    assert sum(state.get("status") == "not_started" for state in states(store)) == 1
    assert _budget(store, settings).reserved == 2
    await search_run(store)
    assert len(provider.calls) == 3
    assert len(set(provider.calls)) == 3
    assert all(state.get("result") for state in states(store))
    assert _budget(store, settings).reserved == 3


@pytest.mark.asyncio
async def test_unknown_estimate_is_settled_when_search_returns_a_known_cost(tmp_path, monkeypatch):
    provider = ImmediateFixture(1)
    install_provider(monkeypatch, provider, estimate=None)
    store, settings = make_store(tmp_path, provider, budget=2)
    progress = []

    await search_run(store, progress=progress.append)

    assert len(provider.calls) == 2
    assert progress[-1]["estimated_spend_usd"] == 2
    assert progress[-1]["unknown_cost_tasks"] == 0
    assert states(store)[-1]["status"] == "not_started"
    restored = _budget(store, settings)
    assert restored.reserved == 2
    assert restored.unknown_operations == 0


@pytest.mark.asyncio
async def test_unpriced_searches_remain_unknown_across_restart_not_free(tmp_path, monkeypatch):
    provider = ImmediateFixture(None)
    install_provider(monkeypatch, provider, estimate=None)
    store, settings = make_store(tmp_path, provider, budget=0.5)
    progress = []

    await search_run(store, progress=progress.append)

    assert len(provider.calls) == 3  # The soft estimate cannot cap opaque native charges.
    assert progress[-1]["unknown_cost_tasks"] == 3
    assert all(state["result"]["cost"]["public_usd"] is None for state in states(store))
    restored = _budget(store, settings)
    assert restored.unknown_operations == 3
    assert restored.reserved == 0  # Only the known subtotal is zero.
    await search_run(store)
    assert len(provider.calls) == 3
    assert _budget(store, settings).unknown_operations == 3


@pytest.mark.asyncio
async def test_unknown_final_cost_keeps_known_reservation_after_restart(tmp_path, monkeypatch):
    provider = ImmediateFixture(None)
    install_provider(monkeypatch, provider, estimate=2)
    store, settings = make_store(tmp_path, provider, budget=4)

    await search_run(store)

    assert len(provider.calls) == 2
    assert _budget(store, settings).reserved == 4
    assert states(store)[-1]["status"] == "not_started"
    await search_run(store)
    assert len(provider.calls) == 2
    assert _budget(store, settings).reserved == 4
    assert all(
        state["result"]["cost"]["public_usd"] is None
        for state in states(store)
        if state.get("result")
    )


class InterruptedPollingFixture(JobProvider):
    name = "polling-fixture"

    def __init__(self):
        self.submissions = 0
        self.polls = 0

    async def submit(self, request, context):
        self.submissions += 1
        return SearchJob(id="accepted-job", status="queued")

    async def poll(self, request, job, context):
        self.polls += 1
        if self.polls == 2:
            raise ProviderRequestError("Temporary polling failure", 503)
        return SearchJob(
            id=job.id,
            status="running" if self.polls == 1 else "completed",
            result=SearchResult(
                provider=self.name,
                candidates=[CompanyCandidate(position=1, name="Alpha", domain="alpha.example")],
                cost=Cost(public_usd=0.5 if self.polls == 1 else 1),
            ),
        )


@pytest.mark.asyncio
async def test_polling_interruption_saves_last_result_without_terminalizing_or_settling(
    tmp_path, monkeypatch
):
    provider = InterruptedPollingFixture()
    install_provider(monkeypatch, provider, estimate=2)
    store, settings = make_store(tmp_path, provider, count=1, budget=2)
    progress = []

    await search_run(store, progress=progress.append)

    state = states(store)[0]
    assert state["status"] == "interrupted"
    assert "result" not in state
    assert state["last_result"]["metadata"]["resumable"]
    assert state["last_result"]["candidates"][0]["name"] == "Alpha"
    assert state["checkpoints"]["job"]["id"] == "accepted-job"
    assert state["checkpoints"]["job"]["status"] == "running"
    assert progress[-1]["estimated_spend_usd"] == 2
    assert _budget(store, settings).reserved == 2
    with pytest.raises(ValueError, match="Finish the selected search cohort"):
        await research_run(store, researcher=DemoResearcher())

    await search_run(store, progress=progress.append)

    state = states(store)[0]
    assert provider.submissions == 1
    assert provider.polls == 3
    assert state["status"] == "searched"
    assert state["result"]["status"] == "completed"
    assert state["result"]["timing_censored"]
    assert progress[-1]["estimated_spend_usd"] == 1
    assert _budget(store, settings).reserved == 1


class AmbiguousSubmissionFixture(ImmediateProvider):
    name = "ambiguous-fixture"

    def __init__(self):
        self.requests_sent = 0

    async def search(self, request, context):
        def uncertain(native_request):
            self.requests_sent += 1
            raise httpx.ReadTimeout("Synthetic lost paid response", request=native_request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(uncertain)) as client:
            context.client = client
            await context.post(
                "https://mock.example/search",
                operation="submit",
                billable=True,
                json={"query": request.query.query},
            )
        raise AssertionError("The synthetic transport must fail before a result")


@pytest.mark.asyncio
async def test_ambiguous_acceptance_never_repeats_native_submission_on_resume(
    tmp_path, monkeypatch
):
    provider = AmbiguousSubmissionFixture()
    install_provider(monkeypatch, provider, estimate=1)
    store, settings = make_store(tmp_path, provider, count=1, budget=1)

    await search_run(store)
    first = states(store)[0]
    operation_path = store.operation_file(first["task_id"], "submit")
    original_operation = store.read(operation_path)
    assert first["status"] == "submission_unknown"
    assert "result" not in first
    assert original_operation["status"] == "acceptance_unknown"

    await search_run(store)

    assert provider.requests_sent == 1
    assert states(store)[0]["status"] == "submission_unknown"
    assert "result" not in states(store)[0]
    assert store.read(operation_path) == original_operation
    assert _budget(store, settings).reserved == 1
    with pytest.raises(ValueError, match="Finish the selected search cohort"):
        await research_run(store, researcher=DemoResearcher())


@pytest.mark.asyncio
async def test_bad_native_positions_cannot_overwrite_identities_or_backfill_credit(
    tmp_path, monkeypatch
):
    provider = ImmediateFixture(
        1,
        candidates=[
            CompanyCandidate(position=1, name="Alpha", domain="alpha.example"),
            CompanyCandidate(position=1, name="Beta", domain="beta.example"),
            CompanyCandidate(position=7, name="Gamma", domain="gamma.example"),
            CompanyCandidate(position=4, name="Alpha LLC", domain="alpha.example"),
            CompanyCandidate(position=5, name="Delta", domain="delta.example"),
        ],
    )
    install_provider(monkeypatch, provider, estimate=1)
    store, _ = make_store(tmp_path, provider, count=1, target=4)

    await search_run(store)
    version = await research_run(store, researcher=DemoResearcher())
    await grade_run(store, judge_instances={"demo": DemoJudge()})

    task = states(store)[0]
    identities = store.read(f"evidence/{version}/identities.json")[task["task_id"]]
    assert set(identities) == {"1", "4"}
    assert identities["1"]["name"] == "Alpha"
    assert identities["1"]["id"] == identities["4"]["id"]
    assert len(store.read(f"evidence/{version}/packets.json")) == 1
    row = metric_rows(store)[0]
    assert row["returned_companies"] == 4
    assert row["valid_companies"] == row["duplicates"] == 1
    assert row["malformed"] == 2
    assert row["precision"] == 0.25
    assert row["quality_score"] == 25
    assert [slot["native_position"] for slot in row["slots"]] == [1, 1, 7, 4]
    assert [slot["category"] for slot in row["slots"]] == [
        "valid_companies",
        "malformed",
        "malformed",
        "duplicates",
    ]
