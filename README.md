# Outcome-First Agent Loop — Senior Engineer Assignment

**Formi · Agentic Universe** · Greenfield · Suggested effort 12–16 hours across 7 calendar days

---

## 1. Context

Formi runs outbound voice agents for insurers and lenders across India: renewal reminders, KYC follow-ups,
cancellation reconfirmations. Each **workflow** dials leads within a business-set window, following a retry
cadence the business configures. Today a human sets the agent's configuration once and rarely revisits it.
That configuration covers which welcome message it opens with, which voice it uses, which caller ID it dials
from, which prompt version runs, and which hours each kind of person gets called in.

Two numbers decide whether a workflow is healthy, and we never blend them:

- **Pickup rate**: of the dials, how many connected.
- **Engagement rate**: of the people who picked up, how many stayed to a conclusion (interested *or* not).

The business outcome sits above both: **leads concluded ÷ leads uploaded**.

We are moving from agents that humans tune to agents that **earn autonomy**. They notice what's working, try
better options on the right people, ask for permission in a form a busy supervisor can say yes to, apply
changes safely, and back out when something stops working. A client supervisor can see why every change was
made. Nothing changes on a live client's agent without passing the governance boundary.

**Your job is to build that loop.**

## 2. The problem

Build a **controller**: an always-on service that, for every workflow on the platform, continuously:

1. **Detects**: pickup or engagement drifting, a caller ID going bad, hallucinations spiking. It works on
   thresholds and populations, never on single calls.
2. **Hypothesises and experiments**: it forms cohorts, splits traffic between variants, and decides how much
   evidence is enough.
3. **Proposes**: it packages a change with a summary, a diff, the affected leads, a dry run, an impact score
   and a rationale, then routes it through governance.
4. **Applies and learns**: it applies what's approved, keeps measuring, promotes winners and rolls back
   variants that degrade.
5. **Explains**: from your system's own records, anyone can answer *"why did workflow X change at 14:00 on
   D3, who approved it, and what happened next?"*

You won't connect to our production. `sim/` contains a **closed-loop simulator** of the world: tenants,
workflows, leads, a dialler, people who pick up or don't, client approvers, client supervisors who edit things
mid-day, and an LLM that suggests prompt fixes. Your controller drives it over HTTP. The simulator holds
hidden ground truth: which variants actually work, for whom, for how long, and what happens when you change
things. Read **[docs/SIMULATOR.md](docs/SIMULATOR.md)** carefully. It is the contract.

## 3. Scale envelope

Design for the platform we're building toward, not the dev scenario:

- **~2 crore dials/month**: about 6.5 lakh/day inside a 12-hour window, peaking above 50k dials/hour.
- Tens of tenants and hundreds of workflows, each with its own policy, objective and approver.
- Events arrive continuously. Your loop's decisions are only as fresh as its ingestion.

`scenarios/public_03_load.yaml` (12 workflows, ~650k dials/day, ~850k events/day) proves your system keeps up.
We will ask for numbers: ingest throughput, decision lag, memory, and where it would break at 10×.

## 4. What's in the box

```
sim/                      the world (don't modify — we run our own copy)
scenarios/
  public_01_dev.yaml      3 workflows, 8 days, no incidents — start here
  public_02_incidents.yaml  caller ID flagged, a supervisor ships a bad prompt, a supervisor edits mid-experiment
  public_03_load.yaml     12 workflows at full scale, 5 days
stakeholders/             six memos from people who want things from you. They disagree.
examples/naive_controller.py   shows the API round-trip. It is deliberately bad.
docs/SIMULATOR.md         API, config, governance, events
SUBMISSION_TEMPLATE.md    copy to SUBMISSION.md
```

```bash
python -m sim baseline --scenario scenarios/public_01_dev.yaml --run-dir runs/base01
python -m sim serve    --scenario scenarios/public_01_dev.yaml --run-dir runs/mine01     # :8700
./run.sh http://127.0.0.1:8700                                                        # your controller
python -m sim grade --run runs/mine01 --baseline runs/base01 --out reports/public_01.md
```

### Controller runtime setup

