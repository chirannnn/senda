# Outcome-First Agent Loop — Submission

**Name:** CHIRANJEEVI D · **Date:** 2026-11-09 · **Hours actually spent (be honest):** Not formally tracked. · **Language / storage:** Python 3.12 / PostgreSQL 18

> Official evaluator-facing submission. Confirm actual hours before submission; repository access has not been shared.

## 0. TL;DR

- The Controller ingests documented simulator events, derives separate KPI facts, and changes customer-facing configuration only through human approval.
- `public_01` finished with no changes; `public_02` applied one approved prompt change graded beneficial, using four credits.
- The pre-optimization full `public_03_load` took **5,878.203 seconds (97.97 minutes)** for **9,737,439 events**; its database reached about **8.79 GiB**.
- On isolated 1,000-row fixtures, COPY staging lowered KPI-page elapsed time 86.6% and event-chunk elapsed time 68.2%; these are not full-run speedups.
- No optimized full run was performed; the 50-minute target remains unverified. Automatic decay rollback, caller-ID/call-time optimization, and runtime experiment scheduling remain unbuilt.

## 1. Assumptions

1. Simulator HTTP responses and `docs/SIMULATOR.md` are authoritative. Scenario files, simulator implementation and run directories are not Controller decision inputs.
2. Pickup and engagement stay distinct: `connected / dials` and `concluded / connected`. Business outcome is separate: concluded leads / uploaded leads.
3. The simulator does not guarantee person-stable variant assignment. People seen in multiple experiment arms are excluded; remaining estimates resample by person cluster.
4. Missing primary/guardrail policy, experiment assumptions, or remote evidence must fail closed. The controller does not invent business thresholds or claim an ambiguous POST succeeded.
5. The simulator contract does not document real-service authentication. Deployment authentication/TLS policy must be supplied by the service owner.
6. The final full load benchmark was not rerun after COPY staging. Isolated persistence microbenchmarks do not estimate full end-to-end runtime.

## 2. Scope decisions

| # | Capability | Built / Partial / Cut | Why | What it would take to finish |
|---|---|---|---|---|
| C1 | Event ingestion | Built | Ordered history/live ingestion, durable checkpoints, replay and crash recovery. | Re-run the permitted full load only after resource preflight. |
| C2 | Lead/feature joins and shared cohort | Partial | One versioned cohort definition and lead joins exist; no supervisor view. | Add the product surface if required. |
| C3 | History bootstrap | Built | History is discovered and ingested before live advances. | No known gap in selected scope. |
| C4 | Call-time optimization | Cut | Calling windows/cadence/capacity are business-owned and call-time optimization was out of scope. | Separate approved policy and evaluation plan. |
| C5 | Caller-ID health | Cut | No validated causal estimator or governed rotation workflow was selected. | Population estimator, evidence policy and public validation. |
| C6 | Variant experimentation | Partial | Deterministic evaluator, person-cluster bootstrap, Holm correction and governed proposal integration exist. | Runtime plan scheduling and traffic assignment are not implemented; explicit plan assumptions are required. |
| C7 | Decay and rollback | Partial | Approved winners can be proposed/applied through governance. | Automatic decay detection and rollback are explicitly deferred. |
| C8 | Population prompt incident | Built | Public_02 path: population QA, credit check, untrusted suggestion, validation, approval/apply, monitoring. | The final QA/engagement paired sample was insufficient; no post-change corrective proposal was justified. |
| C9 | Governance client | Partial | Durable proposal/outbox, approval reconciliation, state machine and safe apply are implemented. | Approver-hours/fatigue batching and policy-permitted auto-apply are not implemented. |
| C10 | Impact, affected leads, dry run | Partial | Proposal schema carries these fields and incident/experiment evidence. | A general reproducible historical dry-run and validated impact scorer are not implemented. |
| C11 | Supervisor view | Cut | A UI/API was excluded; durable audit queries were prioritized. | Build a read surface if required. |
| C12 | Queryable audit | Built | Durable, filterable, keyset-paginated audit records. | No known gap in the selected scope. |
| C13 | Crash recovery | Built | Real process-kill/restart evidence plus PostgreSQL recovery tests. | Remote writes remain bounded by simulator reconciliation APIs. |
| C14 | Observability | Partial | Structured JSON logs, heartbeats, bounded metrics and measurement runner. | No metrics server, alert routing or paging integration. |
| C15 | Multi-tenant fairness | Cut | Cross-tenant scheduling was outside the chosen effort. | Add a fair credit/approver scheduler with starvation tests. |

