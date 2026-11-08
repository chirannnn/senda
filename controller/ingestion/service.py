"""History discovery and bounded-memory chunk decoding."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

from controller.clients.simulator import SimulatorClient
from controller.ingestion.events import (
    ChunkDescriptor,
    EventValidationError,
    IngestedEvent,
)
from controller.ingestion.parser import iter_ndjson, parse_descriptors
from controller.storage.store import DatabaseStore, EventRecord
from controller.telemetry import RuntimeTelemetry


class EventIngestionService:
    """Read and validate API-published chunks; persistence is added separately."""

    def __init__(
        self,
        simulator: SimulatorClient,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        self._simulator = simulator
        self._telemetry = telemetry

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

    async def ingest_descriptor(
        self,
        store: DatabaseStore,
        descriptor: ChunkDescriptor,
        *,
        checkpoint_key: str = "simulator",
        batch_size: int = 500,
    ) -> int:
        """Fetch and atomically persist one descriptor; safe to repeat its cursor."""
        checkpoint = await store.get_checkpoint(checkpoint_key)
        if checkpoint is not None and descriptor.chunk < checkpoint:
            return 0

        started = time.perf_counter()
        # stream_chunk_lines fully downloads to its 1 MiB memory / temp-file
        # spool before yielding, so the SQL transaction never spans HTTP I/O.
        async with self._simulator.stream_chunk_lines(descriptor.chunk) as lines:

            def records():
                for event in iter_ndjson(lines, descriptor):
                    yield EventRecord(
                        event_id=event.event_id,
                        chunk_number=descriptor.chunk,
                        kind=descriptor.kind,
                        payload=event.payload,
                    )

            async def async_records() -> AsyncIterator[EventRecord]:
                for event in records():
                    yield event

            inserted = await store.commit_chunk_stream(
                checkpoint_key,
                descriptor.chunk,
                async_records(),
                batch_size=batch_size,
            )
            if self._telemetry is not None:
                self._telemetry.record_ingestion(
                    chunk=descriptor.chunk,
                    observed_events=descriptor.events,
                    inserted_events=inserted,
                    seconds=time.perf_counter() - started,
                )
            return inserted

    async def list_available_chunks(self, *, after: int = -1) -> list[ChunkDescriptor]:
        """Merge history/live lists into one validated global descriptor order."""
        history = await self.list_history_chunks(after=after)
        live = await self.list_event_chunks(after=after)
        descriptors = sorted(history + live, key=lambda item: item.chunk)
        return parse_descriptors([item.metadata for item in descriptors], after=after)

    async def ingest_descriptors(
        self,
        store: DatabaseStore,
        descriptors: list[ChunkDescriptor],
        *,
        checkpoint_key: str = "simulator",
        batch_size: int = 500,
    ) -> tuple[int, int]:
        """Commit the supplied complete ordered descriptor sequence in order."""
        checkpoint = await store.get_checkpoint(checkpoint_key)
        after = -1 if checkpoint is None else checkpoint
        descriptors = parse_descriptors(
            [item.metadata for item in descriptors], after=after
        )
        if self._telemetry is not None:
            self._telemetry.set_pending_chunks(len(descriptors))
        event_count = 0
        committed_chunks = 0
        previous_last_event_id = await store.get_latest_event_id()
        for descriptor in descriptors:
            if (
                previous_last_event_id is not None
                and descriptor.first_event_id <= previous_last_event_id
            ):
                raise EventValidationError(
                    "event ID ranges must increase across API-ordered chunks"
                )
            previous_last_event_id = descriptor.last_event_id
            event_count += await self.ingest_descriptor(
                store,
                descriptor,
                checkpoint_key=checkpoint_key,
                batch_size=batch_size,
            )
            committed_chunks += 1
            if self._telemetry is not None:
                self._telemetry.set_pending_chunks(len(descriptors) - committed_chunks)
        return committed_chunks, event_count

    async def ingest_available_chunks(
        self,
        store: DatabaseStore,
        *,
        checkpoint_key: str = "simulator",
        batch_size: int = 500,
    ) -> tuple[int, int]:
        """Replay every currently listed descriptor after the durable checkpoint."""
        checkpoint = await store.get_checkpoint(checkpoint_key)
        after = -1 if checkpoint is None else checkpoint
        descriptors = await self.list_available_chunks(after=after)
        return await self.ingest_descriptors(
            store,
            descriptors,
            checkpoint_key=checkpoint_key,
            batch_size=batch_size,
        )