The controller requires Python 3.12 and its project virtual environment. On Linux, create it with
`python3.12 -m venv .venv`; on Windows PowerShell use `py -3.12 -m venv .venv`. Install the repository
dependencies with `.venv/bin/python -m pip install -r requirements.txt` on Linux or
`.venv\Scripts\python.exe -m pip install -r requirements.txt` in PowerShell. Start it with
`./run.sh http://127.0.0.1:8700` from Linux/macOS Bash or Windows Git Bash. PowerShell can invoke the same
entry point directly with `.\.venv\Scripts\python.exe -m controller http://127.0.0.1:8700`.

`run.sh` selects the local virtual environment and does not start or require Docker. The runtime requires the
PostgreSQL connection in `CONTROLLER_DATABASE_URL` and uses `CONTROLLER_*` settings for advance interval, HTTP
timeouts, bounded GET retries, connection limits, and log level; defaults are listed in
`controller/config/settings.py`. It checks simulator health,
workflows and state over HTTP, then advances until the simulator reports completion. `Ctrl+C` or `SIGTERM`
stops after any in-flight HTTP operation finishes. JSON logs go to stderr; the simulator URL and credentials
are not logged. An ambiguous advance response exits safely without issuing another advance.

The runtime persists simulator events and advance intents, then derives durable event facts after startup
reconciliation and each committed advance. It does not schedule experiments or automatically invoke governance;
those remain explicit callable components.

### Runtime telemetry (Chunk 9)

The controller writes a structured `advance_ingested` heartbeat after each durable live advance and a final
`runtime_metrics` JSON record when it completes or stops. Heartbeats include simulator time/state, committed
chunk and latest event IDs, pending chunks and approvals, credits when the API provides them, ingestion lag,
and cumulative API request/error counts. The final record includes event/fact throughput, bounded API latency
histograms and status counts, peak process RSS, PostgreSQL database bytes, checkpoints/outbox/proposal states,
decision rate, and separate pickup, engagement, and business-outcome rates. Undefined KPI denominators remain
`null`. API latency measures time to response headers; chunk ingestion duration includes response download,
validation and persistence.

The runtime is a CLI process and does not expose a metrics or health server. It emits no credentials or
unbounded request labels. There are no universal alert thresholds; operators should alert on failed/ambiguous
operations, stale ingestion, nonterminal outbox/proposal state, and resource limits using deployment budgets.
Use `python scripts/measure_load.py <SIM_URL> --output reports/public_03_load.md` for the reproducible public
load run; it samples controller RSS and PostgreSQL database size while retaining the controller JSON log. Start
the permitted public load simulator separately and point the measurement at a fresh, dedicated controller
database (the runner rejects databases containing prior simulator events):

```powershell
py -3.12 -m sim serve --scenario scenarios/public_03_load.yaml `
  --run-dir runs/public_03_measurement --host 127.0.0.1 --port 8700
# In another shell, set CONTROLLER_DATABASE_URL to a disposable PostgreSQL database.
py -3.12 scripts/measure_load.py http://127.0.0.1:8700 `
  --output reports/public_03_load.md --log reports/public_03_load.jsonl `
  --samples reports/public_03_load_samples.jsonl
```

The measurement tool starts the real `python -m controller` process, samples documented read-only simulator
state and PostgreSQL size, and records the Controller's JSON log. It does not use the simulator run directory
as an input. A timed-out/nonzero Controller is reported as incomplete, and a missing simulated-time interval
is never counted as an SRE pass.

### Durable storage (Chunk 2)

The async store in `controller/storage/` uses `CONTROLLER_DATABASE_URL` in SQLAlchemy's
`postgresql+asyncpg://...` form. Set it only in
the current process environment using the local development credentials stored outside the repository or a
database connection explicitly supplied for the assessment. Do not put the URL in source, `.env` files under
version control, command output, or logs. `DatabaseStore.initialize()` checks connectivity and
`DatabaseStore.migrate()` applies the checked-in Alembic revisions. The launcher requires this setting, checks
readiness, and applies migrations before simulator ingestion. For manual migration work, set the environment variable in the shell and run
`python -m alembic -c alembic.ini upgrade head` with the project virtual environment's Python on Windows or
Linux. Ensure the selected PostgreSQL service is running before launch; Docker is optional and is not started by
the launcher. Store and ingestion integration tests require `CONTROLLER_TEST_DATABASE_URL` to point at a
disposable PostgreSQL database.

### Event ingestion (Chunk 3)

