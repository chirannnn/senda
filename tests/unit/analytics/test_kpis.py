"""Explicit KPI denominators and empty-population behavior."""

from controller.analytics.kpis import KpiSummary


def _summary(*, dials=0, connected=0, concluded=0):
    return KpiSummary(
        workflow_id="wf",
        config_version=None,
        cohort_id=None,
        variant=None,
        start_at=None,
        end_at=None,
        dials=dials,
        connected=connected,
        concluded=concluded,
        uploaded=0,
        invalid_facts=0,
        incomplete_facts=0,
        unsupported_facts=0,
    )


def test_pickup_and_engagement_use_different_denominators():
    summary = _summary(dials=10, connected=4, concluded=3)
    assert summary.pickup_rate == 0.4
    assert summary.engagement_rate == 0.75


def test_zero_denominators_are_undefined_not_zero_rates():
    assert _summary().pickup_rate is None
    assert _summary().engagement_rate is None
    assert _summary(dials=9, connected=0, concluded=0).pickup_rate == 0
    assert _summary(dials=9, connected=0, concluded=0).engagement_rate is None
