# Reliability model

The prototype treats restart and ambiguous service responses as normal failure
conditions. Its safety model is built around durable local intent, ordered
checkpoints, repeatable ingestion, and evidence-based remote reconciliation.

## Local durability

- Event uniqueness is enforced by the event primary key. A repeated event ID
  with identical stored content is tolerated; a replay with different chunk,
  kind, or payload is rejected.
- Event rows and the descriptor checkpoint commit in one PostgreSQL
  transaction.
- Derived fact rows and the analytics cursor commit in one transaction.
- Governance proposals, outbox operations, incidents, experiment decisions,
  and audit evidence are persisted before they are needed for restart
  continuation.
- PostgreSQL migrations define the durable schema. Startup checks the store
  and applies Alembic revisions.

## Remote effects

A database transaction cannot include the remote API. For advances, approvals,
and applies, the controller persists an intent first and reconciles after an
ambiguous response using documented reads. It does not claim exactly-once
remote delivery. If remote state remains inconclusive, it stops the affected
operation for manual review.

## Availability trade-offs

The process is serial at ordered event and fact cursor boundaries. This limits
write concurrency but keeps checkpoint ordering and recovery understandable.
There is no broker, independent worker fleet, failover process, HTTP health
server, or alert service in this repository. Process supervision and database
availability are deployment responsibilities that remain to be designed.
