# Product boundaries

## Current system boundary

The current repository is a controller process. It consumes a documented
HTTP contract, writes operational records to PostgreSQL, and uses the same
contract for reads and governed writes. A bundled simulator acts as a local
development and contract-test harness. It is not the Senda voice agent and is
not a production dependency for a future integration.

The controller does not originate or route telephone calls. It does not own
the source system's workflow configuration, customer records, or identity
policy. It should act only on fields exposed by the connected service contract.

## Not established in this repository

- Hosted multi-tenant product, tenant isolation, user accounts, role-based
  authorization, or customer-facing UI.
- Production integrations, service authentication, TLS deployment policy,
  secrets rotation, or customer data retention controls.
- Traffic allocation or stable person-level experiment assignment.
- Runtime experiment scheduling, decay detection, automatic rollback, or
  workflow fairness across tenants.
- Billing, customer support, service-level objectives, backups, and disaster
  recovery for a hosted service.

These are not implicit capabilities. They require product and operational
design before customer use.
