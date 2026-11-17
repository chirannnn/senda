# Engineering roadmap

Proposed priorities, ordered by prerequisites rather than estimated dates:

1. Define real-service authentication, authorization, tenant isolation, data
   retention, and privacy requirements.
2. Add production deployment configuration, secrets management, database
   backup/restore, migration rollout, and tested recovery objectives.
3. Add an operator-facing read surface and approval visibility with clear
   authorization boundaries.
4. Integrate metrics export, dashboards, alerts, SLOs, and operational
   ownership.
5. Define assignment/randomization guarantees and an experiment scheduler
   before automatically launching experiments.
6. Measure a representative optimized workload on declared target
   infrastructure and set capacity/resource budgets from those results.
7. Reassess scaling architecture only when observed bottlenecks justify more
   workers, concurrency, or additional infrastructure.

No work in this roadmap is represented as implemented by listing it here.
