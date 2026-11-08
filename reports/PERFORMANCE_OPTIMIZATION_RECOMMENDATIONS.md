# Public 03 partial-load performance recommendations

## Scope and result

This profile used a fresh `public_03_load` simulator and the dedicated empty
PostgreSQL database `formi_public03_10pct_20261109`. The Controller was run using
the current optimized `run_controller()` path, with the same 1,000-row fact page
and 500-row event insert defaults. A separate read-only monitor set the runtime
stop event when public `GET /sim/state` reported minute 720. The active request
then completed ingestion and fact projection before the Controller exited.

The Controller stopped safely at **720/7,200 simulated minutes** with
`completed=false`, `stopped=true`, and simulator `finished=false`. It made 48
successful advances. No request advanced the remaining 90%.

This is 10% of the simulator's live time, but not 10% of its data work. The
fresh simulator publishes all **5,537,379 history events** before live time
starts. At minute 720 it had published 354,012 additional events, for
**5,891,391 total events and facts**. That is about 60.5% of the full-run event
count. The measured Controller runtime was **3,697.404 seconds (61.62 minutes)**;
the timed profile interval, including a final idempotency replay and database
verification, took **3,724.094 seconds (62.07 minutes)**. These measurements do
not demonstrate whether a complete 7,200-minute run meets the 50-minute target.

Before launch, C: had about **15.01 GiB free**. The prior full run's 9.44 GB
database size, adjusted for the known 5,537,379 history events plus 10% of its
live events, implied roughly 5.8 GB of database data; a 1.5x reserve fit within
available space. At the end, the dedicated database was about **5.69 GB** and
about **7.08 GiB** remained free. The database and evidence are retained.

## Verified correctness and workload counts

- Initial state: minute 0, `finished=false`, 23 history chunks, last event ID
  5,537,379.
- Final public state: minute 720, `finished=false`, 36 chunks, last event ID
  5,891,391.
- Durable simulator checkpoint: chunk 35, matching the final chunk (`chunks`
  is a count, so the last zero-based chunk is 35).
- Event rows: 5,891,391, IDs 1 through 5,891,391, all distinct.
- KPI facts: 5,891,391, source IDs 1 through 5,891,391, all distinct. The
  analytics cursor is 5,891,391.
- Fact quality: 5,891,391 valid; zero invalid, incomplete, or unsupported.
- Replaying the final committed chunk inserted **zero** rows; counts and
  checkpoint stayed unchanged.
- Persisted additive KPI totals: dials 4,355,295; connected 1,172,857;
  concluded 642,705; uploaded 1,536,000. Pickup was 26.93% (connected/dials),
  engagement was 54.80% (concluded/connected), and business outcome was 41.84%
  (concluded/uploaded). The metrics remain separate.
- Advance outbox: 48 succeeded. No experiment decisions or proposals were
  created. Simulator credits stayed at 400.

## Measured stage timings

The table reports accumulated duration across observations. Stages can be
nested, so **do not add these totals to estimate wall time**. SQL `execute`
timers measure elapsed time at the Controller's database call boundary,
including driver and wait time; they do not isolate PostgreSQL server CPU time.

