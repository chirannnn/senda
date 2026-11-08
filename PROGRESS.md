# Progress Tracking â€” Outcome-First Agent Loop

## Overall status: COMPLETE THROUGH CHUNK 9

**Current phase:** Chunk 9 load and observability complete
**Current chunk:** 9 - Load and observability (complete)
**Current next action:** Chunk 10 - Submission and final grading (NOT STARTED)

## Implementation progress

| Chunk | Name | Status | Commit |
|---|---|---|---|
| 1 | Runtime and launcher | COMPLETE | `cfc28d2`, `730cf26`, `71bdd8a` (and earlier foundation/config commits listed below) |
| 2 | Durable schema and audit | COMPLETE | `aab9708`, `2443889`, `31af0ea`, `a3d7a6b`, `e88ed27` |
| 3 | Simulator chunk ingestion | COMPLETE | `d921490`, `b6b88f6`, `6bdce40`, `f975203`, `09bf0b6`, `adf5b54` |
| 4 | History, KPI facts, audit query | COMPLETE | `79caf3d`, `bf4e93b`, `237d6b2`, `f640e9b`, `bd1f94b`, `b87a39b` |
| 5 | Governance lifecycle | COMPLETE | `29d7e81`, `d04a0bf`, `40aca99`, `feb3c77`, `50fbc35`, `df4d945` |
| 6 | Statistical comparison | COMPLETE | `7a3d228`, `eef6137`, `ecb6d92`, `dba33bf`, `faca570` |
| 7 | Public incident workflow | COMPLETE | `3d28f68`, `7a4dadf`, `88f71bc`, `aa59820`, `ade9e79`, `4a0f257` |
| 8 | Crash recovery demonstration | COMPLETE | `7222007`, `b2ef987`, `3b3a521`, `9f6f61b`, `82ed4c7`, `6e11334` |
| 9 | Load and observability | COMPLETE | `a1dab4d`, `fa5845a`, `41e52a0`, `74b43eb`, `6a18a96` |
| 10 | Submission and final grading | NOT STARTED | planned |

## Chunk 6 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Statistical primitives | COMPLETE | 13 focused unit tests passed; Ruff, formatting, compileall and diff check passed | `7a3d228` |
| 2. Variant evidence and evaluation | COMPLETE | 17 focused unit tests passed; Ruff, formatting, compileall and `git diff --check` passed | `eef6137` |
| 3. Durable decisions and recovery | COMPLETE | 39 focused unit/PostgreSQL integration tests passed; migration `0006` applied; Ruff, formatting, compileall and `git diff --check` passed | `ecb6d92` |
| 4. Governed proposal integration | COMPLETE | 46 focused PostgreSQL governance/experiment tests passed; Ruff, formatting, compileall and `git diff --check` passed | `dba33bf` |
| 5. Integration and acceptance | COMPLETE | Full suite: 222 passed on isolated PostgreSQL 18; Ruff, format, compileall, Alembic check and diff check passed; public_01_dev finished and grader ran | this completion commit |

## Chunk 6 validation and limitations

- Isolated PostgreSQL 18 integration database applied migrations through `0007_experiment_family_lock`; Alembic reported no schema drift.
- Final checks: `pytest -q`: 222 passed; `ruff check .`, `ruff format --check .`, `python -m compileall -q controller tests`, Alembic check and `git diff --check` passed.
- `public_01_dev` completed through HTTP ingestion: simulator finished at 11,520 simulated minutes with 297,024 events; grader output was written under the OS temporary directory. It reported zero applied changes and 0.0% KPI delta versus baseline because runtime does not schedule statistical plans.
- Chunk 6 decision and proposal components are callable but not wired to runtime scheduling. A caller must supply an explicit plan (baselines, allocations, clustering, power, imbalance tolerance, fixed window/cutoff). Missing inputs or workflow guardrails produce no decision/proposal. Simulator per-dial assignment cannot guarantee one arm per person; cross-arm people are excluded and the limitation is documented in `docs/DECISION_POLICY.md`.
- Chunk 7 is complete; incident workflow details and limitations are recorded below.

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

Subtask 6 adds end-to-end pending â†’ approved â†’ applied coverage, including a store reopen after the approval decision and before apply; checks exact diff, actor, reason, and outcome in audit evidence; covers restart before apply and after remote apply; and hardens proposal values against mutation after hashing. README and this file now describe the implemented boundary. Final verification on the isolated PostgreSQL 18 test cluster: `pytest -q` â€” 189 passed; `ruff check .` â€” passed; `ruff format --check .` â€” 76 files already formatted; `python -m compileall -q controller tests` â€” passed; `git diff --check` â€” passed; Alembic `check` â€” no new operations; head is `0005_governance_proposals`. The first full-suite run exposed the old expected Alembic revision in a restart test; it was updated from `0004` to `0005` and the full suite then passed. No public scenario or live assessment simulator was run, and no simulator source was modified.