The selection principle was to build durable ingestion, auditable evidence,
approval safety and crash recovery before adding broader automation. See
`reports/REQUIREMENT_IMPLEMENTATION_AUDIT.md` for source and test references.

## 3. Stakeholders

### 01 — Sales / Nikhil

Accepted: show outcome rates and explain evidence clearly. Pushed back: blended
score, unvalidated persuasive prompts and bypassing approvals. The simulator's
workflow-specific policy is authoritative; a memo or prior account setting
does not establish that Kestrel or Northwind permits auto-apply. No
Controller auto-apply path is used. **Reply:** “We will report pickup,
engagement and business outcome separately. A candidate goes live only after
the workflow's evidence and approval requirements pass. We will not reuse a
prompt across accounts just because it worked in a demo. This is slower than a
same-day rollout when evidence or approval is pending, but it keeps the change
defensible for each client.”

### 02 — Compliance / Meera

Accepted: named human approval for customer-facing prompt changes, validation
evidence, traceable config versions and slow/safe decisions. The simulator
provides workflow policy and validation floor over HTTP; real-world legal
requirements beyond those fields remain a deployment input. Approver-hours
scheduling is not implemented. **Reply:** “Prompt changes are submitted with
the matching validation run and config version, and the approved payload is
the one applied. The audit store records evidence, actor, approval and outcome.
We do not currently schedule submissions around approver hours; we will not
claim that timing control until it is implemented. We also fail closed when a
base version is stale or remote apply state cannot be proven.”

### 03 - Platform/SRE / Kabir

Accepted: durable recovery, idempotent local effects, operational signals and
no unnecessary stateful infrastructure. Pushed back on: claiming capacity
without a representative run. **Reply:** "The pre-optimization full public_03
run processed 9.74 million events in 5,878.203 seconds (97.97 minutes), with a
final database size of about 8.79 GiB; it missed the one-day SRE threshold.
The pre-COPY profile isolated KPI fact and event insertion as the largest
measured database-call costs. COPY staging then reduced 1,000-row fixture
medians by 86.6% and 68.2%, respectively, and the optimized code passed 306
tests plus migration checks. Those tests and fixtures do not establish
full-run throughput. A second full run was not performed because the execution
window and available storage were insufficient for a representative
multi-million-event profile. The 50-minute target remains unverified."

### 04 — Client Ops / Anita

Accepted: preserve supervisor edits by checking current config/version before
apply, and do not treat safety validation as engagement evidence. Pushed back:
claiming request batching, approval timing or a single screen that do not
exist. The contract documents no notification endpoint. **Reply:** “A
supervisor edit changes the base version and makes an older proposal stale;
we do not silently overwrite it. The incident proposal includes its evidence
and rationale and still requires approval. We do not yet batch multiple
changes around your daily cap or send a notification before a running campaign
change, and there is no supervisor screen. I would treat those as required
product work before enabling broader automation.”

### 05 — Product / Tara

Accepted: separate KPIs, shared versioned cohort definitions, actual exposure
only, fresh evidence after expiry, and workflow-local changes. The statistical
plan uses cluster resampling, Holm correction and explicit sample assumptions.
Pushed back: per-person timing, UI and caller-ID health. **Reply:** “Pickup is
connected/dials; engagement is concluded/connected. We store the observed
cohort and variant with each fact. Experiment evaluation requires a fixed
window, cutoff, allocation and clustering assumptions; the runtime does not
invent them or schedule traffic. An expired proposal is not resubmitted as-is:
new evidence and a fresh proposal identity are needed. A general historical
dry-run and impact-ranking model remain incomplete.”

### 06 - CTO / Aryamann

