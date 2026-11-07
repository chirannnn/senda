"""Deterministic statistical primitive tests."""

import pytest

from controller.statistics.estimation import (
    arm_counts,
    estimate_rate_difference,
    holm_adjust,
    required_noninferiority_sample_sizes,
    required_sample_sizes,
)
from controller.statistics.models import PersonOutcome


def _outcomes():
    rows = []
    for arm, wins in (("control", 6), ("treatment", 9)):
        for person in range(10):
            rows.append(PersonOutcome(f"{arm}-{person}", arm, int(person < wins), 1))
    return rows


def test_cluster_bootstrap_is_repeatable_and_uses_person_clusters():
    result = estimate_rate_difference(
        _outcomes(),
        control_arm="control",
        treatment_arm="treatment",
        seed_key="exp-cutoff",
    )
    replay = estimate_rate_difference(
        _outcomes(),
        control_arm="control",
        treatment_arm="treatment",
        seed_key="exp-cutoff",
    )
    assert result == replay
    assert result.estimate == pytest.approx(0.3)
    assert result.lower <= result.estimate <= result.upper
    assert result.replicates == 10_000


def test_person_cannot_be_counted_in_multiple_arms():
    with pytest.raises(ValueError, match="multiple experiment arms"):
        estimate_rate_difference(
            [
                PersonOutcome("same", "control", 1, 1),
                PersonOutcome("same", "treatment", 1, 1),
            ],
            control_arm="control",
            treatment_arm="treatment",
            seed_key="x",
        )


def test_arm_counts_report_people_and_exclude_undefined_rate():
    counts = arm_counts(
        [PersonOutcome("p1", "a", 1, 2), PersonOutcome("p1", "a", 0, 1)]
    )["a"]
    assert (counts.successes, counts.trials, counts.people) == (1, 3, 1)
    assert counts.rate == pytest.approx(1 / 3)


def test_holm_is_stable_and_preserves_input_order():
    assert holm_adjust({"b": 0.02, "a": 0.01, "c": 0.5}) == {
        "b": 0.04,
        "a": 0.03,
        "c": 0.5,
    }


def test_power_calculation_requires_all_assumptions_and_accounts_for_clusters():
    independent = required_sample_sizes(
        baseline_rate=0.4,
        mde=0.05,
        allocation_ratio=1,
        intracluster_correlation=0,
        mean_cluster_size=1,
        alpha=0.05,
        power=0.8,
        comparisons=3,
    )
    clustered = required_sample_sizes(
        baseline_rate=0.4,
        mde=0.05,
        allocation_ratio=1,
        intracluster_correlation=0.2,
        mean_cluster_size=4,
        alpha=0.05,
        power=0.8,
        comparisons=3,
    )
    assert independent[0] > 0 and independent[0] == independent[1]
    assert clustered[0] > independent[0]


@pytest.mark.parametrize(
    "overrides",
    [
        {"mde": 0},
        {"allocation_ratio": 0},
        {"intracluster_correlation": 2},
        {"mean_cluster_size": 0},
        {"alpha": 1},
        {"power": 1},
        {"comparisons": 0},
    ],
)
def test_invalid_power_inputs_are_rejected(overrides):
    parameters = {
        "baseline_rate": 0.4,
        "mde": 0.05,
        "allocation_ratio": 1,
        "intracluster_correlation": 0,
        "mean_cluster_size": 1,
        "alpha": 0.05,
        "power": 0.8,
        "comparisons": 2,
    }
    parameters.update(overrides)
    with pytest.raises(ValueError):
        required_sample_sizes(**parameters)


def test_zero_baseline_rate_is_a_valid_explicit_power_input():
    assert (
        required_sample_sizes(
            baseline_rate=0,
            mde=0.05,
            allocation_ratio=1,
            intracluster_correlation=0,
            mean_cluster_size=1,
            alpha=0.05,
            power=0.8,
            comparisons=1,
        )[0]
        > 0
    )


def test_noninferiority_sample_size_uses_the_configured_margin():
    narrow = required_noninferiority_sample_sizes(
        baseline_rate=0.8,
        margin=0.02,
        allocation_ratio=1,
        intracluster_correlation=0,
        mean_cluster_size=1,
        alpha=0.05,
        power=0.8,
        comparisons=2,
    )
    wider = required_noninferiority_sample_sizes(
        baseline_rate=0.8,
        margin=0.1,
        allocation_ratio=1,
        intracluster_correlation=0,
        mean_cluster_size=1,
        alpha=0.05,
        power=0.8,
        comparisons=2,
    )
    assert narrow[0] > wider[0]


@pytest.mark.parametrize("baseline_rate", [0, 1])
def test_noninferiority_power_rejects_degenerate_baselines(baseline_rate):
    with pytest.raises(ValueError, match="strictly between"):
        required_noninferiority_sample_sizes(
            baseline_rate=baseline_rate,
            margin=0.02,
            allocation_ratio=1,
            intracluster_correlation=0,
            mean_cluster_size=1,
            alpha=0.05,
            power=0.8,
            comparisons=1,
        )