Chunk 5 code is not yet wired to an autonomous proposal generator or the runtime loop; it is callable governance lifecycle functionality. Decision generation remains later work. Actual OpenAPI has only generic object schemas for these routes, so response validation uses documented examples and observed route-handler fields. Ambiguous apply remains manual-review-only because the simulator does not document safe retry semantics. No real OS process-kill demonstration was performed in this chunk.

## Chunk 4 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Historical query foundation | COMPLETE | `pytest -q tests/integration/storage/test_history_queries.py` â€” 2 passed; Ruff check/format and `git diff --check` passed | `79caf3d` |
| 2. KPI fact derivation | COMPLETE | analytics unit: 8 passed; analytics PostgreSQL integration: 2 passed; history/storage/schema tests: 18 passed; Alembic upgrade/check and Ruff passed | `bf4e93b` |
| 3. KPI calculations and cohorts | COMPLETE | analytics unit: 16 passed; analytics PostgreSQL integration: 3 passed; history/storage/schema tests: 19 passed; Alembic and Ruff passed | `237d6b2` |
| 4. Read-only audit queries | COMPLETE | audit unit: 3 passed; PostgreSQL query integration: 1 passed; storage regression suite: 11 passed; Alembic check and Ruff passed | `f640e9b` |
| 5. Integration and hardening | COMPLETE | analytics unit: 17 passed; analytics integration: 5 passed; full suite: 123 passed in 46.61s; Ruff, format, compileall and repeated Alembic upgrade/check passed | `bd1f94b`, `b87a39b` |

Subtask 1 adds bounded keyset pagination over persisted event IDs, optional workflow filtering, and discovery of workflow IDs observed in event payloads. It does not assume consecutive event IDs or merge history/live rows by arithmetic; event ID uniqueness already deduplicates both streams in storage. Integration tests used an isolated PostgreSQL 18 cluster under the system temporary directory; Docker was not started and the existing host/Compose databases were not changed.

Subtask 2 adds migration `0002_kpi_facts`, provenance-linked per-event facts and a dedicated analytics scan cursor. The processor reads keyset pages, records supported event types and explicitly labels invalid, incomplete, or unsupported payloads. `campaign_created.leads` contributes uploads; only `call` events contribute dials, connected counts use the contract's boolean, and concluded counts use the two documented conclusion dispositions (not the separate `engaged` flag). A unique source event ID and one transaction for each fact page plus cursor make reruns/restarts safe. The cursor refuses regression. Failure injection verified an FK failure rolls back the cursor. A fresh store reopened committed facts, and a second pass inserted zero duplicate rows. No plan change to prior migrations was made.

Subtask 3 adds migration `0003_cohort_definitions`, with immutable rules keyed by workflow/config/cohort and a unique order per config version. The canonical `CohortDefinition` representation is constructed only from an HTTP-supplied config snapshot, applies ordered AND/OR predicates from the documented config shape, and includes the documented default fallback. Persisting an unchanged version is idempotent; trying to change its predicate without incrementing config version fails. KPI aggregation runs in SQL and can filter by workflow, half-open event-time range, config version, observed cohort, and exact recorded variant. Pickup is `connected / dials`; engagement is `concluded / connected`; zero denominators return `None`. PostgreSQL integration verified counts of 2 dials, 1 connected call, 1 concluded call, and 20 uploaded leads (0.5 pickup, 1.0 engagement). Unit tests cover no-dial and zero-connected cases. No scenario was run.

Subtask 4 adds bounded, read-only audit filtering by workflow, time, actor, action, config version, approval ID, outcome, and resource, with deterministic `(occurred_at, UUID)` keyset continuation and page sizes capped at 500. Migration `0004_audit_query_indexes` replaces the less useful workflow/time index with ordered workflow/time, workflow/actor/time, and workflow/action/time indexes; the prior migration history remains intact. PostgreSQL verified tied timestamp ordering across pages, exact evidence filters, and unchanged row count. Actor and outcome are read from the controller's own audit fields/evidence; query results do not mutate audit history.

Subtask 5 completes the persisted-event â†’ fact â†’ lead/call context â†’ KPI â†’ cohort-scoped audit path. Lead/call context joins only the latest lead record preceding a call, reports a missing join explicitly, and detects returning people only when an earlier lead record for that person precedes the current lead. Historical attribution reconstructs the cohort from that lead's stored features and compares it to observed call exposure. The same `CohortDefinition` is accepted by KPI calculation and the cohort-scoped audit adapter. Runtime invokes fact derivation after startup/reconciliation and each successfully ingested advance. PostgreSQL restart/replay tests show a fresh store reads committed facts and a repeat derives zero duplicates; injected FK failure leaves the scan cursor at 0. The final full suite and code/schema checks passed.

