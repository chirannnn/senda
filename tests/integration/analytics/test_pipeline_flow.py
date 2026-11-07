"""Persisted events flow through KPI and audit queries using one cohort identity."""

import pytest

from controller.analytics.audit import query_for_cohort
from controller.analytics.cohorts import definitions_from_config
from controller.analytics.facts import KpiFactProcessor
from controller.analytics.history import HistoricalQueries
from controller.analytics.kpis import KpiCalculator
from controller.storage.audit import AuditEntry, AuditLog
from controller.storage.store import DatabaseStore, EventRecord


@pytest.mark.anyio
async def test_event_facts_cohort_and_audit_share_a_versioned_identity(postgres_store):
    store: DatabaseStore = postgres_store
    events = [
        EventRecord(
            960001,
            1,
            "history",
            {
                "event_id": 960001,
                "type": "lead",
                "ts": "2027-01-04T00:00:00+05:30",
                "workflow_id": "pipeline-wf",
                "campaign_id": "campaign-1",
                "lead_id": "lead-1",
                "person_id": "person-1",
                "features": {"age_band": "60+", "lang": "ta"},
            },
        ),
        EventRecord(
            960004,
            4,
            "events",
            {
                "event_id": 960004,
                "type": "call",
                "ts": "2027-01-04T00:15:00+05:30",
                "workflow_id": "pipeline-wf",
                "campaign_id": "campaign-1",
                "lead_id": "lead-1",
                "person_id": "person-1",
                "config_version": 5,
                "cohort_id": "senior_ta",
                "variant": {"voice": "V2"},
                "connected": True,
                "engaged": False,
                "disposition": "concluded_not_interested",
            },
        ),
    ]
    async with store.transaction() as session:
        await store.insert_events(session, events)
    assert await KpiFactProcessor(store, page_size=1).process_available() == (2, 2)

    definitions = definitions_from_config(
        "pipeline-wf",
        5,
        {
            "cohorts": [
                {
                    "id": "senior_ta",
                    "where": {"age_band": ["60+"], "lang": ["ta"]},
                }
            ]
        },
    )
    cohort = definitions[0]
    await store.save_cohort_definitions(
        cohort.workflow_id,
        cohort.config_version,
        [item.to_record() for item in definitions],
    )

    context = await anext(HistoricalQueries(store).iter_call_context("pipeline-wf"))
    assert context.cohort_matches(definitions) is True
    metrics = await KpiCalculator(store).calculate(
        "pipeline-wf", cohort=cohort, variant={"voice": "V2"}
    )
    assert (metrics.dials, metrics.connected, metrics.concluded) == (1, 1, 1)
    assert metrics.pickup_rate == metrics.engagement_rate == 1.0

    audit = AuditLog(store)
    await audit.record(
        AuditEntry(
            action="cohort_observation",
            actor="controller",
            reason="persisted fact and observed call cohort agree",
            workflow_id="pipeline-wf",
            evidence={
                "config_version": cohort.config_version,
                "cohort_id": cohort.cohort_id,
                "outcome": "observed",
            },
        )
    )
    page = await query_for_cohort(audit, cohort)
    assert len(page.records) == 1
    assert page.records[0].evidence["cohort_id"] == cohort.cohort_id
