# Scalability considerations

## Current design limits

The runtime advances one connected service serially. Event chunks are
processed in descriptor order, each with a bounded in-memory/temp-file spool,
bounded COPY batches, and one atomic checkpoint transaction. Fact projection
uses bounded pages and one ordered cursor. These choices bound memory and make
replay/recovery tractable but limit parallelism.

The PostgreSQL store uses indexes for event provenance, workflow/time and
audit queries. Bulk persistence uses temporary staging plus set-based merges.
Changing batch sizes, adding indexes, or introducing concurrent writers can
change memory, WAL, lock, rollback, and query costs. Such changes require
representative measurement and replay/checkpoint tests.

## Measurement approach

For a declared workload, measure wall time, events and facts per second,
worker-tree CPU/RSS, event reads, projection, COPY/merge, transaction commit,
checkpoint updates, PostgreSQL buffers/WAL/waits, and disk growth. Keep API
response, body transfer, and database call timing distinct. Stage intervals
may overlap and must not be summed as wall time.

Establish a baseline on the target environment, vary one factor at a time,
verify row counts and recovery invariants, and preserve raw results. Fixture
timings are not production throughput. See
[performance methodology](../engineering/PERFORMANCE.md).

No production capacity certification or horizontal-scaling design is present.
The current single-process prototype should not be assumed to meet a target
throughput without a complete workload-specific run.
