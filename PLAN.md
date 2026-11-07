# Reviewed Implementation Plan — Outcome-First Agent Loop

## 1. Objective and decision status

Build a single-process controller that runs `./run.sh <SIM_URL>` to completion, consumes only the simulator HTTP contract, optimizes each workflow's declared primary KPI while protecting its separate guardrail, submits every controller-initiated customer-facing change through governance, and keeps a restart-safe audit trail. The mandatory delivery floor is unattended `public_01_dev` and `public_02_incidents`, C12 auditability, constraint compliance, and completed `SUBMISSION.md`. Other capabilities are scoped below.

This reviewed plan is the implementation specification. Chunks 1–4 are complete; Chunk 5 has not started. The approved infrastructure direction is Python 3.12 locally in `.venv`, PostgreSQL 18 in Docker for local development with a persistent named volume, SQLAlchemy async + asyncpg, and Alembic. If an assessment database is provided at execution, use it; otherwise use the configured local development PostgreSQL environment. Statistical operating point and optional capability scope remain unapproved until review.

## 2. Repository findings and verified contract

Reviewed the root README, simulator reference, submission template, six stakeholder memos, public scenario files, existing plan/progress, requirements, `run.sh`, Makefile, smoke tests, and HTTP route definitions. Hidden scenario truth must never be inspected. No scenario YAML or simulator run-directory content may be used to derive controller decisions, tune thresholds, select variants, or infer incidents. Public permitted HTTP data and the controller's own persisted observations are the decision inputs. Do not import `sim`, read its run directory, or parse scenario files at runtime.

Verified from `docs/SIMULATOR.md` and route definitions:

- `POST /sim/advance` moves simulated time in positive multiples of 15 minutes and returns `new_chunks`, `ts`, and `finished`. `409 finished` means no further advances. Simulator state remains in memory; restarting the controller does not reset simulator time.
- `GET /events/chunks?kind=events|history&after=n` lists chunk descriptors. `GET /events/chunks/{n}` returns NDJSON. Chunk numbering is global/increasing across history and events; `event_id` is strictly increasing. History is generated before D0 under default config. No fixed chunk size, event count, or retention bound is promised.
- Documented event types include `campaign_created`, `lead`, `call`, `config_changed`, `approval_decided`. Calls record config version, cohort, variant, outcomes, and QA when connected. `hallucination_flag` is explicitly imperfect.
- Reads: `/health`, `/sim/state`, `/workflows`, `/workflows/{id}`, `/workflows/{id}/config`, `/workflows/{id}/config/history`, chunk list/fetch. Writes: `/sim/advance`, `/approvals`, `/workflows/{id}/apply`, `/workflows/{id}/config`, `/llm/suggest-fixes`, `/validation/runs`.
- Approval submission uses unique `proposal_id` (duplicate => 409), `base_version` (stale => 409), full proposed config, nonempty summary/diff/dry-run/rationale, nonnegative affected estimate, impact score in [0,1], optional evidence/expiry. Prompt changes require a validation run for the same workflow/prompt meeting the workflow policy floor.
- Lifecycle: pending to approved/rejected/expired, then approved to applied/lapsed. `GET /approvals/{id}` and filtered list support reconciliation; decisions also arrive as events. Apply may fail stale (409) or lapsed (410). Auto-apply is only allowed when policy enables it, changed dimensions are allowed, and distribution shift is within policy.
- Validation accepts `n` 50–2000, costs ceil(n/100) credits; suggestion costs 3 credits. Validation measures safety/hallucination, not engagement. Credits can exhaust with 402.
- Pickup = connected/dials. Engagement = engaged/connected. Business outcome = concluded leads/uploaded leads. Never blend. Each workflow declares primary and guardrail.
- Config supports ordered cohorts (up to 20), allocations, prompt, hour weights, person-preferred hour. Schedule changes who gets called, not volume; omitted hours in a supplied table get zero. Cadence, capacity and call windows are business-owned. `next_campaign_only` locks cohorts/allocations for running campaigns; prompt and schedule still apply immediately.
- At initial plan review, the repository had no controller, database schema, migrations, or application package, and `run.sh` invoked the deliberately naive example. Chunks 1 and 2 have since added the controller runtime, durable store, and migrations; there is still no controller Docker image. Assignment allows Docker but does not require it; grading uses a 4-vCPU/8-GB Linux box. Existing requirements included numpy, pyyaml, fastapi, uvicorn, httpx, and pytest.
- Assignment README explicitly allows any language and any storage, and does not specify a supplied database or a Python version. The existing dependency file has lower bounds but no Python-version declaration. No assessment database connection is described in the assignment materials or configured in the inspected environment.

**UNVERIFIED:** whether an assessment database connection will be supplied at grading time; actual grader Python patch; exact response/error fields beyond docs; chunk retention/size bounds; and real HTTP throughput. API contract, not stakeholder recollection, is authoritative.

### Pre-implementation infrastructure selection

