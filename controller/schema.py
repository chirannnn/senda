"""Versioned SQLAlchemy schema for durable controller state."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Metadata root for Alembic and controller models."""


class ControllerRun(Base):
    """One controller process attempt; simulator events are run-independent."""

    __tablename__ = "controller_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'interrupted')",
            name="ck_controller_runs_status",
        ),
        Index("ix_controller_runs_started_at", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default="running"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )


class WorkflowReference(Base):
    """A controller-side reference to a workflow learned from permitted API data."""

    __tablename__ = "workflow_references"

    workflow_id: Mapped[str] = mapped_column(Text, primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SimulatorEvent(Base):
    """Raw event envelope; payload remains flexible until ingestion validates it."""

    __tablename__ = "simulator_events"
    __table_args__ = (
        Index("ix_simulator_events_chunk", "chunk_number"),
        Index("ix_simulator_events_kind_chunk", "kind", "chunk_number"),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chunk_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    stored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProcessingCheckpoint(Base):
    """Last fully committed chunk position for a named ordered descriptor stream."""

    __tablename__ = "processing_checkpoints"
    checkpoint_key: Mapped[str] = mapped_column(Text, primary_key=True)
    chunk_number: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditRecord(Base):
    """Append-only explanation of a controller observation or action."""

    __tablename__ = "audit_records"
    __table_args__ = (
        Index("ix_audit_records_workflow_time", "workflow_id", "occurred_at"),
        Index("ix_audit_records_action_time", "action", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("workflow_references.workflow_id")
    )
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict, server_default="{}"
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutboxOperation(Base):
    """Durable record of an external operation requiring later reconciliation."""

    __tablename__ = "outbox_operations"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_outbox_operations_attempts"),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'succeeded', 'failed', 'reconciliation_required')",
            name="ck_outbox_operations_status",
        ),
        Index("ix_outbox_operations_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    workflow_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("workflow_references.workflow_id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
