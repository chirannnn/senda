# Progress Tracking — Outcome-First Agent Loop

## Overall status: IN PROGRESS — CHUNK 6

**Current phase:** Chunk 6 statistical comparison
**Current chunk:** 6 — Statistical comparison
**Current next action:** Complete Chunk 6 subtask 2: observed variant evidence and deterministic experiment evaluation.

## Implementation progress

| Chunk | Name | Status | Commit |
|---|---|---|---|
| 1 | Runtime and launcher | COMPLETE | `cfc28d2`, `730cf26`, `71bdd8a` (and earlier foundation/config commits listed below) |
| 2 | Durable schema and audit | COMPLETE | `aab9708`, `2443889`, `31af0ea`, `a3d7a6b`, `e88ed27` |
| 3 | Simulator chunk ingestion | COMPLETE | `d921490`, `b6b88f6`, `6bdce40`, `f975203`, `09bf0b6`, `adf5b54` |
| 4 | History, KPI facts, audit query | COMPLETE | `79caf3d`, `bf4e93b`, `237d6b2`, `f640e9b`, `bd1f94b`, `b87a39b` |
| 5 | Governance lifecycle | COMPLETE | `29d7e81`, `d04a0bf`, `40aca99`, `feb3c77`, `50fbc35`, this subtask's final hardening commit |
| 6 | Statistical comparison | NOT STARTED | — |
| 7 | Public incident workflow | NOT STARTED | — |
| 8 | Crash recovery demonstration | NOT STARTED | — |
| 9 | Load and observability | NOT STARTED | — |
| 10 | Submission and final grading | NOT STARTED | — |

## Chunk 6 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Statistical primitives | COMPLETE | 13 unit tests passed; Ruff, formatting, compileall and git diff --check passed | pending |
| 2. Variant evidence and evaluation | NOT STARTED | — | — |
| 3. Durable decisions and recovery | NOT STARTED | — | — |
| 4. Governed proposal integration | NOT STARTED | — | — |
| 5. Integration and acceptance | NOT STARTED | — | — |

## Chunk 5 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Proposal validation and lifecycle model | COMPLETE | Focused governance unit tests: 31 passed; Ruff check/format and `git diff --check` passed | `29d7e81` |
| 2. Durable governance storage | COMPLETE | Isolated PostgreSQL integration and schema tests: 10 passed; migration `0005` applied; Ruff check/format and `git diff --check` passed | `d04a0bf` |
| 3. Approval submission and reconciliation | COMPLETE | Focused client/governance tests: 48 passed with isolated PostgreSQL; Ruff check/format and `git diff --check` passed | `40aca99` |
| 4. Approval state machine | COMPLETE | Focused state-machine plus submission tests: 47 passed with isolated PostgreSQL; Ruff check/format and `git diff --check` passed | `feb3c77` |
| 5. Stale checks and safe apply | COMPLETE | Focused governance/client/PostgreSQL suite: 67 passed; Ruff check/format and `git diff --check` passed | `50fbc35` |
| 6. Integration and hardening | COMPLETE | Full suite: 189 passed; Ruff check, format, compileall, `git diff --check`, Alembic check and head verification passed | this subtask's final hardening commit |

Subtask 1 adds an immutable approval request payload matching the documented required fields, with optional evidence and expiry. It validates values and JSON compatibility and exposes a deterministic SHA-256 hash for detecting payload changes under a stable proposal ID. The lifecycle model encodes proposal, approval, apply, stale, lapse, and reconciliation-required states; same-state observations are idempotent and terminal outcomes cannot transition onward. Focused tests cover accepted/rejected fields, hash stability, valid transitions, invalid transitions, and terminal states. The isolated server's `/openapi.json` was queried: these routes publish generic JSON object bodies and no concrete response schemas. Exact approval fields are therefore validated against `docs/SIMULATOR.md` and the route/handler behavior, not inferred from OpenAPI schemas.

