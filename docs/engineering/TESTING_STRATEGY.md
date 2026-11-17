# Testing strategy

## Test layers

- **Unit tests** cover input validation, KPI math, fact mapping, state
  transitions, estimation, settings, logging, and telemetry without a live
  database or simulator.
- **Contract tests** exercise the bundled API route shapes against the client
  expectations. They do not certify compatibility with an independently
  deployed production service.
- **PostgreSQL integration tests** cover persistence, constraints, COPY
  staging/merge, replay conflict behavior, transaction rollback, cursor and
  checkpoint atomicity, query behavior, governance state, and migrations.
- **Recovery integration tests** exercise interrupted operations and restart
  against persistent local state. Their claims are limited to the tested
  scenarios and database/service contract.

## Safe local commands

```powershell
# Configure CONTROLLER_TEST_DATABASE_URL to a disposable PostgreSQL database.
python -m pytest -q tests/unit tests/contract
python -m pytest -q tests/integration
python -m pytest -q
ruff check .
ruff format --check .
python -m compileall -q controller tests
python -m alembic -c alembic.ini check
git diff --check
```

The integration fixture applies migrations and deletes rows from controller
tables before each database test. Point it only at an isolated disposable
test database. If the URL is absent, PostgreSQL integration tests are skipped;
that is not a passing integration verification.

## Retained verification evidence

The latest repository verification record reports 214 unit/contract tests and
92 PostgreSQL integration tests passing (306 total), migration
downgrade/upgrade and schema check passing, and Ruff, format, compile, and diff
checks passing. This is historical evidence recorded in the repository on
2026-11-09. The documentation transformation has not rerun application tests.

Tests verify component behavior under fixtures; they do not establish
production capacity, external-service compatibility, or the full optimized
workload runtime.
