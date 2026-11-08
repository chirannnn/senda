# Public 03 load report review

This review accompanies `public_03_load.md`; the original report, raw controller
log, and sampled JSONL evidence are retained unchanged.

## Verified completion and performance

- The public `public_03_load` simulator completed all 7,200 simulated minutes.
- The Controller exited with code 0 after 5,878.203 wall seconds.
- It persisted 9,737,439 events and the same number of valid KPI facts. The final
  chunk checkpoint was 267 and the analytics cursor was 9,737,439.
- All 480 simulator advances returned HTTP 200. The collected API request
  summary reports zero failures.
- Event insert throughput was 1,656.962 events/second. Simulated ingestion lag
  was 181 minutes and latest-event wall persistence lag was 53.784 seconds.
- PostgreSQL size grew from 8,279,743 bytes to 9,441,285,823 bytes.
- The SRE target of less than 600 wall seconds per simulated day **failed**:
  measured time was 1,175.641 seconds per simulated day.
- The largest aggregate stage was fact processing at 4,320.740 seconds. Stage
  durations overlap and must not be summed as wall runtime.
- The simulator ended `finished=true` at minute 7,200, with 268 chunks and
  `last_event_id=9,737,439`. No governance decisions were persisted; all 400
  credits remained.

## Process metric limitation

The original sampler recorded the Windows virtual-environment Python launcher
process. That launcher spawned the actual Python worker, so the report's sampled
4,546,560-byte peak RSS and zero process CPU seconds are not valid measurements
of the Controller worker. Treat whole-run process CPU and peak RSS as unknown.
Read-only spot checks during execution saw the worker at roughly 90–103 MiB
working set at sampled points, but those observations do not establish a peak
or a full-run CPU total.

After the scenario finished, the sampler was corrected to include Windows
descendant processes and the runtime RSS probe was corrected. Focused tests
passed and a Windows smoke check returned nonzero RSS and CPU values for the
current process. The completed scenario was not rerun after this fix; therefore
no corrected whole-run process measurements are claimed.

## Evidence and interpretation

The controller report and raw data are `public_03_load.md`,
`public_03_load.jsonl`, and `public_03_load_samples.jsonl`. The first interrupted
attempt's partial log is retained as `public_03_load_attempt1.jsonl`; it is not
part of the successful-run measurements. No simulator internals, scenario
files, or hidden truth were used for Controller decisions.