Synthetic PostgreSQL 18 analytics measurement on the Windows development host: 5,000 call events scanned and 5,000 facts inserted in bounded pages of 250; cursor advanced to event ID `990004999`; measured processing rate 557 events/s; peak traced Python memory 3.94 MiB; one filtered SQL aggregate took 10.3 ms and returned 5,000 dials, 2,500 connected and 1,250 concluded. This is a synthetic local observation, not a public load-scenario or grading-host result. No public scenario was run. Automatic collection of historic cohort predicates from config-history responses remains unverified; the canonical representation accepts a permitted config snapshot, but runtime does not guess undocumented response fields.

Final Chunk 4 checks: `pytest -q tests/unit/analytics/` â€” 17 passed; `pytest -q tests/integration/analytics/` â€” 5 passed; `pytest -q` â€” 123 passed in 46.61s; `ruff check .` passed; `ruff format --check .` reported 65 files already formatted; `python -m compileall -q controller tests` passed; Alembic `upgrade head` repeated and `check` reported no new operations; `git diff --check` passed. PostgreSQL tests used a temporary PostgreSQL 18 cluster and a disposable database under system temporary storage; Docker/Compose and the assessment simulator were not run.

## Chunk 3 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Ingestion foundation | COMPLETE | `.venv/Scripts/python.exe -m pytest -q tests/unit/ingestion/` â€” 13 passed; Ruff check/format and `git diff --check` passed | `d921490` |
| 2. Historical ingestion | COMPLETE | `.venv/Scripts/python.exe -m pytest -q tests/unit/ingestion/ tests/unit/test_simulator_client.py` â€” 25 passed; Ruff check/format and `git diff --check` passed | `b6b88f6` |
| 3. Transactional persistence | COMPLETE | PostgreSQL integration plus storage regression tests â€” 14 passed; Ruff check/format and `git diff --check` passed | `6bdce40` |
| 4. Live ingestion | COMPLETE | Focused unit/runtime and PostgreSQL storage/ingestion checks â€” 40 passed; one live POST persisted two events through checkpoint 7 and completed its outbox intent; Ruff and format passed | `f975203` |
| 5. Crash recovery | COMPLETE | Focused parser/runtime and PostgreSQL recovery suite â€” 29 passed; timeout-after-apply recovered without another POST; unchanged state plus empty listing allowed one persisted retry; API outage made no retry | `09bf0b6` |
| 6. Hardening | COMPLETE | 19 ingestion unit tests; 11 ingestion integration tests; full suite 93 passed; Ruff, format, compile, Alembic and diff checks passed | `adf5b54` |

## Environment setup results (2026-11-06)

- Installed and selected Python **3.12.10**. Project virtual environment: `.venv` (interpreter `.venv/Scripts/python.exe`).
- Installed repository requirements plus requested packages into `.venv`: SQLAlchemy **2.1.8**, Alembic **1.20.1**, HTTPX **0.28.1**, Pytest **9.2.0**, asyncpg **0.32.0**, greenlet **3.5.6**, NumPy **2.5.4**, PyYAML **6.0.3**, FastAPI **0.162.0**, Uvicorn **0.54.0**.
- Imports for async SQLAlchemy/asyncpg and repository requirements passed. `.venv` `pip check`: **No broken requirements found**.
- Docker Engine **29.5.3** and Compose **5.1.4** verified running/available. Added `docker-compose.dev.yml` using `postgres:18`, localhost-only port **5433** (host PostgreSQL uses 5432), health check, and named volume `formi-outcome-loop-pg18-devdata` mounted at `/var/lib/postgresql`.
- Generated development-only credentials are stored outside the repository at `%LOCALAPPDATA%\FormiAgenticUniverse\postgres-dev.env`; credential values were not displayed. Compose config validation passed.
- Container became healthy. Async SQLAlchemy connection succeeded against PostgreSQL **18.6**. A temporary row was written, the container was restarted, and the row was read back successfully. Temporary probe table was then removed. Volume mount inspected and confirmed.
- `requirements.txt` now includes `SQLAlchemy[asyncio]`, Alembic and asyncpg so the selected database driver/async support is reproducible. No assessment database connection was specified in the assignment materials or configured in the inspected environment.
- Chunks 1â€“3 are complete. Chunk 4 history, KPI facts and audit queries are complete. Chunk 5 has not started.

## Chunk 1 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Package foundation, typed errors and JSON logging | COMPLETE | `python -m pytest -q tests/unit/test_logging.py` â€” 2 passed; `compileall` passed | `e547b63 feat(runtime): add package foundation and safe JSON logging` |
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
| public_01_dev | COMPLETE | Finished at minute 11,520 with 297,024 events; grader reported zero applied changes and 0.0% KPI delta; see Chunk 6 results above |
| public_02_incidents | COMPLETE | Finished at minute 14,400; governed prompt change approved/applied and rated beneficial; see Chunk 7 results above |
| public_03_load | COMPLETE | Completed 7,200 simulated minutes; SRE time target failed; see `reports/public_03_load.md` and `reports/public_03_load_review.md` |
| Crash recovery | COMPLETE | Real Controller process kills/restarts verified against unchanged public_02 simulator and PostgreSQL state; see `reports/crash_recovery.md` |