At startup, the controller lists history and live chunk descriptors over HTTP, merges them by the API's global
chunk order, fetches NDJSON through the HTTP client, and stores validated events with the descriptor checkpoint
in one transaction. Memory use is bounded with a 1 MiB in-memory download spool and 500-row database batches;
larger response bodies spill to operating-system temporary storage. Before each `/sim/advance` POST, it commits
an advance intent to the outbox. The POST is sent once, its returned descriptors are persisted, then those
chunks are ingested. If the POST outcome is ambiguous, the controller stops without blindly retrying. On
restart, it compares the saved pre-request simulator state with `/sim/state` and ordered chunk listings. It
ingests published chunks and completes the intent when progress is visible; it retries the same intent only
after the state is unchanged and no new descriptors are available. Missing or conflicting evidence leaves the
intent unresolved and stops further advances. Chunk reads may be replayed; unique event IDs and per-chunk
transactions provide effectively-once local persistence, not end-to-end exactly-once delivery. Advance is
bounded to the initial POST plus one reconciled retry; repeated ambiguity requires manual reconciliation.

The event insert batch defaults to 500 rows and KPI fact projection defaults to 1,000 rows. Operators may
set `CONTROLLER_EVENT_INSERT_BATCH_SIZE` (1–5,000) and `CONTROLLER_FACT_PAGE_SIZE` (1–2,000) for a
controlled, measured run. These conservative bounds stay below PostgreSQL's 65,535 bind-parameter limit
for the current event and fact row shapes. Changing them does not change per-chunk or per-fact-page
transaction boundaries. The defaults remain unchanged because no representative throughput comparison
has established a faster setting.

### Historical facts and audit queries (Chunk 4)

After raw chunks are committed, a restartable analytics processor reads persisted events in bounded pages and
stores one fact per source event. The event ID is the uniqueness key, and each page's facts and scan cursor
commit together. Facts retain source event/chunk/kind, workflow, campaign, lead/person, timestamp, config
version, observed cohort, actual call variant, and a data-quality status. `campaign_created.leads` contributes
uploaded leads; `call` contributes one dial, its `connected` boolean contributes pickup, and the documented
`concluded_interested`/`concluded_not_interested` dispositions contribute conclusions. The independent rates
are `connected / dials` (pickup) and `concluded / connected` (engagement). An empty denominator produces an
undefined (`None` in the Python summary) rate. The `engaged` event flag is preserved in raw events but is not
used as a substitute for the documented conclusion dispositions.

Lead-to-call history joins use only preceding lead events for the same workflow and lead ID. Missing lead
records remain explicit; returning-person status is based on an earlier lead event with the same persisted
`person_id`. A canonical `CohortDefinition` carries workflow, config version, ID, order, and predicate. It can
be built from an HTTP-supplied config snapshot, evaluated in first-match order, persisted immutably per config
version, and passed to KPI or cohort-scoped audit queries. Call facts retain the simulator's observed cohort
and config version as the actual exposure. Audit browsing is read-only and keyset-paginated by event time and
record UUID, with filters for workflow, actor, time, config/cohort, approval, outcome, action, and resource.

Facts and cohort rules are durable in Alembic migrations `0002_kpi_facts` and
`0003_cohort_definitions`; audit query indexes are in `0004_audit_query_indexes`. Public scenario execution,
query latency at load scale, and automatic collection of historical cohort predicates from config-history
responses have not yet been verified. No scenario YAML or simulator run-directory data is used by this code.

### Governed proposals (Chunk 5)

The governance package validates and hashes approval payloads, persists proposal state and external-operation
intents, submits through `POST /approvals`, and reconciles approval state from lookup responses or supplied
`approval_decided` events. Before apply, it rereads the approval and current config version, persists an apply
intent, then calls `POST /workflows/{id}/apply` with the approval ID. Ambiguous apply results are reconciled
against approval status and config history; if the evidence is inconclusive, the proposal is marked for
manual reconciliation and is not retried. These are callable governance components; the runtime does not yet
generate proposals or automatically invoke this lifecycle. The current simulator contract does not document
apply idempotency after a lost response, so the controller never retries an ambiguous apply.

Proposal state is stored in `governance_proposals` from migration `0005_governance_proposals`; submission and
apply intents reuse `outbox_operations`. Governance audit entries retain the exact diff, payload hash, reason,
evidence, approval identity and reconciled outcome. No customer-facing change has an auto-apply path in this
chunk.

### Statistical comparison (Chunk 6)