Subtask 2 adds `governance_proposals` in migration `0005_governance_proposals`, storing immutable request payload/hash, workflow/base version, local/remote status, approval ID, and reconciliation outcome. `DatabaseStore` can create/get/list/update proposals. Proposal creation can share a transaction with its outbox operation; duplicate identical IDs are idempotent, while a different payload under the same ID fails. PostgreSQL tests cover persistence, outbox linkage, duplicate conflict, invalid status constraints, and rollback of proposal plus intent. The focused run used an isolated temporary PostgreSQL 18 cluster on localhost; no configured development database was touched.

Subtask 3 adds GET client operations for current config, config history, and approval list/get, plus a single-attempt POST `/approvals`. Approval responses are validated for the documented IDs, status, base version, and changed dimensions. `submit_proposal()` persists the immutable payload, outbox intent, and pre-submit audit evidence in one transaction before sending the POST. It reconciles duplicate 409 and ambiguous outcomes by listing approvals for the workflow and matching `proposal_id`; no match or unavailable lookup leaves the proposal and outbox in `reconciliation_required`, with no blind retry. A confirmed approval stores its ID/status and closes the submission outbox. Mocked HTTP/PostgreSQL tests verify intent-before-POST, duplicate lookup, timeout-after-acceptance recovery, no-match manual review, and lookup outage. The 48-test focused suite passed.

Subtask 4 adds bounded approval polling with per-read timeouts and an event reconciliation function for persisted `approval_decided` payloads. Both use the same transition validator and durable proposal updater; repeated same-status observations do not add duplicate audit rows. Unknown approval events are ignored until the approval ID is known, and workflow mismatches fail closed. Tests cover pending, approved, rejected, expired, applied, and lapsed observations, polling limits/idempotence, event application, and invalid event identity. The focused state-machine/submission suite passed 47 tests.

Subtask 5 adds a single-attempt apply client call and the approved-proposal apply path. It rereads approval identity/status and current workflow version, marks stale proposals without sending a POST, then atomically persists apply intent, applying status, and audit evidence before posting the approval ID. It confirms application from matching config-history proposal ID, approval ID, actor, and version, along with the current config version. If an ambiguous response cannot be resolved, it stops in `reconciliation_required`; it never retries apply because the simulator contract does not document lost-response idempotency. PostgreSQL/mock-HTTP cases cover successful apply, stale version, lost response after remote application, unresolved apply, duplicate invocation, and restart with an existing intent. The focused suite passed 67 tests.

Subtask 6 adds end-to-end pending → approved → applied coverage, including a store reopen after the approval decision and before apply; checks exact diff, actor, reason, and outcome in audit evidence; covers restart before apply and after remote apply; and hardens proposal values against mutation after hashing. README and this file now describe the implemented boundary. Final verification on the isolated PostgreSQL 18 test cluster: `pytest -q` — 189 passed; `ruff check .` — passed; `ruff format --check .` — 76 files already formatted; `python -m compileall -q controller tests` — passed; `git diff --check` — passed; Alembic `check` — no new operations; head is `0005_governance_proposals`. The first full-suite run exposed the old expected Alembic revision in a restart test; it was updated from `0004` to `0005` and the full suite then passed. No public scenario or live assessment simulator was run, and no simulator source was modified.

Chunk 5 code is not yet wired to an autonomous proposal generator or the runtime loop; it is callable governance lifecycle functionality. Decision generation remains later work. Actual OpenAPI has only generic object schemas for these routes, so response validation uses documented examples and observed route-handler fields. Ambiguous apply remains manual-review-only because the simulator does not document safe retry semantics. No real OS process-kill demonstration was performed in this chunk.