Accepted: explainable decisions, measured database work and explicit scope
cuts. Pushed back on: a claimed 10x breakpoint or full-run improvement without
evidence. **Reply:** "The old full run took 97.97 minutes. A later pre-COPY
profile measured 2,094.723 seconds in KPI fact SQL calls and 575.626 seconds in
event insertion calls at the Controller database-call boundary; these are not
PostgreSQL executor-only timings and profile stages can overlap. I replaced
wide row-bound persistence on the two hot paths with PostgreSQL COPY staging
and set-based merges, preserving replay checks and transaction/checkpoint
boundaries. Isolated 1,000-row fixtures improved by 86.6% for a KPI page and
68.2% for an event chunk. We did not rerun the full workload, so no end-to-end
speedup or 50-minute result is claimed. Query-specific waits and the full-run
worker CPU/RSS remain unknown."

## 4. Architecture

```text
Simulator HTTP API
    -> SimulatorClient
    -> runtime lifecycle
       -> ordered event ingestion + advance outbox/reconciliation
       -> PostgreSQL raw events/checkpoints
       -> restartable KPI facts and analytics cursor
       -> population incident evaluation
          -> credit-checked suggestion -> validation
          -> proposal/outbox -> human approval -> exact approved apply
       -> audit records and structured metrics
```

PostgreSQL holds raw events, facts, checkpoints, proposals, outbox operations,
incident evidence, experiment decisions, cohorts, run records and audit rows.
On restart, the Controller replays missing chunks, resumes fact projection,
reconciles remote advance/approval/apply state, and halts unresolved ambiguity
for review. More detail and a Mermaid diagram are in `docs/ARCHITECTURE.md`.

## 5. The learning loop

- **Cohorts:** constructed from the HTTP-supplied workflow config and evaluated
  in ordered first-match order. Calls retain observed cohort/config/variant.
  No independent duplicate cohort schema is used.
- **Minimum evidence:** experiments calculate required trial counts from
  explicit baseline rate, allocation, mean cluster size, ICC, alpha, power,
  MDE and comparison count. There is no hidden universal business threshold.
  Incident QA uses a 30-distinct-person/75%-completeness engineering gate to
  avoid acting on sparse or incomplete QA evidence; it is an engineering rule,
  not a calibrated false-positive guarantee.
- **Experiment design:** the caller declares allocations and assumptions; the
  evaluator counts observed exposure only. It uses one cohort, one changed
  dimension, one control and up to three challengers per config version to
  limit traffic fragmentation. There is no runtime traffic assignment or
  exploration/exploitation scheduler. Welcome-by-voice interactions require a
  separately declared design and are not estimated here.
- **Winner selection:** a frozen time window, event cutoff and full planned
  comparison family prevent repeated unadjusted peeking. Deterministic
  person-cluster bootstrap and Holm correction use the caller's alpha and
  comparison count; no LLM selects winners. The configured 5-point MDE is an
  engineering default, not a business target. The false-winner bound follows
  declared statistical assumptions; an empirical false-positive rate was not
  measured. Person crossovers are excluded because sticky arm assignment is
  not guaranteed.
- **Decay/rollback:** automatic C7 decay detection and rollback are not
  implemented. A harmful incident monitor can only submit a new proposal
  through approval against current config; it does not roll back directly.
- **Call time/caller ID:** neither optimizer is implemented. The business owns
  cadence, dialling windows and capacity; caller-ID health lacks a validated
  causal estimator in this scope.
- **Guardrail:** the evaluator reads the actual workflow objective and
  configured non-inferiority margin. Missing or malformed guardrails block a
  proposal.

## 6. Governance

Proposal state progresses through draft/submitted/pending and then approved,
rejected or expired; an approved change may be applied, lapse, or become stale.
Proposal and apply intents are committed before remote writes. Lost responses
are reconciled via approval lookup, current config and config history; when
remote state remains ambiguous, the operation stops for manual review.

The simulator enforces approval TTL and apply windows; the Controller polls
within bounded limits and handles expired/lapsed decisions. It does not schedule
around approver hours or batch under fatigue caps. It has no auto-apply path
for customer-facing prompt changes. Expired or stale proposals are not replayed
as-is; a new evidence snapshot and proposal identity are needed. Proposal
payloads carry a diff, affected estimate, dry-run field, impact score, rationale
and evidence, but a general historical dry-run and validated common impact
scorer are not implemented, so those fields are not presented as calibrated
impact forecasts. Supervisor edits change the config version and prevent
stale apply.

