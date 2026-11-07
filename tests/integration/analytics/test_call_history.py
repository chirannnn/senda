"""PostgreSQL history joins preserve missing and returning-person evidence."""

import pytest

from controller.analytics.cohorts import definitions_from_config
from controller.analytics.facts import KpiFactProcessor
from controller.analytics.history import HistoricalQueries
from controller.storage.store import DatabaseStore, EventRecord


@pytest.mark.anyio
async def test_call_history_joins_preceding_leads_and_flags_returning_people(
    postgres_store,
):
    store: DatabaseStore = postgres_store
    events = [
        EventRecord(
            950001,
            1,
            "history",
            {
                "event_id": 950001,
                "type": "lead",
                "ts": "2027-01-04T00:00:00+05:30",
                "workflow_id": "join-wf",
                "campaign_id": "c1",
                "lead_id": "l1",
                "person_id": "p1",
                "features": {"lang": "hi"},
            },
        ),
        EventRecord(
            950004,
            4,
            "history",
            {
                "event_id": 950004,
                "type": "call",
                "ts": "2027-01-04T00:15:00+05:30",
                "workflow_id": "join-wf",
                "campaign_id": "c1",
                "lead_id": "l1",
                "person_id": "p1",
                "config_version": 1,
                "cohort_id": "default",
                "connected": True,
                "disposition": "short_hangup",
            },
        ),
        EventRecord(
            950009,
            9,
            "events",
            {
                "event_id": 950009,
                "type": "lead",
                "ts": "2027-01-05T00:00:00+05:30",
                "workflow_id": "join-wf",
                "campaign_id": "c2",
                "lead_id": "l2",
                "person_id": "p1",
                "features": {"lang": "ta"},
            },
        ),
        EventRecord(
            950012,
            12,
            "events",
            {
                "event_id": 950012,
                "type": "call",
                "ts": "2027-01-05T00:15:00+05:30",
                "workflow_id": "join-wf",
                "campaign_id": "c2",
                "lead_id": "l2",
                "person_id": "p1",
                "config_version": 2,
                "cohort_id": "tamil",
                "connected": False,
                "disposition": "no_answer",
            },
        ),
        EventRecord(
            950017,
            17,
            "events",
            {
                "event_id": 950017,
                "type": "call",
                "ts": "2027-01-05T00:30:00+05:30",
                "workflow_id": "join-wf",
                "campaign_id": "c2",
                "lead_id": "missing-lead",
                "person_id": "unknown-person",
                "config_version": 2,
                "connected": False,
                "disposition": "no_answer",
            },
        ),
    ]
    async with store.transaction() as session:
        await store.insert_events(session, events)
    assert await KpiFactProcessor(store, page_size=2).process_available() == (5, 5)

    contexts = [
        row
        async for row in HistoricalQueries(store, page_size=1).iter_call_context(
            "join-wf"
        )
    ]
    assert [item.call.source_event_id for item in contexts] == [950004, 950012, 950017]
    assert contexts[0].lead is not None
    assert contexts[0].lead.features == {"lang": "hi"}
    assert not contexts[0].is_returning_person
    default = definitions_from_config("join-wf", 1, {"cohorts": []})
    assert contexts[0].cohort_matches(default) is True
    assert contexts[1].lead is not None and contexts[1].lead.lead_id == "l2"
    assert contexts[1].is_returning_person
    tamil = definitions_from_config(
        "join-wf", 2, {"cohorts": [{"id": "tamil", "where": {"lang": ["ta"]}}]}
    )
    assert contexts[1].cohort_matches(tamil) is True
    assert contexts[2].has_missing_lead
    assert not contexts[2].is_returning_person
