# Population incident policy

This policy defines conservative evidence gates for the implemented population
incident workflow. The evidence-quality thresholds are engineering rules, not
business harm targets.

## Detection

- Detection is triggered by the documented workflow config history when a
  supervisor changes `prompt_version`.
- The controller compares persisted connected-call QA facts from the config
  immediately before the change with facts after it. Only calls with valid QA
  and provenance are included.
- The default evidence gate is 30 distinct people per version and at least 75%
  QA completeness among connected calls. These values are constants accepted as
  parameters by the orchestration function and can be changed by a caller.
- A person observed in both versions is excluded from both arms. The simulator
  contract does not promise sticky person-to-arm assignment, so the controller
  does not treat cross-version observations as independent.
- Once both arms pass the evidence gate, the controller evaluates one fixed
  event cutoff. A deterministic person-cluster bootstrap estimates the increase
  in hallucination flags. A proposal is considered only when the observed rate
  increased and the one-sided bootstrap tail probability is at most 0.10. This
  is an engineering triage screen over an imperfect proxy, not an approved
  business significance level; the fixed cutoff prevents repeated peeking.
  This is a population detector, not a claim about hidden hallucination truth.
- If a later supervisor config replaces the candidate config before evidence
  reaches the minimum, the earlier change is not evaluated. No decision is made
  from an incomplete window.

## Suggestion and validation

- The controller checks `/sim/state` before calling `/llm/suggest-fixes` (cost
  3 credits). It records intent before the POST and never retries an ambiguous
  request because the simulator documents no suggestion lookup endpoint.
- Suggestions are untrusted. Only prompt IDs in the workflow's documented
  allowlist are retained. Candidate order is sorted by stable prompt ID; LLM
  summaries do not rank candidates.
- Runtime validation uses `n=100` as a low-cost engineering default (1 credit),
  after checking credits. A candidate passes only if the exact validation result
  matches its workflow and prompt and meets the workflow's configured
  `min_validation_pass_rate`. This synthetic check measures safety only.
- An HTTP 402 or ambiguous paid POST stops that paid phase. It does not trigger
  an uncontrolled retry.

## Governance and monitoring

- A passing prompt candidate is submitted through Chunk 5 `/approvals` with the
  exact validation run ID, base config version and prompt-only diff. Only the
  Chunk 5 approval reconciler can apply it.
- Pending approvals are polled once per simulator tick. Rejected, expired,
  lapsed, stale and ambiguous outcomes remain durable and are never applied.
- After apply, the controller waits for the same 30-person and 75%-complete
  post-apply evidence gate, then freezes one event cutoff and compares QA,
  primary KPI and configured guardrail with the pre-change population. The
  guardrail uses the workflow's actual configured margin; missing configuration
  prevents a positive safety decision.
- If fixed-window evidence establishes harm, a follow-up prompt proposal is
  generated and submitted against the current config through the same approval
  lifecycle. There is no automatic rollback or direct config write.

## Limitations

The QA hallucination flag is explicitly imperfect. Per-person version
assignment is not guaranteed by the simulator, so overlapping people are
excluded and can reduce power. The evidence threshold and bootstrap rule have
not been calibrated as business policy. The documented APIs do not provide
lookup endpoints for ambiguous suggestion or validation POSTs; those phases
halt for manual review. Incident orchestration is bounded by observed facts and
simulated time and does not infer scenario truth.