The assessment does not require a database service, a specific Python version, or Docker; README permits any storage/language and says Docker is fine. Use an assessment-provided DB at grading only if one is supplied. Local development was prepared with Python 3.12.10 in `.venv`, Docker Engine 29.5.3 and Compose 5.1.4, and a PostgreSQL 18.6 container. `docker-compose.dev.yml` binds only to localhost port 5433 (host PostgreSQL occupies 5432) and mounts named volume `formi-outcome-loop-pg18-devdata` at `/var/lib/postgresql`. Development credentials are stored outside the repository at `%LOCALAPPDATA%\FormiAgenticUniverse\postgres-dev.env`; do not copy them into source, logs, or reports. Async SQLAlchemy connectivity succeeded and a probe row survived container restart; the probe table was removed. `requirements.txt` includes SQLAlchemy's asyncio extra, Alembic and asyncpg. No PostgreSQL 16 or separate database installation is needed. During Chunk 2, Docker was stopped and PostgreSQL integration checks used a temporary PostgreSQL 18 test instance. The controller launcher does not require Docker.

## 3. Architecture and technology review

### Runtime

Use a single Python 3.12 asyncio process with `httpx`, in a modular monolith. The assignment has no Python version requirement; Python 3.12.10 is installed in `.venv`. Async HTTP suits independent bounded reads without adding workers or broker. Start with one writer/decision loop and bounded concurrency only for independent reads. Avoid FastAPI/Uvicorn in the controller: no controller server is required; CLI, structured logs and reports suffice. `pytest` is already listed. Pydantic 2 is optional; add only if strict parsing reduces complexity over typed parsing/dataclasses. Add numpy only if selected statistical computation needs it.

### Durable store decision — PostgreSQL

Use an assessment-provided PostgreSQL connection if one is actually supplied at grading. For local development use the verified PostgreSQL 18 Docker Compose environment. The existing host PostgreSQL service on port 5432 is not the development database; the container uses localhost port 5433. The named Docker volume persists the database across container restarts, verified by a read/write probe. The assignment does not guarantee a managed assessment database or Docker engine availability in grading. Persistence alone does not make remote simulator operations atomic with local commits or guarantee zero loss.

Docker Compose is for local development; `run.sh` must not require Docker unless grading environment availability is verified. The user starts Docker Desktop manually. Launcher accepts simulator URL and explicit/configured database settings, checks DB and simulator readiness with bounded waits, applies schema migrations idempotently, runs to `finished`, and exits nonzero on unrecoverable failure. Keep PostgreSQL storage in the named persistent volume, outside ephemeral controller containers.

Use SQLAlchemy 2 and Alembic as the preferred persistence layer, with bounded connection pooling and startup migration/readiness checks. Shutdown stops new advances, completes or rolls back the open transaction, flushes logs, and closes pools. Restart reloads checkpoints/proposals and reconciles remote APIs. PostgreSQL durability does not provide end-to-end exactly-once behavior or eliminate the need for backup/recovery planning.

No Redis, broker, microservices, Kubernetes, controller UI/API, or worker fleet: no assignment need justifies operating them. Keep workflow/tenant isolation logical in one store.

## 4. Event ingestion and recovery

### Exact sequence

1. Startup: check `/health`; read `/sim/state`, `/workflows`, required workflow metadata/config/version through HTTP.
2. Bootstrap history before decisions: enumerate `kind=history` using chunk list `after=-1`, fetch listed chunks, parse NDJSON, persist events and derived facts with a history checkpoint. Repeated enumeration uses highest fully committed global chunk while retaining the `kind` filter. Store unique `event_id`, chunk number and kind. History/live share uniqueness, so any repeated event cannot double count.
3. Live loop: after prior returned chunks are committed, persist an advance intent *before* `POST /sim/advance`, including intent ID, requested minutes, and pre-request `/sim/state` (`ts`, `finished`, `last_event_id`, chunk count) plus latest known ordered chunk descriptors. Issue the POST once. On normal response, persist its `ts`, `finished` and `new_chunks` descriptors against the intent before fetching/processing. Fetch every returned chunk, validate parse and API ordering, insert events idempotently, update derived state and checkpoint in one transaction per chunk. Advance only the contiguous descriptor checkpoint once all rows and derivations commit; never skip an earlier listed chunk.
4. If the advance POST times out, the connection breaks after sending, or restart finds an unresolved intent, do not repeat it immediately. GET `/sim/state` and list available `kind=events` descriptors after the last committed chunk; compare current `ts`/`finished`/`last_event_id` and ordered descriptors with saved pre-request observations. If state or descriptors show progress, treat the advance as possibly completed, ingest available chunks and reconcile the intent without another advance. If observations prove no progress, another advance may be considered only after verifying endpoint synchronous semantics; otherwise retain ambiguity and halt rather than risk advancing twice. If observations conflict or are unavailable, issue no second POST. The API documents no advance idempotency key. `GET /sim/state` fields are verified in the route implementation and `World.state()` contract shape.
5. On restart, reconcile ordered API descriptors for both kinds against committed local descriptors and recover uncommitted chunks. Re-fetch is safe by event ID. Do not assume filtered numbering starts at zero or resets between kinds.
6. Event rows, derived KPI facts and descriptor checkpoint commit atomically locally. A duplicate event is ignored for both persistence and business effects. External advance/proposal/apply cannot join a local DB transaction: persist intents and reconcile; document the residual remote/local crash window.