## 7. Incidents

In the recorded `public_02` run, supervisor prompt config version 2 for
`ks_cancel` was evaluated at event cutoff 234187. The population QA comparison
had 37 baseline people and 39 post-change people, 7.69% post-change
hallucination flags, no overlapping people, and one-sided cluster-bootstrap
`p=0.0695`. The flag is an imperfect proxy and the 0.10 threshold is an
engineering triage gate, not business significance. This nominal threshold is
not a measured false-positive rate; repeated monitoring and proxy-label
quality were not calibrated to estimate one.

The Controller requested candidates only after population evidence passed its
triage gate, treated the response as untrusted, kept only allowlisted prompt
IDs, and deterministically selected the first allowed candidate, P1. It
validated P1 at `n=100`; pass rate was 98% against the workflow's 97% floor,
using 1 credit. Only the approved validated change could proceed to apply. Proposal
`incident-69d4b4d0595dd7d3131ef27e3a05ed5a` was approved as `apr_00001` and
applied at config version 3. Total spend was 4 credits. Post-apply QA and
engagement had only 11 paired people versus the 30-person gate; the snapshot
was insufficient and no corrective proposal was submitted. Full timeline and
grader evidence: `reports/public_02.md`.

## 8. Scale

### A. Original full-run baseline (before the persistence optimizations)

The completed `public_03_load` run advanced all 7,200 simulated minutes and
persisted 9,737,439 events and valid facts in **5,878.203 seconds (97.97
minutes)**. This was the pre-optimization full benchmark. PostgreSQL grew from
8,279,743 bytes to 9,441,285,823 bytes, or about **8.79 GiB** at the end. The
SRE threshold of under 600 wall seconds per simulated day failed at 1,175.641
seconds/day. Its final event checkpoint was 267 and analytics cursor was
9,737,439. The saved whole-run CPU/RSS measurements are invalid: the sampler
followed the Windows Python launcher rather than the worker, so full-run worker
CPU and peak memory are unknown.

### B. What the earlier stage profile identified

The later 720-minute profile ran before the final COPY changes. It separated
these accumulated stages:

| Measured stage | Elapsed time | What the timer represents |
|---|---:|---|
| KPI fact insert `execute` | 2,094.723 s | Largest measured persistence operation |
| Event insert `execute` | 575.626 s | Second largest measured persistence operation |
| Ordered event-page reads for fact projection | 134.315 s | Source reads from stored event pages |
| Fact-page transaction commits | 73.348 s | Commit time, much smaller than fact insert calls |
| Python fact projection | 47.694 s | `derive_fact()` projection work |
| Workflow-reference upserts | 11.560 s | Batched reference writes |
| Fact payload construction | 10.255 s | Building database records |
| Fact cursor updates | 6.566 s | Durable analytics cursor writes |

The insert/read/commit timings are elapsed time measured around Controller
database calls, not pure PostgreSQL executor time. Stage totals accumulate
across observations and can be nested; they must not be added to predict wall
runtime. Python projection was measured separately and was small compared with
the two insertion stages. PostgreSQL counters showed reads, writes and WAL,
but they do not attribute time to a particular query. `pg_stat_statements` and
query-specific wait data were unavailable, so the profile did not prove how
much KPI-call time was PostgreSQL execution versus driver, serialization,
index, storage or wait time. The stage totals are recorded in
`reports/public_03_10pct_20261109_metrics.json`; the narrative analysis is in
`reports/PERFORMANCE_OPTIMIZATION_RECOMMENDATIONS.md`.

### C. Changes implemented on the measured persistence paths

- **KPI facts (`386cfa5`):** `DatabaseStore.commit_kpi_page()` copies bounded
  fact records into a transaction-local PostgreSQL staging table, then merges
  with set-based `INSERT ... SELECT ... ON CONFLICT (source_event_id)
  DO NOTHING`. Workflow references, facts and the analytics cursor remain in
  the same page transaction. The source-event uniqueness rule remains active.
