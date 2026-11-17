# Senda transformation progress

## Current status

The repository is being reframed as Senda, an outbound operations platform
prototype. Application code, migrations, tests, simulator behavior, runtime
configuration, and dependencies are out of scope. All statements about current
capabilities are checked against source; product and market ideas are labeled
as hypotheses or future direction.

## Evidence inventory

| Area | Verified current implementation | Important limit |
|---|---|---|
| Runtime | Async CLI; readiness checks; migrations; serial advance/ingestion/fact/incident loop; orderly signal handling | No hosted service or HTTP health/metrics listener |
| Integration | Central HTTP client; typed response checks; bounded GET retry; ambiguous writes reconciled or halted | Authentication requirements for a real service are not established |
| Ingestion | Ordered history/live chunks; bounded NDJSON streaming; event uniqueness/replay validation; atomic chunk checkpoint | Remote delivery is not exactly once |
| Analytics | Durable event facts; separate pickup and engagement; versioned cohort and history queries | KPI meaning is limited to the fields exposed by the connected contract |
| Governance | Durable proposals/outbox; approval state machine; config-version check; apply intent and reconciliation | Human approval lifecycle is simulator-contract-backed; production identity/access policy is absent |
| Experiments | Deterministic person-cluster estimator, Holm correction, fixed cutoff and persisted decisions | Runtime scheduling and person-stable arm assignment are absent/not guaranteed |
| Incidents | Population QA evaluation, credit-aware suggestions, candidate validation, governed prompt changes and monitoring | No automatic decay detector or rollback |
| Observability | Structured JSON logs, bounded metrics, heartbeat/final summaries | No metrics endpoint, alert routing, or operations team integration |
| Performance | Isolated database fixtures compare current bulk persistence paths | Full optimized workload runtime and 50-minute target are unverified |
| SaaS readiness | Prototype uses PostgreSQL and environment configuration | Tenant isolation, authn/authz, deployment, billing, support, and backups are not established |

## Transformation work log

### Completed

- Senda product identity, vision, positioning, and brand foundation
  (`2b0a180`).
- Product principles, requirements, and explicit boundaries are being added
  in the current unit.
- Initial audience hypotheses and operator journeys documented; no customer
  validation is claimed.
- Feature catalog now labels implemented, partial, and absent capabilities
  with source boundaries.
- Verified the repository is clean on local `main` before this transformation.
- Read the existing README, plans/progress, architecture and policy docs,
  reports, template, configuration, migrations, runtime, storage, analytics,
  governance, incident, statistics, simulator client, and relevant tests.
- Confirmed the latest repository state contains the COPY staging paths and
  isolated fixture evidence documented in the source reports. No new benchmark
  or application test has been run for this transformation.
- Established the Senda product name and evidence boundaries in
  `docs/product/PRODUCT_VISION.md` and `docs/brand/`.

### In progress

- Replace assignment-oriented documentation in reviewable documentation-only
  commits.
- Add product, architecture, engineering, business-hypothesis, roadmap, brand,
  and launch documents with cross-links.
- Remove evaluator-only reports and submission artifacts after their useful
  implementation evidence has been transferred into product engineering docs.

### Verification

- Initial `git status --short --branch`: clean on `main` tracking `origin/main`.
- No tests, load tests, benchmarks, migrations, simulator writes, or database
  operations have been run as part of this transformation.
- Final Markdown-link, stale-branding, factual-claim, diff, and Git checks are
  pending.

## Owner review before public release

- Confirm product-name/trademark/domain availability.
- Decide whether to publish the bundled local simulator and scenario fixtures.
- Confirm the intended public repository name and URL.
- Review launch drafts. No external submission or push is performed here.