### Failure behavior

Separate GET and POST policy. Safe/idempotent GET reads may retry boundedly with exponential backoff/jitter for transport failures, 408/429/5xx, honoring `Retry-After`; classify other 4xx. Never blindly retry ambiguous `POST /sim/advance`, `POST /approvals`, or `POST /workflows/{id}/apply`. For advance, reconcile with `/sim/state` and ordered descriptors as above; issue another advance only when no progress is proven and retry semantics verified, otherwise halt safely. For approval submission, persist proposal ID/payload first; after timeout/restart, list approvals by workflow/status and match proposal ID if returned, then resume the existing approval. If existence cannot be established, do not submit another ID or repeat blindly; halt that workflow for reconciliation. For apply, GET approval status, current config and config history, and inspect related `config_changed` event/approval ID before concluding applied; never repeat apply on ambiguity. If status cannot be established, stop changes for that workflow. A POST may be retried only when a definitive response proves it was not accepted and endpoint semantics permit it. If API is unavailable, do not decide from stale data; retry GETs boundedly then exit nonzero. If DB is unavailable, do not checkpoint or advance. Partial fetch/parse/transaction failure leaves checkpoint unchanged; replay whole chunk. Do not interpret numeric event-ID gaps as loss: the contract says strictly increasing, not consecutive. Resolve completeness from ordered API chunk descriptors, then validate event ordering within available chunks.

Guarantee at-least-once fetch/replay and effectively-once local event effects through unique IDs and atomic transactions, conditional on API retention and durable local DB. Event IDs are strictly increasing but are not documented as consecutive; numeric gaps alone are not evidence of missing events. Checkpoint progression follows ordered API chunk descriptors and completion of each earlier listed chunk. Do not claim end-to-end exactly-once or zero loss: remote and local changes are not atomic, and losing the sole DB or API retention can lose state.

## 5. History, KPI facts, cohorts, audit

Use history chunks and public HTTP metadata only. Reconstruct historical totals/variant/cohort measurements from actual events; verify locally whether history has all feature joins. Join calls to lead features via IDs; person continuity uses `person_id`. Preserve raw facts. Track dials, connected, engaged, concluded and uploaded with denominators explicit. Call's recorded `config_version`, `cohort_id`, variant are exposure attribution; only arms that actually ran enter learning. Label pre-D0 history separately; never duplicate as live exposure. History can inform baseline context but cannot substitute for concurrent randomized comparison where drift/confounding matters.

One versioned cohort definition (ID, ordered predicate, workflow, config version) is shared by attribution, experiment and audit queries. Cohort evaluation follows config order, then default. Audit must answer workflow/time/actor/approver, proposal and config versions, before/after diff, affected estimate, dry-run inputs/result, rationale/evidence, event/sample window, deterministic rule version, approval ID, applied version and post-change metrics. Retain raw events needed to reproduce explanation; simulator logs are not audit evidence.

## 6. Statistical decisions — thresholds not yet selected

The old `N >= 200`, Z-test, universal 5% guardrail, and 24-hour rollback are unsupported as universal rules. Contract defines metrics and per-workflow objective, not test/sample/significance/stopping values. Set these by pre-implementation simulation on public API-derived events, feasible exposure, and an explicit false-winner tradeoff; record rationale and sensitivity. Non-contract values are engineering choices.

Use workflow primary KPI and other-KPI guardrail. Engagement denominator is connected calls; pickup denominator is dials. Compare randomized challenger against concurrent champion/control within workflow and preferably shared cohort. Record allocation probability and actual delivered variants; only actual calls count. Do not attribute joint welcome×voice changes to either alone: start one changed dimension per experiment or justify a powered factorial. Handle imbalance with a method valid for the actual binomial denominators/allocation ratio; report counts and uncertainty. Do not pool workflows/cohorts merely to meet sample size.

Candidate for review: exact/binomial or score interval/test for two proportions; predeclared fixed or group-sequential looks and correction for planned variants/cohorts. Many arms and repeated peeking inflate false positives: cap active comparisons and correct or use calibrated sequential design. Define operational MDE from business value and feasible scenario duration; power analysis determines sample needs. These are **UNVERIFIED** inputs until fixed before experiments. Insufficient evidence means retain config (or continue only safe planned exploration); never promote on point estimate/LLM recommendation.

Use the workflow's guardrail max-drop (public examples include 0.02) with uncertainty and a predeclared observation window. A per-workflow policy value is not a universal threshold. Separate apparent breach from supported harm; use a conservative predeclared harm boundary for pause/corrective-proposal review. Minimum sample, confidence/significance, MDE, look plan, multiplicity, cohort pooling and harm boundary remain **UNVERIFIED** choices until justified. Reproducibility requires deterministic input ordering, algorithm/version, tie-break, persisted inputs/outputs and assignment seed if randomization is used.