- **Streamed events (`005c1a7`):** event rows are copied to a transaction-local
  staging table and merged set-wise with `ON CONFLICT (event_id) DO NOTHING`.
  The affected-row count avoids collecting returned IDs for fresh inserts. A
  single bounded conflict lookup runs only when some IDs were already present;
  it compares event ID, chunk, kind and JSONB payload and rejects different
  replay content. Event writes and the descriptor checkpoint remain atomic.
- **Fewer round trips (`8f68b9a`, `10c98f9`, `99c8ad9`):** fact-page workflow
  references are batched, fresh streamed-event readback is avoided, and the
  streaming path avoids returning an ID collection when only an inserted-row
  count is needed. Existing replay validation still runs for conflicts.
- **Index maintenance (`3ef4344`):** removed the unused
  `(kind, chunk_number)` event index after the retained database showed zero
  scans and about 39.37 MB of index size; the chunk-only index was retained.
  This reduces index storage and maintenance work in principle, but no
  throughput improvement was measured from this index change.
- **Migration safety (`0009_drop_unused_event_idx`, `344c4c5`):** the index
  migration uses concurrent DDL and has a concurrent downgrade that restores
  the index. Startup migration transaction handling was fixed for concurrent
  DDL. Upgrade/downgrade and repeated-startup behavior were verified.
- **Measurement support (`e2f20de`, `ba01164`, `ca5961c`):**
  stage timers separate projection, reads, inserts, workflow writes, commits
  and cursor/checkpoint work. PostgreSQL database/WAL counter snapshots add
  context without claiming query-level attribution.

The set-based paths preserve the existing unique constraints, foreign keys,
indexes, event ordering and page/chunk transaction boundaries. KPI pages still
advance their cursor atomically with fact writes; event checkpoints advance
atomically with the event chunk. Exact event replays remain idempotent, and
conflicting replays remain errors.

### D. Controlled PostgreSQL fixture measurements

Five alternating 1,000-row/page or 1,000-event/chunk trials were run against
isolated PostgreSQL fixtures with the same schema and transaction work for each
path. The KPI rows used representative call/lead facts across four workflows;
event chunks used the same 500-row batch setting and checkpoint transaction.
These are small microbenchmarks, not public-workload runs.

| Operation | Previous path | Optimized path | Lower fixture elapsed time |
|---|---:|---:|---:|
| KPI page | 356.699 ms | 47.685 ms | **86.6%** |
| Event chunk | 74.300 ms | 23.658 ms | **68.2%** |

The KPI page included the workflow upsert, fact insert, cursor update and commit.
Its prior fact SQL median was 281.374 ms with a 2.357 ms commit; the COPY path
used about 5.7 ms for COPY plus a 26.049 ms merge, with a 2.447 ms commit. The
event chunk included two 500-row batches and the checkpoint transaction. Its
prior event SQL median was 58.068 ms with about a 1.178 ms commit; the COPY
path took about 10.1 ms for COPY plus merge, with about a 2.1 ms commit. Paired
`EXPLAIN (ANALYZE, BUFFERS, WAL)` fixture probes also measured server-side
INSERT execution at 52.377 ms versus 30.425 ms for the COPY-stage merge on
1,000 rows. Full-page-image counts differed and WAL deltas were cluster-wide,
so no WAL reduction is claimed; these probes do not establish production-scale
waits or workload throughput. Commit time was similar in the KPI trials; the
observed gain was in the persistence SQL path.

These reductions show the implementations are more efficient for the tested
fixtures. They are not end-to-end speedups and must not be multiplied by event
count or applied to the old wall time to project a new runtime. The qualitative
expectation is less database-call work on comparable persistence pages; the
actual full-run impact is unknown.

### E. Earlier 720-minute profile (pre-COPY), not a post-optimization result

