# Public load optimization plan: reach the 50-minute target

## Goal and baseline

The completed public `public_03_load` benchmark took **5,878.203 seconds**
(97.97 minutes) to advance all 7,200 simulated minutes and persist 9,737,439
events and 9,737,439 KPI facts. Overall observed throughput was 1,656.962
events/second. PostgreSQL grew from 8,279,743 bytes to 9,441,285,823 bytes.

The target is a complete run in **less than 3,000 seconds** (50 minutes). That
requires at least a 49% reduction in wall time, or about 1.96x end-to-end
throughput (approximately 3,246 events/second at the same event count). This is
also stricter than the plan's `<600 seconds per simulated day` SRE criterion:
the baseline was 1,175.641 seconds/day. A run taking exactly 50 minutes does
not meet a strict under-50-minute target.

The saved benchmark was run on Windows 11, Python 3.12.10, with 12 effective
CPU capacity and about 16.9 GB effective memory. It is not a measurement on the
assignment's 4-vCPU/8-GB grading host. The benchmark artifacts and their
limitations are described in `public_03_load.md` and
`public_03_load_review.md`. This investigation does not rerun the scenario or
modify those files.

## Findings: confirmed and unknown

### Confirmed by the report and raw artifacts

- The `fact_processing_seconds` histogram accumulated **4,320.740 seconds** in
  481 observations; its maximum was **2,417.395 seconds**. This is the largest
  aggregate stage and includes startup processing of the history backlog.
- Chunk persistence accumulated **1,338.924 seconds**; chunk ingestion
  accumulated **1,377.132 seconds**; chunk downloads accumulated **38.184
  seconds**; advance-and-ingest accumulated **656.658 seconds**. These metrics
  overlap: persistence is included in ingestion, and startup history work is
  not all inside the live advance timer. Do not sum them as wall time.
- The API request latency p95 upper bucket was 5 ms to response headers, with
  zero recorded request failures. API request time accumulated 93.003 seconds.
  Network wait is not indicated as the dominant bottleneck by this run.
- The sampler wrote 1,125 rows. Its process RSS values stayed around 4.1–4.5 MB
  and process CPU remained zero because it sampled the Windows launcher shim.
  Those are invalid worker measurements. Worker CPU use, peak RSS, and any
  Python-versus-PostgreSQL time split are **unknown**.
- No Python CPU profile, PostgreSQL query plan, statement-level timing, or
  database wait profile was captured. The stage histogram is the only saved
  profiling evidence and cannot attribute fact-processing time to Python or
  PostgreSQL.
- Database size and event/fact counts are measured. The report did not collect
  WAL bytes, query plans, statement timings, lock waits, commit latency, or
  PostgreSQL CPU and I/O statistics. Database growth alone does not prove that
  storage bandwidth or a particular index is the bottleneck.
- `public_03_load_attempt1.jsonl` is an interrupted partial attempt without a
  terminal runtime summary; it is not a second completed performance sample.

### Confirmed from the current implementation

- `controller/runtime/lifecycle.py` processes chunks and facts sequentially.
  It awaits `advance_and_ingest()` and then `KpiFactProcessor.process_available()`.
  The default SQLAlchemy pool has capacity for more connections, but this path
  does not run concurrent fact pages or event writes.
- `controller/analytics/facts.py` reads ordered event pages of 1,000 rows,
  constructs one `KpiFactRecord` per source event in Python, then writes the
  page. A page write, workflow-reference refresh, and analytics cursor update
  share one transaction. The loop repeats until it reaches the current end of
  the event stream; at the measured count this implies about 9,738 fact pages.
- `controller/storage/store.py` writes facts with a multi-row PostgreSQL
  `INSERT ... ON CONFLICT DO NOTHING`, and the `KpiFact` model maintains its
  primary key and three secondary indexes. Page commits and those indexes are
  real work, but their individual costs were not measured.
- `controller/ingestion/service.py` fetches and validates descriptors in order.
  Chunk bodies are fully spooled (1 MiB memory threshold, then temporary file)
  before a transaction begins. `commit_chunk_stream()` then inserts 500-row
  batches and commits the descriptor checkpoint atomically. No bounded
  cross-chunk prefetch or parallel writes are used.
- The pool size is not evidence of available write concurrency: the event
  checkpoint is ordered, fact cursor updates are ordered, and current runtime
  orchestration is serial. Blindly adding concurrent writers risks lock/WAL
  pressure and makes ordering/recovery harder.

### Current code is ahead of the recorded benchmark

The saved public benchmark predates `10c98f9` and `8f68b9a`. The current code
already (1) skips the event readback `SELECT` for freshly inserted IDs while
checking replay conflicts, and (2) batches workflow-reference upserts and uses
PostgreSQL affected-row counts for fact inserts instead of returning every fact
ID. Their small PostgreSQL tests verify behavior and statement shape. They have
not been measured on a full load, so the 5,878-second baseline neither includes
nor quantifies their effect. Do not count them as additional future work.