Chunk 2 verification used `CONTROLLER_TEST_DATABASE_URL` for an isolated PostgreSQL 18 test database outside the repository. `python -m pytest -q tests/integration/storage/test_store.py` â€” **11 passed**. `python -m pytest -q` â€” **63 passed** with PostgreSQL integration tests enabled. `ruff check controller tests/integration tests/unit migrations`, `ruff format --check controller tests/integration tests/unit migrations`, `python -m compileall -q controller tests migrations`, `alembic check`, and `git diff --check` passed. No assessment scenario was run.

Historical note from Chunk 1: runtime and launcher tests used fakes/HTTPX mock transport and did not require the assessment simulator or Docker. At that point no full assessment scenario, load run, crash-recovery demonstration, or type checker run had been performed. Ruff now covers `controller`, `tests`, and `migrations`.

At the time of the initial Chunk 1 work, no assessment scenario or full end-to-end run had been performed. Later scenario results are recorded under Chunks 7 and 8.

## Repository structure refactor (2026-11-07)

- Grouped database models, async connection/store code, and audit access under `controller/storage/` without changing model definitions or migration revision IDs.
- Separated database-backed persistence tests into `tests/integration/storage/`; kept schema/config checks in `tests/unit/`; moved the existing simulator test suite to `tests/contract/` without changing test behavior.
- Kept root-level `alembic.ini` and the conventional `migrations/` directory; updated migration metadata imports and the store's lookup of the root Alembic config.
- Added the current tree to README. No Chunk 3 code has started.
- Verification after the refactor: `.venv/Scripts/python.exe -m pytest -q` â€” 63 passed; `ruff check controller tests migrations` â€” passed; `ruff format --check controller tests migrations` â€” passed; `python -m compileall -q controller tests migrations` â€” passed; `alembic -c alembic.ini check` against the isolated PostgreSQL 18 test database â€” no new upgrade operations detected; `git diff --check` â€” passed. No schema or migration revision changed.
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
- Runtime throughput, memory, DB growth, 10Ã— bottleneck and failure behavior on the grading host.
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

Chunks 1-9 are complete. Chunk 10 - Submission and final grading remains NOT STARTED.

### Chunk 3 measured evidence (synthetic HTTP stream, PostgreSQL 18)

- Ordered history/live descriptors at chunk IDs 0 and 3 committed four events with IDs 1, 4, 9, and 12; the committed global descriptor checkpoint was 3. This verifies chunk and event IDs are not treated as consecutive.
- A malformed second chunk was partially inserted in batches of one for failure injection, then rejected by descriptor count validation. Its event rows and checkpoint rolled back; the first chunk remained committed at checkpoint 1. Restart replay committed chunk 4 and reached checkpoint 4 without duplicating chunk 1.
- Live-advance integration issued one POST, persisted its intent and returned descriptor, stored two events, and reached checkpoint 7. Timeout-after-apply restart recovered two events at checkpoint 12 without a second POST. A separate no-progress case retried the same intent after matching pre/post state and an empty listing; its attempt count was 2 and it completed at checkpoint 5. Reconciliation GET outage left the intent in progress and made no additional POST.
- Synthetic streamed PostgreSQL integration ingested 30,000 generated events in batches of 500 at **1,601 events/s** with **2.54 MiB peak traced Python memory** on this Windows development host. This is a local synthetic measurement, not a simulator load-scenario result or grading-host claim.
- Runtime integration bootstrapped two history events before one live advance, persisted two live events, and completed at checkpoint 4. Restart recovery used fresh client/store instances over the same PostgreSQL data; no OS-level kill/restart demonstration was run in this chunk.
- Final checks: `.venv/Scripts/python.exe -m pytest -q tests/unit/ingestion/` â€” **19 passed**; `.venv/Scripts/python.exe -m pytest -q tests/integration/ingestion/` â€” **11 passed**; `.venv/Scripts/python.exe -m pytest -q` â€” **93 passed**; `ruff check .` passed; `ruff format --check .` reported 47 files already formatted; `.venv/Scripts/python.exe -m compileall -q controller tests` passed; Alembic `upgrade head` repeated and `check` reported no new operations; `git diff --check` passed. Ruff excludes only assignment-provided `sim/` and deliberately naive `examples/naive_controller.py`; neither was modified.
- Chunk 6 required no schema migration. Chunk 7 public_02 ran in a fresh simulator and PostgreSQL database; see `reports/public_02.md`. No scenario YAML or run-directory data was used to make controller decisions.

