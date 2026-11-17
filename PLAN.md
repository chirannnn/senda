# Senda repository transformation plan

## Objective

Present this repository as a credible, evidence-led Senda engineering
prototype. Replace evaluator- and assignment-oriented documentation with a
clear product narrative, source-verified architecture, practical developer
guidance, honest production assessment, and launch drafts. Preserve working
application behavior, migrations, tests, and useful engineering decisions.

## Guardrails

- Do not claim planned capabilities, customers, deployment, or performance that
  source code and retained evidence do not establish.
- Distinguish the Senda product direction from the current simulator-backed
  controller prototype.
- Do not modify application behavior, API contracts, schemas, migrations,
  dependencies, simulator behavior, or infrastructure.
- Remove evaluator-only documents from the current public-facing tree while
  preserving the repository's Git history; do not rewrite history.
- Do not run full or partial workloads, stress tests, or benchmarks as part of
  the documentation transformation.
- Do not publish internal database names, machine details, or assignment-only
  benchmark narratives.

## Evidence-based capability status

| Capability | Current status | Source evidence |
|---|---|---|
| HTTP client, readiness checks, serial runtime and shutdown | Implemented and verified in repository tests | `controller/__main__.py`, `controller/runtime/lifecycle.py`, `controller/clients/simulator.py`, `tests/unit/test_runtime.py` |
| Ordered event ingestion, validation, durable checkpoints and advance recovery | Implemented and verified in unit/integration tests | `controller/ingestion/`, `controller/storage/store.py`, `tests/integration/ingestion/` |
| PostgreSQL persistence, migrations and replay-conflict checks | Implemented and verified in PostgreSQL tests | `controller/storage/`, `migrations/`, `tests/integration/storage/test_store.py` |
| Event-derived facts, cohort/history queries and separate pickup/engagement rates | Implemented and verified in unit/integration tests | `controller/analytics/`, `tests/unit/analytics/`, `tests/integration/analytics/` |
| Governance approval and safe apply reconciliation | Implemented as callable lifecycle and verified in tests | `controller/governance/`, `tests/integration/governance/` |
| Statistical comparison and governed proposal integration | Implemented as callable evaluator; runtime scheduling is not implemented | `controller/statistics/`, `controller/experiments.py`, `tests/unit/test_experiments.py` |
| Population incident workflow, candidate validation and governed remediation | Implemented and invoked by runtime; demonstrated by retained tests | `controller/incidents.py`, `tests/unit/test_incidents.py`, `tests/integration/` |
| Structured logs and bounded runtime telemetry | Implemented; external metrics/alert delivery is absent | `controller/telemetry.py`, `controller/observability/`, `tests/unit/test_telemetry.py` |
| Hosted service, authentication, authorization, tenant isolation and customer UI | Not implemented or not established by the repository | No service/UI/authentication implementation in `controller/` |
| Full optimized workload target | Unverified | `docs/engineering/PERFORMANCE.md` |

## Documentation workstreams

1. Define Senda's vision, audience hypotheses, principles, capabilities, and
   boundaries.
2. Replace legacy architecture notes with source-accurate system, data,
   API, reliability, security, and operations documentation.
3. Establish engineering, testing, performance, and production-readiness
   guidance grounded in current code and evidence.
4. Document business hypotheses and roadmap without implying validation.
5. Prepare restrained brand and launch drafts, then rewrite the README as the
   repository entry point.
6. Remove obsolete evaluator-facing material, update the progress log, and
   verify links, claims, and repository cleanliness.

## Acceptance criteria

- README answers what Senda is, what exists today, how the components work,
  how to configure/run/test the prototype, and what remains unbuilt.
- Architecture diagrams match the actual single-process runtime and current
  PostgreSQL storage; they do not invent services or infrastructure.
- Every capability is identified as implemented, partial, designed, or future
  direction where relevant.
- Product and business documents distinguish hypotheses from verified facts.
- Performance documentation reports only scoped measurements, makes no
  projected full-run claim, and says the full-workload target is unverified.
- Production readiness is assessed by dimension; the product is not labeled
  production-ready.
- Launch drafts contain no fabricated URL, contact, customer, or traction.
- Evaluator-only submission and stakeholder documents are absent from the
  current tree; source code, tests, migrations, and useful test harnesses are
  retained.
- Markdown links and `git diff --check` pass. No application tests, benchmark,
  load test, or simulator workload is needed for this documentation-only work.

## Decisions requiring owner review before public release

- Confirm Senda name and trademark/domain availability.
- Choose the eventual public repository name/URL and decide whether to publish
  the bundled simulator and scenario fixtures.
- Supply an official support/security contact only if one is established.
- Review public launch drafts before posting. Nothing is submitted or pushed by
  this plan.
