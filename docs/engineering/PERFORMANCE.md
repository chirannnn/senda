# Performance measurement and evidence

Senda performance should be evaluated against a declared workload and
environment, not a universal average. The operating principle is:

> Measure the workload, establish a baseline, identify the bottleneck,
> optimize the limiting component, and verify the improvement.

## What to measure

For ingestion, report end-to-end wall time, events/facts per second, API
response and body-transfer time, event-page read time, validation/projection
time, database COPY/merge execution, commit time, checkpoint writes, worker
process-tree CPU/RSS, PostgreSQL waits/buffers/WAL, and storage growth. Record
whether intervals overlap; stage totals must not be summed as wall time.

Use a fresh isolated database and representative, permitted workload. Capture
environment details and correctness checks before interpreting performance.
Change one variable at a time. Check database headroom before a run and retain
the raw evidence alongside a concise report. Verify event/fact counts,
checkpoint continuity, replay behavior, KPI correctness, and remote completion
before calling a run successful.

Small fixture measurements answer local implementation questions only. Do not
extrapolate fixture percentages to full workload throughput. Do not compare
results across different database state, workload, or hardware as if they
were equivalent.

## Persisted fixture evidence

The repository's isolated PostgreSQL verification record reports five
alternating 1,000-row trials per persistence path. Each operation used a small
fixture with the same schema and transaction boundary for both implementations.

| Operation | Multi-row INSERT median | COPY stage + set-based merge median | Fixture elapsed-time change |
|---|---:|---:|---:|
| KPI page, including workflow references, cursor update, and commit | 356.699 ms | 47.685 ms | 86.6% lower |
| Event chunk, including two 500-row batches and checkpoint transaction | 74.300 ms | 23.658 ms | 68.2% lower |

For the KPI page, recorded execute/merge work was 281.374 ms on the earlier
INSERT path; the COPY path recorded about 5.7 ms COPY plus 26.049 ms merge.
Recorded commit medians were 2.357 ms and 2.447 ms, respectively. For the
event chunk, the earlier event SQL path was 58.068 ms with about 1.178 ms
commit; the COPY path was about 10.1 ms for COPY plus merge and about 2.1 ms
commit. A cold temporary-stage creation affected the first new-path event
trial.

These values are isolated local fixture measurements, not full workload
throughput or an end-to-end runtime improvement. They do not prove how much of
a production run is spent in those paths. The final verification record also
reports the automated tests and migration checks in
[`TESTING_STRATEGY.md`](TESTING_STRATEGY.md). No full workload was rerun after
the COPY changes; optimized end-to-end performance remains unverified.

## Implemented persistence changes

- KPI facts and streamed events use PostgreSQL COPY into transaction-local
  staging tables and set-based conflict-safe merges (`386cfa5`, `005c1a7`).
- Event replay still validates the previously stored content on conflict.
- Fact/source uniqueness, workflow foreign keys, query indexes, transaction
  boundaries, and event/fact cursor atomicity remain in place.
- Fresh event writes avoid returning large identifier collections, and
  workflow-reference writes are batched (see `99c8ad9`, `8f68b9a`).
- A reviewed unused event composite index was removed by a reversible
  migration (`3ef4344`); no full-run speedup is attributed to it.

## Performance conclusion

The fixture comparisons support retaining the COPY staging implementation for
these bounded persistence operations. They do not establish a full-workload
runtime, a 50-minute result, or a projected end-to-end speedup. Full-run worker
resource behavior and PostgreSQL wait attribution remain unverified after the
optimized persistence change.
