"""Deterministic evaluation of fixed, observed variant experiments."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from controller.governance.proposals import (
    ApprovalSubmitAPI,
    ProposalPayload,
    submit_proposal,
)
from controller.statistics.estimation import (
    ArmCounts,
    BootstrapInsufficientDataError,
    BootstrapResult,
    arm_counts,
    estimate_rate_difference,
    holm_adjust,
    required_noninferiority_sample_sizes,
    required_sample_sizes,
)
from controller.statistics.models import (
    STATISTICS_POLICY_VERSION,
    Decision,
    Kpi,
    PersonOutcome,
)
from controller.storage.audit import AuditEntry, AuditLog
from controller.storage.models import KpiFact
from controller.storage.store import (
    DatabaseStore,
    ExperimentDecisionConflictError,
)
from controller.telemetry import RuntimeTelemetry

_DIMENSIONS = {"welcome_message", "voice"}
_PRIMARY_NAMES = {"pickup_rate": Kpi.PICKUP, "engagement_rate": Kpi.ENGAGEMENT}


class GovernedExperimentAPI(ApprovalSubmitAPI, Protocol):
    """Documented reads needed to recheck config before approval submission."""

    async def list_workflows(self) -> list[dict[str, Any]]: ...

    async def get_workflow_config(self, workflow_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    """Predeclared experiment and statistical assumptions for one frozen window."""

    experiment_id: str
    workflow_id: str
    config_version: int
    cohort_id: str
    dimension: str
    control_value: str
    challengers: tuple[str, ...]
    start_at: datetime
    end_at: datetime
    cutoff_event_id: int
    objective: dict[str, Any]
    allocation_probabilities: dict[str, float]
    imbalance_tolerance: float
    primary_baseline_rate: float
    guardrail_baseline_rate: float
    intracluster_correlation: float
    mean_cluster_size: float
    power: float
    mde: float = 0.05
    alpha: float = 0.05
    bootstrap_replicates: int = 10_000
    policy_version: str = STATISTICS_POLICY_VERSION

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (
                self.experiment_id,
                self.workflow_id,
                self.cohort_id,
                self.policy_version,
                self.control_value,
                self.dimension,
            )
        ):
            raise ValueError(
                "experiment identity and arm values must be non-empty strings"
            )
        if (
            not isinstance(self.challengers, tuple)
            or not self.challengers
            or any(
                not isinstance(value, str) or not value.strip()
                for value in self.challengers
            )
        ):
            raise ValueError("challengers must be a non-empty tuple of variant strings")
        if not all(
            (self.experiment_id, self.workflow_id, self.cohort_id, self.policy_version)
        ):
            raise ValueError("experiment, workflow and cohort IDs are required")
        if self.dimension not in _DIMENSIONS:
            raise ValueError("only welcome_message or voice can be tested")
        if not self.control_value or not self.challengers:
            raise ValueError("control and at least one challenger are required")
        if (
            len(set(self.challengers)) != len(self.challengers)
            or self.control_value in self.challengers
        ):
            raise ValueError("experiment arm values must be unique")
        if len(self.challengers) > 3:
            raise ValueError(
                "at most three challengers may share one comparison family"
            )
        if (
            isinstance(self.config_version, bool)
            or not isinstance(self.config_version, int)
            or self.config_version < 0
            or isinstance(self.cutoff_event_id, bool)
            or not isinstance(self.cutoff_event_id, int)
            or self.cutoff_event_id < 0
        ):
            raise ValueError("config version and event cutoff must be nonnegative")
        if (
            not isinstance(self.start_at, datetime)
            or not isinstance(self.end_at, datetime)
            or self.start_at.utcoffset() is None
            or self.end_at.utcoffset() is None
            or self.end_at <= self.start_at
        ):
            raise ValueError(
                "fixed experiment window must be timezone-aware and ordered"
            )
        if not isinstance(self.objective, dict) or not isinstance(
            self.objective.get("guardrail"), dict
        ):
            raise TypeError("workflow objective and configured guardrail are required")
        if not isinstance(self.allocation_probabilities, dict):
            raise TypeError("declared allocation probabilities are required")
        expected_arms = {self.control_value, *self.challengers}
        if set(self.allocation_probabilities) != expected_arms:
            raise ValueError(
                "allocation probabilities must name each declared arm exactly"
            )
        probabilities = tuple(self.allocation_probabilities.values())
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            or value > 1
            for value in probabilities
        ):
            raise ValueError("allocation probabilities must be finite and in (0, 1]")
        if not math.isclose(sum(probabilities), 1.0, rel_tol=0, abs_tol=1e-6):
            raise ValueError("declared arm allocation probabilities must sum to one")
        if (
            isinstance(self.imbalance_tolerance, bool)
            or not isinstance(self.imbalance_tolerance, (int, float))
            or not math.isfinite(self.imbalance_tolerance)
            or not 0 <= self.imbalance_tolerance < 1
        ):
            raise ValueError(
                "a predeclared relative allocation tolerance in [0,1) is required"
            )
        _rate(self.primary_baseline_rate, "primary baseline")
        _rate(self.guardrail_baseline_rate, "guardrail baseline")
        _rate(self.mde, "MDE", allow_zero=False)
        if self.mde > 1:
            raise ValueError("MDE must not exceed one")
        if (
            isinstance(self.intracluster_correlation, bool)
            or not isinstance(self.intracluster_correlation, (int, float))
            or not math.isfinite(self.intracluster_correlation)
            or not 0 <= self.intracluster_correlation <= 1
        ):
            raise ValueError("intracluster correlation must be in [0,1]")
        if (
            isinstance(self.mean_cluster_size, bool)
            or not isinstance(self.mean_cluster_size, (int, float))
            or not math.isfinite(self.mean_cluster_size)
            or self.mean_cluster_size < 1
        ):
            raise ValueError("mean cluster size must be at least one")
        if (
            isinstance(self.power, bool)
            or not isinstance(self.power, (int, float))
            or not math.isfinite(self.power)
            or not 0 < self.power < 1
        ):
            raise ValueError("a planned power in (0,1) is required")
        if (
            isinstance(self.alpha, bool)
            or not isinstance(self.alpha, (int, float))
            or not math.isfinite(self.alpha)
            or not 0 < self.alpha < 1
        ):
            raise ValueError("alpha must be in (0,1)")
        if (
            isinstance(self.bootstrap_replicates, bool)
            or not isinstance(self.bootstrap_replicates, int)
            or self.bootstrap_replicates < 100
        ):
            raise ValueError("at least 100 bootstrap replicates are required")
        _objective_kpis(self.objective)


@dataclass(frozen=True, slots=True)
class VariantResult:
    """Independent KPI evidence for one challenger against the control."""

    challenger: str
    primary: BootstrapResult | None
    guardrail: BootstrapResult | None
    primary_counts: dict[str, ArmCounts]
    guardrail_counts: dict[str, ArmCounts]
    required_primary_trials: tuple[int, int]
    required_guardrail_trials: tuple[int, int]
    primary_adjusted_p: float
    guardrail_adjusted_p: float
    guardrail_margin: float
    balanced: bool
    sufficient: bool
    guardrail_passed: bool
    won: bool
    evidence_error: str | None = None


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    """Reproducible decision and reason for a frozen experiment snapshot."""

    experiment_id: str
    workflow_id: str
    config_version: int
    cohort_id: str
    dimension: str
    cutoff_event_id: int
    start_at: datetime
    end_at: datetime
    primary_kpi: Kpi
    guardrail_kpi: Kpi
    decision: Decision
    winner: str | None
    reason: str
    excluded_crossover_people: int
    variants: tuple[VariantResult, ...]
    policy_version: str
    input_hash: str
    plan_snapshot: dict[str, object]
    input_snapshot: dict[str, object]


class ExperimentEvaluator:
    """Query valid persisted call facts and make one deterministic final decision."""

    def __init__(
        self,
        store: DatabaseStore,
        *,
        page_size: int = 1000,
        audit: AuditLog | None = None,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        if page_size < 1:
            raise ValueError("page size must be positive")
        self._store = store
        self._page_size = page_size
        self._audit = audit or AuditLog(store)
        self._telemetry = telemetry

    async def evaluate(self, plan: ExperimentPlan) -> ExperimentResult:
        """Persist and return the one terminal comparison for this config family."""
        return await self.evaluate_and_persist(plan)

    async def _evaluate_snapshot(self, plan: ExperimentPlan) -> ExperimentResult:
        """Evaluate exactly the predeclared time window and event-ID cutoff.

        This is a terminal analysis for the supplied cutoff, not an interim look.
        Persistence/idempotency for the result is added by the following chunk
        subtask; callers must not evaluate and act on changing cutoffs.
        """
        primary_kpi, guardrail_kpi, margin = _objective_kpis(plan.objective)
        arms = (plan.control_value, *plan.challengers)
        after_event_id = 0
        facts: list[KpiFact] = []
        while True:
            page = await self._store.list_experiment_facts(
                workflow_id=plan.workflow_id,
                config_version=plan.config_version,
                cohort_id=plan.cohort_id,
                dimension=plan.dimension,
                arm_values=arms,
                start_at=plan.start_at,
                end_at=plan.end_at,
                cutoff_event_id=plan.cutoff_event_id,
                after_event_id=after_event_id,
                limit=self._page_size,
            )
            if not page:
                break
            facts.extend(page)
            after_event_id = page[-1].source_event_id

        grouped: dict[str, list[KpiFact]] = defaultdict(list)
        person_arms: dict[str, set[str]] = defaultdict(set)
        for fact in facts:
            assert fact.person_id is not None and fact.variant is not None
            value = fact.variant.get(plan.dimension)
            if not isinstance(value, str) or value not in arms:
                continue
            grouped[value].append(fact)
            person_arms[fact.person_id].add(value)
        crossovers = {
            person for person, values in person_arms.items() if len(values) > 1
        }
        outcomes = {
            kpi: _person_outcomes(grouped, arms, plan.dimension, crossovers, kpi)
            for kpi in (primary_kpi, guardrail_kpi)
        }

        raw: list[dict[str, Any]] = []
        for challenger in plan.challengers:
            ratio = (
                plan.allocation_probabilities[challenger]
                / plan.allocation_probabilities[plan.control_value]
            )
            primary_required = _sample_size(
                plan, plan.primary_baseline_rate, plan.mde, ratio
            )
            guardrail_required = required_noninferiority_sample_sizes(
                baseline_rate=plan.guardrail_baseline_rate,
                margin=margin,
                allocation_ratio=ratio,
                intracluster_correlation=plan.intracluster_correlation,
                mean_cluster_size=plan.mean_cluster_size,
                alpha=plan.alpha,
                power=plan.power,
                comparisons=len(plan.challengers),
            )
            primary_rows = outcomes[primary_kpi]
            guardrail_rows = outcomes[guardrail_kpi]
            p_counts = _pair_counts(primary_rows, plan.control_value, challenger)
            g_counts = _pair_counts(guardrail_rows, plan.control_value, challenger)
            balanced = _balanced(
                p_counts,
                plan,
                challenger,
            ) and _balanced(g_counts, plan, challenger)
            sufficient = (
                balanced
                and _enough(p_counts, plan.control_value, challenger, primary_required)
                and _enough(
                    g_counts, plan.control_value, challenger, guardrail_required
                )
                and min(
                    p_counts[plan.control_value].people,
                    p_counts[challenger].people,
                    g_counts[plan.control_value].people,
                    g_counts[challenger].people,
                )
                >= 2
            )
            if sufficient:
                seed_key = (
                    f"{plan.policy_version}:{plan.experiment_id}:"
                    f"{plan.cutoff_event_id}:{plan.dimension}:{challenger}"
                )
                try:
                    primary = estimate_rate_difference(
                        primary_rows,
                        control_arm=plan.control_value,
                        treatment_arm=challenger,
                        seed_key=seed_key + ":" + primary_kpi.value,
                        replicates=plan.bootstrap_replicates,
                        alpha=plan.alpha / len(plan.challengers),
                    )
                    guardrail = estimate_rate_difference(
                        guardrail_rows,
                        control_arm=plan.control_value,
                        treatment_arm=challenger,
                        seed_key=seed_key + ":" + guardrail_kpi.value,
                        replicates=plan.bootstrap_replicates,
                        alpha=plan.alpha / len(plan.challengers),
                        evidence_threshold=-margin,
                    )
                    bootstrap_error = None
                except BootstrapInsufficientDataError as exc:
                    primary = guardrail = None
                    sufficient = False
                    bootstrap_error = str(exc)
            else:
                primary = guardrail = None
                bootstrap_error = None
            raw.append(
                {
                    "challenger": challenger,
                    "primary": primary,
                    "guardrail": guardrail,
                    "primary_counts": p_counts,
                    "guardrail_counts": g_counts,
                    "primary_required": primary_required,
                    "guardrail_required": guardrail_required,
                    "balanced": balanced,
                    "sufficient": sufficient,
                    "margin": margin,
                    "evidence_error": bootstrap_error,
                }
            )

        primary_adjusted = holm_adjust(
            {
                item["challenger"]: (
                    item["primary"].p_not_greater_than_threshold
                    if item["primary"] is not None
                    else 1.0
                )
                for item in raw
            }
        )
        guardrail_adjusted = holm_adjust(
            {
                item["challenger"]: (
                    item["guardrail"].p_not_greater_than_threshold
                    if item["guardrail"] is not None
                    else 1.0
                )
                for item in raw
            }
        )
        results = tuple(
            VariantResult(
                challenger=item["challenger"],
                primary=item["primary"],
                guardrail=item["guardrail"],
                primary_counts=item["primary_counts"],
                guardrail_counts=item["guardrail_counts"],
                required_primary_trials=item["primary_required"],
                required_guardrail_trials=item["guardrail_required"],
                primary_adjusted_p=primary_adjusted.get(item["challenger"], 1.0),
                guardrail_adjusted_p=guardrail_adjusted.get(item["challenger"], 1.0),
                guardrail_margin=item["margin"],
                balanced=item["balanced"],
                sufficient=item["sufficient"],
                guardrail_passed=bool(
                    item["sufficient"]
                    and item["guardrail"].lower > -item["margin"]
                    and guardrail_adjusted.get(item["challenger"], 1.0) <= plan.alpha
                ),
                won=bool(
                    item["sufficient"]
                    and item["primary"].estimate >= plan.mde
                    and primary_adjusted.get(item["challenger"], 1.0) <= plan.alpha
                    and item["guardrail"].lower > -item["margin"]
                    and guardrail_adjusted.get(item["challenger"], 1.0) <= plan.alpha
                ),
                evidence_error=item["evidence_error"],
            )
            for item in raw
        )
        passing = [result for result in results if result.won]
        winner_result = min(
            passing,
            key=lambda result: (
                result.primary_adjusted_p,
                -result.primary.estimate,
                result.challenger,
            ),
            default=None,
        )
        if winner_result is not None:
            decision, winner, reason = (
                Decision.WINNER,
                winner_result.challenger,
                "primary evidence passed Holm correction and configured guardrail non-inferiority",
            )
        elif not results or not any(item.sufficient for item in results):
            bootstrap_errors = [
                item.evidence_error for item in results if item.evidence_error
            ]
            detail = f"; {bootstrap_errors[0]}" if bootstrap_errors else ""
            decision, winner, reason = (
                Decision.INSUFFICIENT_EVIDENCE,
                None,
                "no challenger met predeclared evidence, balance and sample-size requirements"
                + detail,
            )
        elif any(
            item.won is False
            and item.sufficient
            and item.primary.estimate >= plan.mde
            and item.primary_adjusted_p <= plan.alpha
            for item in results
        ):
            decision, winner, reason = (
                Decision.UNSAFE,
                None,
                "primary evidence exists but configured guardrail non-inferiority did not pass",
            )
        else:
            decision, winner, reason = (
                Decision.NO_WINNER,
                None,
                "no challenger met the predeclared primary KPI rule",
            )
        plan_snapshot = _plan_snapshot(plan)
        input_hash, input_snapshot = _input_digest(plan_snapshot, facts)
        return ExperimentResult(
            experiment_id=plan.experiment_id,
            workflow_id=plan.workflow_id,
            config_version=plan.config_version,
            cohort_id=plan.cohort_id,
            dimension=plan.dimension,
            cutoff_event_id=plan.cutoff_event_id,
            start_at=plan.start_at,
            end_at=plan.end_at,
            primary_kpi=primary_kpi,
            guardrail_kpi=guardrail_kpi,
            decision=decision,
            winner=winner,
            reason=reason,
            excluded_crossover_people=len(crossovers),
            variants=results,
            policy_version=plan.policy_version,
            input_hash=input_hash,
            plan_snapshot=plan_snapshot,
            input_snapshot=input_snapshot,
        )

    async def evaluate_and_persist(self, plan: ExperimentPlan) -> ExperimentResult:
        """Return an existing frozen result or atomically persist this final look."""
        existing = await self._store.get_experiment_decision(plan.experiment_id)
        snapshot = _plan_snapshot(plan)
        if existing is not None:
            if existing.plan_snapshot != snapshot:
                raise ExperimentDecisionConflictError(
                    "experiment ID already belongs to a different plan"
                )
            return _result_from_record(existing)
        family = await self._store.get_experiment_decision_for_config(
            plan.workflow_id, plan.config_version
        )
        if family is not None:
            raise ExperimentDecisionConflictError(
                "this workflow config version already has a terminal experiment decision"
            )
        result = await self._evaluate_snapshot(plan)
        async with self._store.transaction() as session:
            _stored, inserted = await self._store.save_experiment_decision(
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
            if inserted:
                await self._audit.append(
                    session,
                    AuditEntry(
                        action="experiment_decided",
                        actor="controller",
                        reason=result.reason,
                        workflow_id=result.workflow_id,
                        resource_type="experiment",
                        resource_id=result.experiment_id,
                        evidence={
                            "decision": _result_to_record(result),
                            "policy_version": result.policy_version,
                            "config_version": result.config_version,
                            "cohort_id": result.cohort_id,
                            "input_hash": result.input_hash,
                            "plan_snapshot": result.plan_snapshot,
                            "input_snapshot": result.input_snapshot,
                            "cutoff_event_id": result.cutoff_event_id,
                        },
                    ),
                )
        if inserted and self._telemetry is not None:
            self._telemetry.record_decision()
        return result


async def create_governed_proposal(
    result: ExperimentResult,
    *,
    simulator: GovernedExperimentAPI,
    store: DatabaseStore,
    audit: AuditLog,
) -> dict[str, Any] | None:
    """Submit an evidence-backed winner through Chunk 5 approvals only.

    This function intentionally never invokes the workflow apply endpoint. The
    approval and apply state machine remain owned by ``controller.governance``.
    """
    if result.decision != Decision.WINNER or result.winner is None:
        await _audit_proposal_skip(
            audit,
            result,
            "statistical decision did not establish a safe winner",
            {"decision": result.decision.value, "reason": result.reason},
        )
        return None
    durable = await store.get_experiment_decision(result.experiment_id)
    if (
        durable is None
        or durable.input_hash != result.input_hash
        or durable.plan_snapshot != result.plan_snapshot
        or durable.decision != _result_to_record(result)
    ):
        await _audit_proposal_skip(
            audit,
            result,
            "only the exact durable experiment decision may create a proposal",
            {"durable_decision_found": durable is not None},
        )
        return None
    selected = next(
        (item for item in result.variants if item.challenger == result.winner), None
    )
    if selected is None or not selected.won or not selected.guardrail_passed:
        await _audit_proposal_skip(
            audit, result, "winner evidence is incomplete or unsafe", {}
        )
        return None

    workflows = await simulator.list_workflows()
    workflow = next(
        (item for item in workflows if item.get("workflow_id") == result.workflow_id),
        None,
    )
    config_response = await simulator.get_workflow_config(result.workflow_id)
    version = config_response.get("version")
    current_config = config_response.get("config")
    stale_reason: str | None = None
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or not isinstance(current_config, dict)
    ):
        stale_reason = "current workflow configuration response is malformed"
    elif version != result.config_version:
        stale_reason = "workflow config version changed after the experiment"
    elif workflow is None or workflow.get("objective") != result.plan_snapshot.get(
        "objective"
    ):
        stale_reason = "workflow objective or guardrail changed after the experiment"
    options = workflow.get("options") if workflow is not None else None
    if stale_reason is None and (
        not isinstance(options, dict)
        or not isinstance(options.get(result.dimension), list)
        or result.winner not in options[result.dimension]
    ):
        stale_reason = "winning value is no longer an allowed workflow option"
    if stale_reason is not None:
        await _audit_proposal_skip(
            audit,
            result,
            stale_reason,
            {
                "expected_config_version": result.config_version,
                "current_config_version": version,
            },
        )
        return None

    cohorts = current_config.get("cohorts", [])
    if result.cohort_id != "default" and (
        not isinstance(cohorts, list)
        or not any(
            isinstance(item, dict) and item.get("id") == result.cohort_id
            for item in cohorts
        )
    ):
        await _audit_proposal_skip(
            audit,
            result,
            "experiment cohort is no longer present in current config",
            {},
        )
        return None

    proposed_config = deepcopy(current_config)
    allocations = proposed_config.get("allocations")
    if not isinstance(allocations, dict):
        await _audit_proposal_skip(
            audit, result, "current config has no valid allocation object", {}
        )
        return None
    target_id = "default" if result.cohort_id == "default" else result.cohort_id
    target = allocations.get(target_id)
    if target is None:
        target = {}
        allocations[target_id] = target
    if not isinstance(target, dict):
        await _audit_proposal_skip(
            audit, result, "current cohort allocation is malformed", {}
        )
        return None
    before = deepcopy(target.get(result.dimension))
    after = {result.winner: 1.0}
    target[result.dimension] = after
    diff = {
        "cohort_id": result.cohort_id,
        "dimension": result.dimension,
        "before": before,
        "after": after,
    }
    primary_evidence = _metric_evidence(selected.primary, selected.primary_counts)
    guardrail_evidence = _metric_evidence(selected.guardrail, selected.guardrail_counts)
    evidence: dict[str, Any] = {
        "experiment_id": result.experiment_id,
        "policy_version": result.policy_version,
        "input_hash": result.input_hash,
        "input_snapshot": result.input_snapshot,
        "cutoff_event_id": result.cutoff_event_id,
        "start_at": result.start_at.isoformat(),
        "end_at": result.end_at.isoformat(),
        "config_version": result.config_version,
        "cohort_id": result.cohort_id,
        "dimension": result.dimension,
        "winner": result.winner,
        "excluded_crossover_people": result.excluded_crossover_people,
        "primary_kpi": result.primary_kpi.value,
        "primary": primary_evidence,
        "guardrail_kpi": result.guardrail_kpi.value,
        "guardrail": guardrail_evidence,
        "guardrail_margin": selected.guardrail_margin,
        "primary_adjusted_p": selected.primary_adjusted_p,
        "guardrail_adjusted_p": selected.guardrail_adjusted_p,
        "mde": result.plan_snapshot["mde"],
        "method": "deterministic person-cluster percentile bootstrap; Holm correction",
    }
    affected_people = (
        selected.primary_counts[result.winner].people
        + selected.primary_counts[result.plan_snapshot["control_value"]].people
    )
    proposal_id = (
        "exp-" + hashlib.sha256(result.experiment_id.encode()).hexdigest()[:32]
    )
    proposal = ProposalPayload(
        proposal_id=proposal_id,
        workflow_id=result.workflow_id,
        base_version=result.config_version,
        proposed_config=proposed_config,
        change_summary=f"Promote {result.dimension} {result.winner} for {result.cohort_id}",
        diff=diff,
        affected_leads=affected_people,
        dry_run={"type": "observed_experiment_comparison", **evidence},
        impact_score=min(1.0, max(0.0, selected.primary.estimate)),
        rationale=(
            f"Fixed-window {result.primary_kpi.value} estimate was "
            f"{selected.primary.estimate:.4f} (MDE {result.plan_snapshot['mde']:.4f}); "
            f"configured {result.guardrail_kpi.value} guardrail passed. "
            "This is an approval request; no configuration was applied."
        ),
        evidence=evidence,
    )
    response = await submit_proposal(simulator, store, audit, proposal)
    await audit.record(
        AuditEntry(
            action="experiment_governance_proposal_linked",
            actor="controller",
            reason="Safe statistical winner submitted through the existing approval lifecycle",
            workflow_id=result.workflow_id,
            resource_type="experiment",
            resource_id=result.experiment_id,
            evidence={
                "experiment_id": result.experiment_id,
                "proposal_id": proposal_id,
                "config_version": result.config_version,
                "input_hash": result.input_hash,
                "diff": diff,
                "evidence": evidence,
                "approval_status": response.get("status"),
            },
        )
    )
    return response


async def _audit_proposal_skip(
    audit: AuditLog,
    result: ExperimentResult,
    reason: str,
    details: dict[str, object],
) -> None:
    await audit.record(
        AuditEntry(
            action="experiment_proposal_skipped",
            actor="controller",
            reason=reason,
            workflow_id=result.workflow_id,
            resource_type="experiment",
            resource_id=result.experiment_id,
            evidence={
                "experiment_id": result.experiment_id,
                "decision": result.decision.value,
                "winner": result.winner,
                "config_version": result.config_version,
                "input_hash": result.input_hash,
                "details": details,
            },
        )
    )


def _metric_evidence(
    estimate: BootstrapResult, counts: dict[str, ArmCounts]
) -> dict[str, object]:
    return {
        "estimate": estimate.estimate,
        "lower": estimate.lower,
        "upper": estimate.upper,
        "replicates": estimate.replicates,
        "arms": {
            arm: {
                "successes": count.successes,
                "trials": count.trials,
                "people": count.people,
                "rate": count.rate,
            }
            for arm, count in sorted(counts.items())
        },
    }


def _objective_kpis(objective: dict[str, Any]) -> tuple[Kpi, Kpi, float]:
    primary_name = objective.get("primary")
    if not isinstance(primary_name, str) or primary_name not in _PRIMARY_NAMES:
        raise ValueError("workflow primary KPI must be pickup_rate or engagement_rate")
    primary = _PRIMARY_NAMES[primary_name]
    guardrail = objective.get("guardrail")
    if not isinstance(guardrail, dict) or len(guardrail) != 1:
        raise ValueError("workflow must declare exactly one configured guardrail")
    configured_name, margin = next(iter(guardrail.items()))
    suffix = "_max_drop"
    if not isinstance(configured_name, str) or not configured_name.endswith(suffix):
        raise ValueError("workflow guardrail must declare its configured max-drop")
    guardrail_name = configured_name[: -len(suffix)]
    if (
        guardrail_name not in _PRIMARY_NAMES
        or _PRIMARY_NAMES[guardrail_name] == primary
    ):
        raise ValueError("workflow guardrail must be the other independent rate KPI")
    if (
        isinstance(margin, bool)
        or not isinstance(margin, (int, float))
        or not math.isfinite(margin)
        or not 0 < margin <= 1
    ):
        raise ValueError(
            "workflow guardrail non-inferiority margin is missing or invalid"
        )
    return primary, _PRIMARY_NAMES[guardrail_name], float(margin)


def _person_outcomes(
    grouped: dict[str, list[KpiFact]],
    arms: tuple[str, ...],
    dimension: str,
    crossovers: set[str],
    kpi: Kpi,
) -> list[PersonOutcome]:
    accum: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for arm in arms:
        for fact in grouped.get(arm, []):
            assert fact.person_id is not None
            if fact.person_id in crossovers:
                continue
            if not fact.variant or fact.variant.get(dimension) != arm:
                continue
            if kpi == Kpi.PICKUP:
                successes, trials = fact.connected, fact.dials
            else:
                successes, trials = fact.concluded, fact.connected
            values = accum[(fact.person_id, arm)]
            values[0] += successes
            values[1] += trials
    return [
        PersonOutcome(person, arm, values[0], values[1])
        for (person, arm), values in sorted(accum.items())
    ]


def _pair_counts(
    outcomes: list[PersonOutcome], control: str, treatment: str
) -> dict[str, ArmCounts]:
    all_counts = arm_counts(outcomes)
    return {
        arm: all_counts.get(arm, ArmCounts(0, 0, 0)) for arm in (control, treatment)
    }


def _sample_size(
    plan: ExperimentPlan, baseline: float, mde: float, ratio: float
) -> tuple[int, int]:
    return required_sample_sizes(
        baseline_rate=baseline,
        mde=mde,
        allocation_ratio=ratio,
        intracluster_correlation=plan.intracluster_correlation,
        mean_cluster_size=plan.mean_cluster_size,
        alpha=plan.alpha,
        power=plan.power,
        comparisons=len(plan.challengers),
    )


def _balanced(
    counts: dict[str, ArmCounts], plan: ExperimentPlan, challenger: str
) -> bool:
    control_people = counts[plan.control_value].people
    treatment_people = counts[challenger].people
    expected = (
        plan.allocation_probabilities[challenger]
        / plan.allocation_probabilities[plan.control_value]
    )
    if not control_people or not treatment_people:
        return False
    observed = treatment_people / control_people
    return abs(observed - expected) / expected <= plan.imbalance_tolerance


def _enough(
    counts: dict[str, ArmCounts],
    control: str,
    treatment: str,
    required: tuple[int, int],
) -> bool:
    return (
        counts[control].trials >= required[0]
        and counts[treatment].trials >= required[1]
    )


def _rate(value: float, name: str, *, allow_zero: bool = True) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        or value > 1
        or (not allow_zero and value == 0)
    ):
        raise ValueError(f"{name} must be a finite rate in [0,1]")


def _plan_snapshot(plan: ExperimentPlan) -> dict[str, object]:
    primary, guardrail, margin = _objective_kpis(plan.objective)
    return {
        "schema_version": 1,
        "experiment_id": plan.experiment_id,
        "workflow_id": plan.workflow_id,
        "config_version": plan.config_version,
        "cohort_id": plan.cohort_id,
        "dimension": plan.dimension,
        "control_value": plan.control_value,
        "challengers": list(plan.challengers),
        "start_at": plan.start_at.isoformat(),
        "end_at": plan.end_at.isoformat(),
        "cutoff_event_id": plan.cutoff_event_id,
        "primary_kpi": primary.value,
        "guardrail_kpi": guardrail.value,
        "guardrail_margin": margin,
        "objective": plan.objective,
        "allocation_probabilities": plan.allocation_probabilities,
        "imbalance_tolerance": plan.imbalance_tolerance,
        "primary_baseline_rate": plan.primary_baseline_rate,
        "guardrail_baseline_rate": plan.guardrail_baseline_rate,
        "mde": plan.mde,
        "alpha": plan.alpha,
        "power": plan.power,
        "intracluster_correlation": plan.intracluster_correlation,
        "mean_cluster_size": plan.mean_cluster_size,
        "bootstrap_replicates": plan.bootstrap_replicates,
        "policy_version": plan.policy_version,
    }


def _input_digest(
    plan_snapshot: dict[str, object], facts: list[KpiFact]
) -> tuple[str, dict[str, object]]:
    evidence = [
        {
            "source_event_id": fact.source_event_id,
            "person_id": fact.person_id,
            "config_version": fact.config_version,
            "cohort_id": fact.observed_cohort_id,
            "variant": fact.variant,
            "quality": fact.quality,
            "dials": fact.dials,
            "connected": fact.connected,
            "concluded": fact.concluded,
        }
        for fact in facts
    ]
    canonical = json.dumps(
        {"plan": plan_snapshot, "facts": evidence},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest, {
        "source_fact_count": len(evidence),
        "input_sha256": digest,
        "cutoff_event_id": plan_snapshot["cutoff_event_id"],
    }


def _result_to_record(result: ExperimentResult) -> dict[str, object]:
    def counts(values: dict[str, ArmCounts]) -> dict[str, dict[str, int]]:
        return {
            arm: {
                "successes": count.successes,
                "trials": count.trials,
                "people": count.people,
            }
            for arm, count in sorted(values.items())
        }

    def bootstrap(value: BootstrapResult | None) -> dict[str, object] | None:
        if value is None:
            return None
        return {
            "estimate": value.estimate,
            "lower": value.lower,
            "upper": value.upper,
            "p_not_greater_than_threshold": value.p_not_greater_than_threshold,
            "replicates": value.replicates,
        }

    return {
        "decision": result.decision.value,
        "winner": result.winner,
        "reason": result.reason,
        "excluded_crossover_people": result.excluded_crossover_people,
        "variants": [
            {
                "challenger": item.challenger,
                "primary": bootstrap(item.primary),
                "guardrail": bootstrap(item.guardrail),
                "primary_counts": counts(item.primary_counts),
                "guardrail_counts": counts(item.guardrail_counts),
                "required_primary_trials": list(item.required_primary_trials),
                "required_guardrail_trials": list(item.required_guardrail_trials),
                "primary_adjusted_p": item.primary_adjusted_p,
                "guardrail_adjusted_p": item.guardrail_adjusted_p,
                "guardrail_margin": item.guardrail_margin,
                "balanced": item.balanced,
                "sufficient": item.sufficient,
                "guardrail_passed": item.guardrail_passed,
                "won": item.won,
                "evidence_error": item.evidence_error,
            }
            for item in result.variants
        ],
    }


def _result_from_record(record: Any) -> ExperimentResult:
    payload = record.decision

    def bootstrap(value: dict[str, Any] | None) -> BootstrapResult | None:
        if value is None:
            return None
        return BootstrapResult(
            estimate=value["estimate"],
            lower=value["lower"],
            upper=value["upper"],
            p_not_greater_than_threshold=value["p_not_greater_than_threshold"],
            replicates=value["replicates"],
        )

    def counts(values: dict[str, dict[str, int]]) -> dict[str, ArmCounts]:
        return {arm: ArmCounts(**count) for arm, count in values.items()}

    plan = record.plan_snapshot
    return ExperimentResult(
        experiment_id=record.experiment_id,
        workflow_id=record.workflow_id,
        config_version=record.config_version,
        cohort_id=record.cohort_id,
        dimension=plan["dimension"],
        cutoff_event_id=record.cutoff_event_id,
        start_at=datetime.fromisoformat(plan["start_at"]),
        end_at=datetime.fromisoformat(plan["end_at"]),
        primary_kpi=Kpi(plan["primary_kpi"]),
        guardrail_kpi=Kpi(plan["guardrail_kpi"]),
        decision=Decision(payload["decision"]),
        winner=payload["winner"],
        reason=payload["reason"],
        excluded_crossover_people=payload["excluded_crossover_people"],
        variants=tuple(
            VariantResult(
                challenger=item["challenger"],
                primary=bootstrap(item["primary"]),
                guardrail=bootstrap(item["guardrail"]),
                primary_counts=counts(item["primary_counts"]),
                guardrail_counts=counts(item["guardrail_counts"]),
                required_primary_trials=tuple(item["required_primary_trials"]),
                required_guardrail_trials=tuple(item["required_guardrail_trials"]),
                primary_adjusted_p=item["primary_adjusted_p"],
                guardrail_adjusted_p=item["guardrail_adjusted_p"],
                guardrail_margin=item["guardrail_margin"],
                balanced=item["balanced"],
                sufficient=item["sufficient"],
                guardrail_passed=item["guardrail_passed"],
                won=item["won"],
                evidence_error=item.get("evidence_error"),
            )
            for item in payload["variants"]
        ),
        policy_version=record.policy_version,
        input_hash=record.input_hash,
        plan_snapshot=plan,
        input_snapshot=record.input_snapshot,
    )
