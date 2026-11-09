# Submission Checklist

Checked: 2026-11-09. No external submission or GitHub push has been made.

| Deliverable | Required or optional | Status | Location / verification |
|---|---|---|---|
| Controller and launcher | Required | COMPLETE | `controller/`, `run.sh`; README setup instructions reviewed. |
| Official evaluator-facing submission | Required | READY - actual hours pending | `SUBMISSION_TEMPLATE.md`; candidate name is filled. `SUBMISSION.md` is the matching supporting copy. |
| Public_01 grader output | Required | COMPLETE | `reports/public_01.md`; recovered unchanged from recorded temporary grader output; SHA-256 source/destination match. Shows zero changes and 0% KPI delta. |
| Public_02 grader and incident results | Required | COMPLETE | `reports/public_02.md`; one approved change rated beneficial, credit and timeline evidence. |
| Public_03 load numbers | Required | COMPLETE, target failed | `reports/public_03_load.md` and raw logs/samples. Historical full run: 97.97 minutes; SRE time criterion failed. |
| Crash-recovery evidence | Required | COMPLETE | `reports/crash_recovery.md`; real process kill/restart against unchanged public_02 simulator and PostgreSQL. |
| Commit history | Required | COMPLETE locally | `git log`; incremental implementation and Chunk 10 commits exist on local `main`. |
| README / developer setup | Required | COMPLETE | `README.md`, `.env.example`; Compose, migrations, runtime and disposable integration DB steps documented. |
| Architecture documentation | Required for this handoff | COMPLETE | `docs/ARCHITECTURE.md`. |
| Requirement audit | Required for this handoff | COMPLETE | `reports/REQUIREMENT_IMPLEMENTATION_AUDIT.md`; includes partial/cut/unverified capabilities. |
| Performance report | Required | COMPLETE, target unverified | `reports/FINAL_PERFORMANCE_REPORT.md`, plus detailed investigation/recommendations. No full rerun after COPY. |
| Final engineering report | Required for this handoff | COMPLETE | `reports/FINAL_SUBMISSION_REPORT.md`. |
| Test evidence summary | Required for this handoff | COMPLETE | `SUBMISSION_TEMPLATE.md`, `PROGRESS.md`, `reports/FINAL_SUBMISSION_REPORT.md`; 306 passed against isolated PostgreSQL. |
| Security/governance review | Required for this handoff | COMPLETE with limits | `reports/SECURITY_GOVERNANCE_AUDIT.md`; API auth/TLS remain unverified. |
| Repository URL | Required for external handoff | OWNER ACTION | Configured origin is `https://github.com/chirannnn/formi-agent-controller.git`. Latest commits are local and have not been pushed. Confirm intended repository and update it only when authorized. |
| Grant reviewer access | Required by assignment form | OWNER ACTION | README says share the private repository with the email on the submission form. This was not performed. |
| Walkthrough video | Optional | NOT CREATED | Assignment allows an optional video of five minutes or less. |
| Screenshots | Not specified | NOT REQUIRED | Assignment deliverables do not request screenshots. |
| Submission email | Not specified | NOT REQUIRED | Assignment requests repository sharing via the form; no email draft was created or sent. |
| `PROJECT.md` | Not specified as a deliverable; requested for audit | MISSING IN REPO | README is the in-repository assignment brief. Actual architecture is documented in `docs/ARCHITECTURE.md`. |

## Before external submission

1. Confirm and fill the actual hours in `SUBMISSION_TEMPLATE.md`; candidate name is filled. Keep `SUBMISSION.md` consistent.
2. Review the claims and replies in the official template and matching copy;
   be ready to defend them in the interview.
3. Confirm the configured GitHub origin is the intended private repository.
   The Chunk 10 commits currently exist only in this local branch; publish or
   transfer them only through the owner's authorized workflow.
4. Share repository access with the email on the assignment form.
5. Do not describe the 50-minute target as achieved. It remains unverified
   unless a new complete public_03 benchmark is separately run and passes.