## Chunk 4 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Historical query foundation | COMPLETE | `pytest -q tests/integration/storage/test_history_queries.py` — 2 passed; Ruff check/format and `git diff --check` passed | `79caf3d` |
| 2. KPI fact derivation | COMPLETE | analytics unit: 8 passed; analytics PostgreSQL integration: 2 passed; history/storage/schema tests: 18 passed; Alembic upgrade/check and Ruff passed | `bf4e93b` |
| 3. KPI calculations and cohorts | COMPLETE | analytics unit: 16 passed; analytics PostgreSQL integration: 3 passed; history/storage/schema tests: 19 passed; Alembic and Ruff passed | `237d6b2` |
| 4. Read-only audit queries | COMPLETE | audit unit: 3 passed; PostgreSQL query integration: 1 passed; storage regression suite: 11 passed; Alembic check and Ruff passed | `f640e9b` |
| 5. Integration and hardening | COMPLETE | analytics unit: 17 passed; analytics integration: 5 passed; full suite: 123 passed in 46.61s; Ruff, format, compileall and repeated Alembic upgrade/check passed | `bd1f94b`, `b87a39b` |

Subtask 1 adds bounded keyset pagination over persisted event IDs, optional workflow filtering, and discovery of workflow IDs observed in event payloads. It does not assume consecutive event IDs or merge history/live rows by arithmetic; event ID uniqueness already deduplicates both streams in storage. Integration tests used an isolated PostgreSQL 18 cluster under the system temporary directory; Docker was not started and the existing host/Compose databases were not changed.

Subtask 2 adds migration `0002_kpi_facts`, provenance-linked per-event facts and a dedicated analytics scan cursor. The processor reads keyset pages, records supported event types and explicitly labels invalid, incomplete, or unsupported payloads. `campaign_created.leads` contributes uploads; only `call` events contribute dials, connected counts use the contract's boolean, and concluded counts use the two documented conclusion dispositions (not the separate `engaged` flag). A unique source event ID and one transaction for each fact page plus cursor make reruns/restarts safe. The cursor refuses regression. Failure injection verified an FK failure rolls back the cursor. A fresh store reopened committed facts, and a second pass inserted zero duplicate rows. No plan change to prior migrations was made.

Subtask 3 adds migration `0003_cohort_definitions`, with immutable rules keyed by workflow/config/cohort and a unique order per config version. The canonical `CohortDefinition` representation is constructed only from an HTTP-supplied config snapshot, applies ordered AND/OR predicates from the documented config shape, and includes the documented default fallback. Persisting an unchanged version is idempotent; trying to change its predicate without incrementing config version fails. KPI aggregation runs in SQL and can filter by workflow, half-open event-time range, config version, observed cohort, and exact recorded variant. Pickup is `connected / dials`; engagement is `concluded / connected`; zero denominators return `None`. PostgreSQL integration verified counts of 2 dials, 1 connected call, 1 concluded call, and 20 uploaded leads (0.5 pickup, 1.0 engagement). Unit tests cover no-dial and zero-connected cases. No scenario was run.

Subtask 4 adds bounded, read-only audit filtering by workflow, time, actor, action, config version, approval ID, outcome, and resource, with deterministic `(occurred_at, UUID)` keyset continuation and page sizes capped at 500. Migration `0004_audit_query_indexes` replaces the less useful workflow/time index with ordered workflow/time, workflow/actor/time, and workflow/action/time indexes; the prior migration history remains intact. PostgreSQL verified tied timestamp ordering across pages, exact evidence filters, and unchanged row count. Actor and outcome are read from the controller's own audit fields/evidence; query results do not mutate audit history.

Subtask 5 completes the persisted-event → fact → lead/call context → KPI → cohort-scoped audit path. Lead/call context joins only the latest lead record preceding a call, reports a missing join explicitly, and detects returning people only when an earlier lead record for that person precedes the current lead. Historical attribution reconstructs the cohort from that lead's stored features and compares it to observed call exposure. The same `CohortDefinition` is accepted by KPI calculation and the cohort-scoped audit adapter. Runtime invokes fact derivation after startup/reconciliation and each successfully ingested advance. PostgreSQL restart/replay tests show a fresh store reads committed facts and a repeat derives zero duplicates; injected FK failure leaves the scan cursor at 0. The final full suite and code/schema checks passed.

