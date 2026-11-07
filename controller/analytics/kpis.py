"""Independent, denominator-explicit pickup and engagement summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from controller.analytics.cohorts import CohortDefinition
from controller.storage.store import DatabaseStore


@dataclass(frozen=True, slots=True)
class KpiSummary:
    """Additive facts and rates; a zero denominator produces no rate."""

    workflow_id: str
    config_version: int | None
    cohort_id: str | None
    variant: dict[str, object] | None
    start_at: datetime | None
    end_at: datetime | None
    dials: int
    connected: int
    concluded: int
    uploaded: int
    invalid_facts: int
    incomplete_facts: int
    unsupported_facts: int

    @property
    def pickup_rate(self) -> float | None:
        """Connected calls divided by total dials."""
        return self.connected / self.dials if self.dials else None

    @property
    def engagement_rate(self) -> float | None:
        """Concluded calls divided by connected calls, independent of pickup."""
        return self.concluded / self.connected if self.connected else None


class KpiCalculator:
    """Read precomputed facts using bounded SQL aggregation."""

    def __init__(self, store: DatabaseStore) -> None:
        self._store = store

    async def calculate(
        self,
        workflow_id: str,
        *,
        config_version: int | None = None,
        cohort_id: str | None = None,
        cohort: CohortDefinition | None = None,
        variant: dict[str, object] | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> KpiSummary:
        if cohort is not None:
            if cohort.workflow_id != workflow_id:
                raise ValueError("cohort workflow does not match KPI workflow")
            if config_version is not None and config_version != cohort.config_version:
                raise ValueError("cohort config version conflicts with KPI filter")
            if cohort_id is not None and cohort_id != cohort.cohort_id:
                raise ValueError("cohort ID conflicts with KPI filter")
            config_version = cohort.config_version
            cohort_id = cohort.cohort_id
        totals = await self._store.query_kpi_totals(
            workflow_id,
            config_version=config_version,
            cohort_id=cohort_id,
            variant=variant,
            start_at=start_at,
            end_at=end_at,
        )
        return KpiSummary(
            workflow_id=workflow_id,
            config_version=config_version,
            cohort_id=cohort_id,
            variant=variant,
            start_at=start_at,
            end_at=end_at,
            dials=totals[0],
            connected=totals[1],
            concluded=totals[2],
            uploaded=totals[3],
            invalid_facts=totals[4],
            incomplete_facts=totals[5],
            unsupported_facts=totals[6],
        )