Known limits: the synthetic 30,000-event development-host measurement is not a grading-host result. The public_03 run and its platform-specific measurement limitations are documented in `reports/public_03_load.md` and `reports/public_03_load_review.md`. The 1 MiB memory spool spills larger chunks to temporary disk without a hard total-byte cap, so deployments need sufficient temp capacity. Event IDs and the checkpoint are scoped to one simulator world per database; use a fresh database for independent simulator worlds until multi-run namespacing is designed.

## Chunk 7 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Incident evidence | COMPLETE | 11 focused unit tests and 2 isolated PostgreSQL storage tests passed; migration 0008 applied; Ruff, format, compileall and diff check passed | `3d28f68` |
| 2. Credit-aware LLM suggestion | COMPLETE | 27 focused unit/PostgreSQL integration tests passed; Ruff, format, compileall and diff check passed | `7a4dadf` |
| 3. Candidate validation | COMPLETE | 42 focused unit/PostgreSQL integration tests passed; Ruff, format, compileall, Alembic check and diff check passed | `88f71bc` |
| 4. Governed prompt proposal | COMPLETE | 47 focused unit/PostgreSQL integration tests passed; Ruff, format, compileall, Alembic check and diff check passed | `aa59820` |
| 5. Post-apply monitoring | COMPLETE | 18 focused PostgreSQL incident tests passed; Ruff, format, compileall and diff check passed | `ade9e79` |
| 6. Public scenario and acceptance | COMPLETE | 258 tests passed on isolated PostgreSQL; Ruff, format, compileall, Alembic and diff checks passed; public_02 completed and grader ran | this completion commit |

## Chunk 7 acceptance results

- Public `public_02` completed at simulated minute 14,400. The official grader reports one approved prompt change applied through governance and rated beneficial; 4 credits were spent (one suggestion and one validation).
- Incident evidence at cutoff 234187 compared 37 baseline people with 39 changed-config people after excluding cross-version people. Hallucination proxy rates were 0.0% and 7.69%; the configured engineering one-sided screen p <= 0.10 passed at p=0.0695.
- Candidate P1 passed simulator validation at 0.98 against the workflow floor 0.97 (n=100), was approved as `apr_00001`, and applied as config version 3. Final monitor cutoff 324308 was persisted as insufficient: QA and engagement had 11 paired people, below the 30-person gate; no corrective proposal was sent.
- Final full suite: 258 passed on isolated PostgreSQL 18. `ruff check .`, `ruff format --check .`, `compileall`, Alembic check and `git diff --check` passed. Chunk 8 was subsequently completed; see the recovery evidence below.


## Chunk 8 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Recovery audit | COMPLETE | Recovery windows, invariants, procedure and limits documented | `7222007` |
| 2. Partial chunk ingestion recovery | COMPLETE | Real child-process termination and PostgreSQL rollback/replay test; focused checks passed | `b2ef987` |
| 3. Pending approval recovery | COMPLETE | PostgreSQL restart/reconciliation tests; focused suite passed | `3b3a521` |
| 4. Repeated starts and ambiguous operations | COMPLETE | Restart, no-duplicate POST and manual-review tests passed | `9f6f61b` |
| 5. Real crash demonstration | COMPLETE | Two actual Controller process kills and same-simulator/database restarts verified | `82ed4c7` |
| 6. Final acceptance and report | COMPLETE | Full isolated PostgreSQL suite: 260 passed; Ruff, format, compileall, Alembic check and diff check passed | `6e11334` |

## Chunk 8 recovery audit

- Inspected chunk stream ingestion and PostgreSQL transaction/checkpoint code, advance outbox reconciliation, approval submission and apply reconciliation, incident phase persistence, and their integration tests.
- `docs/CRASH_RECOVERY.md` records code-verified crash windows, invariants, and the planned hard-kill procedure. Chunk stream rows plus checkpoint commit atomically; stable event IDs make committed replay idempotent. Advance, approval, and apply remote effects use persisted intent and documented read-side reconciliation; unresolved ambiguity halts for manual review because remote exactly-once semantics are not documented.
- The audit found no missing behavior requiring a production-code change; the remaining work was to add crash evidence and reproducible recovery tests.


### Chunk 8 subtask 2 ? partial chunk ingestion

- Added `tests/integration/test_real_crash_recovery.py`: a separate Python process executes the real `EventIngestionService` against the isolated PostgreSQL database and pauses after the first batch insert while the encompassing chunk transaction is still open. The parent hard-kills that process.
- Verified after termination: event IDs 990001 and 990003 were absent and `crash-demo` had no checkpoint. Restart replay inserted both events, advanced checkpoint to chunk 900, and a second replay inserted zero duplicates.
- Validation: focused real-process/PostgreSQL test passed; Ruff, formatting, compileall and `git diff --check` passed. This focused test uses a fake chunk byte source; the end-to-end demonstration with a real simulator and the Controller CLI remains for subtask 5.


### Chunk 8 subtask 3 ? pending approval restart

