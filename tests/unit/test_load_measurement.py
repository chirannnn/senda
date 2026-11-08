from pathlib import Path

from scripts.measure_load import (
    LoadSamples,
    _cpu_seconds,
    _histogram_quantile,
    _public_state,
    build_report,
    cpu_capacity,
)


def test_load_report_calculates_sre_target_from_actual_simulated_progress():
    report = build_report(
        status="completed",
        command=["python", "-m", "controller", "http://127.0.0.1:8700"],
        initial_state={"sim_minute": 0},
        final_state={"sim_minute": 1440, "finished": True},
        elapsed_seconds=599,
        controller_return_code=0,
        timed_out=False,
        controller_metrics={"events_per_second": 100.0},
        database_snapshot={"event_count": 60_000},
        initial_database_bytes=100,
        final_database_bytes=200,
        samples=LoadSamples(rss_bytes=[1024], database_bytes=[180]),
        sample_interval_seconds=5,
        log_path=Path("reports/load.jsonl"),
        measurement_id="test-measurement",
        samples_path=Path("reports/load-samples.jsonl"),
    )
    assert "Wall seconds per simulated day: 599.000" in report
    assert "SRE target (<600 seconds per simulated day): PASS" in report
    assert "Events persisted: 60000" in report


def test_load_report_does_not_claim_sre_result_without_simulated_progress():
    report = build_report(
        status="incomplete",
        command=["python", "-m", "controller"],
        initial_state={"sim_minute": 20},
        final_state={"sim_minute": 20, "finished": False},
        elapsed_seconds=900,
        controller_return_code=1,
        timed_out=False,
        controller_metrics=None,
        database_snapshot=None,
        initial_database_bytes=0,
        final_database_bytes=0,
        samples=LoadSamples(),
        sample_interval_seconds=5,
        log_path=Path("reports/load.jsonl"),
        measurement_id="test-measurement",
        samples_path=Path("reports/load-samples.jsonl"),
    )
    assert "SRE target (<600 seconds per simulated day): NOT MEASURED" in report
    assert "Result: **INCOMPLETE**" in report


def test_histogram_quantile_returns_upper_bucket_and_handles_bad_data():
    histogram = {
        "count": 4,
        "le_seconds": [0.1, 0.5, 1.0],
        "buckets_le": [1, 3, 4],
    }
    assert _histogram_quantile(histogram, 0.75) == 0.5
    assert _histogram_quantile(histogram | {"count": 100}, 0.99) == ">1.0"
    assert _histogram_quantile({}, 0.95) is None


def test_cpu_measurement_requires_two_samples_and_uses_process_delta():
    assert _cpu_seconds([(10.0, 4.0)]) is None
    assert _cpu_seconds([(10.0, 4.0), (15.0, 7.5)]) == 3.5
    assert cpu_capacity() > 0


def test_public_state_allowlist_excludes_unrequested_fields():
    assert _public_state(
        {
            "sim_minute": 15,
            "finished": False,
            "hidden_truth": "must not be copied",
        }
    ) == {"sim_minute": 15, "finished": False}
