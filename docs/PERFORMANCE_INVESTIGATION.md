# Chunk 9 performance investigation

This read-only review used `reports/public_03_load.md`,
`reports/public_03_load_review.md`, `reports/public_03_load.jsonl`, and
`reports/public_03_load_samples.jsonl`. The benchmark evidence files are not
modified by this investigation.

## Measured bottlenecks

- The completed run processed 9,737,439 events and facts in 5,878.203 seconds.
- The `fact_processing_seconds` histogram accumulated 4,320.740 seconds across
  481 calls, with a 2,417.395-second maximum. This is the largest aggregate
  stage and includes the large startup history backfill. Its timings overlap
  other stages and are not additional wall time.
- Chunk persistence accumulated 1,338.924 seconds across 268 chunks. Chunk
  download accumulated 38.184 seconds. API request time accumulated 93.003
  seconds, so API latency does not explain the dominant processing time.
- The database grew from 8,279,743 bytes to 9,441,285,823 bytes while storing
  9,737,439 raw events and facts. The existing schema keeps provenance and
  workflow/time indexes used by analytics; this review does not remove them.
- The original Windows process CPU/RSS samples are invalid for worker-level
  conclusions. The review note explains this measurement limitation.

## Code-path findings

The runtime calls `KpiFactProcessor.process_available()` at startup and after
each advance. It reads raw events in keyset pages of 1,000, derives Python fact
records, then commits each page with a fact insert and analytics-cursor upsert.
For each page, storage also refreshed each distinct workflow reference one at a
time. At the completed event count, the configured page size implies roughly
9,738 page transactions, plus bounded source-page reads. Each fact row updates
its primary key and three workflow/campaign indexes.

Raw event ingestion uses 500-record database batches. Before this investigation,
each batch inserted with `RETURNING event_id`, then selected every submitted ID
to verify content equality, including IDs PostgreSQL had just reported as newly
inserted. For a new batch, the readback was unnecessary; replay conflicts still
need the content check.

## Applied optimization: avoid fresh-event readback

`DatabaseStore.insert_events()` now uses the IDs returned by the insert to
identify conflicts and only selects those IDs for content validation. Fresh
batches avoid one database `SELECT`; duplicate and mixed batches still validate
persisted content and raise `EventConflictError` on mismatch. A small PostgreSQL
integration test asserts this query behavior.

Expected benefit: at 500 events per batch, a first-time import of the benchmark
volume would avoid up to about 19,475 readback `SELECT`s. This is a query-count
estimate from the code's batch size, not an elapsed-time measurement. Risks are
limited to conflict classification; tests cover new inserts, idempotent replay,
and conflicting replay.

## Applied fact-write optimization

The KPI write path now uses PostgreSQL's affected-row count instead of returning
and materializing each inserted source ID. Workflow reference refreshes for a
page are issued as one multi-row upsert rather than one statement per workflow.
The existing fact-page transaction and analytics-cursor atomicity are
unchanged. A small PostgreSQL integration test verifies inserted and duplicate
counts, one workflow upsert for multiple workflows, and the absence of a fact
ID `RETURNING` clause.

Expected benefit: avoid returning up to 1,000 source IDs per page and reduce
workflow upsert statements to one per page. If all 12 configured workflows
were represented on a page, that would be 11 fewer upsert statements. Actual
distinct workflows per page were not recorded, so aggregate savings and elapsed
time improvement are not claimed. This path requires PostgreSQL row-count
semantics, consistent with the store's enforced PostgreSQL/asyncpg URL.

Page-size changes, dropping indexes, SQL-side fact derivation, or asynchronous
write parallelism are not included because they need stronger measurements or
carry greater correctness and operational risk.

## Measurement limits

No public load run, million-event fixture, or large temporary dataset is used
for this work. Tests measure correctness and SQL statement shape on small
PostgreSQL fixtures. No wall-time improvement is claimed. Re-measurement on a
fresh permitted public scenario is still needed to quantify throughput, memory,
database growth, and whether the SRE threshold improves.

## Post-optimization verification

- Focused event-storage/fact-persistence tests passed: 16 tests after the first
  optimization; focused fact-pipeline tests passed: 10 tests after the second.
- Full suite on the isolated PostgreSQL test database passed: 283 tests.
- `ruff check .`, `ruff format --check .`,
  `python -B -m compileall -q controller tests scripts`, Alembic `upgrade head`,
  Alembic `check`, and `git diff --check` all passed.
- These checks validate behavior and schema consistency, not a throughput gain.

## Follow-up implementation decision

Stage timers have now been added for a future measured run, but no representative
run has exercised them. The previous public_03 report aggregates the entire
fact-processing loop and cannot establish that `derive_fact()` is a material
CPU hot spot. Python projection changes are therefore deferred. The current
validation and mapping logic remains untouched pending profile evidence.

Additional PostgreSQL persistence changes are also deferred. Existing storage
optimizations already remove a fresh-event readback and batch fact-page
reference writes; no full-run measurement has quantified their benefit. The
saved report provides no statement, commit, WAL, lock, query-plan or I/O profile
to support a new persistence strategy. No index, constraint, transaction, or
WAL setting has been changed.

Prefetch and parallel execution were reviewed and not implemented. The saved
run measured 38.184 seconds of chunk-download time and 93.003 seconds of API
request time out of 5,878.203 seconds; eliminating all observed chunk-download
time would leave nearly the entire 50-minute target gap. CPU saturation and
database wait are unknown, and checkpoint writes must stay ordered. Revisit
concurrency only if corrected end-to-end profiling demonstrates a critical-path
wait or CPU bottleneck with enough expected benefit to justify its recovery and
backpressure complexity.
