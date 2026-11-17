# Engineering principles

- Put HTTP behavior at the client boundary, persistence at the storage
  boundary, and business derivation in analytics/governance components.
- Make durable units explicit. Keep ordered data and its checkpoint in one
  transaction; keep a projected page and cursor in one transaction.
- Assume every process can stop and every remote response can be lost.
  Persist intent before external writes and reconcile from documented reads.
- Preserve source provenance and data-quality classification.
- Keep primary outcome and guardrail measures separate.
- Bound pages, batches, connection pools, timeouts, retries, and telemetry
  dimensions.
- Prefer deterministic, inspectable rules for statistical decisions. Do not
  delegate outcome selection to an LLM.
- Require approval for sensitive changes and apply only the approved payload.
- Optimize measured bottlenecks and verify behavior and recovery before
  interpreting fixture timings.
- Make incomplete evidence and missing operational controls visible.
