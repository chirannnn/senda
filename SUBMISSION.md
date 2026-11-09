# Outcome-First Agent Loop — Submission

**Name:** CHIRANJEEVI D · **Date:** 2026-11-09 · **Hours actually spent (be honest):** [NEEDS YOUR CONFIRMATION: enter actual hours] · **Language / storage:** Python 3.12 / PostgreSQL 18

> This submission follows the official `SUBMISSION_TEMPLATE.md`. Actual hours and external repository access remain pending; confirm the hours before submission.

## 0. TL;DR

The Controller ingests documented simulator events, stores durable KPI facts, and evaluates workflow changes through evidence and human approval.
public_01 finished unchanged (297,024 events, zero changes); public_02 applied one human-approved prompt change graded beneficial (four credits).
The original full public_03 run took 97.97 minutes. COPY staging improved isolated 1,000-row fixtures, but the 50-minute target remains unverified.
Automatic decay rollback, call-time and caller-ID optimization, a supervisor UI, and runtime-scheduled experiments were not built.
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

### 03 — Platform/SRE / Kabir

Accepted: real kill/restart evidence, idempotent local persistence, no new
broker/Redis, and resource measurement. Pushed back: declaring scale success
without a valid full measurement. The original one-day public_03 target failed;
the post-optimization 50-minute target is unverified. Alert delivery and a
10× capacity result are not implemented. **Reply:** “We demonstrated process
termination during a chunk transaction and pending approval, then restarted
against the same database and simulator. The original public_03 run took 97.97
minutes and failed the one-day target. COPY staging improved controlled
1,000-row persistence fixtures, but we have not rerun the full load, so I
cannot claim the 50-minute target. The runtime emits actionable state/metrics
but has no paging integration.”

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

### 06 — CTO / Aryamann

Accepted: auditable choices, no unnecessary stateful infrastructure, and
explicit scope cuts. The public run showed the workload and a meaningful
database persistence cost; current fixture gains are not full-load results.
Pushed back: declaring a 10× breakpoint without evidence. **Reply:** “The
Controller uses one PostgreSQL store and a single ordered writer path. The
original full public_03 run took 97.97 minutes; the largest later measured
costs were KPI fact SQL and event insertion. COPY staging improved isolated
1,000-row fixtures, but we have no post-change full run or valid 10× result.
The remaining capacity questions are full-workload database/WAL behavior and
the grading host's CPU/RSS profile. We deferred decay rollback, caller-ID
health, call-time optimization, a UI and cross-tenant fairness.”

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
  Incident QA uses a 30-distinct-person/75%-completeness engineering gate.
- **Experiment design:** one cohort, one changed dimension, one control and up
  to three declared challengers per config version. No automatic experiment
  assignment/scheduling exists. Welcome-by-voice interactions are not
  estimated; they require a separately declared design.
- **Winner selection:** deterministic person-cluster bootstrap and Holm
  correction, frozen time window/event cutoff and full planned family. The
  configurable 5-point MDE is an engineering default, not a business target.
  No LLM selects winners. Person crossovers are excluded because sticky arm
  assignment is not guaranteed.
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

The runtime does not implement approver-hours scheduling, batching under
fatigue caps, or policy-permitted auto-apply. It uses approval for prompt
changes. Proposal payloads carry diff, affected estimate, dry-run field,
impact score, rationale and evidence, but a general historical dry-run and a
validated common impact scorer are not complete. Supervisor edits change the
config version and prevent stale apply.

## 7. Incidents

In the recorded `public_02` run, supervisor prompt config version 2 for
`ks_cancel` was evaluated at event cutoff 234187. The population QA comparison
had 37 baseline people and 39 post-change people, 7.69% post-change
hallucination flags, no overlapping people, and one-sided cluster-bootstrap
`p=0.0695`. The flag is an imperfect proxy and the 0.10 threshold is an
engineering triage gate, not business significance. This nominal threshold is
not a measured false-positive rate; repeated monitoring and proxy-label
quality were not calibrated to estimate one.

The Controller observed 200 credits, spent 3 on one suggestion request, kept
allowlisted candidates, and validated P1 at `n=100`; pass rate was 98% against
the workflow's 97% floor, using 1 credit. Proposal
`incident-69d4b4d0595dd7d3131ef27e3a05ed5a` was approved as `apr_00001` and
applied at config version 3. Total spend was 4 credits. Post-apply QA and
engagement had only 11 paired people versus the 30-person gate; the snapshot
was insufficient and no corrective proposal was submitted. Full timeline and
grader evidence: `reports/public_02.md`.

## 8. Scale

