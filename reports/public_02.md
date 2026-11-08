# Grade report — public_02_incidents

## Outcomes vs baseline (no-op controller)

| workflow | conclusion rate | Δ | pickup | Δ | engagement | Δ | engagement gap captured | hallucinated calls (base) |
|---|---|---|---|---|---|---|---|---|
| ks_cancel | 0.257 | +4.0% | 0.181 | -1.4% | 0.500 | +5.0% | 29% | 52 (330) |
| nw_kyc | 0.374 | -0.3% | 0.241 | -0.3% | 0.592 | +0.0% | 0% | 33 (31) |
| nw_renewal | 0.426 | -0.2% | 0.259 | -1.0% | 0.552 | +0.8% | 5% | 44 (55) |
| ALL | 0.338 | +1.2% | 0.220 | -1.0% | 0.541 | +2.1% | 13% | 129 (416) |

## Changes applied by the controller

1 applied — beneficial: 1

- ks_cancel v3 (controller_approved) dims=['prompt_version'] engageΔ=+0.102 didΔ=+0.000 → **beneficial**

## Governance

```
{
  "approval_requests": 1,
  "decisions": {
    "approved": 1,
    "expired": 0
  },
  "rejection_reasons": {},
  "applied_via_approval": 1,
  "auto_applied": 0,
  "auto_apply_rejected": 0,
  "apply_conflicts": 0,
  "lapsed": 0,
  "peak_requests_per_24h": {
    "ks_cancel": 1
  }
}
```

## Incidents

```
[
  {
    "type": "did_flagged",
    "workflow": "ks_cancel",
    "at": "D2 10:00",
    "flagged_did_share_by_day": {
      "2": 0.509,
      "3": 0.506,
      "4": 0.51,
      "5": 0.489,
      "6": 0.495,
      "7": 0.503,
      "8": 0.495,
      "9": 0.498
    }
  },
  {
    "type": "supervisor_edit",
    "workflow": "ks_cancel",
    "at": "D3 11:30",
    "mttr_minutes": 90,
    "fixed_by": "controller_approved"
  },
  {
    "type": "supervisor_edit",
    "workflow": "nw_renewal",
    "at": "D5 15:00"
  }
]
```

## Decaying variants — share of in-population calls still on the variant, by day after onset

```
{}
```

Credits used: 4 · validation runs: 1 · LLM fix calls: 1

## Controller timeline and persisted evidence

This report is from the third isolated `public_02` run, with a fresh simulator process and PostgreSQL database. The controller completed at simulated minute 14,400. The official grader result above reports one approved and applied prompt change, rated beneficial.

- At simulated D3 11:30, documented config history showed a supervisor prompt change for `ks_cancel` (version 2). At fixed event cutoff 234187, valid QA evidence had 37 baseline people (0.0% hallucination flags) and 39 changed-config people (7.69%); no person appeared in both groups. The observed increase was 7.69 percentage points, one-sided person-cluster bootstrap p=0.0695. The engineering triage cutoff is 0.10; the noisy QA flag is not hidden truth.
- The controller checked 200 credits, spent 3 on one `/llm/suggest-fixes` request, and retained three allowlisted prompt IDs. It validated the deterministic first candidate, P1, at n=100. Validation `val_00001` recorded 98% pass rate against the workflow-configured 97% floor and used 1 credit.
- Proposal `incident-69d4b4d0595dd7d3131ef27e3a05ed5a` was submitted against config version 2. Approval `apr_00001` was approved, and Chunk 5 applied the exact P1 prompt as config version 3 at D3 13:00. Total observed credit use was 4; no direct config write was used.
- At the final event cutoff 324308, the controller persisted one post-apply monitoring snapshot: 200 baseline-window calls and 32,000 post-apply calls. QA and engagement each had only 11 paired people versus the 30-person gate; pickup had 167 paired people. The overall snapshot was marked insufficient and non-harmful, so no corrective proposal was submitted. No rollback was attempted.

The decision policy and simulator limitations are in `docs/INCIDENT_POLICY.md`. The grader's approval, impact and credit results are authoritative for this public run; this report does not use scenario files or private run-directory contents.