This profile covered **720 of 7,200 simulated minutes** (10% of simulated
time), but the simulator supplied all history first: **5,537,379 historical
events + 354,012 live events = 5,891,391 total events**. That is about 60.5%
of the full event count, not 10% of total event work. It took **3,697.404
seconds (61.62 minutes)**; the timed interval including final replay and
verification took 3,724.094 seconds. The simulator stopped at minute 720 with
`finished=false`; the remaining 90% was not run. The result predates the final
COPY optimizations and is historical workload/correctness evidence only. Its
raw run log, samples and extracted metrics are retained at
`reports/public_03_10pct_20261109_controller_run.jsonl`,
`reports/public_03_10pct_20261109_samples.jsonl`, and
`reports/public_03_10pct_20261109_metrics.json`. The original full-run log and
samples remain at `reports/public_03_load.jsonl` and
`reports/public_03_load_samples.jsonl`.

At this checkpoint the database contained 5,891,391 events and facts, all
valid, with zero invalid, incomplete or unsupported facts. The simulator
checkpoint was 35, the analytics cursor was 5,891,391, and replaying the final
committed chunk inserted zero rows. Pickup, engagement and business outcome
were calculated separately. These checks support the observed ingestion and
persistence state; they do not measure post-COPY performance.

### F. Post-optimization correctness verification and full-run limitation

The optimized implementation passed **306 tests** against isolated PostgreSQL:
**214 unit/contract tests and 92 integration tests**. Coverage included COPY
persistence, exact duplicate and conflicting replay, rollback, event/checkpoint
atomicity, KPI/cursor atomicity, migrations, and subprocess crash recovery. The
Alembic migration was downgraded from 0009 to 0008 and upgraded to head;
`alembic check` reported no schema drift. Ruff, formatting, compileall and
`git diff --check` passed. These results verify tested correctness and schema
behavior; they are not performance measurements.

The optimized implementation was fully verified through isolated PostgreSQL
persistence measurements and the complete automated test suite. A second full
`public_03_load` run was not performed because the available execution window
and storage capacity were insufficient for a representative multi-million-
event run. The previous full profile's database alone reached about 8.79 GiB.
Consequently, optimized end-to-end runtime and the **50-minute target remain
unverified**. No projected runtime or full-run percentage improvement is
claimed.

## 9. Reliability

`reports/crash_recovery.md` records two real Controller process kills. During
partial chunk insertion, uncommitted rows and checkpoint rolled back; restart
reingested the chunk, then finished with 324,308 unique event IDs and
checkpoint 490. During pending approval, restart reconciled by proposal ID
without a second approval POST; the same approved config version 3 was applied
once. No committed event was lost; the in-flight chunk and its checkpoint
rolled back together and the chunk was replayed. Event IDs plus stable proposal
and approval IDs support duplicate detection and remote reconciliation.
Integration tests additionally exercise duplicate/conflicting replay,
checkpoint atomicity, lost POST responses, repeated starts and manual-review
states.

Final isolated PostgreSQL verification used database
`formi_perf_verify_20261109`: `pytest -q tests/unit tests/contract` passed
**214 tests**, `CONTROLLER_TEST_DATABASE_URL=<isolated DB> python -m pytest -q
tests/integration` passed **92 tests**, and `pytest -q` passed **306 tests**.
Alembic downgrade from 0009 to 0008, upgrade to head, and `alembic check`
passed with no schema drift. `ruff check .`, `ruff format --check .`,
`python -B -m compileall -q controller tests scripts migrations`, and
`git diff --check` passed. PostgreSQL integration tests covered COPY
persistence, duplicate and conflicting replay, rollback, event/checkpoint
atomicity, repeated startup migrations, and subprocess crash recovery.

GET requests retry only the documented retryable set (408, 429 and 5xx) with
bounded backoff. Ordinary 4xx responses are surfaced as HTTP errors. POST
writes are never blindly retried; transport failures and 5xx results are treated
as ambiguous and reconciled where documented APIs permit it. Event reads can
be replayed locally by unique event ID; remote exactly-once delivery is not
claimed. The Controller classifies
nonretryable HTTP errors, halts when required data cannot be reconciled, and
keeps a durable outbox/audit record.

## 10. Audit & observability

The audit query API is `AuditLog.query(AuditQuery(...))`. For the public_02
proposal, the audit store was queried read-only in the retained isolated
database by proposal ID. The real record sequence included:

