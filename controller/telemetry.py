"""Bounded in-process runtime metrics for load and operations reporting."""

from __future__ import annotations

import math
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_WORKFLOW_ROUTES = {
    "config/history": "/workflows/{workflow_id}/config/history",
    "config": "/workflows/{workflow_id}/config",
    "apply": "/workflows/{workflow_id}/apply",
}


@dataclass(slots=True)
class _Histogram:
    count: int = 0
    total: float = 0.0
    maximum: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * len(_LATENCY_BUCKETS))
    overflow: int = 0

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.maximum = max(self.maximum, value)
        for index, bound in enumerate(_LATENCY_BUCKETS):
            if value <= bound:
                self.buckets[index] += 1
                break
        else:
            self.overflow += 1

    def export(self) -> dict[str, Any]:
        cumulative = []
        running = 0
        for count in self.buckets:
            running += count
            cumulative.append(running)
        return {
            "count": self.count,
            "sum_seconds": self.total,
            "max_seconds": self.maximum,
            "le_seconds": list(_LATENCY_BUCKETS),
            "buckets_le": cumulative,
            "+Inf": self.count,
        }


class RuntimeTelemetry:
    """Collect bounded counters, histograms and the latest runtime gauges."""

    def __init__(self) -> None:
        self._started = time.perf_counter()
        self._counters: Counter[str] = Counter()
        self._api: dict[str, dict[str, Any]] = {}
        self._histograms: dict[str, _Histogram] = {}
        self._latest: dict[str, Any] = {}

    def increment(self, name: str, amount: int = 1) -> None:
        if not name or isinstance(amount, bool) or not isinstance(amount, int):
            raise ValueError("metric name and integer increment are required")
        if amount < 0:
            raise ValueError("counter increments cannot be negative")
        self._counters[name] += amount

    def observe(self, name: str, seconds: float) -> None:
        if not name or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("metric name and finite nonnegative duration are required")
        self._histograms.setdefault(name, _Histogram()).observe(seconds)

    def observe_api(
        self, method: str, path: str, status_code: int | None, seconds: float
    ) -> None:
        route = _route(path)
        key = f"{method.upper()} {route}"
        item = self._api.setdefault(
            key,
            {
                "requests": 0,
                "failures": 0,
                "retryable_responses": 0,
                "status_codes": Counter(),
            },
        )
        item["requests"] += 1
        if status_code is None or status_code >= 400:
            item["failures"] += 1
        if status_code is not None:
            item["status_codes"][str(status_code)] += 1
            if status_code in {408, 429} or status_code >= 500:
                item["retryable_responses"] += 1
        self.observe("simulator_api_request_seconds", seconds)

    def record_ingestion(
        self, *, chunk: int, observed_events: int, inserted_events: int, seconds: float
    ) -> None:
        values = (chunk, observed_events, inserted_events)
        if any(
            isinstance(value, bool) or not isinstance(value, int) for value in values
        ):
            raise ValueError("ingestion values must be integers")
        if min(chunk, observed_events, inserted_events) < 0:
            raise ValueError("ingestion values cannot be negative")
        if inserted_events > observed_events:
            raise ValueError("inserted events cannot exceed observed events")
        self.increment("chunks_committed")
        self.increment("events_observed", observed_events)
        self.increment("events_inserted", inserted_events)
        self.observe("chunk_ingestion_seconds", seconds)
        self._latest["checkpoint_chunk"] = chunk
        self._latest["last_chunk_events"] = observed_events

    def set_pending_chunks(self, count: int) -> None:
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("pending chunk count must be a nonnegative integer")
        self._latest["pending_chunks"] = count

    def record_state(self, state: dict[str, Any]) -> None:
        for source, target in (
            ("sim_minute", "simulated_minute"),
            ("ts", "simulator_timestamp"),
            ("chunks", "simulator_chunks"),
            ("last_event_id", "simulator_last_event_id"),
            ("finished", "finished"),
            ("credits_remaining", "credits_remaining"),
        ):
            value = state.get(source)
            if value is not None:
                self._latest[target] = value

    def record_initial_credits(self, state: dict[str, Any]) -> None:
        value = state.get("credits_remaining")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            self._latest["credits_initial"] = value
            self._latest["credits_remaining"] = value

    def record_fact_processing(
        self, *, scanned: int, inserted: int, seconds: float
    ) -> None:
        self.increment("fact_source_events_scanned", scanned)
        self.increment("facts_inserted", inserted)
        self.observe("fact_processing_seconds", seconds)

    def record_decision(self) -> None:
        """Count a decision successfully persisted by an instrumented evaluator."""
        self.increment("decisions_persisted")

    def set_database_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._latest["database"] = snapshot

    def export(self) -> dict[str, Any]:
        elapsed = max(time.perf_counter() - self._started, 1e-9)
        initial = self._latest.get("credits_initial")
        remaining = self._latest.get("credits_remaining")
        database = self._latest.get("database", {})
        decisions = self._counters["decisions_persisted"]
        outcomes = {
            "pickup_rate": _ratio(database.get("connected"), database.get("dials")),
            "engagement_rate": _ratio(
                database.get("concluded"), database.get("connected")
            ),
            "business_outcome_rate": _ratio(
                database.get("concluded"), database.get("uploaded")
            ),
        }
        simulated_lag_minutes = _simulated_lag_minutes(
            self._latest.get("simulator_timestamp"), database.get("latest_fact_at")
        )
        stored_at = database.get("latest_event_stored_at")
        wall_lag = None
        if isinstance(stored_at, str):
            try:
                stored_time = datetime.fromisoformat(stored_at)
                wall_lag = max((datetime.now(UTC) - stored_time).total_seconds(), 0.0)
            except ValueError:
                pass
        return {
            "elapsed_seconds": elapsed,
            "events_per_second": self._counters["events_inserted"] / elapsed,
            "decisions_per_hour": decisions * 3600 / elapsed,
            "persisted_decisions_total": database.get("experiment_decisions", 0),
            "credits_spent": (
                max(initial - remaining, 0)
                if isinstance(initial, int) and isinstance(remaining, int)
                else None
            ),
            "outcomes": outcomes,
            "simulated_lag_minutes": simulated_lag_minutes,
            "ingestion_wall_lag_seconds": wall_lag,
            "pending_approvals": sum(
                database.get("proposal_statuses", {}).get(status, 0)
                for status in ("submitted", "pending")
            ),
            "counters": dict(sorted(self._counters.items())),
            "api": {
                key: {
                    "requests": value["requests"],
                    "failures": value["failures"],
                    "retryable_responses": value["retryable_responses"],
                    "status_codes": dict(sorted(value["status_codes"].items())),
                }
                for key, value in sorted(self._api.items())
            },
            "histograms": {
                key: value.export() for key, value in sorted(self._histograms.items())
            },
            "latest": dict(self._latest),
            "process_peak_rss_bytes": process_memory_bytes(),
        }


