import sys

import pytest

from controller.telemetry import RuntimeTelemetry, process_memory_bytes


def test_counters_histograms_and_derived_rates_are_reproducible():
    telemetry = RuntimeTelemetry()
    telemetry.increment("events_inserted", 30)
    telemetry.observe("work_seconds", 0.02)
    telemetry.set_database_snapshot({"experiment_decisions": 2})
    telemetry.record_decision()
    telemetry.record_decision()
    telemetry.record_initial_credits({"credits_remaining": 9})
    telemetry.record_state({"credits_remaining": 7, "sim_minute": 60})

    result = telemetry.export()

    assert result["counters"] == {
        "decisions_persisted": 2,
        "events_inserted": 30,
    }
    assert result["histograms"]["work_seconds"]["count"] == 1
    assert result["histograms"]["work_seconds"]["max_seconds"] == 0.02
    assert result["decisions_per_hour"] >= 2
    assert result["credits_spent"] == 2
    assert result["latest"]["simulated_minute"] == 60


def test_outcome_metrics_keep_kpis_separate_and_zero_denominators_undefined():
    telemetry = RuntimeTelemetry()
    telemetry.set_database_snapshot(
        {"dials": 10, "connected": 4, "concluded": 2, "uploaded": 8}
    )
    assert telemetry.export()["outcomes"] == {
        "pickup_rate": 0.4,
        "engagement_rate": 0.5,
        "business_outcome_rate": 0.25,
    }

    telemetry.set_database_snapshot({"dials": 0, "connected": 0, "concluded": 0})
    assert telemetry.export()["outcomes"] == {
        "pickup_rate": None,
        "engagement_rate": None,
        "business_outcome_rate": None,
    }


def test_api_metrics_use_bounded_routes_and_count_failures():
    telemetry = RuntimeTelemetry()
    telemetry.observe_api("GET", "/approvals/apr_private_123", 503, 0.1)
    telemetry.observe_api("POST", "/workflows/customer-workflow/apply", None, 0.2)

    api = telemetry.export()["api"]

    assert api["GET /approvals/{approval_id}"]["failures"] == 1
    assert api["POST /workflows/{workflow_id}/apply"]["status_codes"] == {}
    assert all("private" not in route for route in api)


@pytest.mark.parametrize("amount", [-1, True, 1.5])
def test_counters_reject_invalid_increments(amount):
    with pytest.raises(ValueError):
        RuntimeTelemetry().increment("events", amount)


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf")])
def test_histograms_reject_invalid_durations(seconds):
    with pytest.raises(ValueError):
        RuntimeTelemetry().observe("request", seconds)


def test_ingestion_rejects_impossible_counts_and_invalid_pending_gauge():
    telemetry = RuntimeTelemetry()
    with pytest.raises(ValueError, match="cannot exceed"):
        telemetry.record_ingestion(
            chunk=1, observed_events=1, inserted_events=2, seconds=0.1
        )
    with pytest.raises(ValueError, match="pending chunk"):
        telemetry.set_pending_chunks(-1)


def test_process_memory_probe_is_safe():
    value = process_memory_bytes()
    if sys.platform == "win32":
        assert value is not None and value > 0
    else:
        assert value is None or value > 0
