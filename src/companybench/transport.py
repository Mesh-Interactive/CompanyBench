"""HTTP transport that records paid request intent before sending it."""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from companybench.storage import RunStore, fingerprint, now_iso


class AcceptanceUnknown(RuntimeError):
    """The server might have accepted a paid request whose response was not saved."""


class ReplayUnavailable(AcceptanceUnknown):
    """Durable recovery needs an unavailable response; no network request was sent."""


class ProviderRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    """A soft estimate; unknown charges and already accepted work remain visible."""

    def __init__(self, limit: float | None = None) -> None:
        if limit is not None and (not math.isfinite(limit) or limit < 0):
            raise ValueError("Budget must be a finite nonnegative dollar amount")
        self.limit = limit
        self.reserved = 0.0
        self.unknown_operations = 0

    def reserve(self, estimate: float | None) -> None:
        if estimate is not None and (not math.isfinite(estimate) or estimate < 0):
            raise ValueError("Cost reservations must be finite and nonnegative")
        if self.limit is not None and self.reserved >= self.limit:
            raise BudgetExceeded("Soft estimated budget reached; no new paid work started")
        if estimate is None:
            self.unknown_operations += 1
            return
        if self.limit is not None and self.reserved + estimate > self.limit:
            raise BudgetExceeded("Next operation exceeds the remaining estimated budget")
        self.reserved += estimate


