"""Bounded in-process runtime metrics for load and operations reporting."""

from __future__ import annotations

import math
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_ROUTES = (
    ("/workflows/{workflow_id}/config/history", "/workflows/"),
    ("/workflows/{workflow_id}/config", "/workflows/"),
    ("/workflows/{workflow_id}/apply", "/workflows/"),
    ("/approvals/{approval_id}", "/approvals/"),
    ("/events/chunks/{chunk_number}", "/events/chunks/"),
)


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
        return {
            "count": self.count,
            "sum_seconds": self.total,
            "max_seconds": self.maximum,
            "le_seconds": list(_LATENCY_BUCKETS),
            "buckets": self.buckets,
            "+Inf": self.overflow + sum(self.buckets),
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
            {"requests": 0, "failures": 0, "status_codes": Counter()},
        )
        item["requests"] += 1
        if status_code is None or status_code >= 500 or status_code == 429:
            item["failures"] += 1
        if status_code is not None:
            item["status_codes"][str(status_code)] += 1
        self.observe("simulator_api_request_seconds", seconds)

    def record_ingestion(
        self, *, chunk: int, observed_events: int, inserted_events: int, seconds: float
    ) -> None:
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

    def set_database_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._latest["database"] = snapshot

    def export(self) -> dict[str, Any]:
        elapsed = max(time.perf_counter() - self._started, 1e-9)
        initial = self._latest.get("credits_initial")
        remaining = self._latest.get("credits_remaining")
        database = self._latest.get("database", {})
        decisions = database.get("experiment_decisions", 0)
        return {
            "elapsed_seconds": elapsed,
            "events_per_second": self._counters["events_inserted"] / elapsed,
            "decisions_per_hour": decisions * 3600 / elapsed,
            "credits_spent": (
                max(initial - remaining, 0)
                if isinstance(initial, int) and isinstance(remaining, int)
                else None
            ),
            "counters": dict(sorted(self._counters.items())),
            "api": {
                key: {
                    "requests": value["requests"],
                    "failures": value["failures"],
                    "status_codes": dict(sorted(value["status_codes"].items())),
                }
                for key, value in sorted(self._api.items())
            },
            "histograms": {
                key: value.export() for key, value in sorted(self._histograms.items())
            },
            "latest": dict(self._latest),
            "process_memory_bytes": process_memory_bytes(),
        }


def _route(path: str) -> str:
    if not path.startswith("/"):
        return "/other"
    for template, prefix in _ROUTES:
        suffix = template.rsplit("/", 1)[-1]
        if path.startswith(prefix) and (
            path.endswith(suffix) or suffix.startswith("{")
        ):
            return template
    return path.split("?", 1)[0]


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
