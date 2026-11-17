# Governance and decision lifecycle

## Proposal path

The governance package validates proposal fields and stable identity, hashes
the exact request, persists the proposal and outbox intent, then submits the
request through the documented approval endpoint. Duplicate/conflict
responses are handled using the stable proposal identity and remote lookups;
an ambiguous POST is not blindly repeated.

Approval states are reconciled through the approval lookup/list APIs and
documented decision events. Before apply, the controller rereads the approval
and workflow configuration, verifies that the approved proposal still matches
the current base version, and persists apply intent. It applies the exact
approved proposal identity. If a response is ambiguous, config history and
approval state are used to prove the result; unresolved cases require manual
review.

The implementation is in `controller/governance/` and storage methods are in
`controller/storage/`. Unit and integration coverage includes proposal
validation, state transitions, approval submission, stale configuration,
ambiguous apply, and safe reconciliation.

## Experiments

`controller/experiments.py` evaluates a caller-supplied fixed plan against
persisted valid KPI facts and a fixed event cutoff. It uses deterministic
person-level cluster bootstrap, Holm correction, separate primary and
guardrail decisions, declared baseline/allocation/clustering inputs, and a
fixed stopping rule. It stores the plan, input digest, evidence snapshot,
decision and reason. It cannot count planned traffic as observed exposure.

The simulator does not guarantee person-stable arm assignment. The evaluator
excludes people observed in multiple arms and clusters remaining repeated calls
by person. This does not prove person-level randomization or eliminate
selection bias. No LLM makes a statistical decision. The runtime does not
schedule experiments or assign traffic.

See [statistical policy](../DECISION_POLICY.md) for the implemented policy
details and assumptions. The decision policy remains an engineering policy,
not a validated business target or customer-approved efficacy threshold.

## Incident path

`controller/incidents.py` evaluates population QA evidence around a prompt
change. It checks quality and minimum evidence, checks credit availability
before requesting a suggestion, validates untrusted candidate output using
the service API, and submits only a matching validated candidate through the
governance lifecycle. After apply it compares observed population evidence;
any correction is a new proposal. It does not automatically roll back a
configuration.

## Audit properties

Decisions and state transitions retain reasons and evidence in PostgreSQL.
The audit query path is read-only and keyset-paginated. Audit persistence
improves local traceability; it is not tamper-proof storage, an external
compliance archive, or proof of regulatory compliance.
