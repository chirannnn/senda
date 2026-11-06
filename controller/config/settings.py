"""Validated, immutable settings for the controller process."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from controller.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration with safe defaults for a single controller process."""

    simulator_url: str
    advance_minutes: int = 15
    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 30.0
    write_timeout_seconds: float = 10.0
    pool_timeout_seconds: float = 5.0
    max_connections: int = 20
    max_keepalive_connections: int = 10
    get_retries: int = 3
    retry_backoff_seconds: float = 0.25
    retry_max_backoff_seconds: float = 2.0
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        object.__setattr__(self, "simulator_url", _validate_url(self.simulator_url))
        if self.advance_minutes <= 0 or self.advance_minutes % 15:
            raise ConfigurationError(
                "advance minutes must be a positive multiple of 15"
            )
        for field_name in (
            "connect_timeout_seconds",
            "read_timeout_seconds",
            "write_timeout_seconds",
            "pool_timeout_seconds",
            "retry_backoff_seconds",
            "retry_max_backoff_seconds",
        ):
            value = getattr(self, field_name)
            if not math.isfinite(value) or value <= 0:
                raise ConfigurationError(
                    f"{field_name} must be a finite positive number"
                )
        if self.max_connections < 1:
            raise ConfigurationError("max connections must be at least 1")
        if not 0 <= self.max_keepalive_connections <= self.max_connections:
            raise ConfigurationError(
                "max keepalive connections must be between 0 and max connections"
            )
        if self.get_retries < 0 or self.get_retries > 10:
            raise ConfigurationError("GET retries must be between 0 and 10")
        if self.retry_max_backoff_seconds < self.retry_backoff_seconds:
            raise ConfigurationError(
                "maximum retry backoff must be at least the base backoff"
            )
        if self.log_level.upper() not in {
            "DEBUG",
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        }:
            raise ConfigurationError(
                "log level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL"
            )
        object.__setattr__(self, "log_level", self.log_level.upper())

    @classmethod
    def from_environment(
        cls, simulator_url: str, environ: Mapping[str, str] | None = None
    ) -> Settings:
        """Build settings from CLI simulator URL and `CONTROLLER_*` environment values."""
        values = os.environ if environ is None else environ
        try:
            return cls(
                simulator_url=simulator_url,
                advance_minutes=_int_value(values, "CONTROLLER_ADVANCE_MINUTES", 15),
                connect_timeout_seconds=_float_value(
                    values, "CONTROLLER_CONNECT_TIMEOUT_SECONDS", 5.0
                ),
                read_timeout_seconds=_float_value(
                    values, "CONTROLLER_READ_TIMEOUT_SECONDS", 30.0
                ),
                write_timeout_seconds=_float_value(
                    values, "CONTROLLER_WRITE_TIMEOUT_SECONDS", 10.0
                ),
                pool_timeout_seconds=_float_value(
                    values, "CONTROLLER_POOL_TIMEOUT_SECONDS", 5.0
                ),
                max_connections=_int_value(values, "CONTROLLER_MAX_CONNECTIONS", 20),
                max_keepalive_connections=_int_value(
                    values, "CONTROLLER_MAX_KEEPALIVE_CONNECTIONS", 10
                ),
                get_retries=_int_value(values, "CONTROLLER_GET_RETRIES", 3),
                retry_backoff_seconds=_float_value(
                    values, "CONTROLLER_RETRY_BACKOFF_SECONDS", 0.25
                ),
                retry_max_backoff_seconds=_float_value(
                    values, "CONTROLLER_RETRY_MAX_BACKOFF_SECONDS", 2.0
                ),
                log_level=values.get("CONTROLLER_LOG_LEVEL", "INFO"),
            )
        except ValueError as exc:
            if isinstance(exc, ConfigurationError):
                raise
            raise ConfigurationError(
                "controller environment settings contain a value with the wrong type"
            ) from exc


def _validate_url(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigurationError(
            "simulator URL is required and must not contain surrounding whitespace"
        )
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ConfigurationError("simulator URL is malformed") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ConfigurationError("simulator URL must be an absolute HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigurationError("simulator URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ConfigurationError("simulator URL must not contain a query or fragment")
    return value.rstrip("/")


def _int_value(values: Mapping[str, str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def _float_value(values: Mapping[str, str], name: str, default: float) -> float:
    raw = values.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
