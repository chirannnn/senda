"""Durable live advances and restart reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from controller.clients.simulator import SimulatorClient
from controller.errors import (
    AmbiguousPostError,
    SimulatorError,
    SimulatorHTTPError,
)
from controller.ingestion.events import EventValidationError
from controller.ingestion.parser import parse_descriptors
from controller.ingestion.service import EventIngestionService
from controller.storage.store import DatabaseStore

ADVANCE_OPERATION = "simulator_advance"
CHECKPOINT_KEY = "simulator"
UNRESOLVED_STATUSES = ("pending", "in_progress", "reconciliation_required")


@dataclass(frozen=True, slots=True)
class AdvanceOutcome:
    """Persisted result of one advance request and its event descriptors."""

    finished: bool
    simulated_time: str
    chunks_committed: int
    events_inserted: int
    intent_key: str


class AdvanceReconciliationError(SimulatorError):
    """Remote advance state conflicts with the durable pre-request intent."""


async def advance_and_ingest(
    simulator: SimulatorClient,
    store: DatabaseStore,
    minutes: int,
    *,
    service: EventIngestionService | None = None,
) -> AdvanceOutcome:
    """Persist intent, issue one advance POST, persist descriptors, then ingest."""
    ingestion = service or EventIngestionService(simulator)
    pending = await store.list_outbox_operations(
        ADVANCE_OPERATION, statuses=UNRESOLVED_STATUSES
    )
    if pending:
        raise AdvanceReconciliationError(
            "an earlier advance intent must be reconciled before another POST"
        )
    await ingestion.ingest_available_chunks(store, checkpoint_key=CHECKPOINT_KEY)
    before = _state_snapshot(await simulator.get_state())
    intent_key = f"advance-intent:{uuid4()}"
    payload: dict[str, Any] = {"requested_minutes": minutes, "before": before}
    operation_id = await _create_intent(store, intent_key, payload)
    return await _perform_intent(
        simulator, store, ingestion, intent_key, operation_id, payload, minutes
    )


async def reconcile_pending_advances(
    simulator: SimulatorClient,
    store: DatabaseStore,
    *,
    service: EventIngestionService | None = None,
) -> tuple[AdvanceOutcome, ...]:
    """Resolve durable advance intents before allowing another simulated tick.

    An unchanged state and empty post-checkpoint descriptor listing proves that
    the synchronous advance made no visible progress. Only then is the same
    intent eligible for one new POST. Any conflicting or unavailable evidence
    fails closed.
    """
    ingestion = service or EventIngestionService(simulator)
    operations = await store.list_outbox_operations(
        ADVANCE_OPERATION, statuses=UNRESOLVED_STATUSES
    )
    outcomes: list[AdvanceOutcome] = []
    for operation in operations:
        if operation.status == "reconciliation_required":
            raise AdvanceReconciliationError(
                f"advance intent {operation.dedupe_key} requires operator reconciliation"
            )
        payload = operation.payload
        before_raw = payload.get("before")
        if not isinstance(before_raw, dict):
            await _set_intent(
                store,
                operation.dedupe_key,
                "reconciliation_required",
                payload | {"reconciliation_error": "missing pre-request state"},
            )
            raise AdvanceReconciliationError("advance intent lacks pre-request state")
        before = _state_snapshot(before_raw)
        current = _state_snapshot(await simulator.get_state())
        chunks, events = await ingestion.ingest_available_chunks(
            store, checkpoint_key=CHECKPOINT_KEY
        )
        progressed = _has_progress(before, current)
        unchanged = before == current and chunks == 0 and events == 0
        if progressed:
            reconciled_payload = payload | {
                "reconciliation": {
                    "result": "simulator_progress_observed",
                    "state": current,
                    "recovered_chunks": chunks,
                    "recovered_events": events,
                }
            }
            await _finish_intent(store, operation.dedupe_key, reconciled_payload)
            outcomes.append(
                AdvanceOutcome(
                    finished=current["finished"],
                    simulated_time=current["ts"],
                    chunks_committed=chunks,
                    events_inserted=events,
                    intent_key=operation.dedupe_key,
                )
            )
            continue
        if not unchanged:
            conflict_payload = payload | {
                "reconciliation": {
                    "result": "state_conflict",
                    "before": before,
                    "current": current,
                    "recovered_chunks": chunks,
                    "recovered_events": events,
                }
            }
            await _set_intent(
                store,
                operation.dedupe_key,
                "reconciliation_required",
                conflict_payload,
            )
            raise AdvanceReconciliationError(
                f"advance intent {operation.dedupe_key} has conflicting remote state"
            )

        minutes = payload.get("requested_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            await _set_intent(
                store,
                operation.dedupe_key,
                "reconciliation_required",
                payload | {"reconciliation_error": "invalid requested minutes"},
            )
            raise AdvanceReconciliationError(
                "advance intent lacks valid requested minutes"
            )
        retry_payload = payload | {
            "reconciliation": {
                "result": "no_progress_proven",
                "state": current,
                "recovered_chunks": chunks,
                "recovered_events": events,
            }
        }
        await _set_intent(
            store,
            operation.dedupe_key,
            "in_progress",
            retry_payload,
            increment_attempts=True,
        )
        outcomes.append(
            await _perform_intent(
                simulator,
                store,
                ingestion,
                operation.dedupe_key,
                operation.id,
                retry_payload,
                minutes,
            )
        )
    return tuple(outcomes)


async def _create_intent(
    store: DatabaseStore, intent_key: str, payload: dict[str, Any]
):
    async with store.transaction() as session:
        operation = await store.create_outbox_operation(
            session,
            operation_type=ADVANCE_OPERATION,
            dedupe_key=intent_key,
            payload=payload,
        )
        await store.update_outbox_status(
            session,
            operation.id,
            "in_progress",
            increment_attempts=True,
        )
    return operation.id


async def _perform_intent(
    simulator: SimulatorClient,
    store: DatabaseStore,
    ingestion: EventIngestionService,
    intent_key: str,
    operation_id: Any,
    payload: dict[str, Any],
    minutes: int,
) -> AdvanceOutcome:
    try:
        response = await simulator.advance(minutes)
    except AmbiguousPostError:
        # Leave the durable intent unresolved; startup reconciliation owns it.
        raise
    except SimulatorHTTPError as exc:
        if exc.status_code == 409 and exc.path == "/sim/advance":
            state = _state_snapshot(await simulator.get_state())
            if state["finished"]:
                observed = payload | {
                    "reconciliation": {"result": "already_finished", "state": state}
                }
                await _finish_intent(store, intent_key, observed)
                return AdvanceOutcome(True, state["ts"], 0, 0, intent_key)
        await _set_intent(
            store,
            intent_key,
            "failed",
            payload | {"response_error_status": exc.status_code},
        )
        raise

    if not isinstance(response.get("new_chunks"), list):
        raise SimulatorError("advance response is missing the new_chunks array")
    checkpoint = await store.get_checkpoint(CHECKPOINT_KEY)
    descriptors = parse_descriptors(
        response["new_chunks"], after=-1 if checkpoint is None else checkpoint
    )
    if any(descriptor.kind != "events" for descriptor in descriptors):
        raise EventValidationError("advance response contains a non-live chunk")
    finished = response.get("finished")
    simulated_time = response.get("ts")
    sim_minute = response.get("sim_minute")
    if not isinstance(finished, bool) or not isinstance(simulated_time, str):
        raise SimulatorError("advance response is missing valid ts or finished fields")
    if isinstance(sim_minute, bool) or not isinstance(sim_minute, int):
        raise SimulatorError("advance response is missing valid sim_minute")

    payload = payload | {
        "response": {
            "sim_minute": sim_minute,
            "ts": simulated_time,
            "finished": finished,
            "new_chunks": [item.metadata for item in descriptors],
        }
    }
    await _set_intent(store, intent_key, "in_progress", payload)
    committed, inserted = await ingestion.ingest_descriptors(
        store, descriptors, checkpoint_key=CHECKPOINT_KEY
    )
    recovered_chunks, recovered_events = await ingestion.ingest_available_chunks(
        store, checkpoint_key=CHECKPOINT_KEY
    )
    committed += recovered_chunks
    inserted += recovered_events
    await _finish_intent(
        store,
        intent_key,
        payload | {"committed_chunks": committed, "inserted_events": inserted},
    )
    return AdvanceOutcome(
        finished=finished,
        simulated_time=simulated_time,
        chunks_committed=committed,
        events_inserted=inserted,
        intent_key=intent_key,
    )


async def _set_intent(
    store: DatabaseStore,
    intent_key: str,
    status: str,
    payload: dict[str, Any],
    *,
    increment_attempts: bool = False,
) -> None:
    async with store.transaction() as session:
        operation = await store.get_outbox_operation(intent_key)
        if operation is None:
            raise RuntimeError("advance intent disappeared before reconciliation")
        await store.update_outbox_status(
            session,
            operation.id,
            status,
            payload=payload,
            increment_attempts=increment_attempts,
        )


async def _finish_intent(
    store: DatabaseStore, intent_key: str, payload: dict[str, Any]
) -> None:
    await _set_intent(store, intent_key, "succeeded", payload)


def _has_progress(before: dict[str, Any], current: dict[str, Any]) -> bool:
    return any(
        current[field] > before[field]
        for field in ("sim_minute", "chunks", "last_event_id")
    ) or (current["finished"] and not before["finished"])


def _state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Keep only documented state fields needed to reconcile advance progress."""
    ts = state.get("ts")
    finished = state.get("finished")
    sim_minute = state.get("sim_minute")
    chunks = state.get("chunks")
    last_event_id = state.get("last_event_id")
    if not isinstance(ts, str) or not isinstance(finished, bool):
        raise SimulatorError("simulator state is missing valid ts or finished fields")
    for name, value in (
        ("sim_minute", sim_minute),
        ("chunks", chunks),
        ("last_event_id", last_event_id),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise SimulatorError(f"simulator state is missing valid {name}")
    return {
        "ts": ts,
        "finished": finished,
        "sim_minute": sim_minute,
        "chunks": chunks,
        "last_event_id": last_event_id,
    }
