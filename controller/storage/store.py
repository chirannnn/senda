"""Async PostgreSQL store and transaction boundary."""

from __future__ import annotations

import os
from collections.abc import AsyncIterable, AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from controller.errors import ConfigurationError
from controller.storage.models import (
    ControllerRun,
    OutboxOperation,
    ProcessingCheckpoint,
    SimulatorEvent,
    WorkflowReference,
)


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Uninterpreted event data supplied by a future HTTP ingestion layer."""

    event_id: int
    chunk_number: int
    kind: str
    payload: dict[str, object]


class EventConflictError(RuntimeError):
    """The same event identity was replayed with different stored content."""


class CheckpointRegressionError(RuntimeError):
    """A write attempted to move an ordered checkpoint backwards."""


class OutboxConflictError(RuntimeError):
    """A stable outbox identifier was reused for a different operation."""


class DatabaseStore:
    """Owns the async engine and exposes explicit transaction scopes."""

    def __init__(
        self,
        database_url: str | URL,
        *,
        pool_size: int = 5,
        max_overflow: int = 5,
    ) -> None:
        url = self._validated_url(database_url)
        if pool_size < 1 or max_overflow < 0:
            raise ConfigurationError(
                "database pool size must be positive and max overflow nonnegative"
            )
        self._engine: AsyncEngine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            hide_parameters=True,
        )
        self._sessions = async_sessionmaker(
            self._engine, expire_on_commit=False, class_=AsyncSession
        )
        self._closed = False

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Self:
        """Read the URL without logging or including it in validation errors."""
        values = os.environ if environ is None else environ
        database_url = values.get("CONTROLLER_DATABASE_URL")
        if not database_url:
            raise ConfigurationError(
                "CONTROLLER_DATABASE_URL is required to use durable storage"
            )
        return cls(database_url)

    @property
    def engine(self) -> AsyncEngine:
        """Engine for integration wiring and diagnostics; it hides SQL parameters."""
        self._ensure_open()
        return self._engine

    async def __aenter__(self) -> Self:
        self._ensure_open()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.close()

    async def initialize(self) -> None:
        """Verify database connectivity without creating schema or changing data."""
        self._ensure_open()
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def migrate(self) -> None:
        """Apply checked-in Alembic revisions through the owned async connection."""
        self._ensure_open()
        alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
        config = Config(str(alembic_ini))
        async with self._engine.begin() as connection:
            await connection.run_sync(self._run_upgrade, config)

    @staticmethod
    def _run_upgrade(sync_connection: Connection, config: Config) -> None:
        config.attributes["connection"] = sync_connection
        command.upgrade(config, "head")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Commit on normal exit and roll back every write on exceptional exit."""
        self._ensure_open()
        async with self._sessions() as session, session.begin():
            yield session

    async def insert_events(
        self, session: AsyncSession, events: list[EventRecord]
    ) -> tuple[int, ...]:
        """Insert unique events and return IDs inserted by this transaction only.

        Callers must apply any event-derived effects only for the returned IDs,
        within this same transaction. A duplicate with different stored content
        raises so a corrupt replay cannot be mistaken for an idempotent retry.
        """
        self._ensure_open()
        if not session.in_transaction():
            raise RuntimeError("insert_events requires an active store transaction")
        if not events:
            return ()

        statement = (
            postgresql_insert(SimulatorEvent)
            .values(
                [
                    {
                        "event_id": event.event_id,
                        "chunk_number": event.chunk_number,
                        "kind": event.kind,
                        "payload": event.payload,
                    }
                    for event in events
                ]
            )
            .on_conflict_do_nothing(index_elements=[SimulatorEvent.event_id])
            .returning(SimulatorEvent.event_id)
        )
        inserted_ids = tuple((await session.execute(statement)).scalars())
        event_ids = {event.event_id for event in events}
        persisted = (
            await session.execute(
                select(SimulatorEvent).where(SimulatorEvent.event_id.in_(event_ids))
            )
        ).scalars()
        persisted_by_id = {record.event_id: record for record in persisted}
        for event in events:
            existing = persisted_by_id[event.event_id]
            if (
                existing.chunk_number != event.chunk_number
                or existing.kind != event.kind
                or existing.payload != event.payload
            ):
                raise EventConflictError(
                    f"event ID {event.event_id} was previously stored with different content"
                )
        return inserted_ids

    async def get_event(self, event_id: int) -> SimulatorEvent | None:
        """Read a raw stored event by its unique simulator ID."""
        self._ensure_open()
        async with self._sessions() as session:
            return await session.get(SimulatorEvent, event_id)

    async def get_latest_event_id(self) -> int | None:
        """Read the highest persisted event ID without assuming contiguous IDs."""
        self._ensure_open()
        async with self._sessions() as session:
            return await session.scalar(select(func.max(SimulatorEvent.event_id)))

    async def list_events_after(
        self, event_id: int, *, limit: int, workflow_id: str | None = None
    ) -> list[SimulatorEvent]:
        """Read a bounded, event-ID ordered page for restartable derivation."""
        self._ensure_open()
        if event_id < 0 or limit < 1:
            raise ValueError("event cursor must be nonnegative and limit positive")
        statement = select(SimulatorEvent).where(SimulatorEvent.event_id > event_id)
        if workflow_id is not None:
            statement = statement.where(
                SimulatorEvent.payload["workflow_id"].as_string() == workflow_id
            )
        statement = statement.order_by(SimulatorEvent.event_id).limit(limit)
        async with self._sessions() as session:
            return list((await session.scalars(statement)).all())

    async def list_workflow_ids(self) -> list[str]:
        """List workflow IDs observed in persisted event payloads."""
        self._ensure_open()
        workflow_id = (
            SimulatorEvent.payload["workflow_id"].as_string().label("workflow_id")
        )
        statement = (
            select(workflow_id)
            .where(workflow_id.is_not(None))
            .distinct()
            .order_by(workflow_id)
        )
        async with self._sessions() as session:
            return list((await session.scalars(statement)).all())

    async def create_run(
        self, session: AsyncSession, details: dict[str, object] | None = None
    ) -> UUID:
        """Create a controller process attempt inside the caller's transaction."""
        self.require_transaction(session)
        run = ControllerRun(details=details or {})
        session.add(run)
        await session.flush()
        return run.id

    async def finish_run(
        self, session: AsyncSession, run_id: UUID, status: str
    ) -> bool:
        """Mark a run terminal; the database constraint validates supported states."""
        self.require_transaction(session)
        if status not in {"succeeded", "failed", "interrupted"}:
            raise ValueError("run status must be succeeded, failed, or interrupted")
        run = await session.get(ControllerRun, run_id)
        if run is None:
            return False
        run.status = status
        run.finished_at = datetime.now(UTC)
        await session.flush()
        return True

    async def get_run(self, run_id: UUID) -> ControllerRun | None:
        self._ensure_open()
        async with self._sessions() as session:
            return await session.get(ControllerRun, run_id)

    async def ensure_workflow(self, session: AsyncSession, workflow_id: str) -> None:
        """Create or refresh a local reference without copying workflow config."""
        self.require_transaction(session)
        statement = (
            postgresql_insert(WorkflowReference)
            .values(workflow_id=workflow_id)
            .on_conflict_do_update(
                index_elements=[WorkflowReference.workflow_id],
                set_={"updated_at": func.now()},
            )
        )
        await session.execute(statement)

    async def advance_checkpoint(
        self, session: AsyncSession, checkpoint_key: str, chunk_number: int
    ) -> int:
        """Advance to an API-ordered chunk descriptor inside the caller's transaction."""
        self.require_transaction(session)
        table = ProcessingCheckpoint
        statement = (
            postgresql_insert(table)
            .values(checkpoint_key=checkpoint_key, chunk_number=chunk_number)
            .on_conflict_do_update(
                index_elements=[table.checkpoint_key],
                set_={"chunk_number": chunk_number, "updated_at": func.now()},
                where=or_(
                    table.chunk_number.is_(None),
                    table.chunk_number <= chunk_number,
                ),
            )
            .returning(table.chunk_number)
        )
        result = await session.execute(statement)
        advanced_to = result.scalar_one_or_none()
        if advanced_to is None:
            current = await session.scalar(
                select(table.chunk_number).where(table.checkpoint_key == checkpoint_key)
            )
            raise CheckpointRegressionError(
                f"checkpoint {checkpoint_key!r} cannot move from {current} to {chunk_number}"
            )
        return advanced_to

    async def get_checkpoint(self, checkpoint_key: str) -> int | None:
        self._ensure_open()
        async with self._sessions() as session:
            return await session.scalar(
                select(ProcessingCheckpoint.chunk_number).where(
                    ProcessingCheckpoint.checkpoint_key == checkpoint_key
                )
            )

    async def commit_chunk(
        self,
        checkpoint_key: str,
        chunk_number: int,
        events: list[EventRecord],
    ) -> tuple[int, ...]:
        """Persist a chunk's raw rows and descriptor checkpoint atomically.

        This is a storage primitive only: it does not fetch, parse, or dispatch
        simulator events. Any failed insert or checkpoint update rolls back both.
        """
        if any(event.chunk_number != chunk_number for event in events):
            raise ValueError("every event must match the checkpoint chunk number")
        async with self.transaction() as session:
            inserted_ids = await self.insert_events(session, events)
            await self.advance_checkpoint(session, checkpoint_key, chunk_number)
            return inserted_ids

    async def commit_chunk_stream(
        self,
        checkpoint_key: str,
        chunk_number: int,
        events: AsyncIterable[EventRecord],
        *,
        batch_size: int = 500,
    ) -> int:
        """Insert a streamed chunk in bounded batches with one atomic checkpoint.

        The caller must provide events in validated API order. No checkpoint is
        written until the input is exhausted successfully and all batches have
        been flushed in the same transaction.
        """
        if batch_size < 1:
            raise ValueError("batch size must be positive")
        inserted_count = 0
        async with self.transaction() as session:
            batch: list[EventRecord] = []
            async for event in events:
                if event.chunk_number != chunk_number:
                    raise ValueError(
                        "every event must match the checkpoint chunk number"
                    )
                batch.append(event)
                if len(batch) >= batch_size:
                    inserted_count += len(await self.insert_events(session, batch))
                    batch.clear()
            if batch:
                inserted_count += len(await self.insert_events(session, batch))
            await self.advance_checkpoint(session, checkpoint_key, chunk_number)
        return inserted_count

    async def create_outbox_operation(
        self,
        session: AsyncSession,
        *,
        operation_type: str,
        dedupe_key: str,
        payload: dict[str, object],
        workflow_id: str | None = None,
    ) -> OutboxOperation:
        """Persist a future external operation once per caller-chosen stable key."""
        self.require_transaction(session)
        if workflow_id is not None:
            await self.ensure_workflow(session, workflow_id)
        statement = (
            postgresql_insert(OutboxOperation)
            .values(
                operation_type=operation_type,
                dedupe_key=dedupe_key,
                payload=payload,
                workflow_id=workflow_id,
            )
            .on_conflict_do_nothing(index_elements=[OutboxOperation.dedupe_key])
            .returning(OutboxOperation.id)
        )
        inserted_id = (await session.execute(statement)).scalar_one_or_none()
        existing = await session.scalar(
            select(OutboxOperation).where(OutboxOperation.dedupe_key == dedupe_key)
        )
        if existing is None:
            raise RuntimeError("outbox row was not visible after its insert")
        if inserted_id is None and (
            existing.operation_type != operation_type
            or existing.payload != payload
            or existing.workflow_id != workflow_id
        ):
            raise OutboxConflictError(
                f"outbox key {dedupe_key!r} was reused for a different operation"
            )
        return existing

    async def get_outbox_operation(self, dedupe_key: str) -> OutboxOperation | None:
        self._ensure_open()
        async with self._sessions() as session:
            return await session.scalar(
                select(OutboxOperation).where(OutboxOperation.dedupe_key == dedupe_key)
            )

    async def list_outbox_operations(
        self,
        operation_type: str,
        *,
        statuses: tuple[str, ...] | None = None,
    ) -> list[OutboxOperation]:
        """Read durable operations in creation order for restart reconciliation."""
        self._ensure_open()
        async with self._sessions() as session:
            statement = select(OutboxOperation).where(
                OutboxOperation.operation_type == operation_type
            )
            if statuses is not None:
                statement = statement.where(OutboxOperation.status.in_(statuses))
            statement = statement.order_by(
                OutboxOperation.created_at, OutboxOperation.id
            )
            return list((await session.scalars(statement)).all())

    async def update_outbox_status(
        self,
        session: AsyncSession,
        operation_id: UUID,
        status: str,
        *,
        increment_attempts: bool = False,
        payload: dict[str, object] | None = None,
    ) -> OutboxOperation | None:
        """Record local reconciliation state; performs no external operation."""
        self.require_transaction(session)
        operation = await session.get(OutboxOperation, operation_id)
        if operation is None:
            return None
        operation.status = status
        if payload is not None:
            operation.payload = payload
        operation.updated_at = datetime.now(UTC)
        if increment_attempts:
            operation.attempts += 1
        operation.completed_at = datetime.now(UTC) if status == "succeeded" else None
        await session.flush()
        return operation

    def require_transaction(self, session: AsyncSession) -> None:
        self._ensure_open()
        if not session.in_transaction():
            raise RuntimeError("storage write requires an active store transaction")

    async def close(self) -> None:
        """Dispose pooled connections; safe to call repeatedly."""
        if not self._closed:
            await self._engine.dispose()
            self._closed = True

    @staticmethod
    def _validated_url(database_url: str | URL) -> URL:
        try:
            url = (
                make_url(database_url)
                if isinstance(database_url, str)
                else database_url
            )
        except (ArgumentError, TypeError, ValueError) as exc:
            raise ConfigurationError("CONTROLLER_DATABASE_URL is malformed") from exc
        if url.drivername != "postgresql+asyncpg":
            raise ConfigurationError(
                "CONTROLLER_DATABASE_URL must use postgresql+asyncpg"
            )
        if not url.host or not url.database:
            raise ConfigurationError(
                "CONTROLLER_DATABASE_URL must include a host and database name"
            )
        return url

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("database store is closed")