`controller/statistics/` contains deterministic person-cluster estimation, Holm correction and explicit
superiority/non-inferiority sample-size planning. `controller/experiments.py` reads valid, persisted KPI facts at
a fixed time window and event cutoff, persists one reproducible decision per workflow config version, and only
submits a matching safe winner through Chunk 5 approvals. Pickup and engagement stay separate. The evaluator
excludes people observed in multiple tested arms because the simulator does not promise person-stable variant
assignment. It does not assign traffic or run inside the runtime loop; callers must provide the baseline,
allocation, clustering, power and imbalance assumptions. Missing assumptions or workflow-configured guardrails
fail closed. See [docs/DECISION_POLICY.md](docs/DECISION_POLICY.md) for formulas, stopping rules and limits.

Experiment decisions and input hashes are stored in `experiment_decisions` (migrations `0006` and `0007`) and
audited atomically. A workflow config version admits one fixed comparison family of up to three challengers, to
prevent new IDs or later cutoffs from repeatedly testing the same version.

### Repository layout

```text
controller/
  analytics/       historical queries, durable fact derivation, KPIs, and cohorts
  clients/          simulator HTTP client
  config/           runtime settings
  errors/           typed application errors
  experiments.py    fixed-window evidence evaluation and governed proposal integration
  governance/       proposal validation, approval lifecycle, apply reconciliation
  observability/    structured logging
  statistics/       cluster estimation, sample planning and multiplicity
  runtime/          process lifecycle
  storage/          database models, async store, audit log
  ingestion/        descriptor validation, NDJSON parsing, replay, and advance recovery
migrations/         Alembic environment and versioned revisions
tests/
  unit/             isolated controller and ingestion tests
  unit/governance/  proposal payload and lifecycle transition tests
  unit/analytics/   fact, KPI, and cohort tests
  unit/statistics/  bootstrap, power and Holm tests
  integration/analytics/  PostgreSQL analytics and lead/call join tests
  integration/storage/  PostgreSQL-backed persistence tests
  integration/ingestion/ PostgreSQL-backed ingestion/recovery tests
  integration/governance/ PostgreSQL and mocked HTTP lifecycle/recovery tests
  integration/experiments/ PostgreSQL-backed governed proposal tests
  contract/         simulator contract tests
```

**Ground rules for the simulator**
- Your controller may only know what the HTTP API tells it. Reading `runs/`, importing `sim`, or parsing
  scenario files (including fields like `base_pickup` or `did_base_health`, which the API never returns) is
  disqualifying. Our hidden scenarios share the schema and
  differ in truth, incidents and policy. A controller tuned to the public truth will do badly on them.
- `./run.sh <SIM_URL>` must start your controller and run until the scenario finishes, with no manual steps.
  We will run it against hidden scenarios on a 4-vCPU / 8 GB Linux box. Docker is fine. Say so in `run.sh`.
- Any language and any storage. Every dependency you add is something you'll have to defend
  (see `stakeholders/03`).

## 5. Constraints that are not up for negotiation

These come from the platform and our regulated clients. They don't move, whatever the memos say.

1. **The governance boundary is absolute.** Every change your controller makes goes through `/approvals` or a
   policy-permitted auto-apply. Supervisor edits are a fact of life. You don't overwrite them silently.
2. **The business owns whether and when to call.** Cadence, dialling windows and capacity are out of scope.
   You decide *who* gets called in a given hour and *how* the agent shows up.
3. **Pickup and engagement stay separate KPIs.** Optimise a workflow's `primary` KPI and respect its
   `guardrail`.
4. **Winner selection is statistical and deterministic.** An LLM may propose what to try. It never decides
   what won.
5. **Act on populations, not anecdotes.** Detection is threshold-based. No change is triggered by one call.
6. **Every decision is auditable** from your own store. The simulator's logs don't count.
7. **Credits are finite.** LLM and validation calls cost credits. Running out mid-incident is a failure.
8. **Crash safety.** Kill your controller at any point, restart it, and it resumes. It must not double-apply,
   lose a pending approval, or forget what it learned.

## 6. Scope: you can't do all of it

Here is everything someone has asked for. Done well, it's **2–3× what fits in the time budget.** Choosing is
part of the assignment. Don't spread yourself thin. We'd rather see five capabilities done properly than
fifteen sketched.

