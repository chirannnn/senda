# Feature catalog and implementation status

Status is based on current source, tests, and retained verification documents;
it is not a commitment to ship each item as a customer feature.

| Capability | Status | What is present | What is not established |
|---|---|---|---|
| API-backed runtime | Implemented and verified | CLI lifecycle, readiness, bounded HTTP client, signal-aware shutdown | Hosted service, authentication, tenant management |
| Event collection | Implemented and verified | Ordered history/live chunk ingestion, validation, durable checkpointing, replay conflict detection | Exactly-once remote delivery; independent production connector |
| KPI and history | Implemented and verified | Event-derived facts, separate pickup/engagement, time/config/cohort filters, historical joins | General-purpose reporting UI |
| Audit | Implemented and verified | Durable action/evidence records and bounded keyset queries | Customer-facing audit console and retention controls |
| Approval governance | Implemented and verified against the contract | Stable proposals, outbox intent, approval reconciliation, stale checks, safe apply | Production IAM integration and multi-approver workflow |
| Experiment evaluation | Implemented as callable logic | Person-cluster bootstrap, fixed cutoff, Holm correction, guardrail evaluation, durable decision | Traffic allocation, runtime scheduler, guaranteed person-stable arms |
| Population prompt incidents | Implemented in a narrow workflow | QA evidence, credit check, suggestion validation, governed change, post-apply monitoring | General incident product, automatic rollback/decay |
| Runtime telemetry | Implemented | Structured logs, heartbeat and bounded summary metrics | Metrics endpoint, alerting, SLOs and on-call integration |
| Performance persistence path | Implemented and fixture-tested | PostgreSQL COPY staging and set-based merge for facts/events | Verified optimized full-workload runtime |

## Current demonstrated scope

The prototype connects to the documented local HTTP simulator contract and
uses PostgreSQL for durable state. Its integration tests exercise storage,
recovery, analytics and governance behavior. It has no customer login,
organization boundary, UI, deployment packaging, or commercial service
operations. See the [production readiness review](../architecture/PRODUCTION_READINESS.md).