- Added a PostgreSQL restart test that submits and durably records a pending approval, closes the first store, opens a fresh store against the same database, reconciles pending then approved status, applies the exact approved config, closes/reopens again, and verifies the apply operation remains succeeded with one attempt and one `approval_apply_prepared` audit row. Repeated apply returns the persisted terminal result without another remote POST.
- Existing approval state-machine and safe-apply tests cover rejected, expired, lapsed, lost-response-after-apply and unresolved ambiguous outcomes; those cases do not apply an unapproved change or blindly repeat an ambiguous apply.
- Focused recovery/governance/incident suite: 28 passed on isolated PostgreSQL. Ruff, formatting, compileall and diff checks passed. The separate-process real simulator approval kill remains for subtask 5.


### Chunk 8 subtask 4 ? repeated starts and ambiguous outcomes

- Extended restart coverage: re-submitting the same durable pending proposal reconciles its stable proposal ID without another POST; after apply, a fresh store sees terminal state and does not repeat the apply POST.
- Extended unresolved apply ambiguity coverage: after the first apply POST has no matching config-history effect, close/reopen PostgreSQL and retry the recovery entry point. The operation remains `reconciliation_required`, with one remote POST and one persisted attempt.
- Existing tests cover restart-safe event IDs/checkpoints, event conflicts, advance lost-response reconciliation, submission timeout lookup, and rejected/expired/lapsed approvals. Focused real process/governance tests: 10 passed. Ruff, formatting, compileall and diff checks passed.


### Chunk 8 subtask 5 ? real process crash demonstration

- Added the exact PowerShell/Git Bash/PostgreSQL trigger procedure to `docs/CRASH_RECOVERY.md`. Two actual Controller CLI process trees were hard-terminated while the actual simulator and isolated PostgreSQL databases remained running.
- Ingestion kill: observed event-1 insert sleeping inside the `commit_chunk_stream` transaction; immediately before kill raw event count was 0 and checkpoint was absent. After killing the Controller and PostgreSQL sleeping session, both remained 0/absent. Restart on the same public_02 simulator and database stored 187,274 unique history events at chunk checkpoint 0 and continued ingesting. This first run later finished at simulator minute 14,400 with 324,308 unique events, checkpoint 490, and no duplicate IDs.
- Approval kill: after observing public `GET /approvals` return `apr_00001` pending, local row was `submitted` with no stored approval ID; its submission outbox was `in_progress`, attempts 1. Hard kill left 234,187 committed events/checkpoint 160 intact. Restart against the same DB/world matched the remote approval by proposal ID, reconciled it, and applied it through Chunk 5. Persisted proposal is applied; submission and apply outboxes each succeeded with attempts 1; audit has one apply-prepared and one apply-reconciled record; remote history has exactly one controller-approved version 3. This scenario subsequently finished at minute 14,400; final state is recorded below.
- No simulator source, scenario YAML contents, truth, or run-directory files were inspected.


## Chunk 8 final acceptance

- Both actual `run.sh` public_02 Controller processes were hard-terminated at controlled database transaction windows and restarted against their unchanged simulator and PostgreSQL state. Both simulator runs finished at simulated minute 14,400 with 324,308 unique event IDs and chunk checkpoint 490.
- Partial-ingestion recovery rolled back the in-flight chunk to zero local rows/no checkpoint, then restarted and committed the complete chunk once. Pending approval recovery matched the already-created remote approval by proposal ID; approval submission/apply outboxes each ended `succeeded` with exactly one attempt; only one controller-approved config version 3 and one apply-prepared audit record exist.
- Full isolated PostgreSQL suite: 260 passed. Ruff check/format, compileall, Alembic check and diff check passed. See `reports/crash_recovery.md` for commands, kill PIDs, remote approval observation, before/after counts and limitations.
- At the time of Chunk 8 acceptance, Chunk 9 had not started; nothing was pushed.

## Chunk 9 subtask progress

| Subtask | Status | Validation | Commit |
|---|---|---|---|
| 1. Telemetry primitives | COMPLETE | 10 unit tests passed; Ruff check/format, compileall, and diff check passed | `a1dab4d` |
| 2. Runtime, HTTP, and storage instrumentation | COMPLETE | 66 focused unit/PostgreSQL tests passed; Ruff check/format, compileall, Alembic check, and diff check passed | `fa5845a` |
| 3. Reproducible load runner and operations guidance | COMPLETE | 66 focused unit/PostgreSQL tests passed; fresh-DB guard, bounded sampler, actual-process launch and report formulas covered; quality checks passed | `41e52a0` |
| 4. Load measurement and public_03 run | COMPLETE | Public `public_03_load` reached 7,200/7,200 simulated minutes, 9,737,439 events and facts; 480 advances succeeded; SRE speed target failed. Windows launcher-only CPU/RSS metrics were found invalid and are disclosed in `reports/public_03_load_review.md` | `74b43eb` (measurement fix), `6a18a96` (run evidence) |
| 5. Final acceptance and documentation | COMPLETE | Full isolated PostgreSQL suite: 281 passed; Ruff check/format, compileall, Alembic upgrade/check, and diff check passed | `6a18a96` |