## Root-cause interpretation

The strongest supported conclusion is that **fact projection plus its database
page writes dominate measured processing time**. The current histogram wraps
the whole `process_available()` call: source page reads, Python validation and
record construction, workflow upserts, fact inserts, cursor updates, and
transaction commits are all included. It cannot distinguish CPU-bound Python
from PostgreSQL or commit-bound work.

Event ingestion also has substantial aggregate persistence time, but chunk
download/API times are small. Its current transaction preserves atomic
event/checkpoint recovery. It should not be shortened by committing partial
chunks. The main hypotheses to test are repeated fact-page transaction/cursor
cost, fact insert and index cost, and Python projection cost. They remain
hypotheses until separately timed.

## Ranked optimization opportunities

| Rank | Opportunity | Expected benefit | Main risk / evidence needed |
|---|---|---|---|
| 1 | Split timings for event-page read, `derive_fact`, fact SQL execute, workflow upsert, cursor write, transaction commit, event insert, and chunk checkpoint. Record worker-tree CPU/RSS and PostgreSQL/WAL/lock/IO snapshots on a fresh isolated run. | No direct speedup. Identifies whether the next change should target Python or PostgreSQL and prevents optimizing the wrong stage. | Keep instrumentation bounded and low overhead. Existing aggregate timers are insufficient; Windows sampler fix exists but was not used for the completed run. |
| 2 | Sweep a small, configurable fact page size and event insert batch size. Keep the ordered cursor/checkpoint and one atomic transaction per current unit. Start with the current 1,000-fact/500-event settings, then test conservative increments supported by the exact asyncpg/PostgreSQL parameter limits. | If per-page commit/cursor overhead is material, increasing page size reduces query/transaction count. For example, doubling a fact page would halve page count from about 9,738 to about 4,869; the time saved is unknown. Larger event batches similarly reduce insert statements. | Larger Python pages and SQL bind sets consume memory and can hit driver/protocol parameter limits. A larger transaction increases rollback work. Verify complete replay, cursor atomicity, ordering, RSS, SQL argument counts and error recovery at each candidate size. |
| 3 | If profiling shows Python derivation is a significant share, optimize the measured hot code in `derive_fact()` and reduce temporary object/dict allocations while preserving strict validation. Keep a simple typed implementation unless profile results justify specialization. | Potentially reduces CPU per event; no defensible percentage until a representative profile exists. | Semantic drift in invalid/incomplete/unsupported classification, timestamps, booleans, QA and separate KPI counts. Require output-equivalence tests for all event families and malformed edge cases. |
| 4 | If PostgreSQL write/commit time remains dominant after page tuning, prototype PostgreSQL bulk loading (e.g. staging plus merge) behind a separate implementation boundary and compare it with the current multi-row inserts. Do this only after measuring query/commit cost. | Bulk loading may reduce per-row protocol and statement overhead over millions of rows. Magnitude is unknown until tested. | Highest correctness risk: duplicate IDs with conflicting content, foreign keys, indexes, transactional cursor/checkpoint, temporary staging lifecycle, cancellation and crash rollback. No direct table `COPY` or weakened idempotency. |
| 5 | Consider one-chunk-ahead bounded download prefetch only if a future run shows transfer wait on the critical path. Preserve descriptor order and commit only the earliest complete chunk. | The existing run reports 38.184 seconds aggregate chunk download, under 1% of total runtime; even eliminating all of it cannot close the 48-minute target gap. | Extra spool/disk use and more complicated shutdown/restart behavior for modest measured upside. Do not prioritize now. |
| 6 | Consider parallel projection only if profiling confirms CPU saturation and the sequential projection is GIL-bound. Preserve ordered persistence and a single cursor owner. | Possible CPU utilization improvement on multiple cores; currently unmeasured. | Extra serialization/memory, queue backpressure, shutdown semantics and ordering. Parallel PostgreSQL writes are not recommended before evidence because they may increase contention and complicate atomic recovery. |

Do not start by dropping the three KPI indexes, raising database pool size, changing WAL/checkpoint settings, or adding general concurrency. There is no query-plan/IO/lock evidence to support those changes, and each can harm analytics, disk use, or recovery.

## Proposed implementation subtasks

1. **Measurement split and repeatable small profile**
   - Add bounded timers around the existing fact read, projection, persistence, and commit phases; add event insert/checkpoint phases. Corrected process-tree CPU/RSS sampling should be enabled in the runner. Record PostgreSQL database/WAL growth and available `pg_stat` data when accessible; mark unavailable measurements explicitly.
   - Files: `controller/analytics/facts.py`, `controller/storage/store.py`, `controller/ingestion/service.py`, `controller/telemetry.py`, `scripts/measure_load.py`, telemetry tests.
   - Tests: histogram/counter formulas, no high-cardinality labels, elapsed-time accounting, sampler child-process metrics, DB stats unavailable/error behavior. Use small PostgreSQL fixtures only.

