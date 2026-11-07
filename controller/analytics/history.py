"""Historical event access with keyset pagination."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from controller.analytics.cohorts import CohortDefinition, assign_cohort
from controller.storage.models import KpiFact, SimulatorEvent
from controller.storage.store import DatabaseStore


@dataclass(frozen=True, slots=True)
class CallContext:
    """A call with its preceding lead record, if retained, and person history."""

    call: KpiFact
    lead: KpiFact | None
    is_returning_person: bool

    @property
    def has_missing_lead(self) -> bool:
        return self.lead is None

    def reconstruct_cohort(
        self, definitions: Sequence[CohortDefinition]
    ) -> CohortDefinition | None:
        """Evaluate the shared rules against persisted lead features, if joined."""
        return assign_cohort(
            self.lead.features if self.lead is not None else None, definitions
        )

    def cohort_matches(self, definitions: Sequence[CohortDefinition]) -> bool | None:
        """Compare reconstructed cohort to actual call exposure when both exist."""
        reconstructed = self.reconstruct_cohort(definitions)
        if reconstructed is None or self.call.observed_cohort_id is None:
            return None
        return reconstructed.cohort_id == self.call.observed_cohort_id


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

    async def iter_call_context(
        self, workflow_id: str, *, after_event_id: int = 0
    ) -> AsyncIterator[CallContext]:
        """Join calls to only preceding lead facts; missing joins stay explicit."""
        cursor = after_event_id
        while True:
            page = await self._store.list_call_context_after(
                cursor, limit=self._page_size, workflow_id=workflow_id
            )
            if not page:
                return
            for call, lead, returning in page:
                yield CallContext(call, lead, returning)
            cursor = page[-1][0].source_event_id
