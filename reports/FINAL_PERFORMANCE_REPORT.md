# Final Performance and Evidence Report

Date: 2026-11-09. This report summarizes retained public-run evidence and
isolated persistence experiments. It does not replace or modify the original
reports, logs, samples, or databases.

## Target and historical baseline

- Assignment target: complete all 7,200 simulated minutes in **under 3,000
  seconds (50 minutes)**.
- Original full `public_03_load`: **5,878.203 seconds (97.97 minutes)** and
  **9,737,439 events/facts**. The historical SRE criterion of less than 600
  wall seconds per simulated day failed at 1,175.641 seconds/day.
- That full run was on a Windows development host, not the assignment's
  4-vCPU/8-GB grading environment. Its original worker CPU/peak-RSS samples
  were invalid because the sampler followed the launcher shim; see
  `reports/public_03_load_review.md`.

## Later partial profile before COPY staging

The permitted 720-simulated-minute run took **3,697.404 seconds**, but history
was also ingested: **5,891,391 events**, about 60.5% of the full event volume.
It is not a 10%-of-events performance sample and is not a post-COPY result.
Its important stage totals were:

| Stage | Controller-observed elapsed time |
|---|---:|
| KPI fact SQL calls | 2,094.723 s |
| Event insertion | 575.626 s |
| Event-page reads | 134.315 s |
| Fact-page commits | 73.348 s |
| Python fact projection | 47.694 s |

These stage timings may nest and are not additive wall time. The SQL timings
were measured at the Controller database-call boundary, not as PostgreSQL
executor-only time. The saved evidence is in `reports/public_03_10pct_20261109_*`.

## Implemented changes

- Reduced KPI fact page database round trips and batched workflow-reference
  writes (`8f68b9a`).
- Avoided unnecessary event readback/returned IDs on fresh streamed batches
  while preserving replay verification (`10c98f9`, `99c8ad9`).
- Added stage timers and PostgreSQL database/WAL counter snapshots
  (`e2f20de`, `ba01164`, `ca5961c`).
- Removed the unused `(kind, chunk_number)` event index after source/catalog
  review (`3ef4344`). The retained profile DB recorded zero scans and about
  39.37 MB for this index; the chunk-only index was retained. No throughput
  gain was measured for this change.
- Fixed runtime migration compatibility with concurrent index DDL (`344c4c5`).
- Replaced wide row-bound KPI/event INSERT batches on the streaming paths with
  PostgreSQL COPY into transaction-local temporary staging tables, followed by
  set-based `INSERT ... SELECT ... ON CONFLICT` merges (`386cfa5`, `005c1a7`).
  Event conflicting replay is compared by ID, chunk, kind and JSONB payload.
  KPI source uniqueness, event uniqueness, workflow foreign keys, indexes,
  transaction boundaries and cursors/checkpoints remain enforced.

## Isolated INSERT versus COPY measurements

Five alternating 1,000-row/page batches per path were measured on isolated
PostgreSQL with the migrated schema. The KPI page included the same workflow
upsert, fact insertion, analytics cursor update and commit for both paths.

| KPI page | Multi-row INSERT | COPY stage + set-based merge |
|---|---:|---:|
| Median full page | 356.699 ms | 47.685 ms |
| Fact SQL/COPY and merge | 281.374 ms INSERT | about 5.7 ms COPY + 26.049 ms merge |
| Transaction commit | 2.357 ms | 2.447 ms |

This fixture was 86.6% faster end to end with COPY staging. Paired
`EXPLAIN (ANALYZE, BUFFERS, WAL)` probes reported server INSERT execution of
52.377 ms versus 30.425 ms for the COPY-stage merge on 1,000 rows. Shared
buffer reads were zero; foreign-key triggers still ran per row. Full-page
image counts varied, so no WAL reduction is claimed. `pg_stat_statements` and
query-specific wait attribution were unavailable; no shared PostgreSQL
settings were changed.

Five alternating 1,000-event chunk trials (two 500-row batches plus checkpoint
transaction) gave:

| Event chunk | Multi-row INSERT | COPY stage + set-based merge |
|---|---:|---:|
| Median full chunk | 74.300 ms | 23.658 ms |
| Event SQL execution | 58.068 ms | about 10.1 ms COPY + merge |
| Transaction commit | about 1.178 ms | about 2.1 ms |

The event fixture was 68.2% faster end to end. The first new-path chunk was
cold at 74.044 ms due largely to temporary-table creation; subsequent chunks
ranged from 21.466 to 26.129 ms. All fixture results are local, synthetic,
small-batch measurements, not throughput or full-run guarantees.

## Correctness and test evidence

The final isolated PostgreSQL verification database
`formi_perf_verify_20261109` ran **306 tests successfully**. The suite includes
same-batch duplicate handling, conflicting replay rejection, transaction
rollback, event/checkpoint atomicity, KPI/cursor atomicity, repeated migration,
and subprocess crash recovery. The 0009 migration was downgraded to 0008 and
upgraded to head; `alembic check` found no schema drift. Ruff, format check,
compileall and `git diff --check` passed. No simulator endpoint was contacted
by these tests.

## Deferred opportunities and limitations

- Python projection took 47.694 seconds in the prior partial profile (about
  1.3% of Controller runtime); no projection rewrite was justified.
- Event/fact batch defaults remain bounded at 500/1,000; no broad batch sweep
  supports larger defaults. Transaction units remain one event chunk and one
  fact page.
- Writers remain serial because event checkpoints and fact cursors are ordered;
  available evidence does not justify concurrent writes or prefetch.
- No further indexes or foreign-key constraints were removed. Existing query
  paths and provenance depend on them.
- The full public workload has not been rerun after the COPY changes. Fixture
  measurements cannot be multiplied by event count to estimate a full run.

**The 50-minute full-workload target remains unverified because the full
benchmark was not rerun after optimization.** No full, partial, stress, or
large benchmark was run during this final audit.