Synthetic PostgreSQL 18 analytics measurement on the Windows development host: 5,000 call events scanned and 5,000 facts inserted in bounded pages of 250; cursor advanced to event ID `990004999`; measured processing rate 557 events/s; peak traced Python memory 3.94 MiB; one filtered SQL aggregate took 10.3 ms and returned 5,000 dials, 2,500 connected and 1,250 concluded. This is a synthetic local observation, not a public load-scenario or grading-host result. No public scenario was run. Automatic collection of historic cohort predicates from config-history responses remains unverified; the canonical representation accepts a permitted config snapshot, but runtime does not guess undocumented response fields.

Final Chunk 4 checks: `pytest -q tests/unit/analytics/` — 17 passed; `pytest -q tests/integration/analytics/` — 5 passed; `pytest -q` — 123 passed in 46.61s; `ruff check .` passed; `ruff format --check .` reported 65 files already formatted; `python -m compileall -q controller tests` passed; Alembic `upgrade head` repeated and `check` reported no new operations; `git diff --check` passed. PostgreSQL tests used a temporary PostgreSQL 18 cluster and a disposable database under system temporary storage; Docker/Compose and the assessment simulator were not run.

## Chunk 3 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Ingestion foundation | COMPLETE | `.venv/Scripts/python.exe -m pytest -q tests/unit/ingestion/` — 13 passed; Ruff check/format and `git diff --check` passed | `d921490` |
| 2. Historical ingestion | COMPLETE | `.venv/Scripts/python.exe -m pytest -q tests/unit/ingestion/ tests/unit/test_simulator_client.py` — 25 passed; Ruff check/format and `git diff --check` passed | `b6b88f6` |
| 3. Transactional persistence | COMPLETE | PostgreSQL integration plus storage regression tests — 14 passed; Ruff check/format and `git diff --check` passed | `6bdce40` |
| 4. Live ingestion | COMPLETE | Focused unit/runtime and PostgreSQL storage/ingestion checks — 40 passed; one live POST persisted two events through checkpoint 7 and completed its outbox intent; Ruff and format passed | `f975203` |
| 5. Crash recovery | COMPLETE | Focused parser/runtime and PostgreSQL recovery suite — 29 passed; timeout-after-apply recovered without another POST; unchanged state plus empty listing allowed one persisted retry; API outage made no retry | `09bf0b6` |
| 6. Hardening | COMPLETE | 19 ingestion unit tests; 11 ingestion integration tests; full suite 93 passed; Ruff, format, compile, Alembic and diff checks passed | `adf5b54` |

## Environment setup results (2026-11-06)

- Installed and selected Python **3.12.10**. Project virtual environment: `.venv` (interpreter `.venv/Scripts/python.exe`).
- Installed repository requirements plus requested packages into `.venv`: SQLAlchemy **2.1.8**, Alembic **1.20.1**, HTTPX **0.28.1**, Pytest **9.2.0**, asyncpg **0.32.0**, greenlet **3.5.6**, NumPy **2.5.4**, PyYAML **6.0.3**, FastAPI **0.162.0**, Uvicorn **0.54.0**.
- Imports for async SQLAlchemy/asyncpg and repository requirements passed. `.venv` `pip check`: **No broken requirements found**.
- Docker Engine **29.5.3** and Compose **5.1.4** verified running/available. Added `docker-compose.dev.yml` using `postgres:18`, localhost-only port **5433** (host PostgreSQL uses 5432), health check, and named volume `formi-outcome-loop-pg18-devdata` mounted at `/var/lib/postgresql`.
- Generated development-only credentials are stored outside the repository at `%LOCALAPPDATA%\FormiAgenticUniverse\postgres-dev.env`; credential values were not displayed. Compose config validation passed.
- Container became healthy. Async SQLAlchemy connection succeeded against PostgreSQL **18.6**. A temporary row was written, the container was restarted, and the row was read back successfully. Temporary probe table was then removed. Volume mount inspected and confirmed.
- `requirements.txt` now includes `SQLAlchemy[asyncio]`, Alembic and asyncpg so the selected database driver/async support is reproducible. No assessment database connection was specified in the assignment materials or configured in the inspected environment.
- Chunks 1–3 are complete. Chunk 4 history, KPI facts and audit queries are complete. Chunk 5 has not started.

