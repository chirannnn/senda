# Production readiness

This review describes the repository, not a deployment certification. A local
controller prototype is not considered production-ready as a SaaS product.

| Area | Status | Evidence and remaining work |
|---|---|---|
| Authentication | Not implemented / contract unknown | No service auth contract or user login exists; define identity and machine credentials |
| Authorization | Not implemented | No role/permission model; define operator, approver, and service privileges |
| Multi-tenancy and isolation | Not implemented | No tenant model or cross-tenant isolation controls |
| Secrets management | Partially addressed locally | Environment configuration and placeholder example; production secret storage/rotation absent |
| Encryption | Not established | TLS and at-rest policy not supplied by the contract or deployment |
| API security | Partially implemented | URL validation, timeouts, redirect disable, input/response validation; no auth/rate limiting policy |
| Rate limiting | Not implemented | No inbound API server or outbound global rate limiter |
| Background reliability | Partially implemented | Single process, durable checkpoints/outbox; no worker orchestration or HA |
| Database migrations | Implemented and verified in isolated PostgreSQL tests | Alembic revisions; production rollout/rollback runbook not validated |
| Backup and restore | Not implemented | No backup schedule, restore test, retention, or disaster-recovery system |
| Monitoring and alerting | Partially implemented | Structured logs and bounded metrics; no exporter, alert routing, or SLO |
| Deployment and horizontal scaling | Not implemented | CLI and local Compose database only; no deployment packaging or multi-worker design |
| Disaster recovery | Partially implemented at operation level | Restart recovery tested; cluster/site recovery and RPO/RTO absent |
| Retention and privacy | Not implemented | No policy for event/person data retention, deletion, or subject requests |
| Billing and onboarding | Not implemented | No commercial service or customer workflow |
| Operational support | Not implemented | No support process, on-call ownership, or customer escalation path |

## Release gates before real customer data

1. Define the production service API, authn/authz, tenant boundary, TLS, and
   data-processing/retention requirements.
2. Implement and review tenant isolation and least-privilege access.
3. Establish secret rotation, encryption, backup/restore, migration rollout,
   and disaster-recovery procedures; test restoration.
4. Deploy behind operational health checks, metrics export, actionable alerts,
   resource budgets, and an on-call owner.
5. Validate against a representative permitted workload on target
   infrastructure and document correctness, capacity, and recovery results.
6. Define human approval roles, audit retention, incident response, and
   customer support ownership.

Until these gates are met, describe this repository as a prototype, not a
production service.
