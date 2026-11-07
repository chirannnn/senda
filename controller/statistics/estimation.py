"""Deterministic person-cluster bootstrap and explicit power calculations."""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist

from controller.statistics.models import PersonOutcome


@dataclass(frozen=True, slots=True)
class ArmCounts:
    """Valid additive KPI counts for one arm."""

    successes: int
    trials: int
    people: int

    def __post_init__(self) -> None:
        if (
            min(self.successes, self.trials, self.people) < 0
            or self.successes > self.trials
        ):
            raise ValueError("arm counts are inconsistent")

    @property
    def rate(self) -> float | None:
        return self.successes / self.trials if self.trials else None


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Observed difference and deterministic percentile uncertainty."""

    estimate: float
    lower: float
    upper: float
    p_not_greater_than_threshold: float
    replicates: int


def arm_counts(outcomes: Sequence[PersonOutcome]) -> dict[str, ArmCounts]:
    """Sum person outcomes without treating calls as independent people."""
    totals: dict[str, list[int]] = {}
    people: dict[str, set[str]] = {}
    for outcome in outcomes:
        row = totals.setdefault(outcome.arm_id, [0, 0])
        row[0] += outcome.successes
        row[1] += outcome.trials
        people.setdefault(outcome.arm_id, set()).add(outcome.person_id)
    return {
        arm: ArmCounts(values[0], values[1], len(people[arm]))
        for arm, values in sorted(totals.items())
    }


def estimate_rate_difference(
    outcomes: Sequence[PersonOutcome],
    *,
    control_arm: str,
    treatment_arm: str,
    seed_key: str,
    replicates: int = 10_000,
    alpha: float = 0.05,
    evidence_threshold: float = 0.0,
) -> BootstrapResult:
    """Estimate treatment-control using independent person-cluster resampling.

    Each input person must occur in one arm only. The deterministic PRNG seed is
    derived from the stable experiment snapshot key, never wall-clock state.
    """
    if not seed_key:
        raise ValueError("bootstrap seed key is required")
    if control_arm == treatment_arm:
        raise ValueError("control and treatment arms must differ")
    if replicates < 100:
        raise ValueError("at least 100 bootstrap replicates are required")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between zero and one")
    person_arm: dict[str, str] = {}
    by_arm: dict[str, list[PersonOutcome]] = {control_arm: [], treatment_arm: []}
    for item in outcomes:
        previous = person_arm.setdefault(item.person_id, item.arm_id)
        if previous != item.arm_id:
            raise ValueError("a person cannot contribute to multiple experiment arms")
        if item.arm_id in by_arm:
            by_arm[item.arm_id].append(item)
    if not by_arm[control_arm] or not by_arm[treatment_arm]:
        raise ValueError("both arms need observed person-level outcomes")

    observed = _difference(by_arm[control_arm], by_arm[treatment_arm])
    if observed is None:
        raise ValueError("both arms need nonzero KPI denominators")
    seed = int.from_bytes(hashlib.sha256(seed_key.encode()).digest()[:8], "big")
    rng = random.Random(seed)
    differences: list[float] = []
    for _ in range(replicates):
        control = _resample(by_arm[control_arm], rng)
        treatment = _resample(by_arm[treatment_arm], rng)
        estimate = _difference(control, treatment)
        if estimate is not None:
            differences.append(estimate)
    if len(differences) != replicates:
        raise ValueError("bootstrap produced a zero-denominator replicate")
    differences.sort()
    lower = _quantile(differences, alpha / 2)
    upper = _quantile(differences, 1 - alpha / 2)
    p_not_greater = (sum(value <= evidence_threshold for value in differences) + 1) / (
        replicates + 1
    )
    return BootstrapResult(observed, lower, upper, p_not_greater, replicates)


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    """Return Holm step-down adjusted p-values in original key order."""
    if not p_values:
        return {}
    if any(
        not math.isfinite(value) or not 0 <= value <= 1 for value in p_values.values()
    ):
        raise ValueError("p-values must be finite values in [0, 1]")
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    count = len(ordered)
    adjusted: dict[str, float] = {}
    running = 0.0
    for index, (key, value) in enumerate(ordered):
        running = max(running, min(1.0, (count - index) * value))
        adjusted[key] = running
    return {key: adjusted[key] for key in p_values}


def required_sample_sizes(
    *,
    baseline_rate: float,
    mde: float,
    allocation_ratio: float,
    intracluster_correlation: float,
    mean_cluster_size: float,
    alpha: float,
    power: float,
    comparisons: int,
) -> tuple[int, int]:
    """Approximate per-arm binomial sample sizes with cluster design effect.

    Inputs are mandatory: the result makes no implicit baseline, allocation,
    clustering, multiplicity, alpha, or power claim. Holm family size is handled
    conservatively by using alpha/comparisons for this planning approximation.
    """
    values = (
        baseline_rate,
        mde,
        allocation_ratio,
        intracluster_correlation,
        mean_cluster_size,
        alpha,
        power,
    )
    if any(not math.isfinite(value) for value in values):
        raise ValueError("sample-size inputs must be finite")
    if not 0 <= baseline_rate < 1 or not 0 < mde <= 1 - baseline_rate:
        raise ValueError("baseline and MDE must define a valid second-arm rate")
    if allocation_ratio <= 0 or mean_cluster_size < 1:
        raise ValueError("allocation ratio and mean cluster size must be positive")
    if not 0 <= intracluster_correlation <= 1:
        raise ValueError("intracluster correlation must be in [0, 1]")
    if not 0 < alpha < 1 or not 0 < power < 1 or comparisons < 1:
        raise ValueError("alpha, power and comparison count are invalid")
    adjusted_alpha = alpha / comparisons
    z_alpha = NormalDist().inv_cdf(1 - adjusted_alpha / 2)
    z_power = NormalDist().inv_cdf(power)
    treatment_rate = baseline_rate + mde
    variance = baseline_rate * (1 - baseline_rate) + (
        treatment_rate * (1 - treatment_rate) / allocation_ratio
    )
    control = (z_alpha + z_power) ** 2 * variance / (mde**2)
    design_effect = 1 + (mean_cluster_size - 1) * intracluster_correlation
    control_n = math.ceil(control * design_effect)
    treatment_n = math.ceil(control_n * allocation_ratio)
    return control_n, treatment_n


def required_noninferiority_sample_sizes(
    *,
    baseline_rate: float,
    margin: float,
    allocation_ratio: float,
    intracluster_correlation: float,
    mean_cluster_size: float,
    alpha: float,
    power: float,
    comparisons: int,
) -> tuple[int, int]:
    """Plan a non-inferiority comparison under an equal-rate alternative."""
    if not math.isfinite(baseline_rate) or not 0 <= baseline_rate <= 1:
        raise ValueError("guardrail baseline must be a finite rate in [0,1]")
    if not math.isfinite(margin) or not 0 < margin <= 1:
        raise ValueError("non-inferiority margin must be in (0,1]")
    if (
        not math.isfinite(allocation_ratio)
        or allocation_ratio <= 0
        or not math.isfinite(mean_cluster_size)
        or mean_cluster_size < 1
    ):
        raise ValueError("allocation ratio and mean cluster size must be positive")
    if (
        not math.isfinite(intracluster_correlation)
        or not 0 <= intracluster_correlation <= 1
    ):
        raise ValueError("intracluster correlation must be in [0,1]")
    if not 0 < alpha < 1 or not 0 < power < 1 or comparisons < 1:
        raise ValueError("alpha, power and comparison count are invalid")
    z_alpha = NormalDist().inv_cdf(1 - (alpha / comparisons))
    z_power = NormalDist().inv_cdf(power)
    variance = baseline_rate * (1 - baseline_rate) * (1 + 1 / allocation_ratio)
    design_effect = 1 + (mean_cluster_size - 1) * intracluster_correlation
    control_n = math.ceil(
        (z_alpha + z_power) ** 2 * variance / margin**2 * design_effect
    )
    treatment_n = math.ceil(control_n * allocation_ratio)
    return control_n, treatment_n


def _resample(
    values: Sequence[PersonOutcome], rng: random.Random
) -> list[PersonOutcome]:
    by_person: dict[str, list[PersonOutcome]] = {}
    for value in values:
        by_person.setdefault(value.person_id, []).append(value)
    people = sorted(by_person)
    sampled: list[PersonOutcome] = []
    for _ in people:
        sampled.extend(by_person[rng.choice(people)])
    return sampled


def _difference(
    control: Sequence[PersonOutcome], treatment: Sequence[PersonOutcome]
) -> float | None:
    control_trials = sum(item.trials for item in control)
    treatment_trials = sum(item.trials for item in treatment)
    if not control_trials or not treatment_trials:
        return None
    control_rate = sum(item.successes for item in control) / control_trials
    treatment_rate = sum(item.successes for item in treatment) / treatment_trials
    return treatment_rate - control_rate


def _quantile(sorted_values: Sequence[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    return sorted_values[lower] * (upper - position) + sorted_values[upper] * (
        position - lower
    )
