# Recovery and restart behavior

## Event and fact recovery

On startup, the runtime checks the remote service, lists history/live chunks
after the durable checkpoint, validates them, and persists each descriptor's
events and checkpoint atomically. If the process dies before commit, the
transaction is rolled back and that descriptor can be replayed. If commit
succeeded, uniqueness and replay-content checks prevent duplicate local
effects. Fact projection resumes from its own transactionally updated cursor.

## Advance recovery

Before a live advance POST, the runner persists a durable intent with the
pre-request state. After a lost response, startup compares the current
documented service state and available chunks. Visible progress completes the
intent; unchanged state and no new chunks permit only the bounded same-intent
reconciliation attempt. Conflicting or incomplete evidence leaves the intent
for manual reconciliation and blocks later advances.

## Approval/apply recovery

Proposal and apply intents are persisted before remote writes. A restart
reconciles approval status and configuration history. Applying requires a
current approved proposal and matching base configuration. If the result
cannot be proven from the service contract, the operation is not resent and
the state requires review.

## What the guarantees mean

These mechanisms provide durable, idempotent local processing within the
database transaction boundaries and safe recovery when remote evidence is
available. They do not provide distributed transactions or exactly-once
remote effects. Operators must retain the same PostgreSQL data and the same
remote service state across restart. Recovery tests are in
`tests/integration/`; no production disaster-recovery or backup/restore
service is included.
