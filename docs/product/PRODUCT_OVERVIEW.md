# Product overview

Senda is being explored as an operational decision layer for outbound
engagement workflows. It connects what happened in a workflow to how outcomes
are measured, how a change is evaluated, who authorizes it, and what is
observed afterward.

## Intended lifecycle

1. Collect operational events from the connected workflow service.
2. Validate and persist events with their source identity and progress.
3. Derive explicit performance measures and preserve data quality.
4. Compare relevant time periods, configurations, variants, or populations.
5. Identify a possible improvement from observed evidence.
6. Evaluate the proposed change against a predeclared policy and guardrail.
7. Submit a validated change for human approval.
8. Apply only the exact approved proposal against the current configuration.
9. Observe actual post-change outcomes.
10. Preserve the evidence, decision, actor, and outcome for later review.

## What the prototype demonstrates

The repository implements event ingestion, raw/fact persistence, separate KPI
calculations, versioned cohort and history queries, deterministic experiment
evaluation, a narrow population prompt-incident path, approval/apply
reconciliation, audit, and bounded runtime telemetry.

Some lifecycle steps are callable components rather than autonomous product
features. In particular, experiments are not scheduled and traffic is not
assigned by the runtime. The repository has no operator UI or hosted service.
The [feature catalog](FEATURE_CATALOG.md) and
[known limitations](../roadmap/KNOWN_LIMITATIONS.md) give the fuller boundary.
