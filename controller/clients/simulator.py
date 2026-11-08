"""Async HTTP client for the documented simulator API."""

from __future__ import annotations

import asyncio
import math
import random
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from io import TextIOWrapper
from typing import Any, Self

import httpx

from controller.config import Settings
from controller.errors import (
    AmbiguousPostError,
    SimulatorError,
    SimulatorHTTPError,
)

Sleep = Callable[[float], Awaitable[None]]
RandomValue = Callable[[], float]


class SimulatorClient:
    """Owns one pooled HTTPX client and applies method-aware retry behavior."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        random_value: RandomValue = random.random,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._sleep = sleep
        self._random_value = random_value
        self._client: httpx.AsyncClient | None = None

    @property
    def is_open(self) -> bool:
        return self._client is not None and not self._client.is_closed

    async def __aenter__(self) -> Self:
        if self.is_open:
            raise SimulatorError("simulator client is already open")
        timeout = httpx.Timeout(
            connect=self._settings.connect_timeout_seconds,
            read=self._settings.read_timeout_seconds,
            write=self._settings.write_timeout_seconds,
            pool=self._settings.pool_timeout_seconds,
        )
        limits = httpx.Limits(
            max_connections=self._settings.max_connections,
            max_keepalive_connections=self._settings.max_keepalive_connections,
        )
        self._client = httpx.AsyncClient(
            base_url=self._settings.simulator_url,
            timeout=timeout,
            limits=limits,
            transport=self._transport,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_health(self) -> dict[str, Any]:
        payload = await self._get_json("/health")
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise SimulatorError(
                "simulator health check returned an unhealthy response"
            )
        return payload

    async def get_state(self) -> dict[str, Any]:
        payload = await self._get_json("/sim/state")
        if not isinstance(payload, dict):
            raise SimulatorError("simulator state response must be a JSON object")
        return payload

    async def list_workflows(self) -> list[dict[str, Any]]:
        payload = await self._get_json("/workflows")
        if not isinstance(payload, list) or any(
            not isinstance(item, dict) for item in payload
        ):
            raise SimulatorError(
                "simulator workflows response must be a list of objects"
            )
        return payload

    async def advance(self, minutes: int) -> dict[str, Any]:
        if minutes <= 0 or minutes % 15:
            raise ValueError("advance minutes must be a positive multiple of 15")
        path = "/sim/advance"
        try:
            response = await self._require_client().post(
                path, json={"minutes": minutes}
            )
        except httpx.RequestError as exc:
            raise AmbiguousPostError(
                path, f"transport failure ({type(exc).__name__})"
            ) from None

        if 500 <= response.status_code <= 599:
            raise AmbiguousPostError(
                path,
                f"server returned HTTP {response.status_code}; reconcile simulator state",
            )
        self._raise_for_status(response, path)
        try:
            payload = response.json()
        except ValueError:
            raise AmbiguousPostError(
                path, "successful response was not valid JSON"
            ) from None
        if not isinstance(payload, dict):
            raise AmbiguousPostError(path, "successful response was not a JSON object")
        return payload

    async def get_workflow_config(self, workflow_id: str) -> dict[str, Any]:
        """Read a workflow's current version and full configuration."""
        if not workflow_id:
            raise ValueError("workflow ID is required")
        payload = await self._get_json(f"/workflows/{workflow_id}/config")
        if (
            not isinstance(payload, dict)
            or payload.get("workflow_id") != workflow_id
            or isinstance(payload.get("version"), bool)
            or not isinstance(payload.get("version"), int)
            or payload["version"] < 0
            or not isinstance(payload.get("config"), dict)
        ):
            raise SimulatorError("workflow config response is malformed")
        return payload

    async def get_config_history(self, workflow_id: str) -> list[dict[str, Any]]:
        """Read documented config-history records for a workflow."""
        if not workflow_id:
            raise ValueError("workflow ID is required")
        payload = await self._get_json(f"/workflows/{workflow_id}/config/history")
        if not isinstance(payload, list) or any(
            not isinstance(item, dict) for item in payload
        ):
            raise SimulatorError("workflow config history response is malformed")
        return payload

    async def suggest_fixes(self, workflow_id: str, problem: str) -> dict[str, Any]:
        """Request LLM candidates once; the caller must preflight credits and treat output as untrusted."""
        if not workflow_id or not isinstance(problem, str) or not problem.strip():
            raise ValueError("workflow ID and incident problem are required")
        path = "/llm/suggest-fixes"
        try:
            response = await self._require_client().post(
                path, json={"workflow_id": workflow_id, "problem": problem}
            )
        except httpx.RequestError as exc:
            raise AmbiguousPostError(
                path, f"transport failure ({type(exc).__name__})"
            ) from None
        if 500 <= response.status_code <= 599:
            raise AmbiguousPostError(
                path,
                f"server returned HTTP {response.status_code}; no lookup API is documented",
            )
        self._raise_for_status(response, path)
        try:
            payload = response.json()
        except ValueError:
            raise AmbiguousPostError(
                path, "successful response was not valid JSON"
            ) from None
        return _validate_suggestions(payload)

    async def submit_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one approval request; ambiguous outcomes are never retried here."""
        path = "/approvals"
        try:
            response = await self._require_client().post(path, json=payload)
        except httpx.RequestError as exc:
            raise AmbiguousPostError(
                path, f"transport failure ({type(exc).__name__})"
            ) from None
        if 500 <= response.status_code <= 599:
            raise AmbiguousPostError(
                path,
                f"server returned HTTP {response.status_code}; reconcile approval by proposal ID",
            )
        self._raise_for_status(response, path)
        try:
            result = response.json()
        except ValueError:
            raise AmbiguousPostError(
                path, "successful response was not valid JSON"
            ) from None
        return _validate_approval(result)

    async def list_approvals(
        self, *, workflow_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Read approvals, optionally filtered by workflow and remote status."""
        params: dict[str, str] = {}
        if workflow_id is not None:
            params["workflow_id"] = workflow_id
        if status is not None:
            params["status"] = status
        payload = await self._get_json("/approvals", params=params or None)
        if not isinstance(payload, list):
            raise SimulatorError("approval listing response must be a JSON array")
        return [_validate_approval(item) for item in payload]

    async def get_approval(self, approval_id: str) -> dict[str, Any]:
        """Read the latest remote state of one approval."""
        if not approval_id:
            raise ValueError("approval ID is required")
        return _validate_approval(await self._get_json(f"/approvals/{approval_id}"))

    async def apply_approval(
        self, workflow_id: str, approval_id: str
    ) -> dict[str, Any]:
        """Apply a known approval once; callers reconcile ambiguous outcomes."""
        if not workflow_id or not approval_id:
            raise ValueError("workflow and approval IDs are required")
        path = f"/workflows/{workflow_id}/apply"
        try:
            response = await self._require_client().post(
                path, json={"approval_id": approval_id}
            )
        except httpx.RequestError as exc:
            raise AmbiguousPostError(
                path, f"transport failure ({type(exc).__name__})"
            ) from None
        if 500 <= response.status_code <= 599:
            raise AmbiguousPostError(
                path,
                f"server returned HTTP {response.status_code}; reconcile config history",
            )
        self._raise_for_status(response, path)
        try:
            payload = response.json()
        except ValueError:
            raise AmbiguousPostError(
                path, "successful response was not valid JSON"
            ) from None
        if (
            not isinstance(payload, dict)
            or payload.get("workflow_id") != workflow_id
            or isinstance(payload.get("version"), bool)
            or not isinstance(payload.get("version"), int)
            or payload["version"] < 0
            or not isinstance(payload.get("changed_dims"), list)
            or any(not isinstance(item, str) for item in payload["changed_dims"])
        ):
            raise AmbiguousPostError(
                path, "successful response did not match the documented apply result"
            )
        return payload

    async def list_chunks(self, kind: str, after: int = -1) -> list[dict[str, Any]]:
        """List event descriptors through the documented read endpoint."""
        if kind not in {"events", "history"}:
            raise ValueError("chunk kind must be events or history")
        if isinstance(after, bool) or not isinstance(after, int) or after < -1:
            raise ValueError("chunk cursor must be an integer >= -1")
        payload = await self._get_json(
            "/events/chunks", params={"kind": kind, "after": after}
        )
        if not isinstance(payload, list) or any(
            not isinstance(item, dict) for item in payload
        ):
            raise SimulatorError("chunk listing response must be a list of objects")
        return payload

    @asynccontextmanager
    async def stream_chunk_lines(self, chunk_number: int):
        """Download NDJSON with bounded RAM, then expose its lines incrementally.

        The spool keeps up to 1 MiB in memory and rolls larger downloads to the
        operating system's temporary storage. A failed transfer is retried
        before any partial body is exposed to the parser.
        """
        if (
            isinstance(chunk_number, bool)
            or not isinstance(chunk_number, int)
            or chunk_number < 0
        ):
            raise ValueError("chunk number must be a nonnegative integer")
        client = self._require_client()
        path = f"/events/chunks/{chunk_number}"
        attempts = self._settings.get_retries + 1
        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as body:
            for attempt in range(attempts):
                body.seek(0)
                body.truncate()
                try:
                    async with client.stream("GET", path) as response:
                        if (
                            self._is_retryable_status(response.status_code)
                            and attempt + 1 < attempts
                        ):
                            delay = self._retry_after(response)
                            if delay is None:
                                delay = self._backoff(attempt)
                            await self._sleep(delay)
                            continue
                        self._raise_for_status(response, path)
                        async for block in response.aiter_bytes():
                            body.write(block)
                    break
                except httpx.RequestError as exc:
                    if attempt + 1 == attempts:
                        raise SimulatorError(
                            f"GET {path} failed after {attempts} attempts ({type(exc).__name__})"
                        ) from None
                    await self._sleep(self._backoff(attempt))
            else:
                raise SimulatorError(f"GET {path} exhausted its bounded retry policy")

            body.seek(0)
            with TextIOWrapper(body, encoding="utf-8", newline="") as text_body:
                yield _iter_lines(text_body)

    async def _get_json(
        self, path: str, *, params: dict[str, object] | None = None
    ) -> Any:
        client = self._require_client()
        attempts = self._settings.get_retries + 1
        for attempt in range(attempts):
            try:
                response = await client.get(path, params=params)
            except httpx.RequestError as exc:
                if attempt + 1 == attempts:
                    raise SimulatorError(
                        f"GET {path} failed after {attempts} attempts ({type(exc).__name__})"
                    ) from None
                await self._sleep(self._backoff(attempt))
                continue

            if (
                self._is_retryable_status(response.status_code)
                and attempt + 1 < attempts
            ):
                delay = self._retry_after(response)
                if delay is None:
                    delay = self._backoff(attempt)
                await self._sleep(delay)
                continue
            self._raise_for_status(response, path)
            try:
                return response.json()
            except ValueError:
                raise SimulatorError(f"GET {path} returned invalid JSON") from None

        raise SimulatorError(f"GET {path} exhausted its bounded retry policy")

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            raise SimulatorError(
                "simulator client is not open; use it inside 'async with'"
            )
        return self._client

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code in {408, 429} or 500 <= status_code <= 599

    @staticmethod
    def _raise_for_status(response: httpx.Response, path: str) -> None:
        if response.is_error:
            raise SimulatorHTTPError(
                "GET" if response.request.method == "GET" else "POST",
                path,
                response.status_code,
            )

    def _backoff(self, retry_index: int) -> float:
        ceiling = min(
            self._settings.retry_backoff_seconds * (2**retry_index),
            self._settings.retry_max_backoff_seconds,
        )
        return ceiling * (0.5 + 0.5 * self._random_value())

    def _retry_after(self, response: httpx.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if header is None:
            return None
        try:
            seconds = float(header)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(header)
            except (TypeError, ValueError, OverflowError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = (retry_at - datetime.now(UTC)).total_seconds()
        return min(max(seconds, 0.0), self._settings.retry_max_backoff_seconds)


def _iter_lines(stream: TextIOWrapper) -> Iterator[str]:
    try:
        for line in stream:
            yield line.rstrip("\r\n")
    except UnicodeError as exc:
        raise SimulatorError("chunk response is not valid UTF-8") from exc


_APPROVAL_STATUSES = {
    "pending",
    "approved",
    "rejected",
    "expired",
    "applied",
    "lapsed",
}


def _validate_approval(value: object) -> dict[str, Any]:
    """Validate fields returned by the simulator's documented approval API."""
    required_strings = ("approval_id", "proposal_id", "workflow_id", "status")
    if not isinstance(value, dict) or any(
        not isinstance(value.get(field), str) or not value[field]
        for field in required_strings
    ):
        raise SimulatorError("approval response is missing required identity fields")
    if value["status"] not in _APPROVAL_STATUSES:
        raise SimulatorError("approval response has an unknown status")
    version = value.get("base_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise SimulatorError("approval response has an invalid base_version")
    dimensions = value.get("changed_dims")
    if not isinstance(dimensions, list) or any(
        not isinstance(dimension, str) for dimension in dimensions
    ):
        raise SimulatorError("approval response has invalid changed_dims")
    return value


def _validate_suggestions(value: object) -> dict[str, Any]:
    """Validate the documented suggestion envelope without trusting prompt choices."""
    if not isinstance(value, dict) or set(value) != {
        "suggestions",
        "credits_remaining",
    }:
        raise SimulatorError("suggestion response has an unexpected schema")
    suggestions = value["suggestions"]
    credits = value["credits_remaining"]
    if (
        not isinstance(suggestions, list)
        or len(suggestions) > 3
        or isinstance(credits, bool)
        or not isinstance(credits, (int, float))
        or not math.isfinite(credits)
        or credits < 0
    ):
        raise SimulatorError("suggestion response has invalid candidates or credits")
    clean: list[dict[str, str]] = []
    for item in suggestions:
        if not isinstance(item, dict) or set(item) != {"prompt_version", "summary"}:
            raise SimulatorError("suggestion candidate has an unexpected schema")
        prompt_version, summary = item["prompt_version"], item["summary"]
        if (
            not isinstance(prompt_version, str)
            or not prompt_version.strip()
            or not isinstance(summary, str)
            or not summary.strip()
        ):
            raise SimulatorError("suggestion candidate is malformed")
        clean.append({"prompt_version": prompt_version, "summary": summary})
    return {"suggestions": clean, "credits_remaining": float(credits)}