The original full public_03 run processed 9,737,439 events/facts in 5,878.203
seconds (97.97 minutes), or 1,656.962 events/second. Wall time was 1,175.641
seconds per simulated day, missing the historical SRE threshold of under 600
seconds/day. Ingestion lag peaked at 181 simulated minutes; latest-event
persistence lag was 53.784 seconds. No experiment or decision loop ran, so
decision lag was not measured. Peak worker memory is unknown because the
retained sampler measured the launcher shim instead of the worker.

The later 720-minute profile took 3,697.404 seconds and processed 5,891,391
events including the full history backlog; it was not 10% of total event work.
Neither this profile nor the historical full run used the post-COPY build. At
10x scale, database write/WAL capacity is a plausible pressure point based on
pre-COPY stage timings, but it is not a measured failure. Measure per-workflow
throughput, database waits, WAL and lag before choosing partitioning. A future
partition design would need to retain ordered ingestion and independent,
durable workflow checkpoints; it has not been implemented or validated.
See `reports/FINAL_PERFORMANCE_REPORT.md` and
`reports/public_03_load_review.md` for the measurement limitations.
## 9. Reliability

`reports/crash_recovery.md` records two real Controller process kills. During
partial chunk insertion, uncommitted rows and checkpoint rolled back; restart
reingested the chunk, then finished with 324,308 unique event IDs and
checkpoint 490. During pending approval, restart reconciled by proposal ID
without a second approval POST; the same approved config version 3 was applied
once. Integration tests additionally exercise duplicate/conflicting replay,
checkpoint atomicity, lost POST responses, repeated starts and manual-review
states.

GET requests use bounded retry behavior. Writes are not blindly retried after
ambiguous outcomes. Event reads can be replayed locally by unique event ID;
remote exactly-once delivery is not claimed. The Controller classifies
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
checkpoints, DB failure, excessive lag or resource exhaustion. Alert delivery
is not built into this CLI.

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

### public_03_load

The historical full benchmark processed 9,737,439 events in 97.97 minutes and
missed the SRE target. Its report is `reports/public_03_load.md`. The
50-minute target after COPY optimization remains unverified; fixture gains are
not full-run results.

## 12. Trade-offs & rejected alternatives

| Option | Why considered | Why rejected / what instead |
|---|---|---|
| Redis, broker, worker fleet | Throughput and distributed scheduling | No measured need justified the operational/recovery cost; one ordered PostgreSQL writer is used. |
| Direct config writes/auto-apply | Lower approval latency | Prompt changes use human approval; no direct config write path. |
| LLM winner selection | Easy variant ranking | Deterministic statistics select winners; LLM only supplies candidates in incident workflow. |
| Remove more KPI indexes/FKs | Reduce write maintenance | Existing analytics/provenance paths use them; no evidence supports removing them. |
| Increase batches or concurrent writers | Reduce per-page overhead | No representative batch sweep; cursors/checkpoints remain ordered and serial. |
| Decay rollback | Restore a former winner quickly | Not in the selected scope; incident correction uses a fresh governed proposal. |
| Full load rerun after COPY | Verify target | Not run in this final phase; full-run target remains unverified. |

## 13. Known weaknesses

- The 50-minute target is unverified; the last full run predates the latest
  storage optimizations.
- The SRE one-day target failed in the historical full run.
- Public_03 worker CPU/RSS from that full run are unknown because its sampler
  measured the launcher shim. Query-specific PostgreSQL waits are unavailable
  without `pg_stat_statements`; shared PostgreSQL configuration was not changed.
- Experiment evaluation requires an explicit caller-supplied plan and is not
  scheduled by runtime.
- Automatic decay rollback, caller-ID health, call-time optimization,
  supervisor view, approver-aware batching and tenant fairness are not built.
- Real simulator authentication/TLS requirements are undocumented; the
  contract says nothing about them.
- Dependencies are lower-bounded but not lock-file pinned.

## 14. If this went to production at Formi next month

1. Agree real API authentication, network/TLS, retention, and remote-write
   reconciliation contracts with platform owners.
2. Run the optimized build against a fresh full public workload and the actual
   grading resource envelope after disk/WAL preflight; validate all counters,
   checkpoints, CPU/RSS and the 50-minute criterion.
3. Add a production alert/export integration and dashboards for stale
   ingestion, stuck outbox/proposals, API/DB errors, resource budgets and
   credit burn.
4. Agree statistically justified experiment plans and a runtime scheduling
   policy before enabling automatic variant experiments.
5. Implement approver-hours/fatigue batching and a consolidated supervisor
   surface before scaling to many workflows.
6. Calibrate population QA criteria and consider decay/rollback only with
   explicit human policy and crash-safe approval semantics.

