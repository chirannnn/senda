"""Minimal structured JSON logging with an allowlist of context fields."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

_CONTEXT_FIELDS = (
    "event",
    "operation",
    "status_code",
    "simulated_time",
    "finished",
    "workflow_count",
    "advance_minutes",
)


class JSONFormatter(logging.Formatter):
    """Format log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _CONTEXT_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Configure the process root logger for safe structured output."""
    resolved_level = getattr(logging, level.upper(), None)
    if not isinstance(resolved_level, int):
        raise ValueError("log level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")

    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved_level)