## Chunk 1 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Package foundation, typed errors and JSON logging | COMPLETE | `python -m pytest -q tests/unit/test_logging.py` — 2 passed; `compileall` passed | `e547b63 feat(runtime): add package foundation and safe JSON logging` |
| 2. Typed configuration and validation | COMPLETE | 20 unit tests passed; Ruff check/format passed; `compileall` passed | `196a9ec feat(config): add validated runtime settings` |
| 3. Async simulator HTTP client | COMPLETE | 28 unit tests passed; Ruff check/format passed; `compileall` passed | `cfc28d2 feat(http): add bounded async simulator client` |
| 4. Runtime bootstrap, advance loop and graceful stop | COMPLETE | 5 runtime unit tests passed; Ruff check/format passed; `compileall` passed | `730cf26 feat(runtime): add readiness and graceful advance loop` |
| 5. CLI, cross-platform launchers, docs and final verification | COMPLETE | Full suite: 42 passed; Ruff check/format and compileall passed for `controller` and `tests/unit`; Git Bash launcher syntax and argument/failure exits verified | `71bdd8a feat(cli): add virtualenv launcher and runtime documentation` |

## Chunk 2 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Versioned database schema and initial Alembic revision | COMPLETE | 4 schema metadata tests passed; Ruff and compile checks passed; `alembic history` lists initial revision | `aab9708 feat(storage): add versioned durable schema` |
| 2. Async database store and configuration | COMPLETE | 6 configuration/lifecycle unit tests passed; Ruff and compile checks passed | `2443889 feat(storage): add async database store` |
| 3. Event uniqueness and atomic persistence primitives | COMPLETE | PostgreSQL insert/read, duplicate no-op, and conflicting replay tests passed; 12 schema/store tests passed | `31af0ea feat(storage): persist raw events idempotently` |
| 4. Checkpoint, audit and outbox operations | COMPLETE | PostgreSQL store tests passed, including atomic chunk/checkpoint writes, rollback, regression rejection, audit and outbox persistence | `a3d7a6b feat(storage): add checkpoint audit and outbox operations` |
| 5. PostgreSQL failure/restart and migration verification | COMPLETE | `tests/integration/storage/test_store.py`: 11 passed; full suite: 63 passed; repeat migrations and Alembic schema check passed; Ruff and compile checks passed | `e88ed27 feat: add durable event and audit store` |

Subtask 1 defines controller-owned run/workflow references, raw event envelopes, nullable ordered-descriptor checkpoints, append-only audit records, and deduplicated outbox operations. Event payloads remain JSON without inferred simulator fields. Checkpoint positions have no numeric consecutiveness constraint. The initial Alembic migration was applied and rerun against the isolated PostgreSQL test database; one version row remained and `alembic check` found no schema drift.

Subtask 2 adds `DatabaseStore`, which reads `CONTROLLER_DATABASE_URL` separately from simulator settings, accepts only `postgresql+asyncpg`, uses a bounded pre-ping pool with SQL parameter hiding, exposes explicit transaction scopes, verifies connectivity, applies Alembic revisions, and disposes connections on close. Configuration and lifecycle tests pass. The test database was verified using an isolated temporary PostgreSQL instance; the configured Compose database remains unavailable because Docker Engine is stopped, and the host PostgreSQL service rejects the external Compose credentials.

