import pytest

from controller.audit import AuditEntry, AuditLog
from controller.store import (
    CheckpointRegressionError,
    EventConflictError,
    EventRecord,
    OutboxConflictError,
)


def _event(event_id, *, payload=None):
    return EventRecord(
        event_id=event_id,
        chunk_number=81,
        kind="events",
        payload=payload or {"event_id": event_id, "opaque": {"value": "kept"}},
    )


@pytest.mark.anyio
async def test_insert_read_and_duplicate_event_is_idempotent(postgres_store):
    store = postgres_store
    event = _event(920001)
    async with store.transaction() as session:
        inserted = await store.insert_events(session, [event])
    assert inserted == (event.event_id,)

    async with store.transaction() as session:
        duplicate = await store.insert_events(session, [event])
    assert duplicate == ()

    stored = await store.get_event(event.event_id)
    assert stored is not None
    assert stored.chunk_number == 81
    assert stored.payload == event.payload


@pytest.mark.anyio
async def test_conflicting_replay_fails_instead_of_hiding_corruption(postgres_store):
    store = postgres_store
    event = _event(920002)
    async with store.transaction() as session:
        await store.insert_events(session, [event])

    with pytest.raises(EventConflictError):
        async with store.transaction() as session:
            await store.insert_events(
                session, [_event(event.event_id, payload={"different": True})]
            )


@pytest.mark.anyio
async def test_chunk_rows_and_ordered_descriptor_checkpoint_commit_together(
    postgres_store,
):
    store = postgres_store
    inserted = await store.commit_chunk("events", 17, [_event(920003)])
    assert inserted == (920003,)
    assert await store.get_checkpoint("events") == 17
    assert await store.get_event(920003) is not None


@pytest.mark.anyio
async def test_failed_event_write_cannot_advance_checkpoint(postgres_store):
    store = postgres_store
    existing = _event(920004)
    async with store.transaction() as session:
        await store.insert_events(session, [existing])

    with pytest.raises(EventConflictError):
        await store.commit_chunk(
            "events",
            18,
            [
                _event(920005),
                _event(existing.event_id, payload={"different": True}),
            ],
        )

    assert await store.get_event(920005) is None
    assert await store.get_checkpoint("events") is None


@pytest.mark.anyio
async def test_exception_rolls_back_event_and_checkpoint_as_one_transaction(
    postgres_store,
):
    store = postgres_store
    with pytest.raises(RuntimeError, match="injected failure"):
        async with store.transaction() as session:
            await store.insert_events(session, [_event(920006)])
            await store.advance_checkpoint(session, "events", 19)
            raise RuntimeError("injected failure")

    assert await store.get_event(920006) is None
    assert await store.get_checkpoint("events") is None


@pytest.mark.anyio
async def test_checkpoint_rejects_regression_and_rolls_back_associated_event(
    postgres_store,
):
    store = postgres_store
    await store.commit_chunk("history", 20, [_event(920007)])

    with pytest.raises(CheckpointRegressionError):
        await store.commit_chunk("history", 19, [_event(920008)])

    assert await store.get_checkpoint("history") == 20
    assert await store.get_event(920008) is None


@pytest.mark.anyio
async def test_audit_record_and_workflow_reference_persist(postgres_store):
    audit = AuditLog(postgres_store)
    record_id = await audit.record(
        AuditEntry(
            action="runtime_observation",
            actor="controller",
            reason="durable test evidence",
            workflow_id="workflow-test",
            evidence={"source": "unit-test", "count": 2},
        )
    )
    rows = await audit.list_for_workflow("workflow-test")
    assert len(rows) == 1
    assert rows[0].id == record_id
    assert rows[0].evidence == {"source": "unit-test", "count": 2}


@pytest.mark.anyio
async def test_outbox_deduplicates_stable_key_and_tracks_attempts(postgres_store):
    store = postgres_store
    async with store.transaction() as session:
        first = await store.create_outbox_operation(
            session,
            operation_type="future_remote_operation",
            dedupe_key="operation-test-1",
            payload={"opaque": True},
        )
    async with store.transaction() as session:
        duplicate = await store.create_outbox_operation(
            session,
            operation_type="future_remote_operation",
            dedupe_key="operation-test-1",
            payload={"opaque": True},
        )
        await store.update_outbox_status(
            session, duplicate.id, "reconciliation_required", increment_attempts=True
        )
    assert first.id == duplicate.id

    stored = await store.get_outbox_operation("operation-test-1")
    assert stored is not None
    assert stored.status == "reconciliation_required"
    assert stored.attempts == 1
    assert stored.updated_at is not None

    with pytest.raises(OutboxConflictError):
        async with store.transaction() as session:
            await store.create_outbox_operation(
                session,
                operation_type="different_operation",
                dedupe_key="operation-test-1",
                payload={"opaque": True},
            )
