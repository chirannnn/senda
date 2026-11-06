"""History discovery and bounded-memory chunk decoding."""

from __future__ import annotations

from collections.abc import AsyncIterator

from controller.clients.simulator import SimulatorClient
from controller.ingestion.events import (
    ChunkDescriptor,
    EventValidationError,
    IngestedEvent,
)
from controller.ingestion.parser import iter_ndjson, parse_descriptors


class EventIngestionService:
    """Read and validate API-published chunks; persistence is added separately."""

    def __init__(self, simulator: SimulatorClient) -> None:
        self._simulator = simulator

    async def list_history_chunks(self, *, after: int = -1) -> list[ChunkDescriptor]:
        """Return ordered history descriptors after the supplied global cursor."""
        raw = await self._simulator.list_chunks("history", after=after)
        descriptors = parse_descriptors(raw, after=after)
        if any(item.kind != "history" for item in descriptors):
            raise EventValidationError("history endpoint returned a non-history chunk")
        return descriptors

    async def list_event_chunks(self, *, after: int = -1) -> list[ChunkDescriptor]:
        """Return ordered live descriptors after the supplied global cursor."""
        raw = await self._simulator.list_chunks("events", after=after)
        descriptors = parse_descriptors(raw, after=after)
        if any(item.kind != "events" for item in descriptors):
            raise EventValidationError("events endpoint returned a non-events chunk")
        return descriptors

    async def iter_chunk_events(
        self, descriptor: ChunkDescriptor
    ) -> AsyncIterator[IngestedEvent]:
        """Yield validated events; validation of trailer metadata completes on exhaustion."""
        async with self._simulator.stream_chunk_lines(descriptor.chunk) as lines:
            for event in iter_ndjson(lines, descriptor):
                yield event
