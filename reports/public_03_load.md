# Public 03 load and observability report

- Measurement ID: `5d8b186f-7698-4ff2-9af1-6a0eb7a72b14`; scenario label: `public_03_load` (the simulator does not expose a scenario identifier in the sampled state)
- Result: **COMPLETED**
- Controller argv: `["C:\\Users\\Chiranjeevi D\\Desktop\\code-journey\\SD\\interviews\\agentic-universe\\.venv\\Scripts\\python.exe", "-m", "controller", "http://127.0.0.1:8721"]`
- Host: Windows-11-10.0.26200-SP0; 12 effective CPU capacity; 16888967168 bytes effective memory limit, 4960960512 bytes available at report time
- Python: 3.12.10; database: PostgreSQL via configured `CONTROLLER_DATABASE_URL`
- Sampler interval: 5 seconds
- Raw process/database/state samples: `reports\public_03_load_samples.jsonl` (1125 rows)
- Controller exit code: `0`; timed out: `False`
- Wall runtime: 5878.203 seconds
- Simulated minutes advanced: 7200
- Wall seconds per simulated day: 1175.641
- SRE target (<600 seconds per simulated day): FAIL
- Events persisted: 9737439
- Facts persisted: 9737439
- Event insert throughput: 1656.962 events/second
- Simulated ingestion lag: 181.000 minutes
- Latest-event wall persistence lag: 53.784 seconds
- API request latency p95 upper bucket: 0.005 seconds (time to response headers)
- API requests/status/failures: `{"GET /events/chunks": {"failures": 0, "requests": 1922, "retryable_responses": 0, "status_codes": {"200": 1922}}, "GET /events/chunks/{chunk_number}": {"failures": 0, "requests": 268, "retryable_responses": 0, "status_codes": {"200": 268}}, "GET /health": {"failures": 0, "requests": 1, "retryable_responses": 0, "status_codes": {"200": 1}}, "GET /sim/state": {"failures": 0, "requests": 962, "retryable_responses": 0, "status_codes": {"200": 962}}, "GET /workflows": {"failures": 0, "requests": 482, "retryable_responses": 0, "status_codes": {"200": 482}}, "GET /workflows/{workflow_id}/config": {"failures": 0, "requests": 5772, "retryable_responses": 0, "status_codes": {"200": 5772}}, "GET /workflows/{workflow_id}/config/history": {"failures": 0, "requests": 5772, "retryable_responses": 0, "status_codes": {"200": 5772}}, "POST /sim/advance": {"failures": 0, "requests": 480, "retryable_responses": 0, "status_codes": {"200": 480}}}`
- Peak controller RSS (sampled): 4546560.000 bytes; runtime OS peak: unknown bytes
- Average process CPU fraction of host capacity: 0.000; process CPU seconds: 0.000
- Database size: 8279743 -> 9441285823 bytes (growth 9433006080 bytes); sampled peak 9441285823.000 bytes
- Checkpoint: 267; analytics cursor: 9737439
- Fact quality counts: valid=9737439, invalid=0, incomplete=0, unsupported=0
- Separate outcomes (pickup, engagement, business): `{"business_outcome_rate": 0.45277647569444446, "engagement_rate": 0.5461295469917766, "pickup_rate": 0.25697406062856376}`
- Decisions/hour: 0.000; persisted decisions total: 0; credits used/remain: None/400.0
- Final simulator state: `{"chunks": 268, "credits_remaining": 400.0, "finished": true, "last_event_id": 9737439, "sim_minute": 7200, "ts": "2027-01-09T00:00:00+05:30"}`
- Governance/outbox state counts: `{"incident_statuses": {}, "outbox_statuses": {"succeeded": 480}, "proposal_statuses": {}}`
- Controller JSON log: `reports\public_03_load.jsonl`
- Sampling errors: `[]`

## Measurement notes

The run is measured only from public simulator HTTP state and the Controller's PostgreSQL records. No scenario content, hidden truth, or simulator run directory was read. API latency is measured to response headers; full chunk download, validation, and persistence are in chunk-ingestion duration. The external sampler adds periodic read-only state and database-size queries. RSS is sampled at the configured interval and may miss short peaks; the Controller also reports its OS peak RSS when available. A successful run is `COMPLETED` only when the Controller exits zero; timeout and nonzero exit are `INCOMPLETE`.

## Bottleneck evidence

Stage histogram totals: `{"advance_and_ingest_seconds": 656.657734399836, "chunk_download_seconds": 38.18409389999579, "chunk_ingestion_seconds": 1377.1315835000423, "chunk_persistence_seconds": 1338.924292599986, "fact_processing_seconds": 4320.7404445999855}`. The largest observed aggregate stage is the evidence-led candidate bottleneck; 10x capacity is not claimed unless a measured stage scales linearly and database/API headroom is known.