## Chunk 9 acceptance results

- The permitted public `public_03_load` run completed at simulated minute 7,200 in 5,878.203 wall seconds. It persisted 9,737,439 events and 9,737,439 valid facts, with checkpoint 267 and analytics cursor 9,737,439. The API recorded zero request failures across 480 successful `/sim/advance` calls.
- Measured throughput was 1,656.962 events/second; simulated ingestion lag was 181 minutes and latest-event persistence lag was 53.784 seconds. The SRE threshold of less than 600 wall seconds per simulated day **failed**: measured 1,175.641 seconds/day. The largest aggregate stage was fact processing (4,320.740 seconds; stage totals overlap).
- PostgreSQL grew from 8,279,743 bytes to 9,441,285,823 bytes. The simulator ended with `finished=true`, `sim_minute=7200`, 268 chunks and `last_event_id=9737439`. No decisions were persisted and credits remained 400.
- The original report, JSON log and sample file are retained unchanged as benchmark evidence. Review found its Windows RSS and CPU fields sampled only the Python launcher shim, producing invalid 4,546,560-byte RSS and zero CPU values. Whole-run process CPU and peak RSS are therefore unknown; read-only spot checks of the actual worker during execution were not a formal peak measurement. A separate review file describes this limitation. A Windows process-tree measurement fix is covered by focused tests and was not used to rerun the already-finished scenario.
- The full test suite passed on the newly created isolated PostgreSQL database `formi_chunk9_final_20261108`: 281 passed. `ruff check .`, `ruff format --check .`, `python -B -m compileall -q controller tests scripts`, Alembic `upgrade head` and `check`, and `git diff --check` passed. Existing databases were not used for this verification.
- Chunk 10 remains NOT STARTED. Nothing was pushed.

## Post-Chunk 9 targeted performance work

- Reviewed the retained public_03 report, raw controller log and samples. The largest aggregate stage is fact processing (4,320.740 seconds, maximum 2,417.395 seconds); chunk persistence accumulated 1,338.924 seconds. API request time was 93.003 seconds and chunk download 38.184 seconds. Stage totals overlap. The run's Windows CPU/RSS values remain invalid as described in the companion review note.
- `DatabaseStore.insert_events()` now queries only event IDs that were not inserted by the current statement. New event batches skip a redundant full-batch readback; replayed/conflicting IDs still receive stored-content validation. Focused PostgreSQL tests verified both query shape and conflict behavior: 16 tests passed across event storage and fact persistence.
- This change establishes a reduction in readback query count, not a measured elapsed-time improvement. No full public load, large synthetic event set, or benchmark evidence rewrite was performed.
- KPI fact persistence now batches all page workflow-reference refreshes into one upsert and uses PostgreSQL `rowcount` rather than returning source IDs solely to count inserted facts. It preserves the same page transaction and cursor semantics. Focused PostgreSQL fact-pipeline tests passed (10 tests); exact SQL behavior is tested on small fixtures.
- Neither optimization has a measured wall-time delta. No public scenario was rerun, no large dataset was generated, and original benchmark evidence was not modified. See `docs/PERFORMANCE_INVESTIGATION.md` for evidence, query-path analysis, expected benefits, risks and remaining measurement gaps.
- Post-optimization full suite passed on the isolated PostgreSQL test database: 283 passed. Ruff check/format, compileall, Alembic upgrade/check, and `git diff --check` passed. No migration was needed.

### Performance optimization implementation phase

