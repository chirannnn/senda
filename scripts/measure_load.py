"""Run a Controller process and capture reproducible public load measurements."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import logging
import os
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from controller.config import Settings
from controller.errors import ControllerError
from controller.storage.store import DatabaseStore

logger = logging.getLogger("measure_load")


@dataclass(slots=True)
class LoadSamples:
    rss_bytes: list[int] = field(default_factory=list)
    cpu_samples: list[tuple[float, float]] = field(default_factory=list)
    database_bytes: list[int] = field(default_factory=list)
    simulator_states: list[dict[str, Any]] = field(default_factory=list)
    points: list[dict[str, Any]] = field(default_factory=list)
    sample_errors: list[str] = field(default_factory=list)


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive number") from exc
    if parsed <= 0 or not parsed < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("simulator_url", help="URL of an already-running simulator")
    parser.add_argument(
        "--output", type=Path, default=Path("reports/public_03_load.md")
    )
    parser.add_argument(
        "--log", type=Path, default=Path("reports/public_03_load.jsonl")
    )
    parser.add_argument(
        "--samples", type=Path, default=Path("reports/public_03_load_samples.jsonl")
    )
    parser.add_argument("--sample-interval-seconds", type=_positive_float, default=5.0)
    parser.add_argument("--timeout-seconds", type=_positive_float, default=7200.0)
    return parser


async def _read_state(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get("/sim/state")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("GET /sim/state must return an object")
    return payload


async def _drain_output(
    process: asyncio.subprocess.Process, logfile: Path
) -> dict[str, Any] | None:
    metrics = None
    assert process.stdout is not None
    with logfile.open("w", encoding="utf-8", newline="\n") as output:
        async for raw_line in process.stdout:
            line = raw_line.decode("utf-8", errors="replace")
            output.write(line)
            output.flush()
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(record, dict) and record.get("event") == "runtime_metrics":
                value = record.get("metrics")
                if isinstance(value, dict):
                    metrics = value
    return metrics


async def _sample(
    process: asyncio.subprocess.Process,
    simulator_url: str,
    store: DatabaseStore,
    interval: float,
    samples: LoadSamples,
) -> None:
    base_url = simulator_url.rstrip("/")
    started = time.monotonic()
    async with httpx.AsyncClient(base_url=base_url, timeout=5.0) as client:
        while process.returncode is None:
            sample_time = time.monotonic()
            point: dict[str, Any] = {
                "elapsed_seconds": sample_time - started,
                "rss_bytes": None,
                "process_cpu_seconds": None,
                "database_bytes": None,
                "simulator_state": None,
            }
            cpu = process_cpu_seconds(process.pid)
            if cpu is not None:
                samples.cpu_samples.append((sample_time, cpu))
                point["process_cpu_seconds"] = cpu
            memory = process_rss_bytes(process.pid)
            if memory is not None:
                samples.rss_bytes.append(memory)
                point["rss_bytes"] = memory
            try:
                async with store.engine.connect() as connection:
                    size = await connection.scalar(
                        text("SELECT pg_database_size(current_database())")
                    )
                if isinstance(size, int):
                    samples.database_bytes.append(size)
                    point["database_bytes"] = size
            except (
                SQLAlchemyError,
                OSError,
                TimeoutError,
            ) as exc:  # collect sampler failure without stopping the Controller
                samples.sample_errors.append(f"database_sample:{type(exc).__name__}")
            try:
                state = await _read_state(client)
                public_state = _public_state(state) or {}
                samples.simulator_states.append(public_state)
                point["simulator_state"] = public_state
            except (
                httpx.HTTPError,
                ValueError,
                TypeError,
            ) as exc:  # the final report records an unavailable read
                samples.sample_errors.append(f"simulator_state:{type(exc).__name__}")
            samples.points.append(point)
            await asyncio.sleep(interval)


async def run_measurement(args: argparse.Namespace) -> int:
    Settings.from_environment(args.simulator_url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    args.samples.parent.mkdir(parents=True, exist_ok=True)
    store = DatabaseStore.from_environment()
    samples = LoadSamples()
    measurement_id = str(uuid4())
    controller_metrics: dict[str, Any] | None = None
    timed_out = False
    process: asyncio.subprocess.Process | None = None
    initial_state: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(
            base_url=args.simulator_url, timeout=5.0
        ) as client:
            initial_state = await _read_state(client)
        await store.initialize()
        if await _existing_event_count(store) > 0:
            raise ValueError(
                "the load run requires a fresh controller database with no stored simulator events"
            )
        initial_db = await _database_size(store)
        started_wall = time.monotonic()
        command = [sys.executable, "-m", "controller", args.simulator_url]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=os.environ.copy(),
        )
        log_task = asyncio.create_task(_drain_output(process, args.log))
        sample_task = asyncio.create_task(
            _sample(
                process,
                args.simulator_url,
                store,
                args.sample_interval_seconds,
                samples,
            )
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=args.timeout_seconds)
        except TimeoutError:
            timed_out = True
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        elapsed = time.monotonic() - started_wall
        sample_task.cancel()
        await asyncio.gather(sample_task, return_exceptions=True)
        controller_metrics = await log_task
        args.samples.write_text(
            "".join(
                json.dumps(point, sort_keys=True) + "\n" for point in samples.points
            ),
            encoding="utf-8",
        )
        final_state = None
        try:
            async with httpx.AsyncClient(
                base_url=args.simulator_url, timeout=5.0
            ) as client:
                final_state = await _read_state(client)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            samples.sample_errors.append(f"final_simulator_state:{type(exc).__name__}")
        try:
            final_db = await _database_size(store)
        except (SQLAlchemyError, OSError) as exc:
            final_db = max(samples.database_bytes, default=initial_db)
            samples.sample_errors.append(f"final_database_size:{type(exc).__name__}")
        try:
            database_snapshot = await store.get_observability_snapshot()
        except SQLAlchemyError as exc:
            database_snapshot = None
            samples.sample_errors.append(
                f"final_database_snapshot:{type(exc).__name__}"
            )
        result = process.returncode
        status = "completed" if result == 0 and not timed_out else "incomplete"
        report = build_report(
            status=status,
            command=command,
            initial_state=initial_state,
            final_state=final_state,
            elapsed_seconds=elapsed,
            controller_return_code=result,
            timed_out=timed_out,
            controller_metrics=controller_metrics,
            database_snapshot=database_snapshot,
            initial_database_bytes=initial_db,
            final_database_bytes=final_db,
            samples=samples,
            sample_interval_seconds=args.sample_interval_seconds,
            log_path=args.log,
            measurement_id=measurement_id,
            samples_path=args.samples,
        )
        args.output.write_text(report, encoding="utf-8")
        return 0 if status == "completed" else 1
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except TimeoutError:
                process.kill()
                await process.wait()
        await store.close()


async def _database_size(store: DatabaseStore) -> int:
    async with store.engine.connect() as connection:
        value = await connection.scalar(
            text("SELECT pg_database_size(current_database())")
        )
    return int(value or 0)


async def _existing_event_count(store: DatabaseStore) -> int:
    async with store.engine.connect() as connection:
        exists = await connection.scalar(text("SELECT to_regclass('simulator_events')"))
        if exists is None:
            return 0
        value = await connection.scalar(text("SELECT count(*) FROM simulator_events"))
    return int(value or 0)


def build_report(
    *,
    status: str,
    command: list[str],
    initial_state: dict[str, Any],
    final_state: dict[str, Any] | None,
    elapsed_seconds: float,
    controller_return_code: int | None,
    timed_out: bool,
    controller_metrics: dict[str, Any] | None,
    database_snapshot: dict[str, Any] | None,
    initial_database_bytes: int,
    final_database_bytes: int,
    samples: LoadSamples,
    sample_interval_seconds: float,
    log_path: Path,
    measurement_id: str,
    samples_path: Path,
) -> str:
    elapsed = max(elapsed_seconds, 0.0)
    simulated_delta = None
    one_day_seconds = None
    sres_pass = None
    if final_state is not None:
        initial_minute = initial_state.get("sim_minute")
        final_minute = final_state.get("sim_minute")
        if (
            isinstance(initial_minute, int)
            and not isinstance(initial_minute, bool)
            and isinstance(final_minute, int)
            and not isinstance(final_minute, bool)
        ):
            simulated_delta = max(final_minute - initial_minute, 0)
            if simulated_delta > 0:
                one_day_seconds = elapsed * 1440 / simulated_delta
                sres_pass = one_day_seconds < 600
    metrics = controller_metrics or {}
    db = database_snapshot or {}
    initial_credits = initial_state.get("credits_remaining")
    final_credits = final_state.get("credits_remaining") if final_state else None
    credits_used = None
    if (
        isinstance(initial_credits, int)
        and not isinstance(initial_credits, bool)
        and isinstance(final_credits, int)
        and not isinstance(final_credits, bool)
    ):
        credits_used = max(initial_credits - final_credits, 0)
    p95 = _histogram_quantile(
        metrics.get("histograms", {}).get("simulator_api_request_seconds"), 0.95
    )
    cpu_seconds = _cpu_seconds(samples.cpu_samples)
    cpu_fraction = (
        cpu_seconds / elapsed / cpu_capacity()
        if cpu_seconds is not None and elapsed > 0 and cpu_capacity()
        else None
    )
    memory_total, memory_available = host_memory_snapshot()
    cpus = cpu_capacity()
    p95_text = _fmt(p95) if isinstance(p95, (float, int)) else (p95 or "unknown")
    lines = [
        "# Public 03 load and observability report",
        "",
        (
            f"- Measurement ID: `{measurement_id}`; scenario label: `public_03_load` "
            "(the simulator does not expose a scenario identifier in the sampled state)"
        ),
        f"- Result: **{status.upper()}**",
        f"- Controller argv: `{json.dumps(command)}`",
        (
            f"- Host: {platform.platform()}; {cpus:g} effective CPU capacity; "
            f"{memory_total or 'unknown'} bytes effective memory limit, "
            f"{memory_available or 'unknown'} bytes available at report time"
        ),
        f"- Python: {platform.python_version()}; database: PostgreSQL via configured `CONTROLLER_DATABASE_URL`",
        f"- Sampler interval: {sample_interval_seconds:g} seconds",
        f"- Raw process/database/state samples: `{samples_path}` ({len(samples.points)} rows)",
        f"- Controller exit code: `{controller_return_code}`; timed out: `{timed_out}`",
        f"- Wall runtime: {elapsed:.3f} seconds",
        f"- Simulated minutes advanced: {simulated_delta}",
        f"- Wall seconds per simulated day: {_fmt(one_day_seconds)}",
        f"- SRE target (<600 seconds per simulated day): {_fmt_bool(sres_pass)}",
        f"- Events persisted: {db.get('event_count', metrics.get('counters', {}).get('events_inserted'))}",
        f"- Facts persisted: {db.get('fact_count', metrics.get('counters', {}).get('facts_inserted'))}",
        f"- Event insert throughput: {_fmt(metrics.get('events_per_second'))} events/second",
        f"- Simulated ingestion lag: {_fmt(metrics.get('simulated_lag_minutes'))} minutes",
        f"- Latest-event wall persistence lag: {_fmt(metrics.get('ingestion_wall_lag_seconds'))} seconds",
        f"- API request latency p95 upper bucket: {p95_text} seconds (time to response headers)",
        f"- API requests/status/failures: `{json.dumps(metrics.get('api', {}), sort_keys=True)}`",
        (
            f"- Peak controller RSS (sampled): {_fmt(max(samples.rss_bytes, default=None))} bytes; "
            f"runtime OS peak: {_fmt(metrics.get('process_peak_rss_bytes'))} bytes"
        ),
        (
            f"- Average process CPU fraction of host capacity: {_fmt(cpu_fraction)}; "
            f"process CPU seconds: {_fmt(cpu_seconds)}"
        ),
        (
            f"- Database size: {initial_database_bytes} -> {final_database_bytes} bytes "
            f"(growth {final_database_bytes - initial_database_bytes} bytes); "
            f"sampled peak {_fmt(max(samples.database_bytes, default=None))} bytes"
        ),
        (
            f"- Checkpoint: {db.get('simulator_checkpoint', metrics.get('latest', {}).get('checkpoint_chunk'))}; "
            f"analytics cursor: {db.get('analytics_cursor')}"
        ),
        (
            f"- Fact quality counts: valid={db.get('valid_facts')}, invalid={db.get('invalid_facts')}, "
            f"incomplete={db.get('incomplete_facts')}, unsupported={db.get('unsupported_facts')}"
        ),
        f"- Separate outcomes (pickup, engagement, business): `{json.dumps(metrics.get('outcomes', {}), sort_keys=True)}`",
        (
            f"- Decisions/hour: {_fmt(metrics.get('decisions_per_hour'))}; "
            f"persisted decisions total: {metrics.get('persisted_decisions_total')}; "
            f"credits used/remain: {credits_used}/{final_credits}"
        ),
        f"- Final simulator state: `{json.dumps(_public_state(final_state), sort_keys=True)}`",
        f"- Governance/outbox state counts: `{json.dumps({key: db.get(key) for key in ('proposal_statuses', 'outbox_statuses', 'incident_statuses')}, sort_keys=True)}`",
        f"- Controller JSON log: `{log_path}`",
        f"- Sampling errors: `{json.dumps(samples.sample_errors)}`",
        "",
        "## Measurement notes",
        "",
        (
            "The run is measured only from public simulator HTTP state and the Controller's PostgreSQL records. "
            "No scenario content, hidden truth, or simulator run directory was read. API latency is measured to "
            "response headers; full chunk download, validation, and persistence are in chunk-ingestion duration. "
            "The external sampler adds periodic read-only state and database-size queries. RSS is sampled at the "
            "configured interval and may miss short peaks; the Controller also reports its OS peak RSS when available. "
            "A successful run is `COMPLETED` only when the Controller exits zero; timeout and nonzero exit are `INCOMPLETE`."
        ),
        "",
        "## Bottleneck evidence",
        "",
        (
            f"Stage histogram totals: `{json.dumps(_stage_totals(metrics), sort_keys=True)}`. "
            "The largest observed aggregate stage is the evidence-led candidate bottleneck; 10x capacity is not "
            "claimed unless a measured stage scales linearly and database/API headroom is known."
        ),
        "",
    ]
    return "\n".join(lines)


def _stage_totals(metrics: dict[str, Any]) -> dict[str, float]:
    histograms = metrics.get("histograms", {})
    return {
        name: float(value.get("sum_seconds", 0.0))
        for name, value in histograms.items()
        if isinstance(value, dict)
        and name
        in {
            "chunk_download_seconds",
            "chunk_persistence_seconds",
            "chunk_ingestion_seconds",
            "fact_processing_seconds",
            "advance_and_ingest_seconds",
        }
    }


def _cpu_seconds(samples: list[tuple[float, float]]) -> float | None:
    if len(samples) < 2:
        return None
    return max(samples[-1][1] - samples[0][1], 0.0)


def _histogram_quantile(histogram: object, quantile: float) -> float | str | None:
    if not isinstance(histogram, dict):
        return None
    count = histogram.get("count")
    bounds = histogram.get("le_seconds")
    buckets = histogram.get("buckets_le")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count <= 0
        or not isinstance(bounds, list)
        or not isinstance(buckets, list)
        or len(bounds) != len(buckets)
    ):
        return None
    rank = max(1, int(count * quantile + 0.999999))
    for bound, cumulative in zip(bounds, buckets, strict=True):
        if cumulative >= rank:
            return float(bound)
    return f">{bounds[-1]}" if bounds else None


def _public_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    allowed = (
        "ts",
        "sim_minute",
        "finished",
        "chunks",
        "last_event_id",
        "credits_remaining",
    )
    return {key: state[key] for key in allowed if key in state}


def _fmt(value: object) -> str:
    return "unknown" if value is None else f"{float(value):.3f}"


def _fmt_bool(value: bool | None) -> str:
    return "NOT MEASURED" if value is None else ("PASS" if value else "FAIL")


def process_rss_bytes(pid: int) -> int | None:
    if sys.platform == "win32":
        try:
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

            handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
            if not handle:
                return None
            try:
                counters = _Counters()
                counters.cb = ctypes.sizeof(counters)
                if not ctypes.windll.psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb
                ):
                    return None
                return int(counters.WorkingSetSize)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return None
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def process_cpu_seconds(pid: int) -> float | None:
    if sys.platform == "win32":
        try:
            from ctypes import wintypes

            class _FileTime(ctypes.Structure):
                _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                creation, exit_time, kernel, user = (
                    _FileTime(),
                    _FileTime(),
                    _FileTime(),
                    _FileTime(),
                )
                if not ctypes.windll.kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                ):
                    return None
                ticks = (kernel.high << 32 | kernel.low) + (user.high << 32 | user.low)
                return ticks / 10_000_000
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return None
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as stat_file:
            raw = stat_file.read()
        fields = raw[raw.rfind(")") + 2 :].split()
        clock_ticks = os.sysconf("SC_CLK_TCK")
        return (int(fields[11]) + int(fields[12])) / clock_ticks
    except (OSError, ValueError, IndexError):
        return None


def host_memory_snapshot() -> tuple[int | None, int | None]:
    if sys.platform == "win32":

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys), int(status.ullAvailPhys)
        return None, None
    try:
        with open("/proc/meminfo", encoding="ascii") as memory:
            values = {}
            for line in memory:
                if line.startswith(("MemTotal:", "MemAvailable:")):
                    name, amount, *_ = line.split()
                    values[name.rstrip(":")] = int(amount) * 1024
            total = values.get("MemTotal")
            available = values.get("MemAvailable")
        limit = _read_integer_file("/sys/fs/cgroup/memory.max")
        current = _read_integer_file("/sys/fs/cgroup/memory.current")
        if limit is not None and current is not None:
            effective_limit = min(limit, total) if total is not None else limit
            effective_available = max(limit - current, 0)
            if available is not None:
                effective_available = min(effective_available, available)
            return effective_limit, effective_available
        return total, available
    except (OSError, ValueError, IndexError):
        return None, None


def _read_integer_file(path: str) -> int | None:
    try:
        value = Path(path).read_text(encoding="ascii").strip()
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except (OSError, ValueError):
        return None


def cpu_capacity() -> float:
    """Return process-visible CPU capacity, honoring common Linux cgroup limits."""
    capacity = float(os.cpu_count() or 1)
    if hasattr(os, "sched_getaffinity"):
        try:
            capacity = float(len(os.sched_getaffinity(0)))
        except OSError:
            pass
    try:
        quota, period = (
            Path("/sys/fs/cgroup/cpu.max").read_text(encoding="ascii").split()
        )
        if quota != "max":
            capacity = min(capacity, int(quota) / int(period))
    except (OSError, ValueError, ZeroDivisionError):
        pass
    return max(capacity, 0.01)


async def _main() -> int:
    args = _parser().parse_args()
    try:
        return await run_measurement(args)
    except (
        ControllerError,
        SQLAlchemyError,
        httpx.HTTPError,
        OSError,
        ValueError,
    ) as exc:
        logger.error("load measurement could not run: %s", type(exc).__name__)
        return 2


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(asyncio.run(_main()))
