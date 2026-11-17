# HTTP integration contract

`controller/clients/simulator.py` is the sole runtime boundary to the bundled
workflow simulator. Endpoint names below describe the integration contract
used by this repository; they are not a promise of a public Senda API.

| Method and path | Used for | Safety behavior |
|---|---|---|
| `GET /health` | Readiness | Response must report healthy state |
| `GET /sim/state` | Current time, completion, credits and advance reconciliation | Read with bounded retry policy |
| `GET /workflows` | Startup workflow discovery | Response shape validated |
| `GET /workflows/{id}/config` | Current config and base version | Version/config shape validated before governed action |
| `GET /workflows/{id}/config/history` | Reconcile an apply and inspect versions | Read-only reconciliation evidence |
| `GET /events/chunks?kind=history\|events&after=n` | Discover ordered chunks | Descriptors are parsed and order-checked |
| `GET /events/chunks/{chunk}` | Stream chunk NDJSON | Body is spooled, parsed, and validated before atomic persistence |
| `POST /sim/advance` | Advance the connected simulator | Intent persisted first; ambiguous response is reconciled, never blindly retried |
| `POST /approvals` | Submit a proposal | Stable ID/outbox persisted first; ambiguous result requires lookup/reconciliation |
| `GET /approvals`, `GET /approvals/{id}` | Poll/reconcile approval state | Read-only; state transitions are idempotent |
| `POST /workflows/{id}/apply` | Apply an approved proposal | Approval and current version rechecked; intent persisted first |
| `POST /llm/suggest-fixes` | Request candidate text for an incident | Credits checked first; output treated as untrusted; no automatic retry |
| `POST /validation/runs` | Validate a candidate prompt | Candidate and config version are matched; credit-spending request is not retried |

The client configures explicit connect/read/write/pool timeouts, bounded
connections, disabled redirects, and bounded GET retry with backoff. POST
transport failures and server errors that leave effect uncertain become
ambiguous-operation errors. The client does not issue a generic retry for
remote writes. It validates response structures and raises typed service
errors for rejected HTTP responses.

The service contract used by the implementation does not specify a real
authentication mechanism. A production integration must define authentication,
TLS, authorization, and credential rotation before deployment.
