"""Population-level incident evidence and durable incident identity."""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import asdict, dataclass

from controller.storage.audit import AuditEntry, AuditLog
from controller.storage.models import KpiFact
from controller.storage.store import DatabaseStore


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


def _quantile(values: list[float], probability: float) -> float:
    index = probability * (len(values) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (index - lower)
