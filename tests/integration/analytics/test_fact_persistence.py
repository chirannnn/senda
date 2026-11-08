"""PostgreSQL persistence and restart tests for KPI fact derivation."""

import os

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from controller.analytics.cohorts import definitions_from_config
from controller.analytics.facts import PROCESSOR_NAME, KpiFactProcessor, derive_fact
from controller.analytics.kpis import KpiCalculator
from controller.storage.models import KpiFact, SimulatorEvent, WorkflowReference
from controller.storage.store import DatabaseStore, EventRecord, KpiFactRecord
from controller.telemetry import RuntimeTelemetry


@pytest.mark.anyio
async def test_kpi_page_batches_workflow_upserts_and_counts_without_returning(
    postgres_store,
):
    store: DatabaseStore = postgres_store
    source_events = [
        EventRecord(
            940101,
            101,
            "events",
            {
                "event_id": 940101,
                "type": "campaign_created",
                "ts": "2027-01-04T01:00:00+05:30",
                "workflow_id": "fact-batch-a",
                "campaign_id": "campaign-a",
                "leads": 10,
            },
        ),
        EventRecord(
            940102,
            101,
            "events",
            {
                "event_id": 940102,
                "type": "campaign_created",
                "ts": "2027-01-04T01:00:00+05:30",
                "workflow_id": "fact-batch-b",
                "campaign_id": "campaign-b",
                "leads": 20,
            },
        ),
    ]
    async with store.transaction() as session:
        await store.insert_events(session, source_events)
    facts = [
        derive_fact(
            SimulatorEvent(
                event_id=source.event_id,
                chunk_number=source.chunk_number,
                kind=source.kind,
                payload=source.payload,
            )
        )
        for source in source_events
    ]

    statements: list[str] = []

    def record_statement(connection, cursor, statement, parameters, context, many):
        statements.append(statement.lower())

    sqlalchemy_event.listen(
        store.engine.sync_engine, "before_cursor_execute", record_statement
    )
    try:
        assert await store.commit_kpi_page("batch-test", facts, 940102) == 2
        reference_inserts = [
            sql for sql in statements if "insert into workflow_references" in sql
        ]
        fact_inserts = [sql for sql in statements if "insert into kpi_facts" in sql]
        assert len(reference_inserts) == 1
        assert len(fact_inserts) == 1
        assert "returning" not in fact_inserts[0]

        statements.clear()
        assert await store.commit_kpi_page("batch-test", facts, 940102) == 0
    finally:
        sqlalchemy_event.remove(
            store.engine.sync_engine, "before_cursor_execute", record_statement
        )


@pytest.mark.anyio
async def test_fact_derivation_is_idempotent_and_survives_store_reopen(postgres_store):
    store = postgres_store
    telemetry = RuntimeTelemetry()
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

    processor = KpiFactProcessor(store, page_size=2, telemetry=telemetry)
    assert await processor.process_available() == (3, 3)
    assert await processor.process_available() == (0, 0)
    assert await store.get_analytics_cursor(PROCESSOR_NAME) == 940009
    metrics = telemetry.export()["histograms"]
    assert metrics["fact_event_page_read_seconds"]["count"] == 4
    assert metrics["fact_projection_seconds"]["count"] == 2
    for metric_name, expected_count in (
        ("fact_workflow_reference_upsert_seconds", 2),
        ("fact_page_payload_build_seconds", 2),
        ("fact_stage_create_seconds", 2),
        ("fact_insert_execute_seconds", 2),
        ("fact_copy_execute_seconds", 2),
        ("fact_stage_merge_execute_seconds", 2),
        ("fact_cursor_update_seconds", 2),
        ("fact_page_commit_seconds", 2),
    ):
        assert metrics[metric_name]["count"] == expected_count
        assert metrics[metric_name]["sum_seconds"] >= 0
        assert metrics[metric_name]["max_seconds"] >= 0

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
        summary = await KpiCalculator(reopened).calculate("analytics-wf")
        assert (
            summary.dials,
            summary.connected,
            summary.concluded,
            summary.uploaded,
        ) == (
            2,
            1,
            1,
            20,
        )
        assert summary.pickup_rate == 0.5
        assert summary.engagement_rate == 1.0
        default_cohort = definitions_from_config("analytics-wf", 2, {"cohorts": []})[-1]
        observed_arm = await KpiCalculator(reopened).calculate(
            "analytics-wf", cohort=default_cohort, variant={"voice": "V1"}
        )
        assert (observed_arm.dials, observed_arm.connected, observed_arm.concluded) == (
            1,
            1,
            1,
        )
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
    async with store.transaction() as session:
        assert await session.get(KpiFact, 999999) is None
        assert await session.get(WorkflowReference, "missing-source-workflow") is None


@pytest.mark.anyio
async def test_cohort_definitions_persist_once_per_config_version(postgres_store):
    store: DatabaseStore = postgres_store
    definitions = definitions_from_config(
        "cohort-wf",
        4,
        {"cohorts": [{"id": "senior", "where": {"age_band": ["60+"]}}]},
    )
    records = [item.to_record() for item in definitions]
    await store.save_cohort_definitions("cohort-wf", 4, records)
    await store.save_cohort_definitions("cohort-wf", 4, records)
    persisted = await store.list_cohort_definitions("cohort-wf", 4)
    assert [row.cohort_id for row in persisted] == ["senior", "default"]
    assert [row.ordinal for row in persisted] == [0, 1]
    assert persisted[0].predicate == {"age_band": ["60+"]}

    changed = definitions_from_config(
        "cohort-wf",
        4,
        {"cohorts": [{"id": "senior", "where": {"age_band": ["46-60"]}}]},
    )
    with pytest.raises(ValueError, match="changed without incrementing"):
        await store.save_cohort_definitions(
            "cohort-wf", 4, [item.to_record() for item in changed]
        )
    version_five = definitions_from_config(
        "cohort-wf",
        5,
        {"cohorts": [{"id": "senior", "where": {"age_band": ["46-60"]}}]},
    )
    await store.save_cohort_definitions(
        "cohort-wf", 5, [item.to_record() for item in version_five]
    )
    assert [
        row.config_version
        for row in await store.list_cohort_definitions("cohort-wf", 5)
    ] == [5, 5]
