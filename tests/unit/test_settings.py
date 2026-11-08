import pytest

from controller.config import Settings
from controller.errors import ConfigurationError


def test_settings_defaults_and_normalizes_base_url():
    settings = Settings.from_environment("http://127.0.0.1:8700/")

    assert settings.simulator_url == "http://127.0.0.1:8700"
    assert settings.advance_minutes == 15
    assert settings.max_connections == 20
    assert settings.get_retries == 3
    assert settings.fact_page_size == 1000
    assert settings.event_insert_batch_size == 500
    assert settings.log_level == "INFO"


def test_settings_reads_environment_overrides():
    settings = Settings.from_environment(
        "https://sim.example.test/api",
        {
            "CONTROLLER_ADVANCE_MINUTES": "30",
            "CONTROLLER_CONNECT_TIMEOUT_SECONDS": "1.5",
            "CONTROLLER_MAX_CONNECTIONS": "8",
            "CONTROLLER_MAX_KEEPALIVE_CONNECTIONS": "4",
            "CONTROLLER_FACT_PAGE_SIZE": "1500",
            "CONTROLLER_EVENT_INSERT_BATCH_SIZE": "750",
            "CONTROLLER_LOG_LEVEL": "debug",
        },
    )

    assert settings.advance_minutes == 30
    assert settings.connect_timeout_seconds == 1.5
    assert settings.max_connections == 8
    assert settings.max_keepalive_connections == 4
    assert settings.fact_page_size == 1500
    assert settings.event_insert_batch_size == 750
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "simulator",
        "ftp://sim.test",
        "http://user:password@sim.test",
        "http://sim.test?q=1",
        "http://sim.test#fragment",
    ],
)
def test_invalid_simulator_urls_are_rejected_without_echoing_input(url):
    with pytest.raises(ConfigurationError):
        Settings.from_environment(url)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"advance_minutes": 0},
        {"advance_minutes": 10},
        {"connect_timeout_seconds": 0},
        {"connect_timeout_seconds": float("inf")},
        {"max_connections": 0},
        {"max_connections": 2, "max_keepalive_connections": 3},
        {"get_retries": 11},
        {"retry_backoff_seconds": 2, "retry_max_backoff_seconds": 1},
        {"log_level": "TRACE"},
        {"fact_page_size": 0},
        {"fact_page_size": 2001},
        {"fact_page_size": True},
        {"event_insert_batch_size": 0},
        {"event_insert_batch_size": 5001},
        {"event_insert_batch_size": 1.5},
    ],
)
def test_invalid_runtime_values_are_rejected(kwargs):
    with pytest.raises(ConfigurationError):
        Settings("http://127.0.0.1:8700", **kwargs)


def test_malformed_environment_value_has_actionable_error_without_value_echo():
    with pytest.raises(
        ConfigurationError, match="CONTROLLER_MAX_CONNECTIONS must be an integer"
    ):
        Settings.from_environment(
            "http://127.0.0.1:8700", {"CONTROLLER_MAX_CONNECTIONS": "secret-value"}
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CONTROLLER_FACT_PAGE_SIZE", "not-an-int"),
        ("CONTROLLER_EVENT_INSERT_BATCH_SIZE", "not-an-int"),
    ],
)
def test_malformed_batch_setting_is_rejected(name, value):
    with pytest.raises(ConfigurationError, match=f"{name} must be an integer"):
        Settings.from_environment("http://127.0.0.1:8700", {name: value})
