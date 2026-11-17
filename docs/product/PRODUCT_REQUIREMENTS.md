# Product requirements

This document distinguishes the intended product loop from the capabilities
present in the repository.

| Requirement | Current state | Evidence / boundary |
|---|---|---|
| Observe workflow outcomes through an API | Implemented in the simulator-backed prototype | `controller/clients/`, `controller/ingestion/` |
| Retain validated events and restart progress | Implemented | PostgreSQL event store and checkpoints; integration tests |
| Measure pickup and engagement independently | Implemented | `controller/analytics/kpis.py`; unit tests |
| Explain changes and evidence | Partially implemented | Durable audit records and queries; no user-facing interface |
| Compare workflow variants deterministically | Implemented as a callable evaluator | Explicit plan required; no runtime scheduling or traffic assignment |
| Recommend changes from population evidence | Implemented for a narrow prompt-incident workflow | `controller/incidents.py`; not a general recommendation engine |
| Gate customer-facing changes with approval | Implemented against the current HTTP contract | `controller/governance/`; real identity and authorization system absent |
| Apply only the reviewed change and reconcile failures | Implemented in the prototype | Safe apply tests; remote exactly-once effect not guaranteed |
| Monitor changes and preserve outcomes | Partially implemented for the incident flow | Population QA monitoring and audit; no general monitoring service |
| Serve multiple customer organizations securely | Not implemented | No tenant/authentication layer in the repository |

## Outcome definitions

The current contract-derived measures are:

- **Pickup:** connected calls divided by dials.
- **Engagement:** concluded calls divided by connected calls.
- **Uploaded-lead conclusion:** concluded leads divided by uploaded leads,
  when those source counts are available.

Pickup and engagement remain distinct. A zero denominator produces an
undefined result rather than a fabricated zero rate.
