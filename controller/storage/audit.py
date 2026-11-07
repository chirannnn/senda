"""Durable append-only audit writer and query helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from controller.storage.models import AuditRecord
from controller.storage.store import DatabaseStore


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """Application-supplied explanation fields; evidence remains JSON data."""

    action: str
    actor: str
    reason: str
    workflow_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    evidence: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AuditCursor:
    """Stable keyset position for audit rows ordered by time and UUID."""

    occurred_at: datetime
    record_id: UUID

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("audit cursor timestamp must include a timezone")


@dataclass(frozen=True, slots=True)
class AuditQuery:
    """Bounded read filters for workflow, actor, time, config and outcome."""

    workflow_id: str | None = None
    actor: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    config_version: int | None = None
    cohort_id: str | None = None
    approval_id: str | None = None
    outcome: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    page_size: int = 100
    after: AuditCursor | None = None

    def __post_init__(self) -> None:
        if isinstance(self.page_size, bool) or not 1 <= self.page_size <= 500:
            raise ValueError("audit page size must be between 1 and 500")
        if self.config_version is not None and (
            isinstance(self.config_version, bool)
            or not isinstance(self.config_version, int)
            or self.config_version < 0
        ):
            raise ValueError("config version must be a nonnegative integer")
        for value in (self.start_at, self.end_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("audit timestamps must include a timezone")
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.end_at <= self.start_at
        ):
            raise ValueError("audit end timestamp must follow start timestamp")


@dataclass(frozen=True, slots=True)
class AuditPage:
    """One ordered page and an optional continuation cursor."""

    records: tuple[AuditRecord, ...]
    next_cursor: AuditCursor | None


class AuditLog:
    """Append-only API that can join a caller transaction or own its transaction."""

    def __init__(self, store: DatabaseStore) -> None:
        self._store = store

    async def append(self, session: AsyncSession, entry: AuditEntry) -> UUID:
        """Add one row to an existing transaction, returning its stable local ID."""
        self._store.require_transaction(session)
        if entry.workflow_id is not None:
            await self._store.ensure_workflow(session, entry.workflow_id)
        record = AuditRecord(
            action=entry.action,
            actor=entry.actor,
            reason=entry.reason,
            workflow_id=entry.workflow_id,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            evidence=entry.evidence,
        )
        session.add(record)
        await session.flush()
        return record.id

    async def record(self, entry: AuditEntry) -> UUID:
        """Append and commit a standalone audit record."""
        async with self._store.transaction() as session:
            return await self.append(session, entry)

    async def list_for_workflow(self, workflow_id: str) -> list[AuditRecord]:
        """Read audit records for one workflow in timestamp and ID order."""
        async with self._store.transaction() as session:
            result = await session.scalars(
                select(AuditRecord)
                .where(AuditRecord.workflow_id == workflow_id)
                .order_by(AuditRecord.occurred_at, AuditRecord.id)
            )
            return list(result)

    async def query(self, query: AuditQuery) -> AuditPage:
        """Return an ordered, read-only page matching the supplied filters."""
        statement = select(AuditRecord)
        conditions = []
        if query.workflow_id is not None:
            conditions.append(AuditRecord.workflow_id == query.workflow_id)
        if query.actor is not None:
            conditions.append(AuditRecord.actor == query.actor)
        if query.action is not None:
            conditions.append(AuditRecord.action == query.action)
        if query.resource_type is not None:
            conditions.append(AuditRecord.resource_type == query.resource_type)
        if query.resource_id is not None:
            conditions.append(AuditRecord.resource_id == query.resource_id)
        if query.config_version is not None:
            conditions.append(
                cast(AuditRecord.evidence["config_version"], JSONB)
                == cast(query.config_version, JSONB)
            )
        if query.cohort_id is not None:
            conditions.append(
                AuditRecord.evidence["cohort_id"].as_string() == query.cohort_id
            )
        if query.approval_id is not None:
            conditions.append(
                AuditRecord.evidence["approval_id"].as_string() == query.approval_id
            )
        if query.outcome is not None:
            conditions.append(
                AuditRecord.evidence["outcome"].as_string() == query.outcome
            )
        if query.start_at is not None:
            conditions.append(AuditRecord.occurred_at >= query.start_at)
        if query.end_at is not None:
            conditions.append(AuditRecord.occurred_at < query.end_at)
        if query.after is not None:
            conditions.append(
                or_(
                    AuditRecord.occurred_at > query.after.occurred_at,
                    and_(
                        AuditRecord.occurred_at == query.after.occurred_at,
                        AuditRecord.id > query.after.record_id,
                    ),
                )
            )
        if conditions:
            statement = statement.where(*conditions)
        statement = statement.order_by(AuditRecord.occurred_at, AuditRecord.id).limit(
            query.page_size + 1
        )
        async with self._store.transaction() as session:
            rows = list((await session.scalars(statement)).all())
        has_more = len(rows) > query.page_size
        records = tuple(rows[: query.page_size])
        cursor = (
            AuditCursor(records[-1].occurred_at, records[-1].id)
            if has_more and records
            else None
        )
        return AuditPage(records, cursor)
