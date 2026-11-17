# Senda FAQ

### What is Senda?

Senda is the product direction for an intelligent outbound operations
platform. This repository currently contains a simulator-backed controller
prototype, not a hosted customer product.

### What does the current prototype do?

It ingests events from an HTTP service contract, persists them in PostgreSQL,
derives KPI facts, supports deterministic experiment evaluation, and runs a
narrow population prompt-incident workflow through a governed approval path.

### Is Senda a voice agent or dialer?

No. The controller observes and manages workflow operations through an API;
the external service runs the calls.

### Does it change customer configuration automatically?

Sensitive changes use a human approval lifecycle. The prototype does not
provide a direct unapproved configuration-write path.

### Is Senda production-ready or available to customers?

No such status is established. Authentication, tenant isolation, deployment,
backups, alerting, support, and customer-facing interfaces remain open work.

### What performance can I expect?

No production throughput or full-workload runtime is claimed. The repository
records isolated PostgreSQL fixture comparisons; see
[performance methodology](../engineering/PERFORMANCE.md) for their scope and
limits.

### Where can I learn about the implementation?

Start with [the system overview](../architecture/SYSTEM_OVERVIEW.md),
[feature catalog](../product/FEATURE_CATALOG.md), and
[developer setup](../../README.md).
