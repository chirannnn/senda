"""Incremental NDJSON parsing and descriptor validation."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import Any

from controller.ingestion.events import (
    ChunkDescriptor,
    EventValidationError,
    IngestedEvent,
)


def parse_descriptor(value: object) -> ChunkDescriptor:
    """Validate one descriptor returned by the documented chunk-list API."""
    if not isinstance(value, dict):
        raise EventValidationError("chunk descriptor must be a JSON object")
    chunk = _integer(value.get("chunk"), "chunk", minimum=0)
    kind = value.get("kind")
    if kind not in {"events", "history"}:
        raise EventValidationError("chunk descriptor kind must be events or history")
    count = _integer(value.get("events"), "events", minimum=0)
    first = _integer(value.get("first_event_id"), "first_event_id", minimum=1)
    last = _integer(value.get("last_event_id"), "last_event_id", minimum=1)
    if count == 0 or first > last:
        raise EventValidationError("chunk descriptor must describe at least one event")
    return ChunkDescriptor(chunk, kind, count, first, last, dict(value))


def parse_descriptors(values: object, *, after: int = -1) -> list[ChunkDescriptor]:
    """Validate an ordered complete API listing without assuming numeric adjacency."""
    if not isinstance(values, list):
        raise EventValidationError("chunk listing must be a JSON array")
    descriptors = [parse_descriptor(value) for value in values]
    previous = after
    for descriptor in descriptors:
        if descriptor.chunk <= previous:
            raise EventValidationError("chunk descriptors must be strictly ordered")
        previous = descriptor.chunk
    return descriptors


def iter_ndjson(
    lines: Iterable[str], descriptor: ChunkDescriptor
) -> Iterator[IngestedEvent]:
    """Parse lines incrementally and validate IDs/count against their descriptor."""
    previous_id = 0
    count = 0
    first_id: int | None = None
    last_id: int | None = None
    for line_number, line in enumerate(lines, start=1):
        if not isinstance(line, str):
            raise EventValidationError(f"NDJSON line {line_number} is not text")
        if not line.strip():
            continue
        try:
            payload: Any = json.loads(line)
        except (json.JSONDecodeError, ValueError) as exc:
            raise EventValidationError(
                f"NDJSON line {line_number} is malformed"
            ) from exc
        if not isinstance(payload, dict):
            raise EventValidationError(f"NDJSON line {line_number} must be an object")
        event_id = _integer(payload.get("event_id"), "event_id", minimum=1)
        event_type = payload.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise EventValidationError(
                f"NDJSON line {line_number} has no non-empty string type"
            )
        if event_id <= previous_id:
            raise EventValidationError("event IDs must be strictly increasing")
        previous_id = event_id
        first_id = event_id if first_id is None else first_id
        last_id = event_id
        count += 1
        yield IngestedEvent(event_id, event_type, payload)

    if count != descriptor.events:
        raise EventValidationError(
            f"chunk {descriptor.chunk} declared {descriptor.events} events but contained {count}"
        )
    if first_id != descriptor.first_event_id or last_id != descriptor.last_event_id:
        raise EventValidationError(
            f"chunk {descriptor.chunk} event ID bounds do not match its descriptor"
        )


def _integer(value: object, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EventValidationError(f"{name} must be an integer >= {minimum}")
    return value
