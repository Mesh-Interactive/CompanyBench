"""Malformed paid responses and expired idempotency contracts never resubmit."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from companybench.storage import RunStore
from companybench.transport import AcceptanceUnknown, Budget, CallContext, ProviderRequestError


@pytest.mark.asyncio
@pytest.mark.parametrize("body", ["upstream HTML", '{"cost":NaN}', '[{"id":"job"}]'])
async def test_accepted_unusable_body_is_durable_and_not_repeated(tmp_path, body):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = RunStore(tmp_path)
        for _ in range(2):
            with pytest.raises(ProviderRequestError):
                await CallContext(store, "task", client=client).post(
                    "https://example.com/jobs", operation="submit", billable=True, json={}
                )
    assert len(calls) == 1
    assert store.read(store.operation_file("task", "submit"))["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["header", "lifetime"])
async def test_resume_cannot_change_idempotency_contract(tmp_path, change):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("lost response", request=request)

    store = RunStore(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        context = CallContext(store, "task", client=client)
        with pytest.raises(AcceptanceUnknown):
            await context.post(
                "https://example.com/jobs",
                operation="submit",
                billable=True,
                json={},
                idempotency_header="Idempotency-Key",
                idempotency_ttl_seconds=10,
            )
        if change == "lifetime":
            relative = store.operation_file("task", "submit")
            state = store.read(relative)
            state["created_at"] = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
            store.write(relative, state)
        with pytest.raises(AcceptanceUnknown):
            await context.post(
                "https://example.com/jobs",
                operation="submit",
                billable=True,
                json={},
                idempotency_header="Other-Key" if change == "header" else "Idempotency-Key",
                idempotency_ttl_seconds=3600,
            )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cleanup_accepts_empty_204_response(tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(204))
    ) as client:
        context = CallContext(RunStore(tmp_path), "task", client=client)
        assert (
            await context.request("DELETE", "https://example.com/jobs/id", operation="delete") == {}
        )


@pytest.mark.parametrize("amount", [float("nan"), float("inf"), -1])
def test_budget_rejects_nonfinite_or_negative_limits_and_reservations(amount):
    with pytest.raises(ValueError):
        Budget(amount)
    with pytest.raises(ValueError):
        Budget().reserve(amount)
