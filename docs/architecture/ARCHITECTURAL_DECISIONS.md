# Architectural decisions

This record captures decisions visible in the current implementation and
repository evidence. It is not a claim that every choice is final for a
hosted product.

## ADR 1: Keep raw observations and derived facts separate

- **Problem:** reporting and evaluation need normalized additive measures,
  while recovery and provenance need the original event envelope.
- **Alternatives:** derive every query from raw JSON, or store only summaries.
- **Decision:** persist `simulator_events` and one source-linked `kpi_facts`
  row per event.
- **Reason:** raw events support replay and investigation; facts support
  typed validation, indexed filtering, and bounded aggregation.
- **Trade-off:** duplicated storage and write work; migration and fact cursor
  recovery are required.
- **Evidence/limit:** schema, source-event FK/unique key, and transaction tests
  exist; this does not make the store an immutable audit archive.

## ADR 2: Use PostgreSQL transactions and ordered checkpoints

- **Problem:** crashes between downloaded events and persisted progress could
  lose or duplicate local effects.
- **Alternatives:** in-memory cursors, per-row commits, or a message broker.
- **Decision:** commit a full descriptor and its checkpoint together; commit
  a fact page and its analytics cursor together.
- **Reason:** a restart can replay from the last committed boundary, and
  failure rolls back both data and progress.
- **Trade-off:** a descriptor/page is the atomic unit and writers stay ordered.
- **Evidence/limit:** PostgreSQL integration and crash recovery tests cover
  selected failure windows; distributed recovery still depends on remote
  service evidence.

## ADR 3: Use COPY staging for bounded hot-path batches

- **Problem:** row-oriented multi-value inserts had high elapsed time in
  isolated persistence comparisons.
- **Alternatives:** retain multi-row INSERT, change indexes, or introduce
  parallel writes.
- **Decision:** COPY rows to a transaction-local staging table, then perform a
  set-based `INSERT ... SELECT ... ON CONFLICT` merge.
- **Reason:** fixture measurements favored the stage/merge path, while source
  uniqueness, foreign keys, conflict validation, and transaction boundaries
  remain in place.
- **Trade-off:** database-specific implementation and staging SQL complexity;
  temporary-table creation has cold-path overhead.
- **Evidence/limit:** small isolated fixture measurements only; full-workload
  throughput after the change is not measured.

## ADR 4: Keep ordered work serial and remote writes explicit

- **Problem:** event checkpoints and analytics cursors must advance in order;
  remote HTTP effects cannot share PostgreSQL transactions.
- **Alternatives:** concurrent writers, automatic POST retries, or external
  queue infrastructure.
- **Decision:** serial ordered persistence, durable outbox intent before
  writes, and bounded reconciliation rather than blind retry.
- **Reason:** this preserves a simple recovery proof under uncertain remote
  outcomes.
- **Trade-off:** lower potential parallel throughput and manual handling of
  unresolved ambiguity.
- **Evidence/limit:** state-machine/recovery tests exist; no capacity or
  horizontal scale claim is made.

## ADR 5: Keep people and approvals in control

- **Problem:** statistical or generated candidates must not directly change a
  customer-facing workflow.
- **Alternatives:** autonomous apply or LLM-selected winner.
- **Decision:** deterministic code evaluates evidence; a validated proposal
  still follows the approval state machine and exact approved apply path.
- **Reason:** separate evaluation from authorization and preserve traceability.
- **Trade-off:** slower changes and dependence on external approver APIs.
- **Evidence/limit:** governance and integration tests cover the contract;
  production IAM and approval UX are absent.