def _route(path: str) -> str:
    if not path.startswith("/"):
        return "/other"
    parts = path.split("/")
    if len(parts) >= 3 and parts[1] == "workflows":
        suffix = "/".join(parts[3:])
        return _WORKFLOW_ROUTES.get(suffix, "/workflows/{workflow_id}/other")
    if path.startswith("/approvals/"):
        return "/approvals/{approval_id}"
    if path.startswith("/events/chunks/"):
        return "/events/chunks/{chunk_number}"
    return path.split("?", 1)[0]


def _ratio(numerator: object, denominator: object) -> float | None:
    if (
        isinstance(numerator, bool)
        or not isinstance(numerator, int)
        or isinstance(denominator, bool)
        or not isinstance(denominator, int)
        or denominator <= 0
    ):
        return None
    return numerator / denominator


def _simulated_lag_minutes(current: object, latest_fact: object) -> float | None:
    if not isinstance(current, str) or not isinstance(latest_fact, str):
        return None
    try:
        current_time = datetime.fromisoformat(current)
        latest_time = datetime.fromisoformat(latest_fact)
    except ValueError:
        return None
    return max((current_time - latest_time).total_seconds() / 60.0, 0.0)


def process_memory_bytes() -> int | None:
    """Return current/peak resident bytes using only platform standard libraries."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = _Counters()
            counters.cb = ctypes.sizeof(counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            )
            return int(counters.PeakWorkingSetSize) if ok else None
        except (AttributeError, OSError):
            return None
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/status", encoding="ascii") as status:
                for line in status:
                    if line.startswith("VmHWM:"):
                        return int(line.split()[1]) * 1024
        except (OSError, ValueError, IndexError):
            return None
    try:
        import resource

        maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(maximum * (1024 if sys.platform != "darwin" else 1))
    except (ImportError, OSError, ValueError):
        return None