| Stage | Calls | Accumulated seconds | Maximum seconds | Interpretation |
| --- | ---: | ---: | ---: | --- |
| Fact processing | 49 | 2,899.880 | 2,715.106 | Dominated by initial history projection; includes page reads, Python work, writes, and commits. |
| KPI fact insert `execute` | 5,899 | 2,094.723 | 4.512 | Largest measured operation; about 72% of accumulated fact-processing time. |
| Event insert `execute` | 11,784 | 575.626 | 4.354 | Second largest persistence operation; 500-row configured batches. |
| Event chunk ingestion | 36 | 757.510 | 69.367 | Includes chunk processing; overlaps/nests with persistence and download timings. |
| Event chunk persistence | 36 | 717.908 | 69.000 | Transactional batch insertion and checkpoint work. |
| Fact source-page read | 5,948 | 134.315 | 1.735 | Ordered reads from `simulator_events` for projection. |
| Fact page transaction commit | 5,899 | 73.348 | 0.549 | Much smaller than fact insert execution. |
| Python fact projection | 5,899 | 47.694 | 0.058 | `derive_fact()` work; about 1.3% of Controller runtime. |
| Chunk download | 37 | 39.621 | 11.676 | HTTP body streaming and chunk materialization; not the primary bottleneck. |
| Workflow-reference upsert | 5,899 | 11.560 | 0.039 | Batched per fact page. |
| Fact payload construction | 5,899 | 10.255 | 0.060 | Building SQLAlchemy row dictionaries. |
| Simulator API request | 1,606 | 8.142 | 1.795 | Controller-instrumented requests; excludes the external state monitor. |
| Fact cursor update | 5,899 | 6.566 | 0.036 | Atomic with each fact page. |
| Event chunk commit | 36 | 2.714 | 1.097 | Chunk transaction commits. |
| Event checkpoint update | 36 | 0.040 | 0.005 | Not a meaningful time target in this run. |
| Advance and ingest wrapper | 48 | 49.373 | 26.089 | Live advance path; initial history bootstrap is outside these observations. |

The 2,094.723 seconds around KPI fact `execute` is directly measured, but it
cannot yet be attributed among SQLAlchemy binding/serialization, network/driver
wait, PostgreSQL execution, index maintenance, and storage I/O. Python fact
derivation itself is measured at only 47.694 seconds. This makes a rewrite of
`derive_fact()` a low-priority change without further evidence.

The Controller process accumulated 3,041.5 CPU seconds over the 3,724.1-second
profiling interval, about 82% of one core on average (6.8% of the reported
12-core host capacity). Sampled peak RSS was 133,173,248 bytes; runtime peak RSS
was 133,939,200 bytes. This does not indicate host-wide CPU saturation or memory
pressure. The process measurement includes the in-process monitoring harness.

Database-local `pg_stat_database` deltas were 193,978,875 buffer hits,
4,344,125 block reads, 11,782,879 inserted tuples, 6,745 commits, zero
deadlocks, and zero temporary bytes. These are counters, not time attribution;
block hits/reads count repeated accesses. The observed `pg_stat_wal` byte delta
was 19,092,683,191 bytes, but that view is cluster-wide and may include other
cluster activity; do not attribute it solely to this database or treat it as
retained disk growth.

## Bottlenecks and ranked recommendations

### 1. Investigate and reduce KPI fact insert elapsed time

**Evidence:** `fact_insert_execute_seconds` accumulated 2,094.723 seconds across
5,899 1,000-row pages. This is the largest isolated measured stage. The current
path is `KpiFactProcessor.process_available()` in
[`controller/analytics/facts.py`](../controller/analytics/facts.py), followed by
`DatabaseStore.commit_kpi_page()` in
[`controller/storage/store.py`](../controller/storage/store.py). Each page
builds a multi-row `INSERT ... ON CONFLICT DO NOTHING`, then updates the
analytics cursor in the same transaction.

**First step:** separate database-server execution and wait causes from client
elapsed time. Collect per-query PostgreSQL execution/buffer/wait evidence for
the KPI insert on an isolated candidate database, using supported PostgreSQL
views or an already available `pg_stat_statements` installation. Do not enable
extensions or change server-wide settings without checking operational impact.
Inspect the generated query and a representative `EXPLAIN` plan on a disposable
fixture before choosing a persistence rewrite.

**Candidate after that diagnosis:** compare the existing 1,000-row fact page
with the already-supported 2,000-row upper bound. A larger page could reduce
statement and transaction count; no speedup is established here because insert
execution time, rather than commit time, dominates. Risks include larger
parameter payloads, serialization allocations, longer transactions, and more
work to roll back after a failure. Keep the cursor update and fact insert in one
transaction, retain all constraints and indexes, and require exact fact/cursor
equivalence after restart and replay tests.

