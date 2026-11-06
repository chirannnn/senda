import json
import logging

import pytest

from controller.observability.logging import JSONFormatter, configure_logging


def test_json_formatter_includes_timestamp_severity_and_allowlisted_context():
    record = logging.LogRecord(
        name="controller.runtime",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="runtime started",
        args=(),
        exc_info=None,
    )
    record.event = "started"
    record.workflow_count = 3
    record.password = "must-not-be-logged"

    payload = json.loads(JSONFormatter().format(record))

    assert payload["severity"] == "INFO"
    assert payload["message"] == "runtime started"
    assert payload["event"] == "started"
    assert payload["workflow_count"] == 3
    assert "timestamp" in payload
    assert "password" not in payload


def test_configure_logging_rejects_invalid_level():
    with pytest.raises(ValueError, match="log level"):
        configure_logging("TRACE")
