"""Typed event and chunk descriptor values at the simulator boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ChunkDescriptor:
    """An API-published event file descriptor; ``chunk`` is globally ordered."""

    chunk: int
    kind: str
    events: int
    first_event_id: int
    last_event_id: int
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class IngestedEvent:
    """One validated raw simulator event, retaining its original JSON object."""

    event_id: int
    event_type: str
    payload: dict[str, Any]


class EventValidationError(ValueError):
    """An API descriptor or event chunk violates the documented contract."""
