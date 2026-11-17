# Observability

The runtime emits structured JSON logs to stderr. It records readiness,
committed advances, ingestion/fact progress, shutdown, errors, and a bounded
final metrics record. Heartbeats include current simulator state, latest
committed event/fact progress, pending work, request outcomes, and lag where
timestamps permit. Final summaries include bounded API latency/status
aggregates, throughput counters, database size snapshot, checkpoint/outbox
state, and separate KPI rates. A zero denominator is emitted as undefined.

Telemetry uses bounded metric names and avoids request IDs or other
high-cardinality labels. API latency is measured to response headers; full
chunk ingestion duration includes download, validation and persistence.

There is no HTTP metrics or health server, exporter, alert routing, dashboard,
trace collector, SLO, or paging integration. A future deployment should alert
on failed or ambiguous operations, stalled ingestion, unresolved outbox work,
database resource exhaustion, and missing heartbeat. Thresholds must come
from workload and service objectives, not generic defaults.

Relevant implementation: `controller/telemetry.py`,
`controller/observability/logging.py`, and tests under `tests/unit/`.
