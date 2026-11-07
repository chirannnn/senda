"""PostgreSQL durability and immutable retry checks for experiment decisions."""

import os
from datetime import UTC, datetime, timedelta

import pytest

from controller.experiments import ExperimentEvaluator, ExperimentPlan
from controller.storage.audit import AuditLog
from controller.storage.store import (
    DatabaseStore,
    EventRecord,
    ExperimentDecisionConflictError,
    KpiFactRecord,
)


def _experiment_plan(**changes):
    start = datetime(2027, 1, 4, tzinfo=UTC)
    values = {
        "experiment_id": "experiment-full-recovery",
        "workflow_id": "wf",
        "config_version": 2,
        "cohort_id": "default",
        "dimension": "voice",
        "control_value": "V1",
        "challengers": ("V2",),
        "start_at": start,
        "end_at": start + timedelta(days=1),
        "cutoff_event_id": 9_300_199,
        "objective": {
            "primary": "engagement_rate",
            "guardrail": {"pickup_rate_max_drop": 0.5},
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


async def _save(
    store,
    *,
    experiment_id="experiment-recovery-1",
    input_hash="a" * 64,
    estimate=0.12,
):
    async with store.transaction() as session:
        return await store.save_experiment_decision(
            session,
            experiment_id=experiment_id,
            workflow_id="experiment-workflow",
            config_version=4,
            cohort_id="default",
            policy_version="stat-policy-v1",
            cutoff_event_id=9981,
            input_hash=input_hash,
            plan_snapshot={"end_at": "2027-01-05T00:00:00+00:00"},
            input_snapshot={"source_fact_count": 42, "input_sha256": input_hash},
            decision={"decision": "winner", "estimate": estimate},
        )


@pytest.mark.anyio
async def test_decision_persists_retries_idempotently_and_survives_store_reopen(
    postgres_store,
):
    (saved, inserted) = await _save(postgres_store)
    (duplicate, duplicate_inserted) = await _save(postgres_store)
    assert inserted is True
    assert duplicate_inserted is False
    assert saved.experiment_id == duplicate.experiment_id
    assert duplicate.input_hash == "a" * 64
    assert duplicate.decision["estimate"] == 0.12

    url = os.environ["CONTROLLER_TEST_DATABASE_URL"]
    reopened = DatabaseStore(url)
    try:
        await reopened.initialize()
        recovered = await reopened.get_experiment_decision("experiment-recovery-1")
        assert recovered is not None
        assert recovered.cutoff_event_id == 9981
        assert recovered.plan_snapshot["end_at"] == "2027-01-05T00:00:00+00:00"
        assert recovered.input_snapshot["source_fact_count"] == 42
        assert recovered.decision == {"decision": "winner", "estimate": 0.12}
    finally:
        await reopened.close()


@pytest.mark.anyio
async def test_reusing_experiment_id_for_different_snapshot_is_rejected(postgres_store):
    await _save(postgres_store)
    with pytest.raises(ExperimentDecisionConflictError):
        await _save(postgres_store, input_hash="b" * 64)

    stored = await postgres_store.get_experiment_decision("experiment-recovery-1")
    assert stored is not None
    assert stored.input_hash == "a" * 64
    assert stored.decision["estimate"] == 0.12


@pytest.mark.anyio
async def test_second_experiment_family_for_same_config_is_rejected(postgres_store):
    await _save(postgres_store)
    with pytest.raises(ExperimentDecisionConflictError, match="config version"):
        await _save(postgres_store, experiment_id="another-peek")


@pytest.mark.anyio
async def test_insufficient_decision_and_audit_replay_without_bootstrap(postgres_store):
    plan = _experiment_plan(
        experiment_id="experiment-insufficient",
        cutoff_event_id=0,
    )
    evaluator = ExperimentEvaluator(postgres_store)
    first = await evaluator.evaluate_and_persist(plan)
    replay = await evaluator.evaluate_and_persist(plan)
    assert first == replay
    assert first.winner is None
    assert first.variants[0].primary is None
    assert first.decision.value == "insufficient_evidence"
    records = await AuditLog(postgres_store).list_for_workflow("wf")
    assert [record.action for record in records] == ["experiment_decided"]


@pytest.mark.anyio
async def test_evaluation_is_frozen_and_recovered_after_restart(postgres_store):
    facts = []
    events = []
    event_id = 9_300_000
    occurred_at = datetime(2027, 1, 4, tzinfo=UTC)
    for arm, successes in (("V1", 1), ("V2", 99)):
        for index in range(100):
            connected = True
            events.append(
                EventRecord(
                    event_id,
                    1,
                    "events",
                    {"event_id": event_id, "type": "call", "workflow_id": "wf"},
                )
            )
            facts.append(
                KpiFactRecord(
                    source_event_id=event_id,
                    source_chunk=1,
                    source_kind="events",
                    workflow_id="wf",
                    event_type="call",
                    occurred_at=occurred_at,
                    campaign_id="c1",
                    lead_id=f"lead-{event_id}",
                    person_id=f"{arm}-{index}",
                    config_version=2,
                    observed_cohort_id="default",
                    variant={"voice": arm},
                    features=None,
                    quality="valid",
                    quality_detail=None,
                    dials=1,
                    connected=int(connected),
                    concluded=int(index < successes),
                )
            )
            event_id += 1
    async with postgres_store.transaction() as session:
        await postgres_store.insert_events(session, events)
    await postgres_store.commit_kpi_page("test-experiment-facts", facts, event_id - 1)

    plan = _experiment_plan(
        experiment_id="experiment-full-recovery",
        cutoff_event_id=event_id - 1,
        objective={
            "primary": "engagement_rate",
            "guardrail": {"pickup_rate_max_drop": 0.5},
        },
        primary_baseline_rate=0.05,
        guardrail_baseline_rate=0.8,
        mde=0.8,
    )
    evaluator = ExperimentEvaluator(postgres_store, page_size=17)
    initial = await evaluator.evaluate_and_persist(plan)
    assert initial.winner == "V2"
    assert initial.input_snapshot["source_fact_count"] == 200

    database_url = os.environ["CONTROLLER_TEST_DATABASE_URL"]
    await postgres_store.close()
    reopened = DatabaseStore(database_url)
    try:
        await reopened.initialize()
        recovered = await ExperimentEvaluator(reopened).evaluate_and_persist(plan)
        assert recovered == initial
        changed_plan = _experiment_plan(
            experiment_id="experiment-full-recovery",
            cutoff_event_id=event_id,
            objective=plan.objective,
            primary_baseline_rate=plan.primary_baseline_rate,
            guardrail_baseline_rate=plan.guardrail_baseline_rate,
            mde=plan.mde,
        )
        with pytest.raises(ExperimentDecisionConflictError):
            await ExperimentEvaluator(reopened).evaluate_and_persist(changed_plan)
    finally:
        await reopened.close()