2. **Bounded batch-size experiment**
   - Make fact page size and event batch size explicit runtime settings with conservative validation; compare a small bounded matrix on the same host and isolated PostgreSQL. Do not change checkpoint or transaction boundaries. Select a new default only if throughput improves without unacceptable memory, WAL, latency or rollback costs.
   - Files: `controller/config/` or current settings module, runtime construction in `controller/runtime/lifecycle.py`, `controller/analytics/facts.py`, `controller/ingestion/service.py`, storage and integration tests, README operational guidance.
   - Tests: accepted/rejected bounds; exact DB driver argument limit; page continuation; duplicate replay; conflicting replay; failed-page rollback; no cursor advance; memory stays bounded on a modest fixture.

3. **Measured Python projection optimization (conditional)**
   - Implement only if subtask 1 attributes enough critical-path time to Python. Retain strict field and KPI behavior.
   - Files: `controller/analytics/facts.py` and `tests/unit/analytics/test_fact_mapping.py`; add a small profile/benchmark helper only if it is useful beyond one investigation.
   - Tests: current mapping cases plus equivalence across valid, invalid, incomplete, unsupported and QA edge cases; deterministic results; a small before/after microbenchmark with environment and no speed claim beyond the measured fixture.

4. **PostgreSQL bulk-load prototype (conditional)**
   - Proceed only if measured database statement/commit time remains dominant after safe batching. Prototype on an isolated disposable schema/database; keep the current path available until parity and recovery pass.
   - Files: storage implementation, migration only if a durable staging object is justified, isolated PostgreSQL integration tests and operations docs.
   - Tests: idempotent retry; duplicate and conflicting IDs; FK/index constraints; transaction rollback; checkpoint atomicity; cancellation/crash during staging; replay after restart. Never benchmark against or truncate the existing public benchmark database.

5. **Full acceptance benchmark**
   - Run a fresh permitted public_03 simulator and a dedicated PostgreSQL database only after the candidate changes pass all earlier checks and disk/memory preflight. Save new uniquely named artifacts; never overwrite the existing benchmark evidence.
   - Tests/checks: full test suite, Ruff, formatting, compile, Alembic check, diff check; then one complete public scenario and count/cursor/outbox consistency review. No hidden scenario files or simulator internals.

## Safe benchmarking strategy

1. Preserve `public_03_load.md`, its JSONL log/samples, `public_03_load_attempt1.jsonl`, and existing databases as immutable evidence. Use a fresh simulator instance and a uniquely named empty PostgreSQL database for every candidate that requires full-run validation.
2. Compare candidates on the same host, Python/PostgreSQL versions, database settings, Controller configuration, and simulator workload. Capture the host resource limits and database/WAL sizes before and after. Require a disk-space preflight from measured database growth plus WAL/temp/log headroom; stop before a run if the available budget cannot contain that footprint safely.
3. First use small fixtures to validate SQL argument bounds and functional/recovery invariants. Use a modest bounded representative event set for batch sweeps; never generate millions of local synthetic events as a substitute for public scenario results.
4. For a full acceptance run, use the corrected process-tree sampler and retain per-run logs separately. Time startup history ingestion and fact projection separately from live advances. Report API response latency separately from full-body download and persistence. Include WAL growth, peak worker RSS/CPU, DB size, row counts, cursor/checkpoint, retries/failures, simulator terminal state, and complete wall time.
5. Change one variable per run. Treat test-fixture speedups and calculated query-count reductions as such; do not call them public-run throughput improvements. Avoid comparing a Windows development run directly with a Linux grading target.

## Decision criteria for the 50-minute target

The target is achieved only when a fresh, permitted public_03 run:

- completes all **7,200 simulated minutes** with Controller exit code 0 in **less than 3,000 wall seconds** on the declared target environment;
- has no missing descriptor/chunk, checkpoint gap, duplicate local event effect, failed fact derivation or unexplained event/fact count mismatch;
- ends with the simulator's terminal state and database event/fact cursors reconciled to the final public API observations;
- has no unresolved transaction, outbox or remote operation and no API/DB errors that compromise correctness;
- records worker memory, CPU, database and WAL measurements with a valid sampler and confirms sufficient resource headroom; and
- preserves raw run metadata and evidence in new report/log/sample files without rewriting earlier results.

If the workload or hardware differs from the declared target, report the result as an environment-specific measurement rather than claiming the 50-minute target generally achieved. Chunk 10 remains outside this plan.
