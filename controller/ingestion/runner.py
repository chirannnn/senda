"""One durable live advance and its returned event chunks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from controller.clients.simulator import SimulatorClient
from controller.errors import SimulatorError, SimulatorHTTPError
from controller.ingestion.events import EventValidationError
from controller.ingestion.parser import parse_descriptors
from controller.ingestion.service import EventIngestionService
from controller.storage.store import DatabaseStore

ADVANCE_OPERATION = "simulator_advance"
CHECKPOINT_KEY = "simulator"


@dataclass(frozen=True, slots=True)
class AdvanceOutcome:
    """Persisted result of one advance request and its event descriptors."""

    finished: bool
    simulated_time: str
    chunks_committed: int
    events_inserted: int
    intent_key: str


async def advance_and_ingest(
    simulator: SimulatorClient,
    store: DatabaseStore,
    minutes: int,
    *,
    service: EventIngestionService | None = None,
) -> AdvanceOutcome:
    """Persist intent, issue one advance POST, persist descriptors, then ingest."""
    ingestion = service or EventIngestionService(simulator)
    # Catch up before moving the simulator so a newly committed cursor can never
    # jump over an already-published descriptor.
    await ingestion.ingest_available_chunks(store, checkpoint_key=CHECKPOINT_KEY)
    before = _state_snapshot(await simulator.get_state())
    intent_key = f"advance-intent:{uuid4()}"
    payload: dict[str, Any] = {
        "requested_minutes": minutes,
        "before": before,
    }
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

    try:
        response = await simulator.advance(minutes)
    except SimulatorHTTPError as exc:
        if exc.status_code == 409 and exc.path == "/sim/advance":
            state = _state_snapshot(await simulator.get_state())
            if state["finished"]:
                await _finish_intent(store, intent_key, payload | {"observed": state})
                return AdvanceOutcome(True, state["ts"], 0, 0, intent_key)
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
    await _save_intent_payload(store, intent_key, payload)
    committed, inserted = await ingestion.ingest_descriptors(
        store, descriptors, checkpoint_key=CHECKPOINT_KEY
    )
    # Re-list both streams to catch API-published descriptors omitted by a stale
    # or incomplete advance response before marking the intent complete.
    recovered_chunks, recovered_events = await ingestion.ingest_available_chunks(
        store, checkpoint_key=CHECKPOINT_KEY
    )
    committed += recovered_chunks
    inserted += recovered_events
    await _finish_intent(
        store,
        intent_key,
        payload
        | {
            "committed_chunks": committed,
            "inserted_events": inserted,
        },
    )
    return AdvanceOutcome(
        finished=finished,
        simulated_time=simulated_time,
        chunks_committed=committed,
        events_inserted=inserted,
        intent_key=intent_key,
    )


async def _save_intent_payload(
    store: DatabaseStore, intent_key: str, payload: dict[str, Any]
) -> None:
    async with store.transaction() as session:
        operation = await store.get_outbox_operation(intent_key)
        if operation is None:
            raise RuntimeError("advance intent disappeared before reconciliation")
        await store.update_outbox_status(
            session, operation.id, "in_progress", payload=payload
        )


async def _finish_intent(
    store: DatabaseStore, intent_key: str, payload: dict[str, Any]
) -> None:
    async with store.transaction() as session:
        operation = await store.get_outbox_operation(intent_key)
        if operation is None:
            raise RuntimeError("advance intent disappeared before completion")
        await store.update_outbox_status(
            session, operation.id, "succeeded", payload=payload
        )


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
