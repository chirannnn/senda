# Architecture and engineering decisions

## Runtime shape

The application is a single asyncio CLI process. The entry point validates
settings, initializes the async SQLAlchemy store, applies Alembic migrations,
opens one bounded HTTPX client, and invokes the runtime lifecycle. The runtime
checks service readiness, restores published history, reconciles pending
advance intents, derives facts, evaluates population incidents, and then
advances serially until completion or a stop signal.

## Main component responsibilities

| Component | Responsibility and boundary |
|---|---|
| `controller/__main__.py` | CLI, signal handling, resource lifecycle, top-level error handling |
| `controller/config/` | Immutable settings and URL/time/batch bounds |
| `controller/clients/simulator.py` | HTTP transport, documented methods, response validation, bounded GET retries, ambiguous write errors |
| `controller/ingestion/` | Chunk descriptors, streaming parse, validation/order, advance intent and reconciliation |
| `controller/storage/` | SQLAlchemy model, explicit transactions, PostgreSQL COPY merge, checkpoints/outbox/audit persistence |
| `controller/analytics/` | Fact projection, KPI summaries, versioned cohort/history queries, audit query validation |
| `controller/statistics/`, `controller/experiments.py` | Deterministic estimator, fixed evidence evaluation and governance handoff; callable rather than scheduled |
| `controller/governance/` | Proposal validation, state machine, approval/apply intent, reconciliation |
| `controller/incidents.py` | Narrow population QA incident workflow and governed prompt remediation |
| `controller/telemetry.py`, `controller/observability/` | Bounded metrics and structured logging |
| `migrations/` | Versioned PostgreSQL schema |

## Persistence choices

Raw events and derived KPI facts are separate records. Raw events preserve the
source envelope; facts carry typed additive counts, quality classification,
and provenance. Event identity and fact source identity are unique. Foreign
keys and query indexes preserve the intended joins and filters.

Bounded event and fact batches are sent through PostgreSQL COPY into
transaction-local staging tables, then merged with set-based
`INSERT ... SELECT ... ON CONFLICT`. This was selected to reduce row-oriented
database-call overhead while retaining unique constraints, conflict checks,
and the existing transaction unit. The isolated fixture measurements and
limits are documented in [performance](../engineering/PERFORMANCE.md).

The event rows and ordered chunk checkpoint commit together. A fact page,
workflow-reference updates, and analytics cursor also commit together. The
controller never commits a partial chunk merely to reduce transaction size.

## Remote operation decisions

PostgreSQL cannot atomically commit with an HTTP server. The controller stores
an outbox intent before a remote write, then uses documented read endpoints to
reconcile. GET retries are bounded; remote POSTs are not blindly retried after
an ambiguous response. If observed state cannot prove whether a change took
effect, the operation stops for review. This favors safety over automatic
availability and does not promise exactly-once remote effects.

## Alternatives and trade-offs

- A broker or worker fleet would add delivery and ordering states without a
  demonstrated need for this single-process prototype; ordered writes remain
  serial.
- Larger batches are configurable within validated limits, but defaults have
  not been changed based on unsupported extrapolation.
- Additional indexes and parallel writers are not introduced without evidence
  that they improve the actual query/write path without harming recovery or
  read performance.
- Statistical evaluation is explicit and deterministic; the runtime does not
  create experiment plans or assign traffic.
