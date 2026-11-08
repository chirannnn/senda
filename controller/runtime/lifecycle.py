"""Simulator lifecycle with durable event ingestion and fact derivation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from controller.analytics.facts import KpiFactProcessor
from controller.config import Settings
from controller.errors import AmbiguousPostError, SimulatorError, SimulatorHTTPError
from controller.incidents import process_population_incidents
from controller.ingestion.runner import (
    advance_and_ingest,
    reconcile_pending_advances,
)
from controller.ingestion.service import EventIngestionService
from controller.storage.audit import AuditLog
from controller.storage.store import DatabaseStore

logger = logging.getLogger(__name__)


class SimulatorAPI(Protocol):
    """The documented simulator operations required by this runtime loop."""

    async def get_health(self) -> dict[str, Any]: ...

    async def get_state(self) -> dict[str, Any]: ...

    async def list_workflows(self) -> list[dict[str, Any]]: ...

    async def advance(self, minutes: int) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RuntimeOutcome:
    """How the runtime loop stopped."""

    completed: bool
    stopped: bool


async def run_controller(
    settings: Settings,
    simulator: SimulatorAPI,
    stop_event: asyncio.Event,
    store: DatabaseStore | None = None,
) -> RuntimeOutcome:
    """Check readiness, then advance until finished or asked to stop."""
    await simulator.get_health()
    workflows = await simulator.list_workflows()
    state = await simulator.get_state()
    logger.info(
        "simulator ready",
        extra={"event": "startup_ready", "workflow_count": len(workflows)},
    )
    ingestion = EventIngestionService(simulator) if store is not None else None
    fact_processor = KpiFactProcessor(store) if store is not None else None
    audit = AuditLog(store) if store is not None else None
    if store is not None and ingestion is not None:
        await ingestion.ingest_available_chunks(store)
        await reconcile_pending_advances(simulator, store, service=ingestion)
        await _process_facts(fact_processor)
        state = await simulator.get_state()
        await _process_incidents(
            simulator, store, audit, state, final=_is_finished(state)
        )
    if _is_finished(state):
        return RuntimeOutcome(completed=True, stopped=False)

    while not stop_event.is_set():
        if store is not None:
            result = await advance_and_ingest(
                simulator, store, settings.advance_minutes, service=ingestion
            )
            await _process_facts(fact_processor)
            state = await simulator.get_state()
            await _process_incidents(
                simulator, store, audit, state, final=result.finished
            )
            if result.finished:
                return RuntimeOutcome(completed=True, stopped=False)
            logger.info(
                "simulator advanced and chunks committed",
                extra={
                    "event": "advance_ingested",
                    "simulated_time": result.simulated_time,
                    "chunks_committed": result.chunks_committed,
                    "events_inserted": result.events_inserted,
                },
            )
            continue
        try:
            result = await simulator.advance(settings.advance_minutes)
        except AmbiguousPostError:
            # Until the durable intent/reconciliation chunk is implemented, no
            # subsequent advance is safe after an ambiguous remote outcome.
            logger.exception("advance response is ambiguous; stopping safely")
            raise
        except SimulatorHTTPError as exc:
            if (
                exc.method == "POST"
                and exc.path == "/sim/advance"
                and exc.status_code == 409
            ):
                state = await simulator.get_state()
                if _is_finished(state):
                    return RuntimeOutcome(completed=True, stopped=False)
            raise

        finished = _is_finished(result)
        logger.info(
            "simulator advanced",
            extra={
                "event": "advance_completed",
                "advance_minutes": settings.advance_minutes,
                "simulated_time": result.get("ts"),
                "finished": finished,
            },
        )
        if finished:
            return RuntimeOutcome(completed=True, stopped=False)

    return RuntimeOutcome(completed=False, stopped=True)


async def _process_facts(processor: KpiFactProcessor | None) -> None:
    if processor is None:
        return
    scanned, inserted = await processor.process_available()
    if scanned:
        logger.info(
            "persisted event-derived KPI facts",
            extra={
                "event": "kpi_facts_processed",
                "source_events_scanned": scanned,
                "facts_inserted": inserted,
            },
        )


async def _process_incidents(simulator, store, audit, state, *, final=False) -> None:
    if store is None or audit is None:
        return
    timestamp = state.get("ts")
    if not isinstance(timestamp, str):
        return
    try:
        now = datetime.fromisoformat(timestamp)
    except ValueError:
        return
    if now.tzinfo is None:
        return
    await process_population_incidents(
        simulator,
        store,
        audit,
        now=now,
        cutoff_event_id=await store.get_latest_event_id() or 0,
        final=final,
    )


def _is_finished(payload: dict[str, Any]) -> bool:
    value = payload.get("finished")
    if not isinstance(value, bool):
        raise SimulatorError("simulator response is missing boolean 'finished' status")
    return value
