"""Population-level incident evidence and durable incident identity."""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from controller.errors import AmbiguousPostError, SimulatorError, SimulatorHTTPError
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
