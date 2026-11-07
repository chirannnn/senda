# Statistical decision policy (stat-policy-v1)

## Scope and inputs

This policy evaluates one workflow, one config version, one cohort, one changed
dimension (`welcome_message` or `voice`), one control and up to three declared
challengers. It uses only persisted `KpiFact` call records. A fact must be
`quality=valid`, have a person ID, config version, cohort, timestamp and observed
variant, and fall in the declared half-open time window and at or below the
declared event-ID cutoff. Facts outside that fixed snapshot are not counted.
Invalid, incomplete, unsupported and unattributed calls are excluded.

The simulator samples variant allocations per dial and does not promise a
person remains in one arm. The evaluator therefore excludes every person who
appears in more than one tested arm, and resamples remaining observations by
person cluster. Calls from one person are never treated as independent
observations. This conservative exclusion can reduce evidence and can introduce
selection effects; it does not make the simulator's assignment a person-level
randomization guarantee. The result must disclose the crossover count.

Pickup and engagement are estimated separately:

- Pickup: connected calls / dials.
- Engagement: concluded calls / connected calls.

No blended KPI is computed. The workflow's API-provided `objective.primary`
selects the primary measure. Its other KPI must have a configured
`<kpi>_max_drop` in `objective.guardrail`; that exact number is the
non-inferiority margin. A missing, malformed or same-as-primary guardrail
prevents evaluation. No controller default is substituted for it.

## Estimation and uncertainty

The point estimate is challenger rate minus control rate, using additive
success/trial counts. Uncertainty uses a deterministic person-cluster percentile
bootstrap with 10,000 resamples by default. The seed is derived from policy
version, experiment ID, event cutoff, changed dimension, challenger and KPI.
Every call for a sampled person is resampled together. Undefined resamples are
discarded and deterministically redrawn, up to ten attempts per requested
replicate; if that bound cannot produce all replicates, that comparison is
insufficient and cannot win.

Bootstrap p-values are computed from the null-centered resampling distribution
for the relevant threshold. Primary superiority uses threshold zero. Guardrail
non-inferiority uses negative the configured max-drop. The persisted interval
uses a two-sided percentile interval at confidence `1 - alpha / challenger
count`, a conservative simultaneous interval for the planned family.

Sample-size planning is explicit rather than a hidden fixed minimum. The plan
must provide primary and guardrail baseline rates, allocation probabilities,
mean cluster size, intracluster correlation, power, alpha, MDE, and planned
comparison count. The superiority approximation uses the two-proportion
variance under the specified baseline and MDE. The guardrail uses a
non-inferiority approximation under an equal-rate alternative and the configured
margin. Both inflate observations by `1 + (mean_cluster_size - 1) * ICC` and
use `alpha / comparison_count` for conservative family planning. The functions
return required control and treatment trial counts for the specified allocation
ratio. A power claim is valid only for the supplied assumptions; the evaluator
does not invent or infer them. The default MDE of 0.05 is an engineering
starting point only, is configurable per plan, and is not an approved business
target.

The plan also requires a predeclared allocation imbalance tolerance. After
crossover exclusion, the evaluator compares the observed person-arm ratio to the
declared allocation ratio. An out-of-tolerance comparison is insufficient; it
is not repaired by reweighting. Since the simulator's assignment is per dial,
this person-ratio check is a conservative diagnostic, not proof of randomized
person-level allocation.

## Multiplicity and decision rule

The full planned challenger family is fixed before evaluation; the maximum is
three. Holm step-down correction is applied separately to primary superiority
tests and guardrail non-inferiority tests. A challenger without sufficient
evidence remains in both families with p-value 1; an unrun challenger is never
removed to make the correction easier.

A challenger is a winner only when all of these hold:

1. It meets the power-calculated primary and guardrail trial counts, has at
   least two distinct people per arm, and passes the declared allocation
   balance check.
2. Its estimated primary lift is at least the configured engineering MDE and
   its Holm-adjusted primary p-value is at most alpha.
3. The lower simultaneous bootstrap bound for the other KPI is strictly above
   negative the workflow's configured max-drop, and its Holm-adjusted
   non-inferiority p-value is at most alpha.
4. Its persisted config version and objective still match the current workflow
   immediately before proposal submission, and the winner is still an allowed
   workflow option.

If no challenger passes, there is no winner and no proposal. A primary result
that passes while the guardrail does not is recorded as unsafe and creates no
proposal. If multiple challengers pass, rank by adjusted primary p-value, then
larger estimated lift, then lexicographically smaller variant ID. That final
tie-break is deterministic.

## Fixed stopping, audit and governance

The evaluator requires a fixed start, end and event-ID cutoff; it does not
perform interim looks. Results are immutable and idempotent by experiment ID.
The database permits only one experiment comparison family per workflow config
version, preventing a second ID or a later cutoff from being used to peek at the
same configuration. A subsequent family requires a new config version. The
family may test only one cohort and one changed dimension; comparisons across
cohorts or dimensions are not pooled or corrected together in this version.

The persisted decision includes the complete plan assumptions, policy version,
cutoff, input digest and count, KPI counts, estimates, intervals, adjusted
p-values, guardrail outcome and reason. The input digest covers the ordered
source facts without copying person identifiers into the decision snapshot. The
decision row and `experiment_decided` audit entry commit in one PostgreSQL
transaction. A replay with the same ID and plan returns the saved decision. A
different plan, input or decision under the same identity is rejected.

Only a matching persisted winner may enter proposal creation. The proposal path
re-reads workflow metadata and full config, checks config version/objective and
allowed option, changes only the selected cohort/dimension allocation, and
submits through the existing `POST /approvals` lifecycle. The proposal carries
the observed evidence, decision reason, policy version and exact diff. No code
in this chunk applies a config or bypasses human approval. Non-winners, stale
config and missing evidence are audited with no proposal.

No LLM participates in estimation, uncertainty, multiplicity, stopping, KPI
selection or winner selection.

## Known limits

- No simulator API currently guarantees stable person-to-arm assignment. Cross-
  arm people are excluded, but this cannot fully remove assignment-selection
  bias or prove person-level randomization.
- The controller runtime does not schedule or automatically start experiment
  plans. An orchestration layer must supply the predeclared plan inputs and call
  the evaluator at the terminal cutoff. Missing baselines, cluster assumptions,
  power, allocation tolerance or workflow guardrail means fail closed.
- One family per workflow config version is intentionally restrictive. It
  prevents repeated peeking and uncorrected cross-family comparisons, but it
  requires a governed config version change before a later experiment.
- The normal-approximation sample-size functions are planning tools, not a
  guarantee under severe cluster-size skew, time drift, noncompliance or
  crossover selection. Input assumptions are stored for review.
- Statistical decisions do not implement winner decay, rollback, incident
  detection or experiment traffic assignment. Those are outside this chunk.