### 2. Evaluate event insert batch size

**Evidence:** `event_insert_execute_seconds` accumulated 575.626 seconds across
11,784 inserts. The current streaming path in
[`controller/storage/store.py`](../controller/storage/store.py) inserts event
batches of 500 and advances the descriptor checkpoint atomically only after the
whole chunk validates.

**Candidate:** compare the existing 500-row default with a bounded 1,000-row
setting before considering larger batches. The expected benefit is fewer SQL
statements and less per-call overhead; the measured gain is unknown. Risks are
larger bind payloads and transient memory use. Keep strict ordered event-ID
validation, duplicate-content conflict checks, transaction rollback, and the
same checkpoint boundary. Verify replay and partial-chunk crash recovery.

### 3. Add database-side attribution before considering SQL/index changes

**Evidence:** the dedicated database recorded 4.34 million block reads and 194
million buffer hits; the cluster recorded about 19.1 GB of WAL during the
interval. These counters make database I/O and write amplification worth
investigating, but do not show which statement caused them or how much time they
cost. The KPI fact table maintains its primary key, foreign key, and workflow,
time, version, and campaign indexes; event idempotency and query contracts rely
on these structures.

**Candidate:** measure per-statement buffers, server execution, WAL, and sampled
wait events before any schema or SQL change. Preserve indexes and constraints.
Do not use the cluster-wide WAL delta as a per-database target. Any proposed
index or write-path change needs query-plan evidence plus migration, rollback,
idempotency, analytics-query, and crash-recovery tests.

### 4. Keep Python projection and small transaction stages below the insert work

The measured Python projection (47.694 seconds), payload building (10.255),
workflow upserts (11.560), cursor updates (6.566), and fact commits (73.348)
are materially smaller than KPI insert execution. Avoid changing the fact
business rules, QA mapping, or the separate pickup/engagement calculations on
this evidence. Revisit Python profiling only if a later profile isolates
substantial time in JSON decoding, object construction, or validation outside
the existing `fact_projection_seconds` boundary.

### 5. Do not add concurrency or prefetch yet

The observed live API work was small relative to the startup fact projection.
The runtime owns one ordered event checkpoint and one analytics cursor; parallel
writes would complicate ordering and recovery. The process used less than one
core on average and the host had 12 reported effective cores, but this alone
does not show independent tasks can safely overlap or that PostgreSQL has spare
write capacity. Preserve serial checkpoint ownership until profiling identifies
a critical-path wait that bounded concurrency can actually remove.

## Likely code areas and tests for follow-up

| Follow-up | Likely files | Focused tests |
| --- | --- | --- |
| PostgreSQL insert attribution and safe fact-page comparison | `controller/storage/store.py`, `controller/config/settings.py`, `controller/analytics/facts.py` | PostgreSQL 1,000/2,000-row fact fixtures; exact row and cursor equivalence; conflict/replay; rollback; parameter limits. |
| Safe event batch comparison | `controller/storage/store.py`, `controller/ingestion/service.py`, settings | Tiny PostgreSQL batches at 500/1,000; duplicate and conflicting replay; malformed later batch rollback; checkpoint continuity. |
| Database-side profiling helper, if required | `scripts/measure_load.py`, `tests/unit/test_load_measurement.py`, documentation | Unit tests for optional PostgreSQL stats collection and missing-view behavior; verify sampler reads only. |
| End-to-end regression | `tests/integration/analytics/test_fact_persistence.py`, `tests/integration/ingestion/test_ingestion.py`, `tests/integration/test_real_crash_recovery.py` | Separate pickup and engagement totals, fact quality, restart, event order, atomic chunk checkpoint and replay idempotency. |

## Safe benchmarking strategy