Stop at supported win with safe guardrail, harm boundary, infeasible evidence, or scenario end. No unadjusted daily peeking. Record basic post-promotion metrics against the saved reference. C7 decay detection and rollback are deferred: this plan does not automatically detect winner decay or roll back a promoted variant. Any separately detected acute incident follows C8's governed corrective-proposal path; it is not an automatic rollback. Decay window/threshold remain **UNVERIFIED** and require an explicit scope update before C7 is added.

## 7. Governance and proposal lifecycle

Persist draft → submitted/pending → approved/rejected/expired; approved → apply-intent → applied/lapsed/stale; terminal outcomes close, and only fresh re-evaluation may create a replacement. Persist unique proposal ID and payload/hash before POST. After timeout, list/get and reconcile by ID; never create a second proposal solely due to lost response. Duplicate 409 triggers lookup. Poll boundedly; approval events also inform state. Re-fetch status immediately before apply.

Before apply, verify base version current, approval not lapsed, deterministic checks still pass; persist intent then POST exact approval ID. After crash/timeout, read approval, workflow config and config history; confirm matching config/proposal or retry only if remote state proves not applied and API semantics permit. **Apply idempotency after lost response is undocumented (UNVERIFIED); verify `/docs` and exercise before relying on retry.** Reconcile `config_changed` actor/IDs and current config. Supervisor edit updates observed version and invalidates old experiment/proposals. On stale, re-fetch config/history, recompute impact/evidence and decision; only submit a new ID if still justified. Never resubmit stale payload blindly.

Every customer-facing controller change uses approvals or policy-permitted auto-apply. Proposed default: approval for welcome/voice/prompt/cohort changes; consider auto-apply only for explicitly policy-allowed low-risk dimensions and only after review. Never silently override supervisor edits. Batch coherent evidence-backed changes, not unrelated changes; enforce per-workflow request cap, approver hours, TTL and apply window. Proposal fields must be honest: affected leads from observed population/horizon; impact score a documented bounded ranking function using estimated outcome delta, population, uncertainty, guardrail risk; dry run labels historical replay and assumptions, not guarantee. No fake precision.

Crash recovery uses durable outbox before POST, reconciliation after ambiguous result, persisted approval ID/status and apply intent, config-history check, audit links. If remote state remains ambiguous, halt changes for that workflow and surface manual review rather than risk duplicate/stale application.

## 8. Incidents, safety, rollback

Documented signals: workflow policy/config; call outcomes; `config_changed`, `approval_decided`; connected-call QA `hallucination_flag` and `flow_adherence`; caller-ID variant/outcomes. QA flag is imperfect. Require population evidence, not one call. Caller health estimates from pickup by caller ID with workflow/cohort/time/exposure context; causal threshold/sample rule is **UNVERIFIED**. Do not infer hidden caller-ID truth. Store window, counts, denominator, uncertainty, severity and signal.

Hallucination path: aggregate QA rise with sufficient evidence; gather affected variants/config and comparison window; call suggest-fixes only when useful and credits allow; treat suggestions as untrusted prompt candidates; validate candidate on `/validation/runs` using justified sample within 50–2000 and workflow floor; validation is safety only, not engagement; reject unsafe or engagement-harmful candidates; submit evidence-backed approval; reconcile lifecycle; apply exact approved version; monitor actual post-apply hallucination proxy and engagement/guardrail. On 402, stop paid calls and retain safe current config. Bad outcome after apply => new governed corrective proposal against current base; no bypass. No direct rollback endpoint/semantics are documented: restore recorded prior config through a fresh approved proposal after revalidation.

Persist incident/proposal states so restart recovers every phase. Never infer success from suggestion, validation, approval alone, or a lost response.

## 9. Capability selection

| ID | Decision | Reason | Dependencies | Validation |
|---|---|---|---|---|
| C1 | Implement, bounded | Scale and correctness foundation; measure rather than assume. | storage, HTTP | load, lag, replay/failure tests |
| C2 | Implement minimum shared cohort object | Chunk 4 already requires one canonical, versioned cohort definition reused by attribution, experiment and audit; this is required for consistency, while a supervisor UI is out of scope. | event joins, config versions | tests prove identical cohort ID/predicate/version across event attribution, experiment and audit |
| C3 | Implement | History is documented baseline context; prevent double count. | C1, C12 | history/live replay dedupe and counts |
| C4 | Exclude | Business-owned schedule; per-person timing is wishlist, high-risk. | cohort/schedule policy | none in first scope |
| C5 | Defer | Core incident/governance reliability first; reconsider if public_02 requires. | events, stats, policy | public_02 if implemented |
| C6 | Implement, narrow | Core learning value; one dimension/comparison initially. | C1, C3, frozen policy | deterministic tests, public_01 grade |
| C7 | Defer | Winner decay detection and rollback are not in the committed chunk scope; finish promotion safely first. A corrective proposal after a prompt incident (C8) is incident recovery, not C7 automatic decay rollback. | C6, governance, post-promotion observation | no C7 implementation in this plan; reconsider only with explicit scope update and decay fixture |
| C8 | Implement | public_02 and floor require incident path. | audit, validation, approvals, credits | public_02, crash/402 tests |
| C9 | Implement | Governance boundary is absolute. | HTTP, outbox, audit | lifecycle/stale/duplicate/crash paths |
| C10 | Defer advanced scorer; basic honest fields in C9 | Required proposal fields, but replay scorer is extra. | history, joins | estimate reproducibility/payload checks |
| C11 | Exclude | No UI required; CLI/queryable audit is higher value. | C12 | documented audit query |
| C12 | Implement, mandatory | Explicit floor and audit constraint. | store | answer why/approver/outcome locally |
| C13 | Implement, mandatory reliability | Explicit crash safety. | C1, store, governance | kill/restart at chunk and approval stages |
| C14 | Implement minimal | SRE requests loop health; CLI metrics suffice initially. | C1 | measured lag/rate/credits/memory |
| C15 | Defer | Multi-tenant fairness complex; bounded per-workflow scheduling first. | C1, C9 | starvation review if time permits |