class CallContext:
    """Per-task HTTP and checkpoints shared by all adapters.

    Completed billable operations return their saved response on resume. Uncertain operations
    can only be resent with a still-valid provider idempotency key. GETs are always refreshed.
    No authorization headers are stored.
    """

    def __init__(
        self,
        store: RunStore,
        task_id: str,
        *,
        client: httpx.AsyncClient | None = None,
        budget: Budget | None = None,
    ) -> None:
        self.store = store
        self.task_id = task_id
        self.client = client
        self.budget = budget
        self.live_started = time.monotonic()
        self.network_seconds = 0.0
        self.replay_only = False

    def checkpoint(self, key: str, value: Any) -> None:
        self.store.checkpoint(self.task_id, key, value)

    def load(self, key: str, default: Any = None) -> Any:
        return self.store.task(self.task_id).get("checkpoints", {}).get(key, default)

    async def get(self, url: str, *, operation: str = "get", **kwargs: Any) -> dict[str, Any]:
        return await self.request("GET", url, operation=operation, **kwargs)

    async def post(self, url: str, *, operation: str, **kwargs: Any) -> dict[str, Any]:
        return await self.request("POST", url, operation=operation, **kwargs)

    async def request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        billable: bool = False,
        json: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_header: str | None = None,
        idempotency_ttl_seconds: int = 86400,
        estimated_usd: float | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        method = method.upper()
        if not math.isfinite(idempotency_ttl_seconds) or idempotency_ttl_seconds <= 0:
            raise ValueError("Idempotency lifetime must be positive")
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("HTTP timeout must be finite and positive")
        if billable and any(key in kwargs for key in {"data", "content", "files"}):
            raise ValueError(
                "Journaled paid requests require a JSON body; data/content/files are unsupported"
            )
        semantic_headers = {
            key.casefold(): value
            for key, value in (headers or {}).items()
            if key.casefold() not in {"authorization", "x-api-key", "api-key", "x-goog-api-key"}
            and (idempotency_header is None or key.casefold() != idempotency_header.casefold())
        }
        request_hash = fingerprint(
            {
                "method": method,
                "url": url,
                "json": json,
                "params": params,
                "headers": semantic_headers,
            }
        )
        relative = self.store.operation_file(self.task_id, operation)
        state = self.store.read(relative)
        outgoing_headers = dict(headers or {})
        if billable:
            if state and state.get("status") not in {
                "completed",
                "rejected",
                "intent",
                "acceptance_unknown",
            }:
                raise ValueError(
                    "Saved operation has an unrecognized state; automatic repeat is unsafe"
                )
            if state and state["request_hash"] != request_hash:
                raise ValueError(
                    "Saved operation has a different request; use a new run or operation"
                )
            if state and state["status"] == "completed":
                if state.get("response_error"):
                    raise ProviderRequestError(state["response_error"], state.get("status_code"))
                return self._object(state["response"])
            if state and state["status"] == "rejected":
                raise ProviderRequestError(state["error"], state.get("status_code"))
            if self.replay_only:
                raise ReplayUnavailable(
                    f"Deadline recovery requires an unsaved response for {operation}; reconcile saved work before finalizing"
                )
            if state and state["status"] in {"intent", "acceptance_unknown"}:
                age = (
                    datetime.now(UTC) - datetime.fromisoformat(state["created_at"])
                ).total_seconds()
                original_header = state.get("idempotency_header")
                ttl = min(idempotency_ttl_seconds, state.get("idempotency_ttl_seconds", 0))
                if (
                    not state.get("idempotency_key")
                    or not idempotency_header
                    or original_header != idempotency_header.casefold()
                    or age < 0
                    or age >= ttl
                ):
                    raise AcceptanceUnknown(
                        f"Submission status unknown for {operation}; automatic repeat is unsafe"
                    )
            if not state:
                if self.budget:
                    self.budget.reserve(estimated_usd)
                state = {
                    "task_id": self.task_id,
                    "operation": operation,
                    "request_hash": request_hash,
                    "method": method,
                    "url": url,
                    "created_at": now_iso(),
                    "status": "intent",
                    "idempotency_key": uuid.uuid4().hex if idempotency_header else None,
                    "idempotency_header": idempotency_header.casefold()
                    if idempotency_header
                    else None,
                    "idempotency_ttl_seconds": idempotency_ttl_seconds,
                    "estimated_usd": estimated_usd,
                }
                self.store.write(relative, state)
                if not self.load("first_submission_at"):
                    self.checkpoint("first_submission_at", now_iso())
                self.store.event(
                    "request_intent",
                    task_id=self.task_id,
                    operation=operation,
                    request_hash=request_hash,
                    estimated_usd=estimated_usd,
                )
            if idempotency_header:
                outgoing_headers[idempotency_header] = state["idempotency_key"]
        elif self.replay_only:
            raise ReplayUnavailable(
                f"Deadline recovery would require network access for {operation}; reconcile saved work before finalizing"
            )

        async def send(client: httpx.AsyncClient) -> httpx.Response:
            return await client.request(
                method,
                url,
                json=json,
                headers=outgoing_headers,
                params=params,
                timeout=timeout if timeout is not None else 120.0,
                **kwargs,
            )

        started = time.monotonic()
        try:
            if self.client:
                response = await send(self.client)
            else:
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    response = await send(client)
            if not 200 <= response.status_code < 300:
                message = f"Provider HTTP {response.status_code}: {response.text[:1000]}"
                if billable and state:
                    uncertain = response.status_code >= 500 or response.status_code in {408, 409}
                    state.update(
                        status="acceptance_unknown" if uncertain else "rejected",
                        error=message,
                        status_code=response.status_code,
                    )
                    self.store.write(relative, state)
                    if uncertain:
                        raise AcceptanceUnknown(message)
                raise ProviderRequestError(message, response.status_code)
            try:
                data = {} if response.status_code == 204 else response.json()
                fingerprint(
                    data
                )  # Reject non-finite JSON numbers before saving a completed charge.
            except ValueError as error:
                if billable and state:
                    state.update(
                        status="completed",
                        response={"_non_json_response": response.text[:10000]},
                        response_error="Provider returned a non-JSON response",
                        status_code=response.status_code,
                        completed_at=now_iso(),
                        request_id=response.headers.get("x-request-id"),
                    )
                    self.store.write(relative, state)
                raise ProviderRequestError("Provider returned a non-JSON response") from error
            if billable and state:
                state.update(
                    status="completed",
                    response=data,
                    completed_at=now_iso(),
                    request_id=response.headers.get("x-request-id"),
                )
                self.store.write(relative, state)
            else:
                self.store.append(
                    "http.jsonl",
                    {
                        "at": now_iso(),
                        "task_id": self.task_id,
                        "operation": operation,
                        "method": method,
                        "url": url,
                        "response": data,
                    },
                )
            return self._object(data)
        except (httpx.TransportError, asyncio.CancelledError) as error:
            if billable and state:
                state.update(status="acceptance_unknown", error=type(error).__name__)
                self.store.write(relative, state)
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise AcceptanceUnknown(
                    f"Submission status unknown for {operation}: {type(error).__name__}"
                ) from error
            raise
        finally:
            self.network_seconds += time.monotonic() - started

    @staticmethod
    def _object(data: Any) -> dict[str, Any]:
        if not isinstance(data, dict) or "_non_json_response" in data:
            raise ProviderRequestError("Provider response must be a JSON object")
        return data