1. Keep this database and all previous evidence intact. Start each candidate in a
   fresh simulator and uniquely named empty database; do not run candidates
   concurrently on the same PostgreSQL cluster.
2. Before each run, estimate the mandatory history footprint from this measured
   profile (about 5.69 GB for history plus 720 live minutes) and confirm free
   space with a documented reserve. Include temporary and WAL retention needs
   from the actual cluster configuration; generated WAL bytes are not equal to
   retained WAL disk usage.
3. Run one 720-minute profile only after correctness tests pass. Capture the
   same page/chunk settings, Python/PostgreSQL versions, database configuration,
   stage histograms, database counters, process CPU/RSS, checkpoints, cursors,
   exact event/fact counts, and separate KPI totals. Stop through a graceful
   runtime event at minute 720; require `finished=false` and preserve the
   completion status as partial.
4. Compare one setting or code change at a time on the same host and hardware
   state. Do not attribute a change to a treatment if stage totals, database
   state, or workload counts differ. Keep all raw logs and samples under unique
   names; never overwrite the original public_03 report.
5. Accept an optimization only if its measured end-to-end and relevant stage
   times improve beyond run-to-run variation, while all event/fact counts,
   quality classifications, KPI totals, replay behavior, checkpoint state, and
   crash-recovery tests remain equivalent. A partial profile is diagnostic and
   cannot establish the full 50-minute target.

## Evidence files and limitations

- [`public_03_10pct_20261109_metrics.json`](public_03_10pct_20261109_metrics.json)
  contains the runtime telemetry, correctness counts, PostgreSQL counters, and
  process measurements.
- [`public_03_10pct_20261109_controller_run.jsonl`](public_03_10pct_20261109_controller_run.jsonl)
  is the structured Controller/runtime log, including final named histograms.
- [`public_03_10pct_20261109_samples.jsonl`](public_03_10pct_20261109_samples.jsonl)
  contains 660 five-second process, database-size, PostgreSQL-counter, and
  public simulator-state samples.
- `public_03_10pct_20261109_setup_attempt.jsonl` records an initial harness
  launch that stopped before Controller startup because the shell had locked
  its output file. It ingested no events and is not included in any timing.

The profile used the controller runtime directly in an in-process measurement
harness because `scripts/measure_load.py` waits for the Controller to finish the
entire simulator scenario and has no stop-at-minute option. The harness set the
same `asyncio.Event` that the runtime checks between advances, then verified the
public state and persisted records. A reusable partial-run mode in the
measurement harness would make future profiles easier to reproduce.

`fact_insert_execute_seconds` is client-observed elapsed time, not PostgreSQL
server execution time. Database wait-event history, query-specific server
timings, storage latency, and detailed CPU attribution between Python and the
PostgreSQL server are not available from this run. The profiling run itself
made no application-code changes. No full load test was run, and no 50-minute
completion claim is made.

## Follow-up investigation and implementation

### PostgreSQL investigation

- PostgreSQL is 18.3. The `pg_stat_statements` extension package (1.12) is
  available, but it is not installed and `shared_preload_libraries` is empty.
  Enabling it would require a cluster-level configuration change and restart.
  This cluster hosts multiple protected project databases, so this task did not
  alter shared settings, restart PostgreSQL, or reset statistics.
- A read-only `EXPLAIN (ANALYZE, BUFFERS, TIMING, SUMMARY)` for the bounded
  1,000-row `simulator_events` page on the retained profile database used
  `simulator_events_pkey`; PostgreSQL reported 0.740 ms execution time, 3.641
  ms planning time, 86 shared buffer hits, and 4 shared buffer reads. The
  Controller's aggregate page-read time was 134.315 seconds over 5,948 reads
  (about 22.6 ms each). This single warm-range query is not representative of
  every page, but it shows that Controller page-read time includes work beyond
  the database executor.