## 10. Production engineering requirements

- JSON logs include run/workflow/chunk/event, simulated timestamp, proposal/approval, transition, retry class and elapsed time. Avoid unnecessary personal data/secrets.
- CLI/environment config for URL, state path, timeout/retries, polling/advance cadence and log level; validate at startup. No credentials in repo/logs. Auth needs are **UNVERIFIED**.
- HTTP pooling, bounded connections and explicit connect/read/write/pool timeouts. Retry transient errors only with capped exponential backoff/jitter and Retry-After. No blind non-idempotent write retries.
- Small bounded DB pool for one controller. One transaction per event chunk; short separate intent/state transactions. Never hold DB transaction during network request. Unique constraints/idempotency keys.
- SIGTERM/SIGINT stops new advances, finishes/rolls back current transaction, persists intent, closes clients. SIGKILL recovery uses checkpoints/reconciliation.
- Startup checks simulator health and DB readiness. Heartbeat reports simulated time, finished, committed chunk/event, ingest lag, retry/errors, pending approvals and credits if available. No health server unless justified.
- Classify transient transport/5xx/429; semantic 4xx; stale/duplicate/lapsed; exhausted credits; malformed/out-of-order event; DB unavailable/full. Map to retry, reconcile, safe halt or operator action.
- Metrics: events/sec, pending chunks, simulated and wall-clock lag, decisions/hour, credit spend/remain, API latency/status, DB bytes, RSS peak, outcomes. Alerts actionable; paging thresholds are deployment-specific and not in contract.
- Bound HTTP concurrency, streaming/line parse, batch memory, backpressure and indexes. Measure before optimize; avoid duplicated unbounded raw data.
- Record host CPU/RAM, runtime, DB mode, scenario, volume, wall time, throughput, lag distribution, RSS, DB growth, failures and 10× bottleneck. No unmeasured claims.
- Reproducible tests use fixed fixtures/seeds; contract integration hits local HTTP simulator; test rollback/replay/state machine; reports retain run IDs. Controller must not read scenario truth.

## 11. Incremental implementation chunks

Chunks 1–4 are complete; Chunk 5 has not started. Each remaining chunk must end in a working, testable repository. Paths are planned new files unless existing files are named. Never modify `sim/`.

### Chunk 1 — Runtime and launcher
- **Objective/why:** replace naive launch with unattended `run.sh <SIM_URL>` and clean lifecycle.
- **Files:** create `controller/__init__.py`, `controller/__main__.py`, `controller/config/settings.py`, `controller/clients/simulator.py`, `controller/runtime/lifecycle.py`, `controller/observability/logging.py`; modify `run.sh` and `README.md` only for verified dependencies.
- **Dependencies:** approved runtime/storage; simulator contract.
- **Steps:** parse config; pooled client/timeouts/errors; health/read-only metadata; finish-detecting skeleton; signals.
- **Unit:** config, URL, error classification, retry bounds. **Integration:** launcher against local simulator; read health/workflow/state. **Failure:** startup outage and SIGTERM.
- **Acceptance:** clean documented setup, one argument, no internals/files, exit 0 only when finished.
- **Expected:** `./run.sh http://127.0.0.1:8700` emits startup/time/finished logs and exits 0.
- **Commit:** `feat: add controller runtime and simulator client`.
- **PROGRESS:** files, runtime, command/output, dependency decision/limits.

### Chunk 2 — Durable schema and audit
- **Objective/why:** persist raw events, checkpoints, outbox and explanation before behavior.
- **Files:** implemented under `controller/storage/` (`store.py`, `models.py`, `audit.py`) with root `alembic.ini` and `migrations/`; unit tests under `tests/unit/` and PostgreSQL integration tests under `tests/integration/storage/`.
- **Dependencies:** Chunk 1; selected PostgreSQL connection and durable data path verified.
- **Steps:** versioned schema; run/workflow; unique event; chunk checkpoints; audit/outbox; bounded transactions.
- **Unit:** reopen, uniqueness, rollback, query. **Integration:** clean init/restart/migration idempotence. **Failure:** interrupted transaction never advances checkpoint.
- **Acceptance:** durable reopen, unique IDs, rollback proven, state path documented.
- **Expected:** `pytest -q tests/integration/storage/test_store.py` passes.
- **Commit:** `feat: add durable event and audit store`.
- **PROGRESS:** schema, store, restart/rollback evidence.

