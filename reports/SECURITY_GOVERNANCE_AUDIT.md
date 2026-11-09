# Security and Governance Audit

Review date: 2026-11-09. This is a source/configuration review, not a
penetration test. Only public contract documentation and repository code were
used; no hidden scenario truth was accessed.

## Simulator boundary

- Runtime HTTP calls are centralized in `controller/clients/simulator.py` and
  correspond to documented endpoints in `docs/SIMULATOR.md`: health/state,
  workflows/config/history, chunk list/fetch, advance, approvals, apply,
  suggestion, and validation.
- `controller/` has no `sim` imports, scenario-YAML reads, or `runs/` reads.
  `tests/contract/test_simulator_contract.py` checks the API boundary.
- Redirects are disabled. GET retries are bounded; write requests do not use
  generic automatic retries. Ambiguous advances, approvals and applies are
  reconciled through documented reads or halt for review.

## Governance and compliance boundary

- The runtime invokes `process_population_incidents()` after startup and each
  committed advance. Prompt changes require persisted evidence, available
  credits, a matching validation result and the existing Chunk 5 approval/apply
  lifecycle. The public run is recorded in `reports/public_02.md`.
- `controller/governance/` persists proposal and outbox intent before approval
  writes, rechecks approval and base config before apply, and audits state
  changes. No Controller code path calls the simulator's direct config-write
  endpoint. An unresolved remote outcome requires reconciliation/manual review.
- The experiment evaluator can create a proposal only through the same
  governance path. It is not automatically scheduled by the runtime.
- The simulator exposes workflow options, objectives, governance policy,
  validation floors and auto-apply policy. This Controller uses the approval
  route for its customer-facing changes. The repository cannot verify legal or
  client restrictions that are not represented by the simulator API; any such
  external policy needs an agreed API/configuration source.

## Secrets, logging and deployment config

- `.gitignore` excludes `.env`, `.env.*`, `runs/`, `.venv/`, local database
  files and bytecode; `.env.example` is explicitly allowed. There was no
  `.env.example` at the start of this audit; a placeholder-only file is added
  with the setup documentation.
- `docker-compose.dev.yml` requires database values through environment
  interpolation and binds PostgreSQL to loopback (`127.0.0.1:5433`). It does
  not embed credentials or expose PostgreSQL on all interfaces.
- A scan of 135 tracked files found no private-key, AWS access-key,
  credential-URL or high-entropy credential-assignment matches. A Git-history
  scan found no private-key/AWS-key pattern commits. This pattern scan is
  limited and is not equivalent to a comprehensive secret-scanning product.
- Settings reject URL-embedded simulator credentials and redact malformed
  values (`tests/unit/test_settings.py`). SQLAlchemy hides SQL parameters;
  structured logging uses an allowlist (`controller/observability/logging.py`,
  `tests/unit/test_logging.py`). Database URLs are read from process
  environment and Alembic renders them with passwords hidden.
- The simulator contract describes no authentication scheme. Authentication,
  TLS policy, and credential rotation for a real deployment are therefore
  unverified and must be supplied by the actual service/deployment contract.

## Result

No concrete approval bypass, hidden-data dependency, committed secret, or
unsafe generic POST retry was found. Compliance enforcement beyond the
workflow policy fields supplied over the API and real-service authentication
remain explicit deployment dependencies.

