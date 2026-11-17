# Failure modes and responses

| Failure | Current response | Recovery boundary |
|---|---|---|
| Database unavailable at startup | Initialization/migration fails and CLI exits nonzero | Restore database availability; restart the process |
| Invalid descriptor or NDJSON event | Validation fails before successful chunk completion | Correct service-side data/contract issue; no invalid checkpoint is recorded |
| Process stops during chunk transaction | PostgreSQL rolls back the uncommitted event/checkpoint transaction | Restart and replay the descriptor |
| Exact event chunk replay | Existing rows are checked; same content is idempotent | Continue from durable checkpoint |
| Conflicting event replay | Conflict is raised | Stop and investigate source identity/content mismatch |
| Ambiguous advance response | Intent remains unresolved; startup compares state and chunk descriptors | Continue only if progress or no-progress is proven; otherwise manual reconciliation |
| Ambiguous approval or apply response | Reconcile using approval lookup and/or config history | Do not blindly repeat an uncertain write |
| Stale base configuration | Proposal is rejected/staled before apply | Generate a fresh proposal against current config if evidence still supports it |
| Suggestion/validation API failure | No approval/apply based on unvalidated output | Retry only through the incident lifecycle's explicit recovery path; no generic POST retry |
| Statistical evidence insufficient | Persist a no-winner decision and reason | No proposal; obtain a new predeclared experiment only when policy permits |
| Shutdown signal | Wait for in-flight operation boundary, close client and database resources | Restart from persisted checkpoint/intents |

An unreconciled remote operation can require human intervention. This is an
intentional fail-closed state, not an exactly-once guarantee.