### Chunk 3 — Chunk ingestion
- **Objective/why:** ingest history/live reliably before decisions.
- **Files implemented:** `controller/ingestion/{events.py,parser.py,service.py,runner.py}`; updated `controller/clients/simulator.py`, `controller/runtime/lifecycle.py`, `controller/storage/store.py`, `controller/__main__.py`, Alembic logging setup, README and PROGRESS; tests under `tests/unit/ingestion/` and `tests/integration/ingestion/`.
- **Dependencies:** Chunks 1–2.
- **Steps completed:** list history/live descriptors via HTTP; spool streamed NDJSON with bounded RAM; validate descriptor metadata, event shape and strictly increasing (not consecutive) event IDs; batch event inserts and checkpoint atomically per ordered descriptor; persist advance intent and returned descriptors; reconcile restarts through state and ordered listings; allow one POST retry only after unchanged state and no new descriptors.
- **Unit:** parser, malformed/out-of-order events, duplicate IDs, descriptor ordering, interrupted stream, database outage before POST. **Integration:** history/live persistence, replay, atomic rollback, runtime loop, advance intent and restart reconciliation. **Failure:** download interruption, malformed/truncated chunk, database write failure, API outage, timeout-after-apply, no-progress and repeated ambiguity.
- **Acceptance:** history/live labels preserved; incomplete chunks never advance checkpoints; bounded-memory stream and 500-event DB batches; duplicate events do not create duplicate local effects; no exactly-once claim.
- **Expected:** `.venv` pytest unit/integration suites pass. A generated 30,000-event PostgreSQL test measured throughput/memory; public load scenario remains unrun.
- **Commits:** `d921490`, `b6b88f6`, `6bdce40`, `f975203`, `09bf0b6`, `adf5b54`.
- **PROGRESS:** actual test counts, checkpoint/replay and recovery evidence, synthetic throughput/memory, unrun public scenarios.

### Chunk 4 — History, KPI and audit query
- **Objective/why:** correct denominators and explainability.
- **Files implemented:** `controller/analytics/{history,facts,kpis,cohorts,audit}.py`; bounded event and KPI/cohort query methods in `controller/storage/`; migrations `0002_kpi_facts`, `0003_cohort_definitions`, `0004_audit_query_indexes`; unit, integration and runtime tests.
- **Dependencies:** Chunk 3.
- **Steps completed:** bounded keyset event/workflow queries; atomic per-page fact derivation/cursor; lead-to-call join using only preceding leads; observed cohort/variant exposure; versioned ordered cohort rules; separate KPI aggregates; read-only audit keyset browsing.
- **Unit:** KPI denominators/zero-connected, cohort predicate order and validation, malformed facts, audit filters and cursors. **Integration:** persisted events through restart-safe facts, cohort match, KPI and audit; PostgreSQL migration and query checks. **Failure:** invalid/incomplete events remain explicitly classified; failed fact transactions do not advance cursors; missing lead joins remain explicit.
- **Acceptance:** pickup is `connected / dials`; engagement is `concluded / connected`; actual call variant and observed cohort are preserved; audit returns workflow, actor, reason, approval/outcome evidence; all reads are bounded and ordered.
- **Verified:** analytics unit 17 passed; analytics integration 5 passed; full suite 123 passed; `ruff check .`, `ruff format --check .`, compileall, repeated Alembic upgrade/check, and `git diff --check` passed. Synthetic local PostgreSQL measurement: 5,000 facts inserted, checkpoint 990004999, 557 events/s, 3.94 MiB peak traced Python memory, 10.3 ms filtered aggregate. Public scenarios and grading-host performance remain unrun.
- **Commits:** `79caf3d feat(analytics): add historical event queries`; `bf4e93b feat(analytics): derive durable KPI facts`; `237d6b2 feat(analytics): add KPI calculations and cohorts`; `f640e9b feat(audit): add historical audit queries`; Chunk 4 hardening commit is being recorded.
- **PROGRESS:** actual formulas, event/fact counts, checkpoint/restart evidence, failures, synthetic throughput and limitations are in `PROGRESS.md`.

### Chunk 5 — Governance lifecycle
- **Objective/why:** safe governed writes and recovery.
- **Files:** create `controller/governance.py`, `controller/reconcile.py`; modify store/audit/client; tests.
- **Dependencies:** Chunks 1–4.
- **Steps:** payload validation/outbox; submit/reconcile duplicate/timeouts; poll/state machine; stale reevaluation; apply intent/config-history reconciliation; policy allowlist only if approved.
- **Unit:** transitions, stable IDs/hash, error handling. **Integration:** pending/approved/rejected/expired/applied/lapsed through documented API. **Failure:** kill after submit/approval/before apply/remote apply before local commit.
- **Acceptance:** no bypass; stale never blindly retried; ambiguity halts workflow; exact diff/evidence audited.
- **Expected:** `pytest -q tests/unit/test_governance.py` passes transitions/recovery.
- **Commit:** `feat: implement governed proposal lifecycle and reconciliation`.
- **PROGRESS:** endpoint results, lifecycle coverage, retry uncertainty.

