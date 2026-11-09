# Architecture and Code Quality Audit

Review date: 2026-11-09. The review covered runtime and shutdown, simulator
client, ingestion/recovery, models/store/migrations, analytics/statistics,
governance/incidents, telemetry/logging, and the matching unit, integration,
and contract tests. No source changes were justified by a clear material defect.

## Boundaries and runtime

- `controller/__main__.py` owns CLI setup, signals, and store/client cleanup;
  `controller/runtime/lifecycle.py` checks readiness, restores ingestion and
  fact work, invokes incident processing, and advances in a serial loop.
- `controller/clients/simulator.py` owns HTTP transport, bounded GET retries,
  method-specific errors, response validation, and streaming. It disables
  redirects and does not log request bodies or credentials.
- `controller/ingestion/` owns descriptor ordering, parsing and replay. No
  Controller module imports simulator implementation code or reads scenario or
  run-directory content.
- Database work is isolated in `controller/storage/`; analytics and lifecycle
  modules call storage methods rather than issuing their own schema writes.

## Persistence and recovery invariants

- `DatabaseStore.transaction()` rolls back for any `BaseException` and commits
  only on normal exit. Event rows and chunk checkpoint share a transaction;
  KPI facts and analytics cursor share a transaction.
- `insert_events_count()` stages bounded streamed batches with PostgreSQL
  COPY, performs a set-based conflict-safe merge, and checks conflicting
  persisted content on replay. The stage is temporary and `ON COMMIT DROP`;
  multiple batches in a chunk reset it inside the still-open transaction.
- `commit_kpi_page()` stages bounded facts with COPY and merges on the
  existing source-event uniqueness key. Workflow references and cursor update
  remain in the page transaction. Failure-injection tests verify rollback.
- Unique constraints and the source/workflow foreign keys remain in force.
  Migration `0009` removes only the reviewed unused event composite index and
  has concurrent create/drop operations; startup migration delegates
  transaction control to Alembic.
- `controller/ingestion/runner.py` persists advance intent before POST and
  reconciles state/descriptors after ambiguous outcomes. It does not claim
  exactly-once remote execution.

## Governance and audit

- `submit_proposal()` persists proposal, stable outbox key, and audit evidence
  before `/approvals`. `apply_approved_proposal()` rechecks approval and base
  config, stores apply intent before POST, and reconciles through documented
  status/config history. Unresolved outcomes halt for manual review.
- Incident suggestions and validation are evidence-gated, credit-aware and
  treated as untrusted; they flow to the same approval/apply lifecycle.
- No direct config write method is used by the Controller. The client does not
  provide an auto-apply `PUT /workflows/{id}/config` method.
- `controller/storage/audit.py` stores durable audit events in transactions
  with state changes. The audit query path is bounded/keyset-paginated.

## Tests supporting these guarantees

- Event duplicate/conflict/rollback and atomic checkpoints:
  `tests/integration/storage/test_store.py` and
  `tests/integration/ingestion/test_ingestion.py`.
- KPI transaction, idempotency and cursor rollback:
  `tests/integration/analytics/test_fact_persistence.py`.
- Ambiguous advance and restart behavior:
  `tests/integration/ingestion/test_ingestion.py`.
- Approval submission, state transitions, stale config, apply ambiguity:
  `tests/integration/governance/`.
- Actual process termination and restart:
  `tests/integration/test_real_crash_recovery.py` and
  `reports/crash_recovery.md`.
- API routes and response contracts:
  `tests/contract/test_simulator_contract.py` and
  `tests/unit/test_simulator_client.py`.

## Findings and limitations

- No material transaction, idempotency, approval-bypass, or resource-lifecycle
  defect was identified in the reviewed code.
- Documentation was inconsistent: README claimed governance was never invoked
  by runtime, while `run_controller()` invokes population incident processing
  after startup and each advance. The README now documents the runtime incident
  hook and separately explains that experiments are not automatically scheduled.
- Statistical experiment evaluation is not scheduled by the runtime; the
  required plan assumptions are caller-provided. This is an explicit current
  capability boundary, not an implicit guarantee of autonomous experimentation.
- `process_population_incidents()` queries workflow config/history while
  processing the workflow list; this contributes work on the serial runtime
  path. Existing saved telemetry does not isolate its cost, so no optimization
  is justified here.
- Request/payload size ceilings are not established by the simulator contract.
  The ingestion spool and row batches are bounded, but one exceptionally large
  NDJSON line could still require correspondingly large parse memory. No
  undocumented payload-size rejection was added.
- API authentication requirements are not specified in the simulator
  contract. Production deployment would need an agreed authentication and
  network policy before connecting to a real service.

This is a source/test review, not a formal penetration test or production
capacity certification.