Subtask 3 adds `EventRecord`, transactional PostgreSQL insert-on-conflict handling, and a read-by-ID operation. The method returns only IDs actually inserted so future derived effects can be limited to new events; a repeated ID with different payload, kind, or chunk raises an error. Integration tests passed against a temporary loopback-only PostgreSQL 18 database initialized outside the repository. Docker and the host database were not modified.

Subtask 4 adds monotonic descriptor checkpoint writes, an atomic raw-event-plus-checkpoint helper, append-only audit writes/queries, and deduplicated outbox creation/status updates. Both audit and outbox records can be related to workflow references. External API calls are not executed. Transaction failure, conflicting event data, and checkpoint regression leave associated data/checkpoints unchanged in PostgreSQL tests.

Subtask 5 verified database initialization, migration application and repeat application against PostgreSQL 18. A fresh `DatabaseStore` instance reopened the database and read committed raw event, checkpoint, audit, outbox and run records. The `alembic_version` table retained one row at `0001_durable_store`; `alembic check` reported no new upgrade operations. Injected transaction exceptions and event/checkpoint failures rolled back their related writes. The isolated test PostgreSQL process was stopped after validation; its data directory remains outside the repository under the system temporary directory. The configured Docker Compose database was not available in this session.

### Chunk 2 files and implementation

- Added `controller/storage/models.py` with controller run, workflow reference, raw event, processing checkpoint, audit and outbox tables; raw simulator payloads are JSON and no event fields beyond ID, descriptor chunk and kind are projected.
- Added Alembic configuration, async environment and initial revision `0001_durable_store`.
- Added `controller/storage/store.py` for environment-supplied async PostgreSQL connections, migration, transactions, idempotent raw-event insertion, atomic event/checkpoint commits, run/workflow/checkpoint methods and outbox persistence/status.
- Added `controller/storage/audit.py` for append-only audit writes and workflow queries.
- Added PostgreSQL integration fixtures/tests under `tests/integration/storage/`; schema and config checks are under `tests/unit/`.
- Updated `.gitignore` for local environment files and SQLite files. No dependency changes were needed.

The Chunk 1 runtime is a foundation only. It does not yet persist events or advance intents and does not make optimization or governance decisions. It halts on ambiguous advance outcomes rather than issuing a duplicate POST; durable reconciliation is required in the later persistence/recovery chunks. Public simulator scenarios were not run as part of Chunk 1.

Subtask 4 starts the simulator through documented readiness/state/workflow reads and advances until `finished`, with graceful stopping between requests. A `409 finished` response is reconciled by reading state. An ambiguous advance stops the process without another POST. Durable advance-intent persistence and recovery are intentionally not implemented in Chunk 1 and remain required in the later database/recovery chunks.

## Test and scenario results

| Scenario/check | Result | Evidence |
|---|---|---|
| public_01_dev | NOT RUN | — |
| public_02_incidents | NOT RUN | — |
| public_03_load | NOT RUN | — |
| Crash recovery | NOT RUN | — |

Chunk 2 verification used `CONTROLLER_TEST_DATABASE_URL` for an isolated PostgreSQL 18 test database outside the repository. `python -m pytest -q tests/integration/storage/test_store.py` — **11 passed**. `python -m pytest -q` — **63 passed** with PostgreSQL integration tests enabled. `ruff check controller tests/integration tests/unit migrations`, `ruff format --check controller tests/integration tests/unit migrations`, `python -m compileall -q controller tests migrations`, `alembic check`, and `git diff --check` passed. No assessment scenario was run.

Chunk 1 runtime and launcher tests use fakes/HTTPX mock transport and do not require the assessment simulator or Docker. No full assessment scenario, load run, crash-recovery demonstration, or type checker run has been performed. Ruff initially found a pre-existing import-order issue in the simulator contract test; it was corrected as an import-only cleanup while moving that test to `tests/contract/`. Ruff check and formatting now cover `controller`, `tests`, and `migrations`.

No assessment scenario or full end-to-end run has been performed yet. Chunk 1 unit-test results are tracked in the subtask table above.

