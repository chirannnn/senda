"""Deterministic evaluation of fixed, observed variant experiments."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from controller.statistics.estimation import (
    ArmCounts,
    BootstrapResult,
    arm_counts,
    estimate_rate_difference,
    holm_adjust,
    required_sample_sizes,
)
from controller.statistics.models import Decision, Kpi, PersonOutcome
from controller.storage.models import KpiFact
from controller.storage.store import DatabaseStore

_DIMENSIONS = {"welcome_message", "voice"}
_PRIMARY_NAMES = {"pickup_rate": Kpi.PICKUP, "engagement_rate": Kpi.ENGAGEMENT}


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

    def __post_init__(self) -> None:
        if not all((self.experiment_id, self.workflow_id, self.cohort_id)):
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
        if (
            isinstance(self.config_version, bool)
            or self.config_version < 0
            or isinstance(self.cutoff_event_id, bool)
            or self.cutoff_event_id < 0
        ):
            raise ValueError("config version and event cutoff must be nonnegative")
        if (
            self.start_at.tzinfo is None
            or self.end_at.tzinfo is None
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
            not math.isfinite(value) or value <= 0 or value > 1
            for value in probabilities
        ):
            raise ValueError("allocation probabilities must be finite and in (0, 1]")
        if not math.isclose(sum(probabilities), 1.0, rel_tol=0, abs_tol=1e-6):
            raise ValueError("declared arm allocation probabilities must sum to one")
        if (
            not math.isfinite(self.imbalance_tolerance)
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
            not math.isfinite(self.intracluster_correlation)
            or not 0 <= self.intracluster_correlation <= 1
        ):
            raise ValueError("intracluster correlation must be in [0,1]")
        if not math.isfinite(self.mean_cluster_size) or self.mean_cluster_size < 1:
            raise ValueError("mean cluster size must be at least one")
        if not math.isfinite(self.power) or not 0 < self.power < 1:
            raise ValueError("a planned power in (0,1) is required")
        if not math.isfinite(self.alpha) or not 0 < self.alpha < 1:
            raise ValueError("alpha must be in (0,1)")
        if self.bootstrap_replicates < 100:
            raise ValueError("at least 100 bootstrap replicates are required")
        _objective_kpis(self.objective)


@dataclass(frozen=True, slots=True)
class VariantResult:
    """Independent KPI evidence for one challenger against the control."""

    challenger: str
    primary: BootstrapResult
    guardrail: BootstrapResult
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


class ExperimentEvaluator:
    """Query valid persisted call facts and make one deterministic final decision."""

    def __init__(self, store: DatabaseStore, *, page_size: int = 1000) -> None:
        if page_size < 1:
            raise ValueError("page size must be positive")
        self._store = store
        self._page_size = page_size

    async def evaluate(self, plan: ExperimentPlan) -> ExperimentResult:
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
            guardrail_required = _sample_size(
                plan, plan.guardrail_baseline_rate, margin, ratio
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
                seed_key = f"{plan.experiment_id}:{plan.cutoff_event_id}:{plan.dimension}:{challenger}"
                primary = estimate_rate_difference(
                    primary_rows,
                    control_arm=plan.control_value,
                    treatment_arm=challenger,
                    seed_key=seed_key + ":" + primary_kpi.value,
                    replicates=plan.bootstrap_replicates,
                    alpha=plan.alpha / (2 * len(plan.challengers)),
                )
                guardrail = estimate_rate_difference(
                    guardrail_rows,
                    control_arm=plan.control_value,
                    treatment_arm=challenger,
                    seed_key=seed_key + ":" + guardrail_kpi.value,
                    replicates=plan.bootstrap_replicates,
                    alpha=plan.alpha / (2 * len(plan.challengers)),
                    evidence_threshold=-margin,
                )
            else:
                primary = guardrail = None
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
                }
            )

        primary_adjusted = holm_adjust(
            {
                item["challenger"]: item["primary"].p_not_greater_than_threshold
                for item in raw
                if item["primary"] is not None
            }
        )
        guardrail_adjusted = holm_adjust(
            {
                item["challenger"]: item["guardrail"].p_not_greater_than_threshold
                for item in raw
                if item["guardrail"] is not None
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
            decision, winner, reason = (
                Decision.INSUFFICIENT_EVIDENCE,
                None,
                "no challenger met predeclared evidence, balance and sample-size requirements",
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
        )


def _objective_kpis(objective: dict[str, Any]) -> tuple[Kpi, Kpi, float]:
    primary_name = objective.get("primary")
    if primary_name not in _PRIMARY_NAMES:
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
