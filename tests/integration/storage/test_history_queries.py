"""PostgreSQL tests for bounded event history queries."""

import pytest

from controller.analytics.history import HistoricalQueries
from controller.storage.store import DatabaseStore, EventRecord


@pytest.mark.anyio
async def test_history_queries_are_ordered_filtered_and_page_bounded(postgres_store):
    store: DatabaseStore = postgres_store
    async with store.transaction() as session:
        await store.insert_events(
            session,
            [
                EventRecord(
                    930001,
                    1,
                    "history",
                    {"event_id": 930001, "workflow_id": "wf-a", "type": "lead"},
                ),
                EventRecord(
                    930004,
                    4,
                    "events",
                    {"event_id": 930004, "workflow_id": "wf-b", "type": "call"},
                ),
                EventRecord(
                    930009,
                    9,
                    "events",
                    {"event_id": 930009, "workflow_id": "wf-a", "type": "call"},
                ),
            ],
        )

    queries = HistoricalQueries(store, page_size=1)
    rows = [row async for row in queries.iter_events(workflow_id="wf-a")]
    assert [row.event_id for row in rows] == [930001, 930009]
    assert await queries.workflow_ids() == ["wf-a", "wf-b"]

    resumed = [row async for row in queries.iter_events(after_event_id=930001)]
    assert [row.event_id for row in resumed] == [930004, 930009]


def test_history_query_rejects_invalid_page_size():
    with pytest.raises(ValueError, match="page size"):
        HistoricalQueries(None, page_size=0)  # type: ignore[arg-type]
