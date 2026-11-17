# User journeys

These are target product journeys. The first is represented by the current
prototype; the others include steps that still require a customer-facing
product surface.

## Understand a workflow

1. An operator selects a workflow and time period.
2. The system shows event freshness, data quality, pickup, engagement, and
   relevant population breakdowns separately.
3. The operator can trace a summary to source observations and configuration
   versions.

The controller stores and queries relevant records, but it has no operator UI.

## Review a proposed change

1. A population-level signal or a declared experiment produces evidence.
2. The controller validates the candidate and records its basis.
3. A proposal is submitted through the service's approval workflow.
4. A human reviews the exact change and approves or rejects it.
5. The controller rechecks the current version, applies only the approved
   proposal, and reconciles an uncertain response.
6. Later observations are associated with the applied version.

The governance and narrow incident path are implemented against the local
simulator contract. A general operator experience and production identity
system are not implemented.

## Recover after interruption

1. The process restarts using the same database and connected service state.
2. It resumes from committed event and analytics checkpoints.
3. It inspects pending remote-operation intents and documented service state.
4. It continues only when evidence is consistent; unresolved remote ambiguity
   remains visible for manual review.

See [recovery](../engineering/RECOVERY.md) for the verified invariants and
limits.