### Chunk 6 — Statistical comparison
- **Objective/why:** one evidence-based optimization loop.
- **Files:** create `controller/statistics.py`, `controller/experiments.py`, `docs/DECISION_POLICY.md`; modify metrics/governance; tests.
- **Dependencies:** Chunks 1–5; approved method/MDE/minimum evidence/look/multiplicity.
- **Steps:** one-dimension design; KPI-specific estimate/uncertainty; imbalance/peeking controls; objective guardrail; persist version/inputs; safe proposal.
- **Unit:** binomial fixtures, imbalance, guardrail, insufficient data, multiplicity, deterministic tie. **Integration:** public_01 and grader. **Failure:** interrupted experiment resumes without crediting unrun arm.
- **Acceptance:** no unadjusted repeated peek; deterministic replay; no LLM winner; no KPI blending.
- **Expected:** stats tests pass; report includes method/counts/reason.
- **Commit:** `feat: add deterministic guarded experiment decisions`.
- **PROGRESS:** method rationale, false-positive control, feasibility, public_01 results.

### Chunk 7 — Public incident workflow
- **Objective/why:** mandatory public_02 response path.
- **Files:** create `controller/incidents.py`; modify audit/governance/statistics; tests.
- **Dependencies:** Chunks 1–6; credit accounting.
- **Steps:** aggregate QA signal; evidence record; budget calls; validate candidate; prompt proposal; post-apply monitor. If evidence warrants correction, submit a new governed corrective proposal against current config. This is incident remediation only; it does not implement automatic C7 decay detection or rollback.
- **Unit:** noisy/insufficient signal, credit and validation. **Integration:** public_02 through completion/report. **Failure:** 402, unsafe validation, rejection/expiry/lapse, crash each state, harmful post-change signal.
- **Acceptance:** population evidence, untrusted LLM, matching validation + governance, actual timeline only.
- **Expected:** run `./run.sh <SIM_URL>` and grader; save `reports/public_02.md`.
- **Commit:** `feat: handle governed population-level prompt incidents`.
- **PROGRESS:** timeline, credits, approval/post-change evidence.

### Chunk 8 — Crash recovery demonstration
- **Objective/why:** prove mandatory restart safety.
- **Files:** create `scripts/crash_recovery.sh` or platform-neutral procedure and `reports/crash_recovery.md`; modify code/tests as needed.
- **Dependencies:** Chunks 1–7.
- **Steps:** kill during partial chunk or pending approval; restart same state against same simulator; reconcile/finish; compare IDs/effects/audit.
- **Unit:** transaction/outbox cases. **Integration/failure:** real kill/restart, lost response, repeated start.
- **Acceptance:** finish without lost committed event, duplicate local effect, forgotten approval or duplicate config effect; disclose remote ambiguity.
- **Expected:** procedure/log, final finished state and reconciliation evidence.
- **Commit:** `test: demonstrate controller crash recovery`.
- **PROGRESS:** kill point, commands, counts/result.

### Chunk 9 — Load and observability
- **Objective/why:** measure load requirement and operational health.
- **Files:** create `controller/telemetry.py`, `scripts/measure_load.py`, `reports/`; modify README.
- **Dependencies:** Chunks 1–8.
- **Steps:** instrument counters/timers/RSS/DB; run public_03 on 4-vCPU/8-GB where available; report throughput/lag/memory/growth/failures; profile evidence-led; identify 10× limit.
- **Unit:** metrics formulas/bounded memory. **Integration/failure:** load run, API interruption, DB pressure.
- **Acceptance:** report environment and measurements; explicitly pass/fail SRE one simulated day under 10 wall minutes; no fabricated result. Other memory/DB ceilings are UNVERIFIED pending host budget.
- **Expected:** `reports/public_03_load.md` includes raw metadata, formulas, results.
- **Commit:** `perf: measure and report controller load behavior`.
- **PROGRESS:** workload, command, results, bottleneck.

### Chunk 10 — Submission and final grading
- **Objective/why:** complete evidence and stakeholder reasoning.
- **Files:** create `SUBMISSION.md` and actual reports; modify README/run only for evidenced gaps.
- **Dependencies:** previous chunks/results.
- **Steps:** baselines, controller and grader; fill template with actual numbers; answer six memos and policy inaccuracies; genuine small commits; optional <=5-minute demo.
- **Tests:** unit/integration/full clean launch; do not claim unrun tests. **Failure:** repeat restart/clean launch; reconcile report/run IDs.
- **Acceptance:** floor scenarios finish unattended, constraints pass, audit/crash evidence, load truthfully reported, all six replies/tradeoffs.
- **Expected:** README/SUBMISSION contain exact commands and report paths.
- **Commit:** `docs: complete assessment evidence and stakeholder responses`.
- **PROGRESS:** actual grading, evidence paths, weaknesses; status only changes after authorized implementation begins.

