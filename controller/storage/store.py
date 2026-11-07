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
from sqlalchemy import cast, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import aliased

from controller.errors import ConfigurationError
from controller.storage.models import (
    AnalyticsCheckpoint,
    ControllerRun,
    ExperimentDecision,
    GovernanceProposal,
    KpiFact,
    OutboxOperation,
    ProcessingCheckpoint,
    SimulatorEvent,
    VersionedCohortDefinition,
    WorkflowReference,
)


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Uninterpreted event data supplied by a future HTTP ingestion layer."""

    event_id: int
    chunk_number: int
    kind: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class KpiFactRecord:
    """Validated additive metrics plus raw-event provenance for one event."""

    source_event_id: int
    source_chunk: int
    source_kind: str
    workflow_id: str | None
    event_type: str
    occurred_at: datetime | None
    campaign_id: str | None
    lead_id: str | None
    person_id: str | None
    config_version: int | None
    observed_cohort_id: str | None
    variant: dict[str, object] | None
    features: dict[str, object] | None
    quality: str
    quality_detail: str | None
    dials: int = 0
    connected: int = 0
    concluded: int = 0
    uploaded: int = 0


class EventConflictError(RuntimeError):
    """The same event identity was replayed with different stored content."""


class CheckpointRegressionError(RuntimeError):
    """A write attempted to move an ordered checkpoint backwards."""


class OutboxConflictError(RuntimeError):
    """A stable outbox identifier was reused for a different operation."""


class GovernanceProposalConflictError(RuntimeError):
    """A stable proposal ID was reused for a different request payload."""


class ExperimentDecisionConflictError(RuntimeError):
    """A stable experiment ID was reused for different inputs or evidence."""


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

    async def get_analytics_cursor(self, processor: str) -> int:
        """Read a derivation cursor, defaulting to the beginning of event history."""
        self._ensure_open()
        async with self._sessions() as session:
            value = await session.scalar(
                select(AnalyticsCheckpoint.event_id).where(
                    AnalyticsCheckpoint.processor == processor
                )
            )
            return value or 0

    async def commit_kpi_page(
        self,
        processor: str,
        facts: list[KpiFactRecord],
        scanned_through_event_id: int,
    ) -> int:
        """Persist a fact page and its scan cursor atomically and idempotently."""
        if scanned_through_event_id < 0 or any(
            fact.source_event_id > scanned_through_event_id for fact in facts
        ):
            raise ValueError("analytics cursor cannot precede a fact in its page")
        inserted_count = 0
        async with self.transaction() as session:
            workflow_ids = {fact.workflow_id for fact in facts if fact.workflow_id}
            for workflow_id in workflow_ids:
                await self.ensure_workflow(session, workflow_id)
            if facts:
                rows = [
                    {
                        "source_event_id": fact.source_event_id,
                        "source_chunk": fact.source_chunk,
                        "source_kind": fact.source_kind,
                        "workflow_id": fact.workflow_id,
                        "event_type": fact.event_type,
                        "occurred_at": fact.occurred_at,
                        "campaign_id": fact.campaign_id,
                        "lead_id": fact.lead_id,
                        "person_id": fact.person_id,
                        "config_version": fact.config_version,
                        "observed_cohort_id": fact.observed_cohort_id,
                        "variant": fact.variant,
                        "features": fact.features,
                        "quality": fact.quality,
                        "quality_detail": fact.quality_detail,
                        "dials": fact.dials,
                        "connected": fact.connected,
                        "concluded": fact.concluded,
                        "uploaded": fact.uploaded,
                    }
                    for fact in facts
                ]
                statement = (
                    postgresql_insert(KpiFact)
                    .values(rows)
                    .on_conflict_do_nothing(index_elements=[KpiFact.source_event_id])
                    .returning(KpiFact.source_event_id)
                )
                inserted_count = len((await session.execute(statement)).scalars().all())

            table = AnalyticsCheckpoint
            cursor_statement = (
                postgresql_insert(table)
                .values(processor=processor, event_id=scanned_through_event_id)
                .on_conflict_do_update(
                    index_elements=[table.processor],
                    set_={
                        "event_id": scanned_through_event_id,
                        "updated_at": func.now(),
                    },
                    where=table.event_id <= scanned_through_event_id,
                )
            )
            updated_cursor = await session.scalar(
                cursor_statement.returning(table.event_id)
            )
            if updated_cursor is None:
                current = await session.scalar(
                    select(table.event_id).where(table.processor == processor)
                )
                raise CheckpointRegressionError(
                    f"analytics cursor {processor!r} cannot move from {current} "
                    f"to {scanned_through_event_id}"
                )
        return inserted_count

    async def save_cohort_definitions(
        self,
        workflow_id: str,
        config_version: int,
        definitions: list[dict[str, object]],
    ) -> None:
        """Persist immutable ordered rules; changed rules need a new version."""
        if (
            not workflow_id
            or isinstance(config_version, bool)
            or not isinstance(config_version, int)
            or config_version < 0
        ):
            raise ValueError("workflow ID and nonnegative config version are required")
        async with self.transaction() as session:
            await self.ensure_workflow(session, workflow_id)
            seen_ids: set[str] = set()
            seen_ordinals: set[int] = set()
            for definition in definitions:
                cohort_id = definition.get("cohort_id")
                ordinal = definition.get("ordinal")
                predicate = definition.get("predicate")
                if (
                    not isinstance(cohort_id, str)
                    or not cohort_id
                    or isinstance(ordinal, bool)
                    or not isinstance(ordinal, int)
                    or ordinal < 0
                    or not isinstance(predicate, dict)
                    or cohort_id in seen_ids
                    or ordinal in seen_ordinals
                ):
                    raise ValueError(
                        "cohort records must have unique IDs/orders and predicates"
                    )
                seen_ids.add(cohort_id)
                seen_ordinals.add(ordinal)
                table = VersionedCohortDefinition
                statement = (
                    postgresql_insert(table)
                    .values(
                        workflow_id=workflow_id,
                        config_version=config_version,
                        cohort_id=cohort_id,
                        ordinal=ordinal,
                        predicate=predicate,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            table.workflow_id,
                            table.config_version,
                            table.cohort_id,
                        ]
                    )
                )
                await session.execute(statement)
                stored = await session.get(
                    table, (workflow_id, config_version, cohort_id)
                )
                if (
                    stored is None
                    or stored.ordinal != ordinal
                    or stored.predicate != predicate
                ):
                    raise ValueError(
                        "cohort definition changed without incrementing config version"
                    )

    async def list_cohort_definitions(
        self, workflow_id: str, config_version: int
    ) -> list[VersionedCohortDefinition]:
        """Read the immutable ordered rules for one observed config version."""
        self._ensure_open()
        async with self._sessions() as session:
            statement = (
                select(VersionedCohortDefinition)
                .where(
                    VersionedCohortDefinition.workflow_id == workflow_id,
                    VersionedCohortDefinition.config_version == config_version,
                )
                .order_by(VersionedCohortDefinition.ordinal)
            )
            return list((await session.scalars(statement)).all())

    async def query_kpi_totals(
        self,
        workflow_id: str,
        *,
        config_version: int | None = None,
        cohort_id: str | None = None,
        variant: dict[str, object] | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> tuple[int, int, int, int, int, int, int]:
        """Aggregate bounded SQL facts for a half-open event-time window."""
        if not workflow_id:
            raise ValueError("workflow ID is required")
        if config_version is not None and (
            isinstance(config_version, bool)
            or not isinstance(config_version, int)
            or config_version < 0
        ):
            raise ValueError("config version must be a nonnegative integer")
        if start_at is not None and start_at.tzinfo is None:
            raise ValueError("start timestamp must include a timezone")
        if end_at is not None and end_at.tzinfo is None:
            raise ValueError("end timestamp must include a timezone")
        if start_at is not None and end_at is not None and end_at <= start_at:
            raise ValueError("end timestamp must follow start timestamp")
        conditions = [KpiFact.workflow_id == workflow_id]
        if config_version is not None:
            conditions.append(KpiFact.config_version == config_version)
        if cohort_id is not None:
            conditions.append(KpiFact.observed_cohort_id == cohort_id)
        if variant is not None:
            conditions.append(cast(KpiFact.variant, JSONB) == cast(variant, JSONB))
        if start_at is not None:
            conditions.append(KpiFact.occurred_at >= start_at)
        if end_at is not None:
            conditions.append(KpiFact.occurred_at < end_at)
        statement = select(
            func.coalesce(func.sum(KpiFact.dials), 0),
            func.coalesce(func.sum(KpiFact.connected), 0),
            func.coalesce(func.sum(KpiFact.concluded), 0),
            func.coalesce(func.sum(KpiFact.uploaded), 0),
            func.count().filter(KpiFact.quality == "invalid"),
            func.count().filter(KpiFact.quality == "incomplete"),
            func.count().filter(KpiFact.quality == "unsupported"),
        ).where(*conditions)
        self._ensure_open()
        async with self._sessions() as session:
            row = (await session.execute(statement)).one()
            return tuple(int(value) for value in row)  # type: ignore[return-value]

    async def list_call_context_after(
        self, event_id: int, *, limit: int, workflow_id: str
    ) -> list[tuple[KpiFact, KpiFact | None, bool]]:
        """Join calls to the latest preceding lead and flag earlier person enrollments."""
        if event_id < 0 or limit < 1 or not workflow_id:
            raise ValueError(
                "event cursor, positive limit and workflow ID are required"
            )
        call = aliased(KpiFact)
        lead_candidate = aliased(KpiFact)
        lead = aliased(KpiFact)
        prior_lead = aliased(KpiFact)
        matching_lead_id = (
            select(func.max(lead_candidate.source_event_id))
            .where(
                lead_candidate.event_type == "lead",
                lead_candidate.workflow_id == call.workflow_id,
                lead_candidate.lead_id == call.lead_id,
                lead_candidate.source_event_id < call.source_event_id,
            )
            .correlate(call)
            .scalar_subquery()
        )
        is_returning = exists(
            select(1)
            .select_from(prior_lead)
            .where(
                prior_lead.event_type == "lead",
                prior_lead.workflow_id == lead.workflow_id,
                prior_lead.person_id == lead.person_id,
                prior_lead.source_event_id < lead.source_event_id,
            )
            .correlate(lead)
        )
        statement = (
            select(call, lead, is_returning)
            .outerjoin(lead, lead.source_event_id == matching_lead_id)
            .where(
                call.event_type == "call",
                call.workflow_id == workflow_id,
                call.source_event_id > event_id,
            )
            .order_by(call.source_event_id)
            .limit(limit)
        )
        self._ensure_open()
        async with self._sessions() as session:
            result = await session.execute(statement)
            return [(row[0], row[1], bool(row[2])) for row in result]

    async def list_experiment_facts(
        self,
        *,
        workflow_id: str,
        config_version: int,
        cohort_id: str,
        dimension: str,
        arm_values: tuple[str, ...],
        start_at: datetime,
        end_at: datetime,
        cutoff_event_id: int,
        after_event_id: int = 0,
        limit: int = 1000,
    ) -> list[KpiFact]:
        """Read a bounded page of valid observed calls for one frozen experiment."""
        if not workflow_id or not cohort_id or not dimension or not arm_values:
            raise ValueError("experiment query identity and arms are required")
        if (
            isinstance(config_version, bool)
            or not isinstance(config_version, int)
            or config_version < 0
            or isinstance(cutoff_event_id, bool)
            or not isinstance(cutoff_event_id, int)
            or cutoff_event_id < 0
            or isinstance(after_event_id, bool)
            or not isinstance(after_event_id, int)
            or after_event_id < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
        ):
            raise ValueError("experiment query versions, cursor and limit are invalid")
        if start_at.tzinfo is None or end_at.tzinfo is None or end_at <= start_at:
            raise ValueError("experiment window must be aware and ordered")
        statement = (
            select(KpiFact)
            .where(
                KpiFact.workflow_id == workflow_id,
                KpiFact.config_version == config_version,
                KpiFact.observed_cohort_id == cohort_id,
                KpiFact.event_type == "call",
                KpiFact.quality == "valid",
                KpiFact.person_id.is_not(None),
                KpiFact.occurred_at >= start_at,
                KpiFact.occurred_at < end_at,
                KpiFact.source_event_id <= cutoff_event_id,
                KpiFact.source_event_id > after_event_id,
                cast(KpiFact.variant, JSONB)[dimension].astext.in_(arm_values),
            )
            .order_by(KpiFact.source_event_id)
            .limit(limit)
        )
        self._ensure_open()
        async with self._sessions() as session:
            return list((await session.scalars(statement)).all())

    async def save_experiment_decision(
        self,
        session: AsyncSession,
        *,
        experiment_id: str,
        workflow_id: str,
        config_version: int,
        cohort_id: str,
        policy_version: str,
        cutoff_event_id: int,
        input_hash: str,
        plan_snapshot: dict[str, object],
        input_snapshot: dict[str, object],
        decision: dict[str, object],
    ) -> tuple[ExperimentDecision, bool]:
        """Persist one immutable decision or verify an identical retry."""
        self.require_transaction(session)
        if not experiment_id or not policy_version or not cohort_id:
            raise ValueError("experiment, policy and cohort identities are required")
        if (
            isinstance(config_version, bool)
            or not isinstance(config_version, int)
            or config_version < 0
            or isinstance(cutoff_event_id, bool)
            or not isinstance(cutoff_event_id, int)
            or cutoff_event_id < 0
        ):
            raise ValueError("experiment config version and cutoff must be nonnegative")
        if (
            not isinstance(input_hash, str)
            or len(input_hash) != 64
            or any(character not in "0123456789abcdef" for character in input_hash)
        ):
            raise ValueError("experiment input hash must be a SHA-256 hex digest")
        await self.ensure_workflow(session, workflow_id)
        values = {
            "experiment_id": experiment_id,
            "workflow_id": workflow_id,
            "config_version": config_version,
            "cohort_id": cohort_id,
            "policy_version": policy_version,
            "cutoff_event_id": cutoff_event_id,
            "input_hash": input_hash,
            "plan_snapshot": plan_snapshot,
            "input_snapshot": input_snapshot,
            "decision": decision,
        }
        statement = (
            postgresql_insert(ExperimentDecision)
            .values(**values)
            .on_conflict_do_nothing()
        )
        inserted = await session.execute(statement)
        stored = await session.get(ExperimentDecision, experiment_id)
        if stored is None:
            family = await session.scalar(
                select(ExperimentDecision).where(
                    ExperimentDecision.workflow_id == workflow_id,
                    ExperimentDecision.config_version == config_version,
                )
            )
            if family is not None:
                raise ExperimentDecisionConflictError(
                    "a decision already exists for this workflow config version"
                )
            raise RuntimeError("experiment decision disappeared after insert")
        if inserted.rowcount == 0 and any(
            getattr(stored, key) != value for key, value in values.items()
        ):
            raise ExperimentDecisionConflictError(
                f"experiment ID {experiment_id!r} was reused for different inputs or outcome"
            )
        return stored, inserted.rowcount == 1

    async def get_experiment_decision(
        self, experiment_id: str
    ) -> ExperimentDecision | None:
        """Read an immutable experiment decision by stable identity."""
        self._ensure_open()
        async with self._sessions() as session:
            return await session.get(ExperimentDecision, experiment_id)

    async def get_experiment_decision_for_config(
        self, workflow_id: str, config_version: int
    ) -> ExperimentDecision | None:
        """Read the single fixed comparison family allowed per workflow version."""
        self._ensure_open()
        async with self._sessions() as session:
            return await session.scalar(
                select(ExperimentDecision).where(
                    ExperimentDecision.workflow_id == workflow_id,
                    ExperimentDecision.config_version == config_version,
                )
            )

    async def list_experiment_decisions(
        self, *, workflow_id: str | None = None
    ) -> list[ExperimentDecision]:
        """List immutable decisions in durable creation order."""
        self._ensure_open()
        statement = select(ExperimentDecision)
        if workflow_id is not None:
            statement = statement.where(ExperimentDecision.workflow_id == workflow_id)
        statement = statement.order_by(
            ExperimentDecision.created_at, ExperimentDecision.experiment_id
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

    async def create_governance_proposal(
        self,
        session: AsyncSession,
        *,
        proposal_id: str,
        workflow_id: str,
        base_version: int,
        payload: dict[str, object],
        payload_hash: str,
        status: str = "draft",
    ) -> GovernanceProposal:
        """Persist an immutable proposal in the caller's transaction."""
        self.require_transaction(session)
        if not proposal_id or not workflow_id:
            raise ValueError("proposal and workflow IDs are required")
        if (
            isinstance(base_version, bool)
            or not isinstance(base_version, int)
            or base_version < 0
        ):
            raise ValueError("base version must be a nonnegative integer")
        if (
            not isinstance(payload_hash, str)
            or len(payload_hash) != 64
            or any(character not in "0123456789abcdef" for character in payload_hash)
        ):
            raise ValueError("proposal payload hash must be a SHA-256 hex digest")
        await self.ensure_workflow(session, workflow_id)
        statement = (
            postgresql_insert(GovernanceProposal)
            .values(
                proposal_id=proposal_id,
                workflow_id=workflow_id,
                base_version=base_version,
                payload=payload,
                payload_hash=payload_hash,
                status=status,
            )
            .on_conflict_do_nothing(index_elements=[GovernanceProposal.proposal_id])
        )
        result = await session.execute(statement)
        existing = await session.get(GovernanceProposal, proposal_id)
        if existing is None:
            raise RuntimeError("governance proposal was not visible after insert")
        if result.rowcount == 0 and (
            existing.workflow_id != workflow_id
            or existing.base_version != base_version
            or existing.payload != payload
            or existing.payload_hash != payload_hash
        ):
            raise GovernanceProposalConflictError(
                f"proposal ID {proposal_id!r} was reused for a different payload"
            )
        return existing

    async def get_governance_proposal(
        self, proposal_id: str
    ) -> GovernanceProposal | None:
        """Read a durable proposal by its stable public ID."""
        self._ensure_open()
        async with self._sessions() as session:
            return await session.get(GovernanceProposal, proposal_id)

    async def get_governance_proposal_by_approval_id(
        self, approval_id: str
    ) -> GovernanceProposal | None:
        """Resolve a simulator decision event to its persisted proposal."""
        self._ensure_open()
        async with self._sessions() as session:
            return await session.scalar(
                select(GovernanceProposal).where(
                    GovernanceProposal.approval_id == approval_id
                )
            )

    async def list_governance_proposals(
        self,
        *,
        workflow_id: str | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> list[GovernanceProposal]:
        """List persisted proposals in creation order, optionally filtered."""
        self._ensure_open()
        statement = select(GovernanceProposal)
        if workflow_id is not None:
            statement = statement.where(GovernanceProposal.workflow_id == workflow_id)
        if statuses is not None:
            statement = statement.where(GovernanceProposal.status.in_(statuses))
        statement = statement.order_by(
            GovernanceProposal.created_at, GovernanceProposal.proposal_id
        )
        async with self._sessions() as session:
            return list((await session.scalars(statement)).all())

    async def update_governance_proposal(
        self,
        session: AsyncSession,
        proposal_id: str,
        *,
        status: str,
        approval_id: str | None = None,
        remote_status: str | None = None,
        outcome: dict[str, object] | None = None,
    ) -> GovernanceProposal | None:
        """Update local lifecycle observations inside the caller's transaction."""
        self.require_transaction(session)
        proposal = await session.get(GovernanceProposal, proposal_id)
        if proposal is None:
            return None
        proposal.status = status
        if approval_id is not None:
            proposal.approval_id = approval_id
        if remote_status is not None:
            proposal.remote_status = remote_status
        if outcome is not None:
            proposal.outcome = outcome
        proposal.updated_at = datetime.now(UTC)
        await session.flush()
        return proposal

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
