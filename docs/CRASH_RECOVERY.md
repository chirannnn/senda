# Controller crash recovery audit

This document describes recovery guarantees verified from the current code. It
does not claim exactly-once remote delivery.

## Event chunk ingestion

`EventIngestionService` lists documented history/live chunk descriptors, streams
and validates NDJSON, and calls `DatabaseStore.commit_chunk_stream`. The store
inserts bounded batches inside one SQL transaction and writes the chunk
checkpoint only after the stream is completely consumed. A process death before
commit should roll back both partial rows and checkpoint. A committed chunk is
replayed by event ID; identical rows are ignored and conflicting content raises
`EventConflictError`. Restart re-lists descriptors after the persisted
checkpoint. A crash after commit but before the caller observes success is
therefore locally idempotent.

`advance_and_ingest` first persists a simulator advance outbox intent with the
pre-request simulator state, then sends one advance POST. A crash after remote
advance but before local descriptor persistence is recovered by
`reconcile_pending_advances`: compare documented simulator state and ingest
available chunks. If progress is observed, the original intent is completed
without another advance. If state is unchanged and there are no new chunks, one
bounded retry is allowed. Conflicting or unavailable state moves the intent to
manual reconciliation. This is not a general exactly-once guarantee for the
simulator POST.

## Approval and configuration operations

`submit_proposal` stores the immutable proposal payload, stable proposal ID,
outbox intent, and audit record before `POST /approvals`. Restart calls
`reconcile_approval_submission`, using documented approval list/get APIs to
match the proposal ID. It never resubmits a potentially accepted ambiguous POST.
Missing or inconsistent remote evidence marks reconciliation required.

`apply_approved_proposal` rechecks approval identity/status and base config
version, then commits apply intent and audit evidence before the apply POST.
Restart with `applying` state uses `reconcile_apply`, consulting approval,
current config and config history. A matching applied history row completes the
operation without another POST. A remote lapsed approval is recorded. If remote
state cannot establish whether apply occurred, the operation stops in manual
reconciliation; the simulator contract does not document apply idempotency.

Incident lifecycle status and timeline are stored transactionally with audit
entries. At runtime, pending approvals are polled on each tick and an approved
proposal is handed to the Chunk 5 safe-apply path. The evidence/suggestion and
validation phases each persist intent before their paid POST. The simulator
provides no lookup endpoint for ambiguous suggestion or validation responses,
so those phases halt for manual review instead of retrying.

## Expected invariants for the demonstration

1. Before a chunk transaction commits, an injected process death leaves neither
   rows from that chunk nor its checkpoint. Earlier committed chunks remain.
2. Restart ingests the incomplete descriptor and stores each event ID once.
3. Checkpoint advances only with a fully committed chunk.
4. A pending approval is reconciled by its stable proposal identity.
5. An approved apply with matching config-history evidence is recorded once;
   controller restart does not issue a second apply POST.
6. Rejected, expired and lapsed approvals do not apply.
7. Ambiguous remote writes without sufficient documented reconciliation data
   remain unresolved for an operator.
8. Audit evidence references the same proposal/approval/event IDs and commits
   with the related local state transitions.

## Reproducible procedure

Use a fresh isolated PostgreSQL database and a dedicated simulator process. Keep
both running across the Controller kill/restart. For the ingestion kill point,
pause a disposable PostgreSQL test database during an INSERT within a multi-batch
chunk transaction, send a hard process termination to the actual Controller,
verify the transaction rolled back and checkpoint stayed at the previous chunk,
then restart and verify complete ingestion. For the approval kill point, leave
the workflow's approval pending after the Controller has durably recorded its
proposal and approval ID, hard-terminate the process, then restart against the
same database and simulator and verify decision reconciliation and a single
configuration history effect. Capture SQL counts/checkpoints and documented
HTTP outcomes; do not read simulator run-directory files or scenario truth.
