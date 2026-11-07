"""Audit-query adapters using the canonical versioned cohort identity."""

from __future__ import annotations

from datetime import datetime

from controller.analytics.cohorts import CohortDefinition
from controller.storage.audit import AuditCursor, AuditLog, AuditPage, AuditQuery


async def query_for_cohort(
    audit: AuditLog,
    cohort: CohortDefinition,
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    actor: str | None = None,
    page_size: int = 100,
    after: AuditCursor | None = None,
) -> AuditPage:
    """Query only audit evidence attached to this shared cohort identity."""
    return await audit.query(
        AuditQuery(
            workflow_id=cohort.workflow_id,
            actor=actor,
            config_version=cohort.config_version,
            cohort_id=cohort.cohort_id,
            start_at=start_at,
            end_at=end_at,
            page_size=page_size,
            after=after,
        )
    )
