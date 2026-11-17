# Component map

```text
controller/
  analytics/       KPI facts, cohort rules, history, audit queries
  clients/         HTTP integration boundary
  config/          validated environment settings
  errors/          typed controller and service errors
  governance/      proposal state, approvals, apply reconciliation
  ingestion/       chunk parsing, event validation, advance recovery
  observability/   structured log configuration
  runtime/         readiness and serial controller lifecycle
  statistics/      comparison inputs and deterministic estimation
  storage/         models, PostgreSQL writes, audit persistence
  __main__.py      CLI process entry point
  experiments.py   persisted evidence evaluation and proposal handoff
  incidents.py     population QA incident orchestration
  telemetry.py     bounded runtime measurements
migrations/        Alembic revisions
tests/             unit, contract, and PostgreSQL integration tests
sim/               bundled local development/test simulator
scenarios/         local simulator fixtures
```

The simulator and scenario fixtures support local development and tests. The
controller's production boundary is the HTTP client; it has no import of the
simulator implementation.