| # | Capability |
|---|---|
| C1 | Event ingestion at load-scenario scale: backlog-safe, idempotent, restartable |
| C2 | Lead/feature joins and **one shared cohort object** used by detection, experiments and the supervisor view |
| C3 | Bootstrapping from history before D0 |
| C4 | Call-time optimisation: per cohort and/or per person |
| C5 | Caller-ID health: detect degradation, rotate, explore safely |
| C6 | Variant experimentation on welcome message and voice: design, minimum sample sizes, multiple comparisons |
| C7 | Winner promotion, **decay detection and rollback** |
| C8 | Hallucination incident response: detect → LLM suggest → validate → propose with evidence → verify after apply |
| C9 | Governance client: proposal lifecycle, batching under approver fatigue, timing against approver hours, expiry/lapse, stale-base re-proposal, auto-apply within policy |
| C10 | Honest impact scores, affected-lead counts, and dry runs against historical cohorts |
| C11 | A consolidated supervisor view (API or minimal UI): one place, not fifty alerts |
| C12 | A queryable decision/audit store: *"why did X change"* |
| C13 | Crash recovery, demonstrated: kill mid-run, restart, show the run completes correctly |
| C14 | Observability of the loop itself: ingest lag, decisions/hour, credit burn, alerts an on-call engineer can act on |
| C15 | Fair allocation of credits and approver attention across tenants |

**C8 incident response:** `controller.incidents.process_population_incidents` watches documented supervisor
prompt changes, waits for a fixed population QA evidence gate, then uses credit-checked suggestions, simulator
validation and the Chunk 5 approval/apply lifecycle. It records one post-apply monitoring snapshot and makes a
new governed proposal only when the evidence gate supports it. It never auto-rolls back a prompt. See
[`docs/INCIDENT_POLICY.md`](docs/INCIDENT_POLICY.md) for the engineering evidence defaults and limitations.

**Floor (mandatory):** the controller runs public_01 and public_02 end to end with no manual steps, keeps an
audit trail (C12, even a minimal one), never breaks the constraints above, and `SUBMISSION.md` is complete.
Everything beyond that is your call. Write down what you cut and why.

## 7. Stakeholders

`stakeholders/` holds six memos: Sales, a client's Compliance head, Platform/SRE, a client operations
supervisor, Product, and the CTO. Each is reasonable on its own. Together they contradict each other, and
some assume things about the system that aren't true. **Check their claims against the actual workflow
policies.** In `SUBMISSION.md`, cover each one: what you accepted, what you pushed back on, and the reply
you'd actually send them.

## 8. Deliverables

A git repository (private is fine; share access with the email on the form) containing:

1. **Your controller**, with `run.sh`.
2. **`SUBMISSION.md`**, from the template. This is where most of the evaluation happens.
3. **`reports/`**: `sim grade` output for public_01 and public_02, plus your load-test numbers for public_03.
4. **Crash-recovery evidence**: a script or log showing a kill/restart mid-run and a correct finish.
5. **Commit history** that shows how your thinking progressed. Small, honest commits beat one squash.
6. *Optional:* a walkthrough video of 5 minutes or less.

## 9. How we evaluate

| Weight | What | How |
|---|---|---|
| 25% | **Outcomes on hidden scenarios** | We run `run.sh` against 2 hidden scenarios. We look at lift versus baseline on the primary KPI (with the guardrail respected), harmful changes applied, incident time-to-fix, decay rollback, and governance hygiene. No points for lift bought by breaking a constraint. |
| 25% | **Design and reasoning** | Is the learning loop statistically sound? Are cohorts sized sensibly? Are false positives handled? Does the design hold at the scale envelope? |
| 20% | **Scope and stakeholder judgement** | Did you choose what mattered, say no clearly, and catch the memos' wrong assumptions? |
| 20% | **Engineering** | Correctness, crash safety, idempotency, throughput on the load scenario, tests where they matter, and code a teammate could own. |
| 10% | **Communication** | Could a new engineer run, debug and extend this from your documents alone? |

A 60-minute review follows. We'll pick one decision your controller made on a hidden scenario and ask you to
explain it from your audit store, live. We'll change a constraint and ask what breaks. We'll ask about the
thing you cut.

## 10. Rules

- **AI tools are allowed, and we expect you to use them.** You must be able to defend every line and every
  decision without them in the review. Generic, unadapted output is easy to spot and scores poorly.
- If something is ambiguous, **state your assumption in `SUBMISSION.md` and proceed**. Don't wait for an
  answer. Questions are welcome but won't block you.
- Don't share the assignment or your solution publicly.
