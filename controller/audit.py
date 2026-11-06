"""Durable append-only audit writer and query helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from controller.schema import AuditRecord
from controller.store import DatabaseStore


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