- Read-only `EXPLAIN` showed the fact insert's conflict arbiter is
  `kpi_facts_pkey`. The table also maintains its three existing secondary
  indexes and its source-event and workflow foreign keys. A 10-row
  `EXPLAIN ANALYZE` probe in the isolated PostgreSQL test database, rolled back
  in the same transaction, reported 0.427 ms server execution for event inserts
  and 3.806 ms for fact inserts. The fact statement's row-level FK trigger
  times were 2.338 ms for source-event references and 0.769 ms for workflow
  references. This tiny probe confirms those checks have server cost, but it
  cannot estimate large-batch time. Constraints and indexes remain unchanged.
- No query-specific statistics were available for the 1,000-row fact inserts.
  Client-observed `fact_insert_execute_seconds` therefore remains split-unknown
  between serialization/driver work and PostgreSQL execution. COPY/staging,
  index changes, and FK changes are deferred until a representative database
  profile supports them.

### Completed low-risk event insertion change

Streaming chunk persistence now uses `DatabaseStore.insert_events_count()`.
For a fresh batch it runs the same ordered, conflict-safe insert without
`RETURNING event_id`, and uses PostgreSQL's affected-row count. This avoids
transferring and materializing every newly inserted ID when the caller only
needs a count. If the affected-row count indicates a conflict, it selects the
bounded batch and compares every stored row's chunk, kind, and payload. The
existing `insert_events()` ID-returning contract remains unchanged. Event rows
and the checkpoint are still committed in the same transaction.

Focused PostgreSQL storage, ingestion, and analytics tests passed (**31
passed**), including SQL-shape inspection, no-readback on fresh batches,
idempotent replay, conflicting replay rejection, and rollback. The full suite
passed (**301 passed**); Ruff lint, repository-wide format check (106 files),
compileall, and `git diff --check` passed. No schema or migration changed.

This optimization removes the fresh-event ID result transfer and related
Python bookkeeping, but no representative before/after timing was run. Do not
claim a measured speedup or material effect on the 50-minute target. The
conflict path now reads all IDs in the bounded insert batch when a conflict
exists, a deliberate trade-off to avoid the normal-path `RETURNING` payload
while retaining exact replay checks. Batch defaults remain 1,000 facts and 500
events; changing them without comparable performance data is deferred.

The dominant KPI fact insert stage (2,094.723 seconds in the partial profile)
remains the primary unresolved performance cost. The event result optimization
does not reduce fact insertion, row-level foreign-key checks, or index writes.
Server-side query timing under a safe per-database statistics setup is the next
evidence needed before choosing a fact SQL strategy.

### Follow-up decision record

The existing load measurement runner now captures best-effort PostgreSQL
statistics snapshots immediately before and after the measured Controller
process. It reports database-local `pg_stat_database` values and cluster-wide
`pg_stat_wal` values, plus numeric deltas for fields available in both
snapshots. Missing or inaccessible views are marked unavailable and do not
fail a run. The sampler does not reset statistics, and it runs outside the
Controller hot path. These counters are not statement timings; concurrent
database activity can affect deltas, and WAL counters cannot be attributed to
one database. No load was run to collect a new profile with this reporter.

The snapshot query was smoke-tested read-only against the isolated PostgreSQL
test database; both scopes were available. The test suite also covers delta
calculation and unavailable/non-numeric data. These checks establish reporter
behavior only, not a performance improvement.

The remaining optimization candidates are deferred on the current evidence:

- **Python projection:** the 10% profile measured 47.694 seconds of
  `fact_projection_seconds` within 3,697.404 seconds of Controller runtime
  (about 1.3%). The projection stage is not the dominant measured opportunity;
  avoid changing validation or data construction without a concrete code
  profile showing a worthwhile safe gain.
