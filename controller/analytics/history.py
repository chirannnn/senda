"""Historical event access with keyset pagination."""

from __future__ import annotations

from collections.abc import AsyncIterator

from controller.storage.models import SimulatorEvent
from controller.storage.store import DatabaseStore


class HistoricalQueries:
    """Read stored workflow history without materializing the full event table."""

    def __init__(self, store: DatabaseStore, *, page_size: int = 1000) -> None:
        if page_size < 1:
            raise ValueError("page size must be positive")
        self._store = store
        self._page_size = page_size

    async def workflow_ids(self) -> list[str]:
        """Return workflow IDs present in stored event payloads."""
        return await self._store.list_workflow_ids()

    async def iter_events(
        self, *, workflow_id: str | None = None, after_event_id: int = 0
    ) -> AsyncIterator[SimulatorEvent]:
        """Yield strictly ordered event pages; event IDs need not be consecutive."""
        cursor = after_event_id
        while True:
            page = await self._store.list_events_after(
                cursor, limit=self._page_size, workflow_id=workflow_id
            )
            if not page:
                return
            for event in page:
                yield event
            cursor = page[-1].event_id
