# Product principles

1. **Measure outcomes separately.** Preserve each KPI's event definition and
   denominator; never hide trade-offs in a blended score.
2. **Use observed evidence.** Keep source provenance, time windows, cohort,
   configuration version, and data-quality status available to decisions.
3. **Treat uncertainty as a result.** Insufficient or conflicting evidence
   should lead to no proposal or a request for review.
4. **Keep humans in control of sensitive changes.** A recommendation does not
   authorize a customer-facing configuration change.
5. **Make recovery part of the workflow.** Persist intent before remote writes,
   reconcile ambiguous outcomes, and do not claim exactly-once remote effects.
6. **Explain decisions.** Retain the evidence, reason, actor, approved change,
   and observed outcome needed to reconstruct a decision.
7. **Earn automation incrementally.** Automate only within explicit policy and
   tested operating boundaries; do not infer permission from a successful
   experiment.
8. **Be precise about maturity.** Separate implemented prototype behavior from
   planned product capabilities and production requirements.
