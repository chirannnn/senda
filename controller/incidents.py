"""Population-level incident evidence and durable incident identity."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Protocol

from controller.errors import (
    AmbiguousPostError,
    ProposalReconciliationRequired,
    SimulatorError,
    SimulatorHTTPError,
)
from controller.governance.proposals import ProposalPayload, submit_proposal
from controller.governance.reconcile import apply_approved_proposal
from controller.governance.state_machine import ProposalStatus, poll_approval
from controller.statistics.estimation import holm_adjust
from controller.storage.audit import AuditEntry, AuditLog
from controller.storage.models import KpiFact
from controller.storage.store import DatabaseStore, IncidentStateConflictError


class IncidentSuggestionAPI(Protocol):
    """Documented read and one-shot suggestion operations used by incident response."""

    async def get_state(self) -> dict[str, Any]: ...

    async def list_workflows(self) -> list[dict[str, Any]]: ...

    async def get_workflow_config(self, workflow_id: str) -> dict[str, Any]: ...

    async def suggest_fixes(self, workflow_id: str, problem: str) -> dict[str, Any]: ...

    async def run_validation(
        self, workflow_id: str, prompt_version: str, sample_size: int
    ) -> dict[str, Any]: ...

    async def submit_approval(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def list_approvals(
        self, *, workflow_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def get_approval(self, approval_id: str) -> dict[str, Any]: ...

    async def apply_approval(
        self, workflow_id: str, approval_id: str
    ) -> dict[str, Any]: ...

    async def get_config_history(self, workflow_id: str) -> list[dict[str, Any]]: ...


SUGGESTION_CREDIT_COST = 3.0


def validation_credit_cost(sample_size: int) -> int:
    """Return the documented ceil(n/100) validation credit cost."""
    if (
        isinstance(sample_size, bool)
        or not isinstance(sample_size, int)
        or not 50 <= sample_size <= 2000
    ):
        raise ValueError("validation sample size must be an integer in [50, 2000]")
    return math.ceil(sample_size / 100)


@dataclass(frozen=True, slots=True)
class PopulationQAEvidence:
    """QA signal aggregated by person, with explicit completeness and uncertainty."""

    workflow_id: str
    config_version: int
    cutoff_event_id: int
    call_count: int
    valid_qa_calls: int
    incomplete_qa_calls: int
    person_count: int
    hallucination_rate: float | None
    lower: float | None
    upper: float | None
    sufficient: bool
    reason: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class IncidentMetricComparison:
    metric: str
    baseline_rate: float
    post_rate: float
    difference: float
    lower: float
    upper: float
    adjusted_p: float
    paired_people: int
    baseline_trials: int
    post_trials: int
    sufficient: bool
    harmful: bool

    def to_json(self) -> dict[str, object]:
        return asdict(self)


async def aggregate_population_qa(
    store: DatabaseStore,
    *,
    workflow_id: str,
    config_version: int,
    cutoff_event_id: int,
    minimum_people: int,
    max_incomplete_fraction: float,
    replicates: int = 10_000,
    page_size: int = 1000,
) -> PopulationQAEvidence:
    """Compute person-clustered hallucination prevalence from valid persisted calls.

    The evidence gate inputs are mandatory so this function does not invent a
    business threshold or silently treat repeated calls as independent people.
    """
    if not workflow_id or config_version < 0 or cutoff_event_id < 0:
        raise ValueError("incident workflow, version and cutoff are required")
    if isinstance(minimum_people, bool) or minimum_people < 2:
        raise ValueError("minimum_people must be at least two")
    if (
        not math.isfinite(max_incomplete_fraction)
        or not 0 <= max_incomplete_fraction < 1
    ):
        raise ValueError("max_incomplete_fraction must be finite and in [0, 1)")
    if replicates < 100 or page_size < 1:
        raise ValueError("bootstrap replicates and page size are too small")

    rows: list[KpiFact] = []
    after = 0
    while True:
        page = await store.list_incident_qa_facts(
            workflow_id=workflow_id,
            config_version=config_version,
            cutoff_event_id=cutoff_event_id,
            after_event_id=after,
            limit=page_size,
        )
        if not page:
            break
        rows.extend(page)
        after = page[-1].source_event_id

    valid = [
        row
        for row in rows
        if row.qa_quality == "valid" and isinstance(row.qa_hallucination, bool)
    ]
    incomplete = len(rows) - len(valid)
    by_person: dict[str, list[int]] = {}
    for row in valid:
        if row.person_id is not None:
            by_person.setdefault(row.person_id, []).append(int(row.qa_hallucination))
    person_rates = [
        sum(values) / len(values) for _, values in sorted(by_person.items())
    ]
    people = len(person_rates)
    call_count = len(rows)
    completeness = len(valid) / call_count if call_count else 0.0
    enough = people >= minimum_people and completeness >= 1 - max_incomplete_fraction
    rate: float | None = None
    lower: float | None = None
    upper: float | None = None
    if enough:
        rate = sum(person_rates) / people
        seed_material = f"{workflow_id}:{config_version}:{cutoff_event_id}:qa-v1"
        seed = int.from_bytes(
            hashlib.sha256(seed_material.encode()).digest()[:8], "big"
        )
        rng = random.Random(seed)
        samples = sorted(
            sum(rng.choice(person_rates) for _ in person_rates) / people
            for _ in range(replicates)
        )
        lower = _quantile(samples, 0.025)
        upper = _quantile(samples, 0.975)
    reason = (
        "sufficient population QA evidence"
        if enough
        else "insufficient people or excessive incomplete QA facts"
    )
    return PopulationQAEvidence(
        workflow_id=workflow_id,
        config_version=config_version,
        cutoff_event_id=cutoff_event_id,
        call_count=call_count,
        valid_qa_calls=len(valid),
        incomplete_qa_calls=incomplete,
        person_count=people,
        hallucination_rate=rate,
        lower=lower,
        upper=upper,
        sufficient=enough,
        reason=reason,
    )


async def persist_incident_evidence(
    store: DatabaseStore,
    audit: AuditLog,
    evidence: PopulationQAEvidence,
) -> str:
    """Persist a fixed-cutoff, idempotent incident snapshot and audit event."""
    identity = (
        f"{evidence.workflow_id}:{evidence.config_version}:{evidence.cutoff_event_id}"
    )
    incident_id = "inc-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
    payload = evidence.to_json()
    status = "evidence_recorded" if evidence.sufficient else "insufficient_evidence"
    async with store.transaction() as session:
        _, created = await store.save_incident(
            session,
            incident_id=incident_id,
            workflow_id=evidence.workflow_id,
            trigger_config_version=evidence.config_version,
            status=status,
            evidence=payload,
            timeline=[
                {
                    "event": status,
                    "cutoff_event_id": evidence.cutoff_event_id,
                    "evidence": payload,
                }
            ],
        )
        if created:
            await audit.append(
                session,
                AuditEntry(
                    action="incident_evidence_aggregated",
                    actor="controller",
                    reason=evidence.reason,
                    workflow_id=evidence.workflow_id,
                    resource_type="incident",
                    resource_id=incident_id,
                    evidence=payload,
                ),
            )
    return incident_id


async def request_candidate_suggestions(
    simulator: IncidentSuggestionAPI,
    store: DatabaseStore,
    audit: AuditLog,
    *,
    incident_id: str,
    problem: str,
) -> dict[str, object]:
    """Request one credit-checked LLM suggestion response with durable no-retry intent."""
    if not isinstance(problem, str) or not problem.strip():
        raise ValueError("a non-empty problem description is required")
    incident = await store.get_incident(incident_id)
    if incident is None:
        raise ValueError(f"unknown incident {incident_id!r}")
    if incident.status == "suggestion_intent":
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="suggestion_ambiguous",
            reason="A prior suggestion request has durable intent but no stored response; no lookup API exists, so it was not retried",
            entry={"event": "suggestion_ambiguous", "manual_review_required": True},
        )
    if incident.status != "evidence_recorded" or not incident.evidence.get(
        "sufficient"
    ):
        return {
            "status": incident.status,
            "reason": "incident evidence is not eligible",
        }

    current = await simulator.get_workflow_config(incident.workflow_id)
    current_version = current.get("version")
    workflow = next(
        (
            item
            for item in await simulator.list_workflows()
            if item.get("workflow_id") == incident.workflow_id
        ),
        None,
    )
    options = workflow.get("options") if isinstance(workflow, dict) else None
    prompt_options = (
        options.get("prompt_version") if isinstance(options, dict) else None
    )
    current_config = current.get("config")
    if (
        isinstance(current_version, bool)
        or not isinstance(current_version, int)
        or current_version != incident.trigger_config_version
        or not isinstance(current_config, dict)
        or not isinstance(current_config.get("prompt_version"), str)
        or not isinstance(prompt_options, list)
        or any(not isinstance(item, str) for item in prompt_options)
        or current_config["prompt_version"] not in prompt_options
    ):
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="stale_before_suggestion",
            reason="Workflow config version or prompt allowlist changed before candidate generation",
            entry={
                "event": "stale_before_suggestion",
                "expected_config_version": incident.trigger_config_version,
                "current_config_version": current.get("version"),
            },
        )

    state = await simulator.get_state()
    remaining = state.get("credits_remaining")
    if (
        isinstance(remaining, bool)
        or not isinstance(remaining, (int, float))
        or not math.isfinite(remaining)
        or remaining < 0
    ):
        raise ValueError("simulator state has no valid credits_remaining balance")
    if remaining < SUGGESTION_CREDIT_COST:
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="insufficient_credits",
            reason="Available simulator credits are below the documented suggestion cost",
            evidence_update={
                **incident.evidence,
                "suggestion_credit_check": {
                    "available_before": float(remaining),
                    "documented_cost": SUGGESTION_CREDIT_COST,
                    "spent": 0,
                },
            },
            entry={
                "event": "insufficient_credits",
                "available_before": float(remaining),
                "documented_cost": SUGGESTION_CREDIT_COST,
            },
        )

    request_evidence = {
        **incident.evidence,
        "suggestion_request": {
            "config_version": incident.trigger_config_version,
            "current_prompt_version": current_config["prompt_version"],
            "available_credits_before": float(remaining),
            "documented_cost": SUGGESTION_CREDIT_COST,
            "problem": problem,
        },
    }
    try:
        await _incident_transition(
            store,
            audit,
            incident_id,
            status="suggestion_intent",
            expected_status="evidence_recorded",
            reason="Persisted one-shot suggestion intent after checking simulator credits",
            evidence_update=request_evidence,
            entry={
                "event": "suggestion_intent",
                "config_version": incident.trigger_config_version,
                "available_credits_before": float(remaining),
                "documented_cost": SUGGESTION_CREDIT_COST,
            },
        )
    except IncidentStateConflictError:
        latest = await store.get_incident(incident_id)
        return {
            "status": latest.status if latest is not None else "unknown",
            "reason": "another request already claimed or completed this incident phase",
        }
    try:
        response = await simulator.suggest_fixes(incident.workflow_id, problem)
    except AmbiguousPostError as exc:
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="suggestion_ambiguous",
            reason="Suggestion POST outcome is ambiguous; the simulator provides no lookup endpoint",
            entry={"event": "suggestion_ambiguous", "reason": exc.reason},
        )
    except SimulatorHTTPError as exc:
        if exc.status_code == 402:
            return await _incident_transition(
                store,
                audit,
                incident_id,
                status="insufficient_credits",
                reason="Suggestion endpoint returned HTTP 402; paid calls stop for this incident",
                evidence_update={
                    **request_evidence,
                    "suggestion_credit_check": {
                        "available_before": float(remaining),
                        "documented_cost": SUGGESTION_CREDIT_COST,
                        "spent": 0,
                        "http_status": 402,
                    },
                },
                entry={"event": "suggestion_402", "available_before": float(remaining)},
            )
        after_error = await _credits_if_available(simulator)
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="suggestion_failed",
            reason="Simulator rejected the suggestion request; no retry was attempted",
            evidence_update={
                **request_evidence,
                "suggestion_failure": {
                    "http_status": exc.status_code,
                    "credits_remaining_after": after_error,
                    "credits_used_observed": (
                        float(remaining) - after_error
                        if after_error is not None
                        else None
                    ),
                },
            },
            entry={"event": "suggestion_failed", "http_status": exc.status_code},
        )
    except SimulatorError as exc:
        after_error = await _credits_if_available(simulator)
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="suggestion_response_invalid",
            reason="Suggestion response could not be validated; candidates were discarded and no retry was attempted",
            evidence_update={
                **request_evidence,
                "suggestion_failure": {
                    "error_type": type(exc).__name__,
                    "credits_remaining_after": after_error,
                    "credits_used_observed": (
                        float(remaining) - after_error
                        if after_error is not None
                        else None
                    ),
                },
            },
            entry={
                "event": "suggestion_response_invalid",
                "error_type": type(exc).__name__,
                "credits_remaining_after": after_error,
            },
        )

    candidates = response["suggestions"]
    allowed = set(prompt_options)
    candidates = [
        candidate
        for candidate in candidates
        if candidate["prompt_version"] in allowed
        and candidate["prompt_version"] != current_config["prompt_version"]
        and len(candidate["summary"]) <= 500
    ]
    after = response["credits_remaining"]
    used = float(remaining) - float(after)
    if used < 0:
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="suggestion_response_invalid",
            reason="Simulator response reported more credits than the pre-call balance",
            entry={"event": "suggestion_response_invalid", "credits_remaining": after},
        )
    result = {
        **request_evidence,
        "suggestion": {
            "candidates": candidates,
            "current_prompt_version": current_config["prompt_version"],
            "available_before": float(remaining),
            "available_after": float(after),
            "credits_used_observed": used,
            "documented_cost": SUGGESTION_CREDIT_COST,
            "config_version": incident.trigger_config_version,
        },
    }
    status = "candidates_ready" if candidates else "no_permitted_candidates"
    return await _incident_transition(
        store,
        audit,
        incident_id,
        status=status,
        reason=(
            "Stored allowlisted candidate suggestions and observed credit balance"
            if candidates
            else "Suggestion response contained no candidate permitted by current workflow options"
        ),
        evidence_update=result,
        entry={
            "event": status,
            "candidate_count": len(candidates),
            "credits_used_observed": used,
            "credits_remaining": float(after),
        },
    )


async def validate_incident_candidate(
    simulator: IncidentSuggestionAPI,
    store: DatabaseStore,
    audit: AuditLog,
    *,
    incident_id: str,
    prompt_version: str,
    sample_size: int,
) -> dict[str, object]:
    """Validate one stored suggestion, binding evidence to its prompt and base version."""
    cost = validation_credit_cost(sample_size)
    if not isinstance(prompt_version, str) or not prompt_version.strip():
        raise ValueError("candidate prompt version is required")
    incident = await store.get_incident(incident_id)
    if incident is None:
        raise ValueError(f"unknown incident {incident_id!r}")
    versions = incident.evidence.get("suggestion", {}).get("candidates", [])
    if not isinstance(versions, list) or not any(
        isinstance(item, dict) and item.get("prompt_version") == prompt_version
        for item in versions
    ):
        raise ValueError("candidate must exactly match a stored allowlisted suggestion")
    validations = dict(incident.evidence.get("validations", {}))
    previous = validations.get(prompt_version)
    if isinstance(previous, dict):
        if previous.get("status") == "intent":
            validations[prompt_version] = {**previous, "status": "ambiguous"}
            return await _incident_transition(
                store,
                audit,
                incident_id,
                status="validation_ambiguous",
                reason="A validation request intent has no persisted response; no lookup API exists, so it was not retried",
                evidence_update={**incident.evidence, "validations": validations},
                entry={
                    "event": "validation_ambiguous",
                    "prompt_version": prompt_version,
                    "manual_review_required": True,
                },
            )
        terminal_status = {
            "ambiguous": "validation_ambiguous",
            "passed": "candidate_validated",
            "rejected": "candidate_rejected",
            "insufficient_credits": "insufficient_credits",
            "invalid_response": "validation_invalid_response",
            "stale_or_mismatched": "validation_failed",
            "credit_mismatch": "validation_failed",
            "failed": "validation_failed",
        }.get(previous.get("status"), "unknown")
        return {**previous, "status": terminal_status}
    if incident.status not in {
        "candidates_ready",
        "candidate_validated",
        "candidate_rejected",
    }:
        return {
            "status": incident.status,
            "reason": "incident is not eligible for validation",
        }

    current = await simulator.get_workflow_config(incident.workflow_id)
    workflow = next(
        (
            item
            for item in await simulator.list_workflows()
            if item.get("workflow_id") == incident.workflow_id
        ),
        None,
    )
    version = current.get("version")
    config = current.get("config")
    governance = workflow.get("governance") if isinstance(workflow, dict) else None
    floor = (
        governance.get("min_validation_pass_rate")
        if isinstance(governance, dict)
        else None
    )
    options = workflow.get("options") if isinstance(workflow, dict) else None
    prompts = options.get("prompt_version") if isinstance(options, dict) else None
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != incident.trigger_config_version
        or not isinstance(config, dict)
        or not isinstance(config.get("prompt_version"), str)
        or not isinstance(prompts, list)
        or prompt_version not in prompts
        or isinstance(floor, bool)
        or not isinstance(floor, (int, float))
        or not math.isfinite(floor)
        or not 0 <= floor <= 1
    ):
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="stale_or_unconfigured_validation",
            reason="Current config, candidate allowlist or configured validation floor is missing or stale",
            entry={
                "event": "validation_not_started",
                "prompt_version": prompt_version,
                "expected_config_version": incident.trigger_config_version,
                "current_config_version": version,
            },
        )

    state = await simulator.get_state()
    remaining = state.get("credits_remaining")
    if (
        isinstance(remaining, bool)
        or not isinstance(remaining, (int, float))
        or not math.isfinite(remaining)
        or remaining < 0
    ):
        raise ValueError("simulator state has no valid credits_remaining balance")
    if remaining < cost:
        validations[prompt_version] = {
            "status": "insufficient_credits",
            "sample_size": sample_size,
            "documented_cost": cost,
            "credits_before": float(remaining),
            "credits_used_observed": 0,
            "config_version": version,
        }
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="insufficient_credits",
            reason="Available simulator credits are below the documented validation cost",
            evidence_update={**incident.evidence, "validations": validations},
            entry={
                "event": "validation_insufficient_credits",
                "prompt_version": prompt_version,
                "available_before": float(remaining),
                "documented_cost": cost,
            },
        )

    validations[prompt_version] = {
        "status": "intent",
        "sample_size": sample_size,
        "documented_cost": cost,
        "credits_before": float(remaining),
        "config_version": version,
        "minimum_pass_rate": float(floor),
    }
    try:
        await _incident_transition(
            store,
            audit,
            incident_id,
            status="validation_intent",
            expected_status=incident.status,
            reason="Persisted one-shot validation intent before spending simulator credits",
            evidence_update={**incident.evidence, "validations": validations},
            entry={
                "event": "validation_intent",
                "prompt_version": prompt_version,
                "sample_size": sample_size,
                "documented_cost": cost,
                "config_version": version,
                "available_before": float(remaining),
            },
        )
    except IncidentStateConflictError:
        latest = await store.get_incident(incident_id)
        return {
            "status": latest.status if latest is not None else "unknown",
            "reason": "another request already claimed this validation phase",
        }
    try:
        result = await simulator.run_validation(
            incident.workflow_id, prompt_version, sample_size
        )
    except AmbiguousPostError as exc:
        validations[prompt_version] = {
            **validations[prompt_version],
            "status": "ambiguous",
            "reason": exc.reason,
        }
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="validation_ambiguous",
            reason="Validation POST outcome is ambiguous and cannot be reconciled by a documented lookup API",
            evidence_update={**incident.evidence, "validations": validations},
            entry={
                "event": "validation_ambiguous",
                "prompt_version": prompt_version,
                "reason": exc.reason,
            },
        )
    except SimulatorHTTPError as exc:
        after = await _credits_if_available(simulator)
        outcome = "insufficient_credits" if exc.status_code == 402 else "failed"
        validations[prompt_version] = {
            **validations[prompt_version],
            "status": outcome,
            "http_status": exc.status_code,
            "credits_after": after,
            "credits_used_observed": (
                float(remaining) - after if after is not None else None
            ),
        }
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="insufficient_credits"
            if exc.status_code == 402
            else "validation_failed",
            reason=f"Validation endpoint returned HTTP {exc.status_code}; no retry was attempted",
            evidence_update={**incident.evidence, "validations": validations},
            entry={
                "event": "validation_http_error",
                "prompt_version": prompt_version,
                "http_status": exc.status_code,
            },
        )
    except SimulatorError as exc:
        after = await _credits_if_available(simulator)
        validations[prompt_version] = {
            **validations[prompt_version],
            "status": "invalid_response",
            "error_type": type(exc).__name__,
            "credits_after": after,
            "credits_used_observed": (
                float(remaining) - after if after is not None else None
            ),
        }
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="validation_invalid_response",
            reason="Validation response was malformed; it was discarded and not retried",
            evidence_update={**incident.evidence, "validations": validations},
            entry={
                "event": "validation_invalid_response",
                "prompt_version": prompt_version,
                "credits_after": after,
            },
        )

    identity_matches = (
        result.get("workflow_id") == incident.workflow_id
        and result.get("prompt_version") == prompt_version
        and result.get("n") == sample_size
    )
    after = result.get("credits_remaining")
    used = float(remaining) - float(after)
    refreshed = await simulator.get_workflow_config(incident.workflow_id)
    if not identity_matches or refreshed.get("version") != version:
        validation_status = "stale_or_mismatched"
    elif not math.isclose(used, float(cost), abs_tol=1e-9):
        validation_status = "credit_mismatch"
    elif result["pass_rate"] < floor:
        validation_status = "rejected"
    else:
        validation_status = "passed"
    validations[prompt_version] = {
        **validations[prompt_version],
        "status": validation_status,
        "validation_run_id": result["validation_run_id"],
        "workflow_id": result["workflow_id"],
        "prompt_version": result["prompt_version"],
        "sample_size": result["n"],
        "failures": result["failures"],
        "pass_rate": result["pass_rate"],
        "ts": result["ts"],
        "credits_after": float(after),
        "credits_used_observed": used,
        "minimum_pass_rate": float(floor),
        "config_version_after": refreshed.get("version"),
    }
    incident_status = {
        "passed": "candidate_validated",
        "rejected": "candidate_rejected",
    }.get(validation_status, "validation_failed")
    return await _incident_transition(
        store,
        audit,
        incident_id,
        status=incident_status,
        reason=(
            "Validation passed the workflow-configured safety floor and matched candidate and config"
            if validation_status == "passed"
            else "Validation did not establish a safe, matching candidate"
        ),
        evidence_update={**incident.evidence, "validations": validations},
        entry={
            "event": "validation_completed",
            "prompt_version": prompt_version,
            "validation_run_id": result["validation_run_id"],
            "validation_status": validation_status,
            "pass_rate": result["pass_rate"],
            "minimum_pass_rate": float(floor),
            "credits_used_observed": used,
        },
    )


async def submit_governed_prompt_proposal(
    simulator: IncidentSuggestionAPI,
    store: DatabaseStore,
    audit: AuditLog,
    *,
    incident_id: str,
    prompt_version: str,
) -> dict[str, Any] | None:
    """Submit exactly one validated prompt candidate through Chunk 5 approvals."""
    incident = await store.get_incident(incident_id)
    if incident is None:
        raise ValueError(f"unknown incident {incident_id!r}")
    validations = incident.evidence.get("validations", {})
    validation = (
        validations.get(prompt_version) if isinstance(validations, dict) else None
    )
    if (
        incident.status != "candidate_validated"
        or not isinstance(validation, dict)
        or validation.get("status") != "passed"
        or validation.get("prompt_version") != prompt_version
        or validation.get("workflow_id") != incident.workflow_id
        or validation.get("config_version") != incident.trigger_config_version
        or validation.get("config_version_after") != incident.trigger_config_version
    ):
        await _incident_transition(
            store,
            audit,
            incident_id,
            status="proposal_skipped",
            reason="No exact candidate validation passed for this incident and config version",
            entry={
                "event": "proposal_skipped",
                "prompt_version": prompt_version,
                "reason": "missing, unsafe or mismatched validation",
            },
        )
        return None
    proposal_evidence = incident.evidence.get("proposal")
    if isinstance(proposal_evidence, dict) and isinstance(
        proposal_evidence.get("proposal_id"), str
    ):
        existing_id = proposal_evidence["proposal_id"]
        from controller.governance.reconcile import reconcile_approval_submission

        response = await reconcile_approval_submission(
            simulator, store, audit, existing_id
        )
        return response

    current = await simulator.get_workflow_config(incident.workflow_id)
    workflow = next(
        (
            item
            for item in await simulator.list_workflows()
            if item.get("workflow_id") == incident.workflow_id
        ),
        None,
    )
    version = current.get("version")
    config = current.get("config")
    options = workflow.get("options") if isinstance(workflow, dict) else None
    prompts = options.get("prompt_version") if isinstance(options, dict) else None
    governance = workflow.get("governance") if isinstance(workflow, dict) else None
    floor = (
        governance.get("min_validation_pass_rate")
        if isinstance(governance, dict)
        else None
    )
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != incident.trigger_config_version
        or not isinstance(config, dict)
        or config.get("prompt_version")
        != incident.evidence.get("suggestion", {}).get("current_prompt_version")
        or not isinstance(prompts, list)
        or prompt_version not in prompts
        or isinstance(floor, bool)
        or not isinstance(floor, (int, float))
        or not math.isfinite(floor)
        or float(floor) != validation.get("minimum_pass_rate")
    ):
        await _incident_transition(
            store,
            audit,
            incident_id,
            status="proposal_stale",
            reason="Workflow configuration or validation policy changed before proposal submission",
            entry={
                "event": "proposal_stale",
                "prompt_version": prompt_version,
                "expected_config_version": incident.trigger_config_version,
                "current_config_version": version,
            },
        )
        return None

    evidence = {
        "incident_id": incident_id,
        "incident_evidence": incident.evidence,
        "candidate_prompt_version": prompt_version,
        "validation_run_id": validation["validation_run_id"],
        "validation": validation,
        "config_version": version,
        "workflow_validation_floor": float(floor),
        "decision": "submit_for_human_approval_only",
    }
    proposed_config = dict(config)
    proposed_config["prompt_version"] = prompt_version
    proposal_id = (
        "incident-"
        + hashlib.sha256(
            f"{incident_id}:{version}:{prompt_version}:{validation['validation_run_id']}".encode()
        ).hexdigest()[:32]
    )
    person_count = incident.evidence.get("person_count")
    if (
        isinstance(person_count, bool)
        or not isinstance(person_count, int)
        or person_count < 0
    ):
        await _incident_transition(
            store,
            audit,
            incident_id,
            status="proposal_skipped",
            reason="Affected population count is missing or invalid",
            entry={
                "event": "proposal_skipped",
                "reason": "invalid affected population",
            },
        )
        return None
    observed_rate = incident.evidence.get("hallucination_rate")
    impact_score = (
        float(observed_rate)
        if isinstance(observed_rate, (int, float))
        and not isinstance(observed_rate, bool)
        and math.isfinite(observed_rate)
        and 0 <= observed_rate <= 1
        else 0.0
    )
    proposal = ProposalPayload(
        proposal_id=proposal_id,
        workflow_id=incident.workflow_id,
        base_version=version,
        proposed_config=proposed_config,
        change_summary=f"Replace prompt {config['prompt_version']} with validated candidate {prompt_version}",
        diff={
            "dimension": "prompt_version",
            "before": config["prompt_version"],
            "after": prompt_version,
        },
        affected_leads=person_count,
        dry_run={
            "type": "observed_population_qa_and_safety_validation",
            "historical_replay": False,
            "estimated_effect": None,
            "validation_is_safety_only": True,
            "affected_people_observed": person_count,
        },
        impact_score=impact_score,
        rationale=(
            f"Population QA for config {version} observed hallucination flags in "
            f"{impact_score:.4f} of person-clustered observations. Candidate {prompt_version} "
            f"passed the workflow-configured safety floor ({validation['pass_rate']:.4f} >= "
            f"{float(floor):.4f}, n={validation['sample_size']}). This requests approval only; "
            "validation does not establish engagement improvement."
        ),
        evidence=evidence,
    )
    try:
        response = await submit_proposal(simulator, store, audit, proposal)
    except ProposalReconciliationRequired as exc:
        await _incident_transition(
            store,
            audit,
            incident_id,
            status="approval_reconciliation_required",
            reason="Approval submission could not be reconciled; no duplicate was sent",
            entry={
                "event": "approval_reconciliation_required",
                "proposal_id": proposal_id,
                "reason": str(exc),
            },
            evidence_update={
                **incident.evidence,
                "proposal": {"proposal_id": proposal_id, "status": "manual_review"},
            },
        )
        return None

    response_status = response.get("status")
    incident_status = (
        "approval_pending" if response_status == "pending" else "approval_submitted"
    )
    return await _incident_transition(
        store,
        audit,
        incident_id,
        status=incident_status,
        reason="Validated prompt candidate submitted through the existing approval lifecycle",
        evidence_update={
            **incident.evidence,
            "proposal": {
                "proposal_id": proposal_id,
                "approval_id": response.get("approval_id"),
                "status": response_status,
                "base_version": version,
                "proposed_prompt_version": prompt_version,
                "payload_hash": proposal.payload_hash,
            },
        },
        entry={
            "event": "approval_submitted",
            "proposal_id": proposal_id,
            "approval_id": response.get("approval_id"),
            "remote_status": response_status,
            "base_version": version,
            "prompt_version": prompt_version,
        },
    )


async def reconcile_incident_approval(
    simulator: IncidentSuggestionAPI,
    store: DatabaseStore,
    audit: AuditLog,
    *,
    incident_id: str,
) -> dict[str, object]:
    """Poll the Chunk 5 approval state machine and apply only an approved proposal."""
    incident = await store.get_incident(incident_id)
    if incident is None:
        raise ValueError(f"unknown incident {incident_id!r}")
    proposal_evidence = incident.evidence.get("proposal")
    proposal_id = (
        proposal_evidence.get("proposal_id")
        if isinstance(proposal_evidence, dict)
        else None
    )
    if not isinstance(proposal_id, str):
        raise TypeError("incident has no persisted governance proposal")
    proposal = await store.get_governance_proposal(proposal_id)
    if proposal is None or proposal.approval_id is None:
        raise ProposalReconciliationRequired(
            "incident proposal has no reconciled approval ID"
        )
    try:
        status = await poll_approval(simulator, store, audit, proposal_id, max_polls=1)
        if status == ProposalStatus.APPROVED:
            result = await apply_approved_proposal(simulator, store, audit, proposal_id)
            next_status = (
                "applied"
                if result.get("status") == ProposalStatus.APPLIED.value
                else "apply_reconciliation_required"
            )
            return await _incident_transition(
                store,
                audit,
                incident_id,
                status=next_status,
                reason="Chunk 5 governed apply lifecycle processed approved incident proposal",
                entry={
                    "event": next_status,
                    "proposal_id": proposal_id,
                    "apply_result": result,
                },
            )
        mapping = {
            ProposalStatus.PENDING: "approval_pending",
            ProposalStatus.REJECTED: "approval_rejected",
            ProposalStatus.EXPIRED: "approval_expired",
            ProposalStatus.LAPSED: "approval_lapsed",
            ProposalStatus.APPLIED: "applied",
            ProposalStatus.STALE: "proposal_stale",
            ProposalStatus.RECONCILIATION_REQUIRED: "approval_reconciliation_required",
        }
        next_status = mapping[status]
        if next_status == incident.status:
            return {"status": next_status, "proposal_id": proposal_id}
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status=next_status,
            reason=f"Observed Chunk 5 approval state {status.value}",
            entry={"event": next_status, "proposal_id": proposal_id},
        )
    except ProposalReconciliationRequired as exc:
        return await _incident_transition(
            store,
            audit,
            incident_id,
            status="approval_reconciliation_required",
            reason="Approval or apply state requires operator reconciliation",
            entry={
                "event": "approval_reconciliation_required",
                "proposal_id": proposal_id,
                "reason": str(exc),
            },
        )


async def monitor_applied_prompt(
    simulator: IncidentSuggestionAPI,
    store: DatabaseStore,
    audit: AuditLog,
    *,
    incident_id: str,
    monitor_end: datetime,
    cutoff_event_id: int,
    minimum_paired_people: int,
    max_incomplete_fraction: float,
    replicates: int = 10_000,
    page_size: int = 1000,
) -> dict[str, object]:
    """Compare one fixed post-apply window with pre-change observations.

    Person-level paired bootstrap intervals are adjusted with Holm across the
    QA rate, declared primary KPI, and configured guardrail. A later cutoff for
    the same applied version is rejected to prevent repeated peeking.
    """
    if monitor_end.tzinfo is None:
        raise ValueError("monitor end must include a timezone")
    if (
        isinstance(minimum_paired_people, bool)
        or not isinstance(minimum_paired_people, int)
        or minimum_paired_people < 2
    ):
        raise ValueError("minimum paired people must be at least two")
    if (
        isinstance(max_incomplete_fraction, bool)
        or not isinstance(max_incomplete_fraction, (int, float))
        or not math.isfinite(max_incomplete_fraction)
        or not 0 <= max_incomplete_fraction < 1
    ):
        raise ValueError("maximum incomplete fraction must be finite and in [0, 1)")
    if replicates < 100 or page_size < 1:
        raise ValueError("monitoring bootstrap settings are invalid")
    incident = await store.get_incident(incident_id)
    if incident is None:
        raise ValueError(f"unknown incident {incident_id!r}")
    if incident.status not in {"applied", "monitoring_complete"}:
        return {
            "status": incident.status,
            "reason": "proposal has not completed governed apply",
        }
    proposal_data = incident.evidence.get("proposal")
    proposal_id = (
        proposal_data.get("proposal_id") if isinstance(proposal_data, dict) else None
    )
    approval_id = (
        proposal_data.get("approval_id") if isinstance(proposal_data, dict) else None
    )
    base_version = (
        proposal_data.get("base_version") if isinstance(proposal_data, dict) else None
    )
    if not isinstance(proposal_id, str) or not isinstance(approval_id, str):
        raise TypeError("applied incident lacks proposal and approval identities")
    if isinstance(base_version, bool) or not isinstance(base_version, int):
        raise TypeError("applied incident lacks a valid base version")

    current = await simulator.get_workflow_config(incident.workflow_id)
    workflow = next(
        (
            item
            for item in await simulator.list_workflows()
            if item.get("workflow_id") == incident.workflow_id
        ),
        None,
    )
    history = await simulator.get_config_history(incident.workflow_id)
    applied = next(
        (
            item
            for item in history
            if item.get("approval_id") == approval_id
            and item.get("proposal_id") == proposal_id
            and item.get("actor") == "controller_approved"
            and isinstance(item.get("version"), int)
            and not isinstance(item.get("version"), bool)
            and item["version"] > base_version
        ),
        None,
    )
    prior = next(
        (
            item
            for item in history
            if item.get("version") == base_version
            and isinstance(item.get("applied_at"), str)
        ),
        None,
    )
    if (
        applied is None
        or prior is None
        or not isinstance(applied.get("applied_at"), str)
        or current.get("version") != applied.get("version")
    ):
        return await _record_monitor_block(
            store,
            audit,
            incident_id,
            status="monitoring_stale",
            reason="Config history does not match the applied approval",
        )
    baseline_start = _parse_timestamp(prior["applied_at"])
    applied_at = _parse_timestamp(applied["applied_at"])
    if baseline_start is None or applied_at is None or monitor_end <= applied_at:
        return await _record_monitor_block(
            store,
            audit,
            incident_id,
            status="monitoring_insufficient",
            reason="Config history or post-apply window is incomplete",
        )
    objective = workflow.get("objective") if isinstance(workflow, dict) else None
    try:
        primary_name, guardrail_name, margin = _incident_objective(objective)
    except (TypeError, ValueError):
        return await _record_monitor_block(
            store,
            audit,
            incident_id,
            status="monitoring_unconfigured",
            reason="Workflow primary KPI or configured guardrail is missing or invalid",
        )
    config = current.get("config")
    if not isinstance(config, dict) or not isinstance(
        config.get("prompt_version"), str
    ):
        return await _record_monitor_block(
            store,
            audit,
            incident_id,
            status="monitoring_stale",
            reason="Current prompt config is malformed",
        )

    baseline_facts = await _incident_monitor_facts(
        store,
        workflow_id=incident.workflow_id,
        config_version=base_version,
        start_at=baseline_start,
        end_at=applied_at,
        cutoff_event_id=cutoff_event_id,
        page_size=page_size,
    )
    post_facts = await _incident_monitor_facts(
        store,
        workflow_id=incident.workflow_id,
        config_version=applied["version"],
        start_at=applied_at,
        end_at=monitor_end,
        cutoff_event_id=cutoff_event_id,
        page_size=page_size,
    )
    names = ("hallucination_rate", primary_name, guardrail_name)
    raw = {
        name: _paired_metric_comparison(
            name,
            baseline_facts,
            post_facts,
            minimum_paired_people=minimum_paired_people,
            max_incomplete_fraction=float(max_incomplete_fraction),
            harm_threshold=(-margin if name == guardrail_name else 0.0),
            seed=f"{incident_id}:{applied['version']}:{cutoff_event_id}:{name}",
            replicates=replicates,
        )
        for name in names
    }
    adjusted = holm_adjust({name: item["p_value"] for name, item in raw.items()})
    metrics: dict[str, dict[str, object]] = {}
    for name, item in raw.items():
        threshold = -margin if name == guardrail_name else 0.0
        expected_direction = 1 if name == "hallucination_rate" else -1
        directional = (
            item["difference"] > threshold
            if expected_direction == 1
            else item["difference"] < threshold
        )
        metrics[name] = {
            key: item[key]
            for key in (
                "baseline_rate",
                "post_rate",
                "difference",
                "lower",
                "upper",
                "paired_people",
                "baseline_trials",
                "post_trials",
                "sufficient",
            )
        } | {
            "adjusted_p": adjusted[name],
            "harmful": directional and adjusted[name] <= 0.05,
        }
    sufficient = all(bool(item["sufficient"]) for item in metrics.values())
    harmful = sufficient and any(bool(item["harmful"]) for item in metrics.values())
    input_material = {
        "incident_id": incident_id,
        "workflow_id": incident.workflow_id,
        "base_version": base_version,
        "applied_version": applied["version"],
        "baseline_start": baseline_start.isoformat(),
        "applied_at": applied_at.isoformat(),
        "monitor_end": monitor_end.isoformat(),
        "cutoff_event_id": cutoff_event_id,
        "minimum_paired_people": minimum_paired_people,
        "max_incomplete_fraction": float(max_incomplete_fraction),
        "bootstrap_replicates": replicates,
        "primary_kpi": primary_name,
        "guardrail_kpi": guardrail_name,
        "guardrail_margin": margin,
    }
    input_hash = hashlib.sha256(
        json.dumps(input_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    snapshot: dict[str, object] = {
        **input_material,
        "input_hash": input_hash,
        "method": "paired person-cluster bootstrap; Holm adjustment across three outcomes",
        "baseline_call_count": len(baseline_facts),
        "post_call_count": len(post_facts),
        "excluded_baseline_invalid": sum(
            fact.quality != "valid" for fact in baseline_facts
        ),
        "excluded_post_invalid": sum(fact.quality != "valid" for fact in post_facts),
        "sufficient": sufficient,
        "harmful": harmful,
        "comparisons": metrics,
    }
    remediation_id = None
    remediation_evidence = None
    if harmful:
        remediation_id = (
            "inc-"
            + hashlib.sha256(
                f"{incident_id}:{applied['version']}:{cutoff_event_id}:remediation".encode()
            ).hexdigest()[:32]
        )
        snapshot["remediation_incident_id"] = remediation_id
        qa = metrics["hallucination_rate"]
        remediation_evidence = {
            "sufficient": True,
            "reason": "Fixed-window post-apply evidence crossed the documented incident harm rule",
            "parent_incident_id": incident_id,
            "cutoff_event_id": cutoff_event_id,
            "person_count": min(
                int(item["paired_people"]) for item in metrics.values()
            ),
            "hallucination_rate": qa["post_rate"],
            "lower": qa["lower"],
            "upper": qa["upper"],
            "post_apply_monitoring": snapshot,
            "suggestion": {"current_prompt_version": config["prompt_version"]},
        }

    async with store.transaction() as session:
        from sqlalchemy import select

        from controller.storage.models import IncidentRecord

        record = await session.scalar(
            select(IncidentRecord)
            .where(IncidentRecord.incident_id == incident_id)
            .with_for_update()
        )
        if record is None:
            raise ValueError(f"unknown incident {incident_id!r}")
        previous = record.evidence.get("post_apply_monitoring")
        if isinstance(previous, dict):
            if previous.get("input_hash") != input_hash:
                raise IncidentStateConflictError(
                    "monitoring already has a different frozen input cutoff"
                )
            return previous
        record.evidence = {**record.evidence, "post_apply_monitoring": snapshot}
        record.status = "monitoring_complete"
        record.timeline = [
            *record.timeline,
            {
                "event": "post_apply_monitoring_complete",
                "cutoff_event_id": cutoff_event_id,
                "harmful": harmful,
                "sufficient": sufficient,
                "input_hash": input_hash,
            },
        ]
        remediation_created = False
        if harmful and remediation_id is not None and remediation_evidence is not None:
            _, remediation_created = await store.save_incident(
                session,
                incident_id=remediation_id,
                workflow_id=incident.workflow_id,
                trigger_config_version=applied["version"],
                status="evidence_recorded",
                evidence=remediation_evidence,
                timeline=[
                    {
                        "event": "remediation_evidence_recorded",
                        "parent_incident_id": incident_id,
                        "cutoff_event_id": cutoff_event_id,
                        "evidence": remediation_evidence,
                    }
                ],
            )
        await audit.append(
            session,
            AuditEntry(
                action="incident_post_apply_monitoring",
                actor="controller",
                reason=(
                    "Post-apply evidence supports a corrective proposal"
                    if harmful
                    else "Post-apply evidence does not establish a harmful change"
                    if sufficient
                    else "Post-apply evidence is insufficient"
                ),
                workflow_id=incident.workflow_id,
                resource_type="incident",
                resource_id=incident_id,
                evidence=snapshot,
            ),
        )
        if remediation_created and remediation_id is not None:
            await audit.append(
                session,
                AuditEntry(
                    action="incident_remediation_evidence_recorded",
                    actor="controller",
                    reason="Created a follow-up incident against the current applied config",
                    workflow_id=incident.workflow_id,
                    resource_type="incident",
                    resource_id=remediation_id,
                    evidence=remediation_evidence or {},
                ),
            )
    return snapshot


async def propose_monitoring_correction(
    simulator: IncidentSuggestionAPI,
    store: DatabaseStore,
    audit: AuditLog,
    *,
    monitoring_result: dict[str, object],
    validation_sample_size: int,
) -> dict[str, object]:
    """Generate, validate and submit one deterministic follow-up candidate.

    Candidate order is sorted by stable prompt ID; the LLM summary/order never
    selects a winner. Safety validation is a required gate, followed by human
    approval. No apply request is sent here.
    """
    remediation_id = monitoring_result.get("remediation_incident_id")
    if monitoring_result.get("harmful") is not True or not isinstance(
        remediation_id, str
    ):
        return {
            "status": "no_remediation",
            "reason": "monitoring evidence did not establish harm",
        }
    incident = await store.get_incident(remediation_id)
    if incident is None:
        raise ValueError("monitoring result references a missing remediation incident")
    comparisons = monitoring_result.get("comparisons")
    harmful_metrics = (
        [
            name
            for name, result in comparisons.items()
            if isinstance(result, dict) and result.get("harmful") is True
        ]
        if isinstance(comparisons, dict)
        else []
    )
    problem = (
        f"Fixed-window post-apply evidence found adverse population outcomes on "
        f"{', '.join(sorted(harmful_metrics))}; hallucination rate was "
        f"{incident.evidence.get('hallucination_rate')} at cutoff "
        f"{incident.evidence.get('cutoff_event_id')}. Provide alternative prompt candidates."
    )
    suggestions = await request_candidate_suggestions(
        simulator, store, audit, incident_id=remediation_id, problem=problem
    )
    if suggestions.get("status") != "candidates_ready":
        return {
            "status": suggestions.get("status", "suggestion_failed"),
            "incident_id": remediation_id,
        }
    # Suggestion generation persists candidate evidence. Reload before using it;
    # the incident fetched above predates that external request and transition.
    incident = await store.get_incident(remediation_id)
    if incident is None:
        raise ValueError("remediation incident disappeared after candidate generation")
    current_prompt = incident.evidence.get("suggestion", {}).get(
        "current_prompt_version"
    )
    candidates = incident.evidence.get("suggestion", {}).get("candidates", [])
    ordered = sorted(
        (item for item in candidates if isinstance(item, dict)),
        key=lambda item: item.get("prompt_version", ""),
    )
    for candidate in ordered:
        prompt_version = candidate.get("prompt_version")
        if not isinstance(prompt_version, str) or prompt_version == current_prompt:
            continue
        validation = await validate_incident_candidate(
            simulator,
            store,
            audit,
            incident_id=remediation_id,
            prompt_version=prompt_version,
            sample_size=validation_sample_size,
        )
        if validation.get("status") == "candidate_validated":
            proposal = await submit_governed_prompt_proposal(
                simulator,
                store,
                audit,
                incident_id=remediation_id,
                prompt_version=prompt_version,
            )
            return {
                "status": "proposal_submitted"
                if proposal is not None
                else "proposal_skipped",
                "incident_id": remediation_id,
                "prompt_version": prompt_version,
                "proposal": proposal,
            }
        if validation.get("status") not in {"candidate_rejected", "validation_failed"}:
            return {
                "status": validation.get("status", "validation_failed"),
                "incident_id": remediation_id,
            }
    return {"status": "no_validated_candidate", "incident_id": remediation_id}


async def _incident_monitor_facts(
    store: DatabaseStore,
    *,
    workflow_id: str,
    config_version: int,
    start_at: datetime,
    end_at: datetime,
    cutoff_event_id: int,
    page_size: int,
):
    facts = []
    after = 0
    while True:
        page = await store.list_incident_monitor_facts(
            workflow_id=workflow_id,
            config_version=config_version,
            start_at=start_at,
            end_at=end_at,
            cutoff_event_id=cutoff_event_id,
            after_event_id=after,
            limit=page_size,
        )
        if not page:
            return facts
        facts.extend(page)
        after = page[-1].source_event_id


async def _record_monitor_block(
    store: DatabaseStore,
    audit: AuditLog,
    incident_id: str,
    *,
    status: str,
    reason: str,
) -> dict[str, object]:
    return await _incident_transition(
        store,
        audit,
        incident_id,
        status=status,
        reason=reason,
        entry={"event": status, "reason": reason},
    )


def _incident_objective(objective: object) -> tuple[str, str, float]:
    if not isinstance(objective, dict):
        raise TypeError("workflow objective is unavailable for incident monitoring")
    primary = objective.get("primary")
    guardrail = objective.get("guardrail")
    if (
        primary not in {"pickup_rate", "engagement_rate"}
        or not isinstance(guardrail, dict)
        or len(guardrail) != 1
    ):
        raise ValueError("workflow primary KPI and guardrail are malformed")
    guardrail_name, margin = next(iter(guardrail.items()))
    suffix = "_max_drop"
    if not isinstance(guardrail_name, str) or not guardrail_name.endswith(suffix):
        raise ValueError("workflow guardrail must specify its configured max-drop")
    guardrail_name = guardrail_name[: -len(suffix)]
    if (
        guardrail_name not in {"pickup_rate", "engagement_rate"}
        or guardrail_name == primary
    ):
        raise ValueError("workflow guardrail must be the other rate KPI")
    if (
        isinstance(margin, bool)
        or not isinstance(margin, (int, float))
        or not math.isfinite(margin)
        or not 0 < margin <= 1
    ):
        raise ValueError("workflow guardrail margin is missing or invalid")
    return primary, guardrail_name, float(margin)


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _paired_metric_comparison(
    metric: str,
    baseline_facts: list[KpiFact],
    post_facts: list[KpiFact],
    *,
    minimum_paired_people: int,
    max_incomplete_fraction: float,
    harm_threshold: float,
    seed: str,
    replicates: int,
) -> dict[str, object]:
    def rates(facts):
        totals: dict[str, list[int]] = {}
        considered = (
            [
                fact
                for fact in facts
                if fact.connected or fact.quality != "valid" or fact.person_id is None
            ]
            if metric == "hallucination_rate"
            else facts
        )
        relevant = [
            fact
            for fact in considered
            if fact.quality == "valid"
            and fact.person_id
            and (
                metric != "hallucination_rate"
                or (fact.qa_quality == "valid" and fact.qa_hallucination is not None)
            )
        ]
        for fact in relevant:
            if metric == "hallucination_rate":
                success, trials = int(fact.qa_hallucination), 1
            elif metric == "pickup_rate":
                success, trials = fact.connected, fact.dials
            else:
                success, trials = fact.concluded, fact.connected
            row = totals.setdefault(fact.person_id, [0, 0])
            row[0] += success
            row[1] += trials
        return (
            {
                person: success / trials
                for person, (success, trials) in totals.items()
                if trials
            },
            sum(v[1] for v in totals.values()),
            len(relevant),
            len(considered),
        )

    before, before_trials, before_people_rows, before_total = rates(baseline_facts)
    after, after_trials, after_people_rows, after_total = rates(post_facts)
    shared = sorted(set(before) & set(after))
    differences = [after[person] - before[person] for person in shared]
    incomplete_before = 1 - before_people_rows / before_total if before_total else 1.0
    incomplete_after = 1 - after_people_rows / after_total if after_total else 1.0
    sufficient = (
        len(shared) >= minimum_paired_people
        and incomplete_before <= max_incomplete_fraction
        and incomplete_after <= max_incomplete_fraction
    )
    if not sufficient:
        return {
            "baseline_rate": sum(before[p] for p in shared) / len(shared)
            if shared
            else 0.0,
            "post_rate": sum(after[p] for p in shared) / len(shared) if shared else 0.0,
            "difference": 0.0,
            "lower": 0.0,
            "upper": 0.0,
            "paired_people": len(shared),
            "baseline_trials": before_trials,
            "post_trials": after_trials,
            "sufficient": False,
            "p_value": 1.0,
        }
    estimate = sum(differences) / len(differences)
    rng = random.Random(
        int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")
    )
    samples = sorted(
        sum(rng.choice(differences) for _ in differences) / len(differences)
        for _ in range(replicates)
    )
    null_threshold = estimate - harm_threshold
    null_tail = (
        sum(sample - estimate >= null_threshold for sample in samples)
        if metric == "hallucination_rate"
        else sum(sample - estimate <= null_threshold for sample in samples)
    )
    return {
        "baseline_rate": sum(before[p] for p in shared) / len(shared),
        "post_rate": sum(after[p] for p in shared) / len(shared),
        "difference": estimate,
        "lower": _quantile(samples, 0.05 / 6),
        "upper": _quantile(samples, 1 - 0.05 / 6),
        "paired_people": len(shared),
        "baseline_trials": before_trials,
        "post_trials": after_trials,
        "sufficient": True,
        "p_value": (null_tail + 1) / (replicates + 1),
    }


async def _incident_transition(
    store: DatabaseStore,
    audit: AuditLog,
    incident_id: str,
    *,
    status: str,
    expected_status: str | None = None,
    reason: str,
    entry: dict[str, object],
    evidence_update: dict[str, object] | None = None,
) -> dict[str, object]:
    incident = await store.get_incident(incident_id)
    if incident is None:
        raise ValueError(f"unknown incident {incident_id!r}")
    async with store.transaction() as session:
        await store.update_incident(
            session,
            incident_id,
            status=status,
            expected_status=expected_status,
            evidence=evidence_update,
            timeline_entry=entry,
        )
        await audit.append(
            session,
            AuditEntry(
                action="incident_phase_changed",
                actor="controller",
                reason=reason,
                workflow_id=incident.workflow_id,
                resource_type="incident",
                resource_id=incident_id,
                evidence={"status": status, **entry},
            ),
        )
    return {"status": status, **entry}


def _quantile(values: list[float], probability: float) -> float:
    index = probability * (len(values) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


async def _credits_if_available(simulator: IncidentSuggestionAPI) -> float | None:
    try:
        state = await simulator.get_state()
        value = state.get("credits_remaining")
    except SimulatorError:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        return None
    return float(value)