- **KPI fact SQL, indexes, foreign keys, or staging/COPY:** fact insert elapsed
  time was 2,094.723 seconds at the Controller database-call boundary, but
  query-specific PostgreSQL timings are unavailable. The tiny rolled-back
  probe and plans identify real indexes/FK work but cannot support a production
  SQL rewrite. Preserve constraints until attributable server timings exist.
- **Event batch size, fact page size, workflow upserts, and checkpoint/commit
  changes:** current values are bounded and correctness-tested, while no
  comparable measurements show a better setting or material cost to target.
  Keep defaults (500 event rows, 1,000 fact rows) and transaction boundaries.
- **Prefetch or parallelism:** the measured serial code path is confirmed, but
  corrected worker/database wait evidence is not sufficient to justify added
  concurrency. Keep ordered cursor/checkpoint ownership serial.
- **50-minute acceptance:** no further load or stress run is permitted in this
  work. The target remains unverified; a future permitted full public run with
  preflight, isolated simulator/database, and preserved evidence is required.

No further SQL, batch-size, projection, index, or concurrency change is
supported by the available evidence. The only additional implementation in
this follow-up improves future measurement coverage; it is not a throughput
optimization and makes no speedup claim.


### Current optimization: remove unused event index maintenance

Read-only inspection of `formi_public03_10pct_20261109` showed
`ix_simulator_events_kind_chunk` had `idx_scan = 0` and occupied 39,370,752
bytes. The database reported `stats_reset = NULL`; the counter is cumulative
from the server statistics lifetime, so this is evidence of no recorded scan
since tracking began, not proof that no external client will ever use this
index. A source-wide query review found no Controller query filtering events
by `(kind, chunk_number)`. The separate `ix_simulator_events_chunk` recorded
7 scans and was retained. The simulator event primary key was retained.

Removed the unused composite index from SQLAlchemy metadata and added Alembic
revision `0009_drop_unused_event_idx`, using PostgreSQL `DROP INDEX
CONCURRENTLY`; downgrade recreates it concurrently. Event rows, primary key,
other indexes, conflict detection, ordering, and the chunk/checkpoint
transaction are unchanged. The existing 10% benchmark database was not
migrated or modified; the migration was applied to isolated test database
`formi_perf_opt2_20261109`. A read-only catalog check confirmed the index is
absent there and remains present in the preserved profile database.

Focused storage/fact/schema suite: **28 passed**. Full isolated PostgreSQL
suite: **304 passed**. Alembic upgrade and `alembic check` passed; Ruff,
formatting, compileall, and `git diff --check` passed. This removes one
39.4-MB B-tree and its per-event maintenance from new writes, but no throughput
measurement was run and no elapsed-time improvement is claimed.

No additional safe KPI index removal is justified: query code still uses
workflow/time, workflow/version/time, and workflow/campaign access paths even
though some were not exercised by this load profile. The KPI primary key and
foreign keys support conflict detection and provenance integrity. Fact SQL
rewrite/bulk loading remains deferred because the reported 2,094.7 seconds is
client-side call elapsed time and the available tiny 10-row server probe does
not establish a large-batch improvement. Keep batch defaults at 1,000 fact
rows and 500 event rows; no bounded same-host batch comparison supports a new
default. Keep serial cursor/checkpoint writes; there is no evidence that
parallelism offsets its added recovery and contention risk.


### Runtime migration compatibility follow-up

The concurrent index DDL exposed that `DatabaseStore.migrate()` wrapped the
Alembic environment in an external SQLAlchemy transaction. That prevented
Alembic's autocommit block from running when the Controller applied revision
0009 on startup. The store now opens a connection and lets the Alembic
migration context own its transaction; the normal transactional migrations
remain managed by Alembic, and the concurrent index operation can run outside
that transaction. A PostgreSQL integration test simulates a database at
revision 0008 with the prior index present, calls the Controller's migration
entry point, and verifies revision 0009 plus index removal. `store.migrate()`
was also exercised twice on the newly created isolated microbenchmark DB.


### KPI fact persistence prototype: COPY staging selected

