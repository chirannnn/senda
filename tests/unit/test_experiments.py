"""Observed-fact experiment decision tests."""

from datetime import UTC, datetime, timedelta

import pytest

from controller.experiments import ExperimentEvaluator, ExperimentPlan
from controller.statistics.models import Decision
from controller.storage.models import KpiFact


class FactStore:
    def __init__(self, facts):
        self.facts = facts
        self.query_count = 0

    async def list_experiment_facts(self, **query):
        self.query_count += 1
        return [
            fact
            for fact in self.facts
            if fact.source_event_id > query["after_event_id"]
            and fact.source_event_id <= query["cutoff_event_id"]
            and fact.workflow_id == query["workflow_id"]
            and fact.config_version == query["config_version"]
            and fact.observed_cohort_id == query["cohort_id"]
            and fact.quality == "valid"
            and fact.event_type == "call"
            and fact.person_id is not None
            and fact.occurred_at >= query["start_at"]
            and fact.occurred_at < query["end_at"]
            and fact.variant is not None
            and fact.variant.get(query["dimension"]) in query["arm_values"]
        ][: query["limit"]]


def _plan(**changes):
    start = datetime(2027, 1, 4, tzinfo=UTC)
    values = {
        "experiment_id": "exp-1",
        "workflow_id": "wf",
        "config_version": 2,
        "cohort_id": "default",
        "dimension": "voice",
        "control_value": "V1",
        "challengers": ("V2",),
        "start_at": start,
        "end_at": start + timedelta(days=1),
        "cutoff_event_id": 10_000,
        "objective": {
            "primary": "engagement_rate",
            "guardrail": {"pickup_rate_max_drop": 0.19},
        },
        "allocation_probabilities": {"V1": 0.5, "V2": 0.5},
        "imbalance_tolerance": 0.5,
        "primary_baseline_rate": 0.05,
        "guardrail_baseline_rate": 0.8,
        "intracluster_correlation": 0.0,
        "mean_cluster_size": 1.0,
        "power": 0.8,
        "mde": 0.8,
        "bootstrap_replicates": 100,
    }
    values.update(changes)
    return ExperimentPlan(**values)


def _fact(
    event_id, person, arm, *, engaged, connected=True, quality="valid", version=2
):
    return KpiFact(
        source_event_id=event_id,
        source_chunk=1,
        source_kind="events",
        workflow_id="wf",
        event_type="call",
        occurred_at=datetime(2027, 1, 4, tzinfo=UTC),
        campaign_id="c1",
        lead_id=f"lead-{event_id}",
        person_id=person,
        config_version=version,
        observed_cohort_id="default",
        variant={"voice": arm},
        features=None,
        quality=quality,
        quality_detail=None,
        dials=1,
        connected=int(connected),
        concluded=int(engaged),
        uploaded=0,
    )


@pytest.mark.anyio
async def test_insufficient_evidence_does_not_choose_winner():
    store = FactStore([_fact(1, "person", "V1", engaged=True)])
    result = await ExperimentEvaluator(store).evaluate(_plan())
    assert result.decision == Decision.INSUFFICIENT_EVIDENCE
    assert result.winner is None
    assert store.query_count == 2


@pytest.mark.anyio
async def test_winner_uses_valid_current_version_facts_and_excludes_crossovers():
    facts = []
    event_id = 1
    for arm, successes in (("V1", 1), ("V2", 99)):
        for index in range(100):
            facts.append(
                _fact(event_id, f"{arm}-{index}", arm, engaged=index < successes)
            )
            event_id += 1
    facts.extend(
        [
            _fact(event_id, "crossover", "V1", engaged=True),
            _fact(event_id + 1, "crossover", "V2", engaged=True),
            _fact(event_id + 2, "invalid", "V2", engaged=True, quality="incomplete"),
            _fact(event_id + 3, "old-version", "V2", engaged=True, version=1),
        ]
    )
    result = await ExperimentEvaluator(FactStore(facts), page_size=7).evaluate(_plan())
    assert result.decision == Decision.WINNER
    assert result.winner == "V2"
    assert result.excluded_crossover_people == 1
    variant = result.variants[0]
    assert variant.primary_counts["V2"].successes == 99
    assert variant.primary_counts["V2"].people == 100
    assert variant.guardrail_margin == 0.19


def test_missing_or_malformed_workflow_guardrail_is_rejected():
    with pytest.raises(ValueError, match="guardrail"):
        _plan(objective={"primary": "pickup_rate", "guardrail": {}})
    with pytest.raises(ValueError, match="max-drop"):
        _plan(
            objective={
                "primary": "pickup_rate",
                "guardrail": {"engagement_rate": 0.2},
            }
        )


def test_plan_requires_fixed_cutoff_allocation_and_cluster_assumptions():
    with pytest.raises(ValueError, match="allocation probabilities"):
        _plan(allocation_probabilities={"V1": 0.5})
    with pytest.raises(ValueError, match="timezone-aware"):
        _plan(start_at=datetime(2027, 1, 4))
    with pytest.raises(ValueError, match="intracluster"):
        _plan(intracluster_correlation=1.5)
