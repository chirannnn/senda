# LinkedIn announcement draft

I’ve been building Senda, a prototype exploring a practical problem in
outbound operations: how do teams improve a workflow without losing the
evidence, context, and human review behind each change?

The engineering approach starts with durable observations, explicit outcome
definitions, and decisions that can be reconstructed later. Pickup and
engagement remain separate measures. Experiment evaluation is deterministic;
sensitive changes still pass through an approval lifecycle. When a remote
write is ambiguous, the system reconciles from available state instead of
assuming it succeeded.

The current repository is a simulator-backed controller prototype, not a
hosted product or customer deployment. It includes PostgreSQL-backed event
ingestion and recovery, fact projection, a narrow population incident flow,
approval reconciliation, and operational telemetry. Production identity,
tenant isolation, UI, deployment, and full-workload capacity validation remain
open work.

I’m interested in feedback from people who operate outbound programs,
especially on outcome definitions, approval workflows, and the evidence teams
need before changing a live workflow. Nothing here should be read as a claim
of customer traction or production availability.
