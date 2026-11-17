# Security and trust boundaries

This is a source/configuration review, not a penetration test or production
security certification.

## Current controls

- The HTTP client accepts an absolute HTTP(S) URL but rejects URL-embedded
  credentials and query/fragment components. Redirects are disabled.
- HTTP timeouts and connection counts are bounded. GET retry is bounded;
  uncertain write responses are surfaced for reconciliation.
- Structured logs use an allowlist of fields and do not log request bodies or
  database parameters. SQLAlchemy hides SQL parameter values.
- Environment secrets are not loaded from the checked-in example. `.env`
  patterns are ignored by Git; `.env.example` contains placeholders.
- The development Compose PostgreSQL port is bound to loopback.
- LLM suggestion output is treated as untrusted candidate data and must pass
  structure, allowlist, version, and simulator validation checks before it can
  enter governance.
- Controller source communicates through its HTTP client and does not import
  the bundled simulator implementation.

## Not implemented or not established

The simulator contract does not define authentication. The repository has no
user authentication, authorization roles, tenant isolation, service-to-service
credential rotation, deployment TLS policy, encryption-at-rest policy, data
retention workflow, or customer privacy controls. Those must be designed with
the actual service and deployment owners before real customer data is used.

The local audit table is queryable but is not tamper-evident or an external
compliance archive. No formal threat model, penetration test, or secret-scanner
certification is claimed.