| Time (UTC) | Action | Actor | Evidence |
|---|---|---|---|
| 2026-11-08 13:28:10 | `approval_submission_prepared` | controller | base v2; prompt diff P2→P1; validation `val_00001`, 0.98 vs 0.97 floor |
| 2026-11-08 13:28:10 | `approval_reconciled` | controller | approval `apr_00001`, pending |
| 2026-11-08 13:28:11 | `approval_status_observed` | simulator_approver | approved |
| 2026-11-08 13:28:11 | `approval_apply_prepared` | controller | approval/base v2 rechecked; exact diff recorded |
| 2026-11-08 13:28:11 | `approval_apply_reconciled` | controller | outcome applied; matching config v3 history |

The complete evidence includes proposal hash, incident comparison, validation,
approval identity and matching config history. Query implementation and
filters are in `controller/storage/audit.py` and tested in
`tests/integration/storage/test_audit_queries.py`.

The runtime emits JSON records, per-advance heartbeats and final metrics for
ingestion lag, API errors/latencies, event/fact counts, credits and rates. In a
production deployment, page on unresolved remote ambiguity, stalled
checkpoints, DB failure, sustained excessive lag or resource exhaustion. Do
not page on a bounded retry that recovers, an approval still pending within its
TTL, or a KPI fluctuation without sufficient evidence. These are proposed
operational rules; alert delivery and paging are not built into this CLI.

## 11. Results

### public_01_dev

| Workflow | Conclusion rate | Delta | Pickup | Delta | Engagement | Delta | Engagement gap captured | Hallucinated calls (actual/base) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ks_cancel | 0.333 | +0.0% | 0.238 | +0.0% | 0.491 | +0.0% | 0% | 47 (47) |
| nw_kyc | 0.364 | +0.0% | 0.242 | +0.0% | 0.591 | +0.0% | 0% | 21 (21) |
| nw_renewal | 0.420 | +0.0% | 0.263 | +0.0% | 0.546 | +0.0% | 0% | 42 (42) |
| ALL | 0.368 | +0.0% | 0.248 | +0.0% | 0.530 | +0.0% | 0% | 110 (110) |

No changes were applied, so outcomes matched the no-op baseline; no optimization
claim follows from this scenario. The grader artifact is `reports/public_01.md`.

### public_02_incidents

| Workflow | Conclusion rate | Delta | Pickup | Delta | Engagement | Delta | Engagement gap captured | Hallucinated calls (actual/base) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ks_cancel | 0.257 | +4.0% | 0.181 | -1.4% | 0.500 | +5.0% | 29% | 52 (330) |
| nw_kyc | 0.374 | -0.3% | 0.241 | -0.3% | 0.592 | +0.0% | 0% | 33 (31) |
| nw_renewal | 0.426 | -0.2% | 0.259 | -1.0% | 0.552 | +0.8% | 5% | 44 (55) |
| ALL | 0.338 | +1.2% | 0.220 | -1.0% | 0.541 | +2.1% | 13% | 129 (416) |

The grader rated the one approved `ks_cancel` prompt change beneficial
(engagement delta +0.102 for that change). At the scenario aggregate level,
engagement and conclusion improved while pickup fell; `nw_kyc` and
`nw_renewal` also had small negative conclusion deltas. These are mixed
observed results, not proof that the controller change caused every movement.
The post-apply paired evidence was too small to justify another proposal. In a
next iteration I would keep the KPIs separate, require a powered follow-up and
make the configured guardrail outcome explicit before broader rollout. The
grader table and incident timeline are in `reports/public_02.md`.

### public_03_load and persistence results

The only completed full workload was the **pre-optimization** run: 9,737,439
events in 5,878.203 seconds (97.97 minutes), with a final database size of
about 8.79 GiB. The later 720-minute profile took 3,697.404 seconds and
processed 5,891,391 events including the full history backlog; it was **before
COPY staging** and covered about 60.5% of the full event count.

| Isolated fixture operation | Previous median | COPY/set-based median | Fixture elapsed reduction |
|---|---:|---:|---:|
| KPI page | 356.699 ms | 47.685 ms | 86.6% |
| Event chunk | 74.300 ms | 23.658 ms | 68.2% |