A small controlled PostgreSQL comparison was run on newly created isolated
`formi_kpi_micro_20261109`, with the current migrated schema, KPI indexes and
foreign keys. Each method handled five 1,000-row batches with the same
synthetic row distribution (four workflows, call/lead facts, metrics, JSON
variant/features, and timezone-aware timestamps). Each measured page performed
the workflow-reference upsert, fact persistence, analytics cursor write, and
commit. The order alternated between methods. No benchmark or test database
that existed before this work was used.

| Path | Median page wall time | Median fact SQL / COPY merge | Median transaction commit |
|---|---:|---:|---:|
| Existing SQLAlchemy multi-row `INSERT ... ON CONFLICT` | 356.699 ms | 281.374 ms | 2.357 ms |
| COPY to transaction-local stage, then `INSERT ... SELECT ... ON CONFLICT` | 47.685 ms | 26.049 ms merge (about 5.7 ms COPY) | 2.447 ms |

On this fixture the staging path reduced median page time by **86.6%** (7.48x
faster). It kept the same indexes, FKs, conflict target, batched workflow
upsert, analytics cursor update, and transaction boundary. Commits were nearly
the same; the observed gain came from moving the 22,000-value page through
COPY instead of binding a wide multi-row SQL statement. This is a fixture
measurement, not a public workload guarantee.

Paired `EXPLAIN (ANALYZE, BUFFERS, WAL)` probes on 1,000 new rows reported
multi-row INSERT planning/execution of 2.215/52.377 ms and COPY-stage merge
planning/execution of 0.278/30.425 ms, both with zero shared reads and about
14.4k shared-buffer hits. Foreign-key triggers still ran 1,000 times each;
reported trigger time was 6.866 ms + 10.936 ms for the multi-row probe and
7.060 ms + 5.956 ms for the merge probe. The plans produced different
full-page-image counts (70 versus 0), so their WAL byte comparison is not
attributable solely to the insertion method. Transaction-level WAL LSN deltas
were cluster-wide and varied with page state; no WAL reduction is claimed.
There is no query-specific wait profile because `pg_stat_statements` is not
installed and changing shared PostgreSQL configuration is outside scope.

`DatabaseStore.commit_kpi_page()` now copies bounded `KpiFactRecord` tuples
into a transaction-local temp table and merges them into `kpi_facts` with
`ON CONFLICT (source_event_id) DO NOTHING`. The stage is created and dropped
inside the existing page transaction. Workflow upsert, fact insert, and cursor
advance remain atomic. JSON fields are serialized with the standard JSON
serializer; database columns keep their original types and defaults. Added
fixed-name timers split stage creation, COPY, and set-based merge while keeping
the existing aggregate insert timer.

The production method was exercised on fresh 1,000-row pages: three warm
samples took 44.596 ms and 42.039 ms for the latter two; the first cold sample
took 192.521 ms, largely in its first workflow upsert (134.267 ms). Replaying
a committed page inserted zero rows and left the cursor at the page's final
source event. A PostgreSQL failure test confirms a failed merge rolls back the
fact rows, workflow-reference upsert, and analytics cursor. The full suite
passes 305 tests; Ruff, formatting, compileall, Alembic check, and diff checks
pass.

Experiment note: the first exploratory EXPLAIN DML transaction exited normally
and therefore committed its 2,000 synthetic fact rows in the isolated
microbenchmark database. They use separate event IDs from the timed trials.
The final paired EXPLAIN probes used explicit rollback. The dedicated
microbenchmark database is retained with only small synthetic fixtures; no
existing database or benchmark evidence was changed.

This measured prototype is the only new fact-persistence implementation. The
full-run impact is unverified: the 50-minute target cannot be claimed without a
permitted full public run. Other KPI SQL/index changes, larger pages, event
persistence changes, and concurrency are assessed separately below/next;
constraints and the ordered cursor remain in place.
