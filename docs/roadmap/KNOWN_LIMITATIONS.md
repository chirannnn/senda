# Known limitations

## Product surface

- The current system is a CLI prototype with no customer UI or hosted service.
- No customer, deployment, service-level objective, or usage evidence is
  established.
- The bundled simulator is a development/test harness, not a production
  Senda API.

## Security and operations

- Real API authentication and TLS policy are unspecified.
- User authorization, multi-tenancy, tenant data isolation, secrets rotation,
  retention/deletion, backups, restore, alerting, and on-call operations are
  absent or unverified.

## Automation and measurement

- Experiments require a caller-supplied fixed plan and are not automatically
  scheduled. The service does not guarantee person-stable experiment arms.
- Incident automation covers a narrow population prompt-QA workflow. Automatic
  decay detection and rollback are not implemented.
- The runtime's serial ordered processing and single-process lifecycle limit
  available concurrency.
- Isolated persistence fixtures demonstrate local operation-level differences
  only. Optimized full-workload runtime and target throughput remain unverified.

These limitations are release gates and roadmap inputs, not hidden exceptions
to the current product description.