## 12. Public scenario validation

### `public_01_dev`
- **Accomplish:** unattended finish, history/live ingestion, auditable KPI/variant facts, supported governed decision only if evidence warrants. No lift is promised.
- **Inputs:** HTTP reads/chunks, workflow/objective/options/policy/config, approval/validation responses only. No YAML truth/run data.
- **Expected decisions:** retain config with insufficient evidence; candidates from actual observed supported options; govern all changes.
- **Safety:** distinct KPIs; no cadence/capacity/window changes; no silent supervisor overwrite or unapproved agent-facing change.
- **Success:** finished/exit 0, all chunks effectively once, auditable decision, zero constraint violations; report observed grader lift whether positive/neutral/negative.
- **Evidence:** run ID, command, logs/audit, baseline and controller grader report in reports/.

### `public_02_incidents`
- **Accomplish:** unattended finish and population-level response to observable incident signals when evidence/policy support it; verify after change.
- **Inputs:** HTTP APIs, call QA, config/approval events; imperfect QA flag, no hidden truth.
- **Expected:** no single-call trigger; aggregate; matching prompt validation evidence; governance; if unsafe/no credits/insufficient data retain config and report.
- **Safety:** LLM is untrusted; validation is not engagement evidence; preserve supervisor edits; re-evaluate stale; corrective rollback proposal is governed.
- **Success:** incident timeline reconstructable, all changes governed, post-change measures and credit/402 behavior recorded; grader reported honestly.
- **Evidence:** report, inputs/timeline, validation IDs/rates, proposal/approval/config versions, credits.

### `public_03_load`
- **Accomplish:** documented 12 workflows/~850k events/day, five-day run without unbounded memory; identify 10× bottleneck.
- **Inputs:** HTTP chunks, state/workflow/config, observed timestamps and local resource metrics only.
- **Expected:** ordered ingestion, bounded concurrency/batch, bounded decisions, no unnecessary LLM spend.
- **Safety:** DB/API backpressure; no checkpoint past missing chunk; no policy bypass; degraded operation explicit.
- **Measurements:** committed unique events/wall second; separate simulated-minute lag from wall processing delay; interval throughput/backlog; OS peak RSS; DB bytes before/after and bytes/event; decision latency/count; API/DB errors/retries; credit spend/remain. Record host CPU/RAM/runtime/store. Measure SRE target of one simulated day under 10 wall minutes as pass/fail. No fixed five-day wall target. No achieved performance claimed before run.
- **Success:** chunk continuity/reconciliation, bounded memory, no duplicate effects, measured failure behavior and 10× bottleneck. Acceptable memory/DB ceilings are UNVERIFIED until host budget is verified.
- **Evidence:** `reports/public_03_load.md`, commands, formulas, run metadata, raw results, audit summary/failure injection.

## 13. Stakeholder responses for `SUBMISSION.md`

Reflect actual wording and tradeoffs; verify per-workflow policy, which varies in objective, approver hours, caps, auto-apply and mid-flight behavior.

1. **Sales/Nikhil:** decline blended KPI and indiscriminate persuasive prompt; speed remains bounded by evidence, guardrails and governance. Verify Kestrel/Northwind claims against actual policies. Explain safe batching.
2. **Compliance/Meera:** accept named human approval for Northwind customer-facing changes, prompt evidence and audit; verify each policy and disclose API limits. Preserve separate KPIs and hours.
3. **SRE/Kabir:** accept kill/restart, idempotency, actionable alerts; report actual store/owner, measured load and 10× bottleneck. Use the assessment DB if supplied, otherwise the existing PostgreSQL service; do not assume an external connection.
4. **Kestrel Ops/Anita:** preserve supervisor edits; batch and give consolidated audit/report. Verify actual policy instead of assuming memo values. Address notification ask: no supervisor notification endpoint is documented; controller can report/audit but must not claim delivery.
5. **Product/Tara:** shared cohort and honest proposal fields; defer wishlist P0s exceeding effort (person timing, UI, caller health if excluded). Expired means fresh evidence, not stale resubmission.
6. **CTO/Aryamann:** explainability, credit discipline, scale and cuts; only measured outcomes; state first 10× bottleneck.

Each reply <=120 words per template. Do not invent memo statements.

## 14. Deliverables and unresolved risks

Deliverables: working `run.sh`; README clean-start/debug instructions; completed `SUBMISSION.md`; `reports/` for public_01/02 grades, public_03 load, crash recovery and audit evidence; genuine incremental commits; optional short demo. `SUBMISSION_TEMPLATE.md` remains source; do not duplicate/move it. Do not fabricate reports or history.

Risks: (1) whether a DB connection is supplied at grading; (2) Docker availability and durable state path in grading; (3) undocumented retention and apply retry semantics; (4) statistical power/false-positive calibration within scenario duration; (5) imperfect hallucination signal and safety/engagement separation; (6) actual throughput/RSS/DB growth; (7) stakeholder requests beyond API affordances. Resolve with API inspection/tests/measurements; if unresolved, mark UNVERIFIED and use safe no-change/halt behavior.
