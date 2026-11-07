"""PostgreSQL persistence and restart tests for KPI fact derivation."""

import os

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from controller.analytics.facts import PROCESSOR_NAME, KpiFactProcessor
from controller.storage.models import KpiFact
from controller.storage.store import DatabaseStore, EventRecord, KpiFactRecord


@pytest.mark.anyio
async def test_fact_derivation_is_idempotent_and_survives_store_reopen(postgres_store):
    store = postgres_store
    source_events = [
        EventRecord(
            940001,
            1,
            "history",
            {
                "event_id": 940001,
                "type": "campaign_created",
                "ts": "2027-01-04T01:00:00+05:30",
                "workflow_id": "analytics-wf",
                "campaign_id": "camp-1",
                "leads": 20,
            },
        ),
        EventRecord(
            940004,
            4,
            "history",
            {
                "event_id": 940004,
                "type": "call",
                "ts": "2027-01-04T01:15:00+05:30",
                "workflow_id": "analytics-wf",
                "campaign_id": "camp-1",
                "lead_id": "lead-1",
                "person_id": "person-1",
                "config_version": 2,
                "cohort_id": "default",
                "variant": {"voice": "V1"},
                "connected": True,
                "engaged": False,
                "disposition": "concluded_not_interested",
            },
        ),
        EventRecord(
            940009,
            9,
            "events",
            {
                "event_id": 940009,
                "type": "call",
                "ts": "2027-01-04T01:30:00+05:30",
                "workflow_id": "analytics-wf",
                "campaign_id": "camp-1",
                "lead_id": "lead-2",
                "connected": False,
                "disposition": "no_answer",
            },
        ),
    ]
    async with store.transaction() as session:
        await store.insert_events(session, source_events)

    processor = KpiFactProcessor(store, page_size=2)
    assert await processor.process_available() == (3, 3)
    assert await processor.process_available() == (0, 0)
    assert await store.get_analytics_cursor(PROCESSOR_NAME) == 940009

    url = os.environ["CONTROLLER_TEST_DATABASE_URL"]
    await store.close()
    reopened = DatabaseStore(url)
    try:
        await reopened.initialize()
        await reopened.migrate()
        assert await KpiFactProcessor(reopened, page_size=1).process_available() == (
            0,
            0,
        )
        async with reopened.transaction() as session:
            rows = list(
                (
                    await session.scalars(
                        select(KpiFact).order_by(KpiFact.source_event_id)
                    )
                ).all()
            )
        assert [row.source_event_id for row in rows] == [940001, 940004, 940009]
        assert [
            (row.dials, row.connected, row.concluded, row.uploaded) for row in rows
        ] == [
            (0, 0, 0, 20),
            (1, 1, 1, 0),
            (1, 0, 0, 0),
        ]
    finally:
        await reopened.close()


@pytest.mark.anyio
async def test_failed_fact_transaction_does_not_advance_analytics_cursor(
    postgres_store,
):
    store = postgres_store
    invalid_fk_fact = KpiFactRecord(
        source_event_id=999999,
        source_chunk=1,
        source_kind="events",
        workflow_id="missing-source-workflow",
        event_type="call",
        occurred_at=None,
        campaign_id=None,
        lead_id=None,
        person_id=None,
        config_version=None,
        observed_cohort_id=None,
        variant=None,
        features=None,
        quality="invalid",
        quality_detail="failure injection",
    )
    with pytest.raises(IntegrityError):
        await store.commit_kpi_page("failure-test", [invalid_fk_fact], 999999)
    assert await store.get_analytics_cursor("failure-test") == 0
    assert await store.get_event(999999) is None
