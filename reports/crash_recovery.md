# Chunk 8 crash recovery demonstration

## Environment

- Windows PowerShell, Git Bash, Python 3.12, PostgreSQL 18.3.
- Two isolated simulator processes served the permitted `public_02_incidents`
  scenario on ports 8710 and 8711. Each had its own fresh PostgreSQL database.
- For each individual kill/restart, the exact same simulator process/state and
  PostgreSQL database were kept running and reused. No simulator or database
  reset occurred between a kill and its recovery.
- Temporary PostgreSQL triggers paused the selected transaction boundary. The
  Controller process tree was terminated with `taskkill /T /F`; the PostgreSQL
  backend blocked in the test trigger was then terminated to force rollback.
- Only documented HTTP state, approval, config-history, and event APIs were
  queried. No scenario YAML contents, hidden truth, simulator source, or
  run-directory data were inspected.

## Kill point 1: partial event-chunk ingestion

Commands and procedure:

```powershell
py -3.12 -m sim serve --scenario scenarios/public_02_incidents.yaml `
  --run-dir $env:TEMP/formi-chunk8-real/sim-run --host 127.0.0.1 --port 8710
$env:CONTROLLER_DATABASE_URL='postgresql+asyncpg://postgres@127.0.0.1:5448/formi_chunk8_20261108'
py -3.12 -m alembic -c alembic.ini upgrade head
# Install a BEFORE INSERT trigger on simulator_events which runs pg_sleep(120)
# only when NEW.event_id=1. Launch ./run.sh http://127.0.0.1:8710 separately.
# Wait for pg_stat_activity to report the INSERT in PgSleep; then:
taskkill /PID 9060 /T /F
# Terminate the blocked PgSleep backend, verify rollback, drop the test trigger,
# then relaunch the same ./run.sh URL with the same CONTROLLER_DATABASE_URL.
```

The descriptor for history chunk 0 was obtained through the documented
`GET /events/chunks?kind=history&after=-1`; it contained event IDs 1–187274.
While the first insert was sleeping, the database had zero `simulator_events`
rows and no `simulator` checkpoint. After hard termination and termination of
the blocked database backend, it still had zero rows and no checkpoint. This
shows the event rows and checkpoint share the chunk transaction.

The Controller restarted at the same simulator minute 0 and reingested chunk
0. PostgreSQL then contained 187,274 unique event IDs (1–187274) and checkpoint
0. The Controller continued to the end of the same simulator. Final documented
simulator state was minute 14,400, `finished=true`, 491 chunks, and last event
324,308. The final database contained 324,308 rows, IDs 1–324,308, all unique,
and checkpoint 490. No approval/config effect was duplicated.

## Kill point 2: remote approval pending, local response not committed

Commands and procedure:

```powershell
py -3.12 -m sim serve --scenario scenarios/public_02_incidents.yaml `
  --run-dir $env:TEMP/formi-chunk8-approval/sim-run --host 127.0.0.1 --port 8711
$env:CONTROLLER_DATABASE_URL='postgresql+asyncpg://postgres@127.0.0.1:5448/formi_chunk8_approval_20261108'
py -3.12 -m alembic -c alembic.ini upgrade head
# Install a BEFORE UPDATE trigger on governance_proposals which sleeps when
# OLD.status='submitted' and NEW.status='pending'. Launch ./run.sh on port 8711.
# Wait for GET /approvals?workflow_id=ks_cancel to report the approval pending
# and pg_stat_activity to report the trigger in PgSleep. Then:
taskkill /PID 21428 /T /F
# Terminate the blocked PgSleep backend, drop the test trigger, and restart the
# same Controller command using the same simulator URL and database URL.
```

Immediately before the kill, documented remote lookup returned proposal
`incident-69d4b4d0595dd7d3131ef27e3a05ed5a`, approval `apr_00001`, status
`pending`, base version 2. Locally, the immutable proposal was `submitted`, its
approval ID had not yet been committed, and the `approval_submission` outbox
row was `in_progress` with one attempt. The database had 234,187 committed
events and checkpoint 160.

After restart, the Controller reconciled by the persisted proposal ID via the
approval list API. It did not send a second approval POST. The approver decided
approved at simulated 12:36; the existing Chunk 5 lifecycle applied the exact
approved prompt at 13:00 as config version 3. The same Controller then finished
at minute 14,400. Final database state: 324,308 unique events, checkpoint 490,
proposal status `applied`, submission outbox `succeeded` with one attempt, apply
outbox `succeeded` with one attempt, and 13 audit records. Audit contains one
each of `approval_submission_prepared`, `approval_reconciled`,
`approval_apply_prepared`, and `approval_apply_reconciled`. Documented config
history contains one controller-approved version 3 referencing that proposal
and approval.

## Test results and guarantees

- The PostgreSQL integration test hard-kills a child process while the first
  one-row batch is inserted inside an open chunk transaction, then verifies
  rollback, restart replay, final checkpoint, and duplicate-free replay.
- A pending approval integration test closes and reopens the store, reconciles
  pending/approved status, applies once, then reopens again and confirms the
  terminal state avoids another apply POST.
- Repeated submission and unresolved ambiguous apply tests verify one remote
  attempt and manual-review state when API evidence cannot establish outcome.
- Existing tests cover rejected, expired and lapsed decisions; lost apply
  responses with matching config history; and safe failure when history cannot
  establish an apply.
- Full isolated PostgreSQL suite: 260 passed. `ruff check .`,
  `ruff format --check .`, `python -m compileall -q controller tests`, Alembic
  check, and `git diff --check` passed.

The simulator contract does not promise exactly-once delivery for remote writes.
Approval submission is reconciled by proposal ID. Apply is reconciled from
approval status, current config and config history. If those documented reads
cannot prove an outcome, the controller halts in reconciliation-required state
for manual review rather than blindly retrying. Suggestion and validation POSTs
have no documented lookup APIs and likewise require manual review after an
ambiguous result.
