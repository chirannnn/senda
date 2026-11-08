"""Population QA evidence tests use only persisted-contract-shaped facts."""

import pytest

from controller.analytics.facts import derive_fact
from controller.incidents import aggregate_population_qa, validation_credit_cost
from controller.storage.models import SimulatorEvent


def _event(event_id: int, *, person: str = "person", qa: object = None):
    payload = {
        "event_id": event_id,
        "type": "call",
        "ts": "2027-01-04T01:00:00+05:30",
        "workflow_id": "wf",
        "lead_id": f"lead-{event_id}",
        "person_id": person,
        "config_version": 2,
        "connected": True,
        "disposition": "short_hangup",
    }
    if qa is not None:
        payload["qa"] = qa
    return derive_fact(
        SimulatorEvent(
            event_id=event_id, chunk_number=1, kind="events", payload=payload
        )
    )


class _FactStore:
    def __init__(self, rows):
        self.rows = rows

    async def list_incident_qa_facts(self, **kwargs):
        return [
            row for row in self.rows if row.source_event_id > kwargs["after_event_id"]
        ][: kwargs["limit"]]


@pytest.mark.anyio
async def test_qa_facts_are_validated_separately_from_kpi_quality():
    valid = _event(1, qa={"hallucination_flag": True, "flow_adherence": 0.75})
    missing = _event(2)
    malformed = _event(
        3, qa={"hallucination_flag": "true", "flow_adherence": float("nan")}
    )
    assert valid.quality == "valid"
    assert (valid.qa_quality, valid.qa_hallucination, valid.qa_flow_adherence) == (
        "valid",
        True,
        0.75,
    )
    assert missing.qa_quality == "incomplete"
    assert malformed.qa_quality == "invalid"


@pytest.mark.anyio
async def test_population_qa_clusters_repeated_calls_by_person_deterministically():
    facts = [
        _event(
            index,
            person=f"p{(index - 1) // 2}",
            qa={"hallucination_flag": index % 2 == 0, "flow_adherence": 1.0},
        )
        for index in range(1, 7)
    ]
    kwargs = {
        "workflow_id": "wf",
        "config_version": 2,
        "cutoff_event_id": 99,
        "minimum_people": 3,
        "max_incomplete_fraction": 0.0,
        "replicates": 500,
        "page_size": 2,
    }
    first = await aggregate_population_qa(_FactStore(facts), **kwargs)
    second = await aggregate_population_qa(_FactStore(facts), **kwargs)
    assert first == second
    assert first.sufficient
    assert first.call_count == first.valid_qa_calls == 6
    assert first.person_count == 3
    assert first.hallucination_rate == pytest.approx(0.5)
    assert first.lower <= first.hallucination_rate <= first.upper


@pytest.mark.anyio
async def test_incomplete_and_insufficient_population_is_rejected():
    facts = [
        _event(1, qa={"hallucination_flag": True, "flow_adherence": 0.5}),
        _event(2),
    ]
    evidence = await aggregate_population_qa(
        _FactStore(facts),
        workflow_id="wf",
        config_version=2,
        cutoff_event_id=2,
        minimum_people=2,
        max_incomplete_fraction=0.1,
        replicates=100,
    )
    assert not evidence.sufficient
    assert evidence.hallucination_rate is None
    assert evidence.incomplete_qa_calls == 1


@pytest.mark.parametrize(
    "sample_size, expected", [(50, 1), (100, 1), (101, 2), (2000, 20)]
)
def test_validation_cost_uses_documented_ceiling(sample_size, expected):
    assert validation_credit_cost(sample_size) == expected


@pytest.mark.parametrize("sample_size", [49, 2001, True, 100.0])
def test_validation_cost_rejects_undocumented_sample_size(sample_size):
    with pytest.raises(ValueError, match="sample size"):
        validation_credit_cost(sample_size)