## Repository structure refactor (2026-11-07)

- Grouped database models, async connection/store code, and audit access under `controller/storage/` without changing model definitions or migration revision IDs.
- Separated database-backed persistence tests into `tests/integration/storage/`; kept schema/config checks in `tests/unit/`; moved the existing simulator test suite to `tests/contract/` without changing test behavior.
- Kept root-level `alembic.ini` and the conventional `migrations/` directory; updated migration metadata imports and the store's lookup of the root Alembic config.
- Added the current tree to README. No Chunk 3 code has started.
- Verification after the refactor: `.venv/Scripts/python.exe -m pytest -q` — 63 passed; `ruff check controller tests migrations` — passed; `ruff format --check controller tests migrations` — passed; `python -m compileall -q controller tests migrations` — passed; `alembic -c alembic.ini check` against the isolated PostgreSQL 18 test database — no new upgrade operations detected; `git diff --check` — passed. No schema or migration revision changed.
- One focused refactor commit is created after verification; no push was made.

## Approved and unapproved decisions

**Fixed by assignment/contract:** communicate with simulator only over HTTP; `run.sh <SIM_URL>` runs unattended to scenario completion; do not modify `sim/`; keep pickup and engagement distinct; controller changes pass approvals or policy-permitted auto-apply; deterministic winner selection, never LLM; detect populations rather than single calls; maintain local audit; recover after crash; public_01/public_02 end-to-end, and complete SUBMISSION.md are mandatory.

**User-approved infrastructure direction:** use an assessment-provided database if supplied; otherwise use the existing PostgreSQL installation. Do not install PostgreSQL 16 or another database solely for version alignment. Python 3.12 is the target; Docker Compose is optional for local development, and the user will start Docker Desktop manually if needed. SQLAlchemy and Alembic remain the preferred persistence tools.

**Unapproved/gated:** determine whether grading supplies a database and document that runtime's connection; statistical method, MDE, sample size, significance/look schedule, guardrail evidence and decay window; optional capability scope; remote apply retry semantics; performance limits beyond the stated SRE target of one simulated day in under 10 wall-clock minutes. Installed dependency versions are recorded below but are not pinned in a lock file.

## Verified contract facts

- Reviewed README, simulator docs, submission template, all six memos, public scenario files, existing plan/progress, tests, requirements, Makefile, launcher and HTTP routes.
- Public HTTP interface documents health/state, workflow/config/history, event chunk listing/fetch, advance, approval submit/list/get, apply, policy-gated auto-apply, suggestion and validation.
- Chunk IDs globally increase; event IDs strictly increase; history is exposed in chunks before D0. Deduplicate history/live together.
- Approval states include pending, approved, rejected, expired, applied/lapsed; stale version requires reread/re-evaluation. Prompt change needs matching validation evidence meeting policy. Apply retry idempotency after lost response is undocumented.
- At initial repository review there was no controller or migration structure; the current implemented layout is documented above. Docker is permitted but not required.
- README says any language/storage and does not require or document an assessment-provided database or pin a Python version. Python 3.12 grammar parsing succeeded for six inspected assessment/runtime source files, and Python 3.12.10 is now installed in `.venv`.
- The pre-existing host PostgreSQL 18 service on port 5432 was not used for development. Local development uses the verified Compose-managed PostgreSQL 18.6 instance on port 5433.
- The Compose-managed DB authenticated through async SQLAlchemy. Named-volume data survived a container restart. Credentials are in the external local env file, not source control.
- Docker Desktop/Engine was verified running during the earlier Compose setup. During Chunk 2 it was stopped, so tests used a temporary PostgreSQL 18 instance and did not start or alter the Compose database. Docker is not assumed available in grading.
- No scenario truth or run-directory data may be controller input; use HTTP only.

## Unverified assumptions and open risks