- Subtask 1 adds bounded histograms for fact source-page reads, per-page Python projection, fact payload building, workflow-reference upsert, fact insert execution, analytics cursor updates, fact transaction commits, event insert execution/conflict lookup, event checkpoint updates, and event chunk transaction commits. These use fixed metric names, keep existing aggregate metrics intact, and omit unavailable stages rather than recording fabricated zeroes. Transaction and checkpoint boundaries are unchanged.
- The corrected Windows process-tree sampler from commit `74b43eb` was re-reviewed; its descendant aggregation tests remain in the focused load-runner suite, so no duplicate sampler code was added.
- Focused validation: 35 telemetry/fact/ingestion unit and PostgreSQL integration tests passed. Ruff and formatting checks, compileall, and `git diff --check` passed. PostgreSQL integration used the existing isolated test database `formi_perf_opt1_20261108`; no benchmark database was used.
- Subtask 2 exposes validated `CONTROLLER_FACT_PAGE_SIZE` (1–2,000; default 1,000) and `CONTROLLER_EVENT_INSERT_BATCH_SIZE` (1–5,000; default 500), wires them into runtime, and enforces PostgreSQL bind limits again at storage boundaries. A tiny four-event PostgreSQL comparison confirmed batch sizes 1 and 2 have the same persisted rows, replay result, and checkpoint semantics. This is a correctness check, not performance evidence; no new default was selected.
- Subtask 2 validation: 80 settings, storage, analytics, and ingestion unit/integration tests passed on the isolated test database. Ruff and format checks, compileall, and `git diff --check` passed. README documents the limits and warns that settings require measurement.
- Subtask 3 (Python projection optimization) was reviewed and intentionally skipped. The existing run has only whole-processor timing; the newly added projection timer has no representative run measurements yet. No avoidable `derive_fact()` operation is established as a material hot spot, so changing its validation or object handling would be speculative. Reconsider only after a valid profile attributes a meaningful share of end-to-end time to Python projection.
- Subtask 4 (additional PostgreSQL persistence optimization) was reviewed and intentionally skipped. The existing event readback and fact-page roundtrip reductions are already implemented in `10c98f9` and `8f68b9a`; neither has a full-run timing comparison. There is no query-plan, commit-latency, WAL, lock, or I/O evidence to justify COPY/staging, index changes, or concurrency. Existing atomic transactions and constraints remain unchanged.
- Subtask 5 (prefetch/parallelism) was reviewed and intentionally skipped. Aggregate chunk download was 38.184 seconds and total API request time 93.003 seconds in the saved 5,878-second run; eliminating that measured download time could not materially close the 2,878-second target gap. Current CPU saturation is unknown. No speculative concurrency or ordering/recovery changes were added.
- Final verification after this phase: full suite **299 passed** on isolated PostgreSQL test database `formi_perf_opt1_20261108`; `ruff check .`, `ruff format --check .` (105 files), `python -B -m compileall -q controller tests scripts`, Alembic `upgrade head`, Alembic `check` (no new upgrade operations), and `git diff --check` passed. No schema changes were made.
- Follow-up boundary review added early PostgreSQL parameter-limit rejection to the direct event insert/chunk APIs as well as the streaming path. Tiny monkeypatched-limit tests and focused storage/ingestion suite passed (33 tests); the full suite above includes this follow-up.
- No public load, stress test, throughput benchmark, or large synthetic dataset was run. Existing benchmark reports, logs, samples, and databases were not modified. No wall-time improvement or 50-minute target achievement is claimed.

## Performance optimization phase 2

- Initial inspection: branch `main`, working tree clean; reviewed the optimization plan/investigation, current settings, pipeline/storage/telemetry code, tests, benchmark artifacts, and commits `10c98f9`, `8f68b9a`, `e2f20de`, and `b0baff2`.
- The preserved public_03 log predates stage timers and contains no measurements for projection, event-page read, fact insert, workflow upsert, cursor update, or commit. The previously measured 4,320.740-second `fact_processing_seconds` interval is a likely sequential critical-path area (about 73.5% of reported runtime), but it aggregates Python plus database activity. Persistence timings are nested within ingestion. No additive sum across these stages is reported.
- The 7.2775 ms single-row insertion probe is explicitly excluded from workload conclusions. It measures one event and one fact, not batching or throughput.
- Existing bounded histograms already isolate the requested named operations in current code; instrumentation tests verify emission and duration behavior. No additional instrumentation change is necessary. Python projection tuning, further PostgreSQL changes, new batch defaults, and concurrency/prefetch are deferred until a representative instrumented run identifies their expected benefit.
- Phase 2 ran no public load, stress test, throughput test, or large synthetic dataset. No benchmark artifact or simulator file was modified. Full tests and quality checks are recorded in the final phase 2 verification below.
- Subtask 2 (Python fact processing) is skipped pending representative data. The saved run combines projection with database reads/writes/commits, and the new `fact_projection_seconds` metric was added after that run. Code inspection alone does not justify removing validated lookups or allocations. Existing event-mapping and KPI separation behavior remains unchanged.
- Subtask 3 (additional PostgreSQL persistence optimization) is skipped pending component timings. The existing fresh-event readback avoidance and fact insert/upsert reductions (`10c98f9`, `8f68b9a`) already cover the supported unnecessary work. There is no evidence for COPY/staging, index changes, query rewrites, or commit-bound tuning; transaction atomicity and all constraints remain unchanged.
- Subtask 4 (batch-size tuning) makes no change. Defaults remain 1,000 fact rows and 500 event rows; settings stay bounded at 2,000 and 5,000 respectively, with storage-level PostgreSQL bind-limit checks. The prior 1/2-row fixture comparison and this phase's exact single-row operation timing are correctness/mechanics checks, not a safe basis for a larger default.
- Subtask 5 (concurrency/prefetch) is skipped. Runtime ingestion and fact processing are serial, but corrected worker CPU and database wait measurements from a representative run are absent. The saved run recorded 38.184 seconds of chunk download and 93.003 seconds of API request time across 5,878.203 seconds. Prefetch cannot close the target gap, and concurrent ordered checkpoint writes would weaken recovery invariants without demonstrated benefit.