These are controlled database fixture results, not full-workload performance.
The optimized build has not completed a new full `public_03_load` run, so its
end-to-end runtime is unknown and the 50-minute target remains unverified. The
full results and limitations are detailed in Section 8 and
`reports/FINAL_PERFORMANCE_REPORT.md`.

## 12. Trade-offs & rejected alternatives

| Option | Why considered | Why rejected / what instead |
|---|---|---|
| PostgreSQL COPY staging plus set-based merge | The pre-COPY profile measured 2,094.723 s in KPI insert calls and 575.626 s in event insert calls. | Selected after isolated alternating fixtures showed 86.6% and 68.2% lower page/chunk elapsed time; retains unique conflicts, replay checks and transaction boundaries. Full-run impact remains unknown. |
| Larger batches or concurrent writers | Could reduce per-call overhead or overlap independent work. | Deferred: no representative batch sweep or evidence of safe spare database capacity. Ordered cursors/checkpoints and bounded memory take priority. |
| Remove additional indexes/constraints | Could lower write maintenance. | Rejected without query-plan evidence. Only the unused event composite index with zero recorded scans was removed; no throughput gain is claimed for that change. |
| Redis, broker or worker fleet | Potential parallel throughput and multi-tenant scheduling. | Not justified by available measurements; adds operational and recovery complexity. Existing ordered PostgreSQL path is retained. |
| Direct config writes or auto-apply | Lower approval latency. | Rejected for customer-facing prompt changes; human approval, exact approved payload and config-version checks remain mandatory. |
| Full post-optimization benchmark | Required to verify the under-50-minute objective. | Not run because available execution time and storage were insufficient for a representative new multi-million-event profile. Fixture results are not substituted for it. |

## 13. Known weaknesses

- The 50-minute full-workload target remains unverified. No full run was done
  after COPY staging; fixture reductions cannot be projected to end-to-end time.
- The historical full run missed the SRE one-day target (1,175.641 s/day vs.
  under 600 s/day). The 720-minute partial profile is pre-COPY and is not 10%
  of total event work.
- Pre-COPY SQL timers were measured at the Controller database-call boundary,
  not solely inside PostgreSQL. Query-specific waits were unavailable without
  `pg_stat_statements`; no shared PostgreSQL configuration was changed.
- Whole-run worker CPU and peak RSS from the original full benchmark are
  unknown because its sampler measured the launcher shim. The partial profile
  does not substitute for a full-run measurement.
- The unused-index removal did not have an isolated throughput measurement;
  it reduced index storage/maintenance only as an expected effect.
- No large batch sweep or concurrency experiment supports higher defaults or
  parallel writers. The code keeps ordered, serial checkpoint ownership.
- Runtime experiment scheduling, automatic C7 decay rollback, caller-ID and
  call-time optimization, a supervisor UI, approver-aware batching and
  cross-tenant fairness are not implemented.
- Simulator authentication/TLS requirements are undocumented; production
  deployment policy remains an owner input.

## 14. If this went to production at Formi next month

1. **Days 1-2:** confirm simulator authentication/TLS, retention, approval
   policy and remote ambiguity handling with platform and compliance owners.
2. **Days 2-4:** provision a fresh isolated simulator/database and verify disk
   headroom for a multi-million-event profile; the previous full database
   reached about 8.79 GiB. Schedule a full optimized run only when execution
   time and storage allow a representative completion.
3. **Days 5-7:** during that run, collect worker-tree CPU/RSS, stage timings,
   PostgreSQL execution/wait evidence and database/WAL counters. Verify all
   event/fact counts, replay behavior, checkpoints and separate KPI denominators.
4. **Days 8-9:** compare completed full-run wall time against 3,000 seconds.
   Until then, treat COPY fixture reductions as local persistence evidence,
   not an end-to-end capacity claim.
5. **Days 8-11:** add production metrics export, alert routing and operator
   runbooks for stalled checkpoints, unresolved remote writes, database
   failures, lag and resource exhaustion.
6. **Days 10-14:** agree explicit statistical and approver-scheduling policies
   before enabling runtime experiment scheduling or broader multi-tenant
   automation.
