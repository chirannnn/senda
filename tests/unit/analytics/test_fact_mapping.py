"""Contract-backed event to fact mapping tests."""

from datetime import datetime

import pytest

from controller.analytics.facts import KpiFactProcessor, derive_fact
from controller.storage.models import SimulatorEvent


def _event(payload):
    return SimulatorEvent(
        event_id=91,
        chunk_number=7,
        kind="events",
        payload={"event_id": 91, **payload},
    )


def test_call_facts_keep_dials_pickup_and_conclusion_distinct():
    event = _event(
        {
            "type": "call",
            "ts": "2027-01-04T01:00:00+05:30",
            "workflow_id": "wf",
            "lead_id": "lead-1",
            "person_id": "person-1",
            "config_version": 3,
            "cohort_id": "returning",
            "variant": {"voice": "V2"},
            "connected": True,
            "engaged": False,
            "disposition": "concluded_not_interested",
        }
    )
    fact = derive_fact(event)
    assert (fact.dials, fact.connected, fact.concluded, fact.uploaded) == (1, 1, 1, 0)
    assert fact.occurred_at == datetime.fromisoformat("2027-01-04T01:00:00+05:30")
    assert fact.person_id == "person-1"
    assert fact.observed_cohort_id == "returning"
    assert fact.variant == {"voice": "V2"}


def test_pickup_without_conclusion_and_campaign_upload_have_separate_counts():
    pickup = derive_fact(
        _event(
            {
                "type": "call",
                "ts": "2027-01-04T01:00:00+05:30",
                "workflow_id": "wf",
                "lead_id": "lead-1",
                "connected": True,
                "disposition": "short_hangup",
            }
        )
    )
    campaign = derive_fact(
        _event(
            {
                "type": "campaign_created",
                "ts": "2027-01-04T01:00:00+05:30",
                "workflow_id": "wf",
                "campaign_id": "camp-1",
                "leads": 17,
            }
        )
    )
    assert (pickup.dials, pickup.connected, pickup.concluded) == (1, 1, 0)
    assert campaign.uploaded == 17
    assert campaign.dials == campaign.connected == campaign.concluded == 0


@pytest.mark.parametrize(
    ("payload", "expected_quality"),
    [
        ({"type": "call", "workflow_id": "wf", "connected": True}, "invalid"),
        (
            {
                "type": "call",
                "ts": "2027-01-04T01:00:00+05:30",
                "workflow_id": "wf",
                "connected": False,
                "disposition": "concluded_interested",
            },
            "invalid",
        ),
        ({"type": "lead", "workflow_id": "wf", "lead_id": "l1"}, "incomplete"),
        ({"type": "future_event", "workflow_id": "wf"}, "unsupported"),
        ({"type": "call", "connected": True}, "invalid"),
    ],
)
def test_invalid_missing_or_unknown_data_is_explicit(payload, expected_quality):
    fact = derive_fact(_event(payload))
    assert fact.quality == expected_quality
    assert fact.dials == fact.connected == fact.concluded == 0


def test_processor_rejects_unbounded_or_empty_page_configuration():
    with pytest.raises(ValueError, match="page size"):
        KpiFactProcessor(None, page_size=0)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="page size"):
        KpiFactProcessor(None, page_size=3000)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="page size"):
        KpiFactProcessor(None, page_size=True)  # type: ignore[arg-type]