- Whether an assessment database connection will be supplied at grading; Docker availability and a durable DB path in that environment; grader Python patch; dependency lock/pins.
- Chunk retention/size bounds, exact response/error fields beyond docs, remote apply behavior after lost response.
- Runtime throughput, memory, DB growth, 10× bottleneck and failure behavior on the grading host.
- Statistical power/false-positive calibration within scenario duration.
- No documented supervisor notification endpoint or direct rollback API; use controller audit/report and governed corrective proposal unless live contract proves otherwise.

## Git history

Chunk 1 commits (created incrementally; none pushed):

- `e547b63 feat(runtime): add package foundation and safe JSON logging`
- `196a9ec feat(config): add validated runtime settings`
- `cfc28d2 feat(http): add bounded async simulator client`
- `730cf26 feat(runtime): add readiness and graceful advance loop`
- `71bdd8a feat(cli): add virtualenv launcher and runtime documentation`

Chunk 2 commits (created incrementally; none pushed):

- `aab9708 feat(storage): add versioned durable schema`
- `2443889 feat(storage): add async database store`
- `31af0ea feat(storage): persist raw events idempotently`
- `a3d7a6b feat(storage): add checkpoint audit and outbox operations`
- `e88ed27 feat: add durable event and audit store`

Do not rewrite prior history.

## Next action

Chunk 4 is complete. Next: Chunk 5 — Governance lifecycle. It has not started; wait for review/approval before beginning.

### Chunk 3 measured evidence (synthetic HTTP stream, PostgreSQL 18)

- Ordered history/live descriptors at chunk IDs 0 and 3 committed four events with IDs 1, 4, 9, and 12; the committed global descriptor checkpoint was 3. This verifies chunk and event IDs are not treated as consecutive.
- A malformed second chunk was partially inserted in batches of one for failure injection, then rejected by descriptor count validation. Its event rows and checkpoint rolled back; the first chunk remained committed at checkpoint 1. Restart replay committed chunk 4 and reached checkpoint 4 without duplicating chunk 1.
- Live-advance integration issued one POST, persisted its intent and returned descriptor, stored two events, and reached checkpoint 7. Timeout-after-apply restart recovered two events at checkpoint 12 without a second POST. A separate no-progress case retried the same intent after matching pre/post state and an empty listing; its attempt count was 2 and it completed at checkpoint 5. Reconciliation GET outage left the intent in progress and made no additional POST.
- Synthetic streamed PostgreSQL integration ingested 30,000 generated events in batches of 500 at **1,601 events/s** with **2.54 MiB peak traced Python memory** on this Windows development host. This is a local synthetic measurement, not a simulator load-scenario result or grading-host claim.
- Runtime integration bootstrapped two history events before one live advance, persisted two live events, and completed at checkpoint 4. Restart recovery used fresh client/store instances over the same PostgreSQL data; no OS-level kill/restart demonstration was run in this chunk.
- Final checks: `.venv/Scripts/python.exe -m pytest -q tests/unit/ingestion/` — **19 passed**; `.venv/Scripts/python.exe -m pytest -q tests/integration/ingestion/` — **11 passed**; `.venv/Scripts/python.exe -m pytest -q` — **93 passed**; `ruff check .` passed; `ruff format --check .` reported 47 files already formatted; `.venv/Scripts/python.exe -m compileall -q controller tests` passed; Alembic `upgrade head` repeated and `check` reported no new operations; `git diff --check` passed. Ruff excludes only assignment-provided `sim/` and deliberately naive `examples/naive_controller.py`; neither was modified.
- No schema migration was needed. Public scenarios remain NOT RUN; no scenario YAML or run-directory data was used by controller decisions.

Known limits: these are synthetic development-host measurements, not `public_03_load` or grading-host results. The 1 MiB memory spool spills larger chunks to temporary disk without a hard total-byte cap, so deployments need sufficient temp capacity. Event IDs and the checkpoint are scoped to one simulator world per database; use a fresh database for independent simulator worlds until multi-run namespacing is designed.
