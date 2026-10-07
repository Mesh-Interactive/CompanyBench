"""Crash and acceptance behavior on paid operations."""

import httpx
import pytest

from companybench.storage import RunStore
from companybench.transport import AcceptanceUnknown, CallContext


@pytest.mark.asyncio
async def test_paid_response_reused_without_second_call(tmp_path):
    """A completed paid operation is replayed locally after restarting the context."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"id": "accepted"})

    store = RunStore(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        context = CallContext(store, "task", client=client)
        first = await context.post(
            "https://example.com/jobs", operation="submit", billable=True, json={"q": "a"}
        )
        second = await CallContext(store, "task", client=client).post(
            "https://example.com/jobs", operation="submit", billable=True, json={"q": "a"}
        )
    assert first == second == {"id": "accepted"}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_uncertain_paid_request_is_not_repeated(tmp_path):
    """A lost response without idempotency must not cause a second paid request."""
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("response lost", request=request)

    store = RunStore(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        context = CallContext(store, "task", client=client)
        with pytest.raises(AcceptanceUnknown):
            await context.post(
                "https://example.com/jobs", operation="submit", billable=True, json={}
            )
        with pytest.raises(AcceptanceUnknown):
            await context.post(
                "https://example.com/jobs", operation="submit", billable=True, json={}
            )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_idempotency_key_survives_lost_response(tmp_path):
    """Safe retries reuse the same idempotency key instead of creating a new operation."""
    keys = []

    def handler(request):
        keys.append(request.headers["idempotency-key"])
        if len(keys) == 1:
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(200, json={"id": "same-job"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        context = CallContext(RunStore(tmp_path), "task", client=client)
        with pytest.raises(AcceptanceUnknown):
            await context.post(
                "https://example.com",
                operation="submit",
                billable=True,
                json={},
                idempotency_header="Idempotency-Key",
            )
        result = await context.post(
            "https://example.com",
            operation="submit",
            billable=True,
            json={},
            idempotency_header="Idempotency-Key",
        )
    assert result["id"] == "same-job"
    assert keys[0] == keys[1]


def test_atomic_state_and_run_lock(tmp_path):
    """A concurrent process cannot mutate the same run and checkpoints round-trip."""
    store = RunStore(tmp_path)
    store.write("state.json", {"a": [1, 2]})
    assert store.read("state.json") == {"a": [1, 2]}
    with store.lock():
        with pytest.raises(Exception, match="lock"):
            with RunStore(tmp_path).lock():
                pass
