import pytest

from controller.store import EventConflictError, EventRecord


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
