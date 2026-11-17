# User personas (hypotheses)

No customer interviews or usage data are included in this repository. These
personas describe the people the product direction is intended to serve; they
are hypotheses to validate.

## Outbound operations leader

Owns campaign outcomes and day-to-day workflow configuration. Needs to see
where people disengage, compare meaningful populations, and understand the
operational impact of a change without relying on a single blended metric.

## Compliance or risk reviewer

Reviews sensitive customer-facing changes. Needs the exact proposed diff,
supporting evidence, validation status, configuration version, approver
identity, and a record of what was eventually applied.

## Platform or data engineer

Operates the integration and its persistence layer. Needs bounded resource
usage, replay-safe ingestion, clear failure states, migration discipline,
observability, and recovery procedures.

## Frontline supervisor

Owns a particular workflow and may edit it while a proposal is pending. Needs
stale changes to be detected rather than silently overwriting current
configuration. A supervisor UI is not present in this repository.
