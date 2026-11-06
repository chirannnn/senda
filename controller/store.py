"""Async PostgreSQL store and transaction boundary."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
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
from controller.schema import SimulatorEvent


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Uninterpreted event data supplied by a future HTTP ingestion layer."""

    event_id: int
    chunk_number: int
    kind: str
    payload: dict[str, object]


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
        alembic_ini = Path(__file__).resolve().parent.parent / "alembic.ini"
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


class EventConflictError(RuntimeError):
    """The same event identity was replayed with different stored content."""
