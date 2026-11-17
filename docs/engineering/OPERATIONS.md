# Local operations guide

## Startup

1. Confirm the PostgreSQL service and database URL refer to the intended local
   development database.
2. Confirm a compatible service API is reachable at the configured URL.
3. Start the Controller with `python -m controller <SERVICE_URL>` or the
   repository's `run.sh` entry point.
4. Review structured startup logs for database initialization, migrations, and
   remote readiness. The runtime exits nonzero on required initialization or
   contract failure.

## During operation

Review `advance_ingested` heartbeat records for progress and lag. The runtime
is intentionally serial for ordered ingestion and fact projection. A
non-terminating or ambiguous operation should be treated as a recovery state,
not as evidence that it is safe to issue another remote write.

## Shutdown and recovery

SIGINT/SIGTERM requests graceful stop around the current in-flight operation.
Use the same PostgreSQL database and same remote service state on restart.
Check unresolved outbox/proposal state and the final telemetry record. An
operation marked `reconciliation_required` needs investigation using the
service's documented state before it is manually resolved.

## Limits

This is not a production runbook: there is no deployment manager, health
endpoint, alert delivery, backup schedule, restore procedure, on-call rotation,
or documented customer-facing incident process. Do not run the integration
test fixture against operational data; it clears Controller table rows.
