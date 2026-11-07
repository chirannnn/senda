"""The statistical winner path reuses the durable approval lifecycle."""

from datetime import UTC, datetime, timedelta

import pytest

from controller.experiments import (
    ExperimentResult,
    VariantResult,
    _result_to_record,
    create_governed_proposal,
)
from controller.statistics.estimation import ArmCounts, BootstrapResult
from controller.statistics.models import Decision, Kpi
from controller.storage.audit import AuditLog


class ApprovalAPI:
    def __init__(self, *, version=2):
        self.version = version
        self.submitted = []
        self.applied = []

    async def list_workflows(self):
        return [
            {
                "workflow_id": "experiment-workflow",
                "objective": {
                    "primary": "engagement_rate",
                    "guardrail": {"pickup_rate_max_drop": 0.1},
                },
                "options": {"voice": ["V1", "V2"]},
            }
        ]

    async def get_workflow_config(self, workflow_id):
        assert workflow_id == "experiment-workflow"
        return {
            "workflow_id": workflow_id,
            "version": self.version,
            "config": {
                "cohorts": [],
                "allocations": {"default": {"voice": {"V1": 0.5, "V2": 0.5}}},
                "prompt_version": "P1",
            },
        }

    async def submit_approval(self, payload):
        self.submitted.append(payload)
        return {
            "approval_id": "approval-exp-1",
            "proposal_id": payload["proposal_id"],
            "workflow_id": payload["workflow_id"],
            "base_version": payload["base_version"],
            "changed_dims": ["voice"],
            "status": "pending",
        }


def _winner_result(*, decision=Decision.WINNER):
    control = ArmCounts(successes=10, trials=100, people=100)
    treatment = ArmCounts(successes=40, trials=100, people=100)
    primary = BootstrapResult(0.3, 0.2, 0.4, 0.001, 10_000)
    guardrail = BootstrapResult(-0.01, -0.05, 0.03, 0.001, 10_000)
    variant = VariantResult(
        challenger="V2",
        primary=primary,
        guardrail=guardrail,
        primary_counts={"V1": control, "V2": treatment},
        guardrail_counts={"V1": control, "V2": treatment},
        required_primary_trials=(50, 50),
        required_guardrail_trials=(50, 50),
        primary_adjusted_p=0.01,
        guardrail_adjusted_p=0.01,
        guardrail_margin=0.1,
        balanced=True,
        sufficient=True,
        guardrail_passed=True,
        won=decision == Decision.WINNER,
    )
    return ExperimentResult(
        experiment_id="experiment-proposal-1",
        workflow_id="experiment-workflow",
        config_version=2,
        cohort_id="default",
        dimension="voice",
        cutoff_event_id=900,
        start_at=datetime(2027, 1, 4, tzinfo=UTC),
        end_at=datetime(2027, 1, 4, tzinfo=UTC) + timedelta(days=1),
        primary_kpi=Kpi.ENGAGEMENT,
        guardrail_kpi=Kpi.PICKUP,
        decision=decision,
        winner="V2" if decision == Decision.WINNER else None,
        reason="fixed-window rule passed"
        if decision == Decision.WINNER
        else "not safe",
        excluded_crossover_people=2,
        variants=(variant,),
        policy_version="stat-policy-v1",
        input_hash="a" * 64,
        plan_snapshot={
            "objective": {
                "primary": "engagement_rate",
                "guardrail": {"pickup_rate_max_drop": 0.1},
            },
            "control_value": "V1",
            "mde": 0.05,
        },
        input_snapshot={"source_fact_count": 200, "input_sha256": "a" * 64},
    )


async def _ensure_workflow(store):
    async with store.transaction() as session:
        await store.ensure_workflow(session, "experiment-workflow")


async def _persist_result(store, result):
    async with store.transaction() as session:
        await store.save_experiment_decision(
            session,
            experiment_id=result.experiment_id,
            workflow_id=result.workflow_id,
            config_version=result.config_version,
            cohort_id=result.cohort_id,
            policy_version=result.policy_version,
            cutoff_event_id=result.cutoff_event_id,
            input_hash=result.input_hash,
            plan_snapshot=result.plan_snapshot,
            input_snapshot=result.input_snapshot,
            decision=_result_to_record(result),
        )


@pytest.mark.anyio
async def test_winner_becomes_a_proposal_but_is_never_applied(postgres_store):
    await _ensure_workflow(postgres_store)
    api = ApprovalAPI()
    audit = AuditLog(postgres_store)
    result = _winner_result()
    await _persist_result(postgres_store, result)

    remote = await create_governed_proposal(
        result, simulator=api, store=postgres_store, audit=audit
    )

    assert remote["status"] == "pending"
    assert len(api.submitted) == 1
    assert api.applied == []
    payload = api.submitted[0]
    assert payload["base_version"] == 2
    assert payload["proposed_config"]["allocations"]["default"]["voice"] == {"V2": 1.0}
    assert payload["evidence"]["input_hash"] == "a" * 64
    assert payload["evidence"]["primary"]["estimate"] == 0.3
    stored = await postgres_store.get_governance_proposal(payload["proposal_id"])
    assert stored is not None
    assert stored.status == "pending"
    records = await audit.list_for_workflow("experiment-workflow")
    assert {row.action for row in records} >= {
        "approval_submission_prepared",
        "approval_reconciled",
        "experiment_governance_proposal_linked",
    }


@pytest.mark.anyio
async def test_stale_config_creates_no_proposal_and_audits_reason(postgres_store):
    await _ensure_workflow(postgres_store)
    api = ApprovalAPI(version=3)
    audit = AuditLog(postgres_store)
    result = _winner_result()
    await _persist_result(postgres_store, result)

    result = await create_governed_proposal(
        result, simulator=api, store=postgres_store, audit=audit
    )

    assert result is None
    assert api.submitted == []
    rows = await audit.list_for_workflow("experiment-workflow")
    assert rows[-1].action == "experiment_proposal_skipped"
    assert rows[-1].evidence["details"]["current_config_version"] == 3


@pytest.mark.anyio
async def test_unsafe_decision_never_calls_simulator_or_submits_proposal(
    postgres_store,
):
    await _ensure_workflow(postgres_store)
    api = ApprovalAPI()

    result = await create_governed_proposal(
        _winner_result(decision=Decision.UNSAFE),
        simulator=api,
        store=postgres_store,
        audit=AuditLog(postgres_store),
    )

    assert result is None
    assert api.submitted == []
    rows = await AuditLog(postgres_store).list_for_workflow("experiment-workflow")
    assert rows[-1].action == "experiment_proposal_skipped"
