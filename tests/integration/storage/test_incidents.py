"""PostgreSQL persistence guarantees for incident evidence."""

import pytest

from controller.storage.audit import AuditEntry, AuditLog, AuditQuery
from controller.storage.store import DatabaseStore


@pytest.mark.anyio
async def test_incident_evidence_is_durable_and_idempotent(
    postgres_store: DatabaseStore,
):
    evidence = {
        "workflow_id": "incident-wf",
        "config_version": 3,
        "cutoff_event_id": 42,
        "person_count": 40,
    }
    async with postgres_store.transaction() as session:
        await postgres_store.ensure_workflow(session, "incident-wf")
        row, created = await postgres_store.save_incident(
            session,
            incident_id="incident-one",
            workflow_id="incident-wf",
            trigger_config_version=3,
            status="evidence_recorded",
            evidence=evidence,
            timeline=[{"event": "evidence_recorded", "cutoff_event_id": 42}],
        )
        assert created
        assert row.status == "evidence_recorded"

    async with postgres_store.transaction() as session:
        row, created = await postgres_store.save_incident(
            session,
            incident_id="incident-one",
            workflow_id="incident-wf",
            trigger_config_version=3,
            status="evidence_recorded",
            evidence=evidence,
            timeline=[{"event": "evidence_recorded", "cutoff_event_id": 42}],
        )
        assert not created
        assert row.evidence == evidence

    recovered = await postgres_store.get_incident("incident-one")
    assert recovered is not None
    assert recovered.timeline[0]["cutoff_event_id"] == 42


@pytest.mark.anyio
async def test_incident_audit_and_state_share_one_transaction(postgres_store):
    audit = AuditLog(postgres_store)
    with pytest.raises(RuntimeError, match="rollback fixture"):
        async with postgres_store.transaction() as session:
            await postgres_store.ensure_workflow(session, "incident-rollback-wf")
            await postgres_store.save_incident(
                session,
                incident_id="incident-rollback",
                workflow_id="incident-rollback-wf",
                trigger_config_version=1,
                status="evidence_recorded",
                evidence={"cutoff_event_id": 7},
                timeline=[{"event": "evidence_recorded"}],
            )
            await audit.append(
                session,
                AuditEntry(
                    action="incident_evidence_aggregated",
                    actor="controller",
                    reason="test transaction",
                    workflow_id="incident-rollback-wf",
                    resource_type="incident",
                    resource_id="incident-rollback",
                    evidence={"cutoff_event_id": 7},
                ),
            )
            raise RuntimeError("rollback fixture")
    assert await postgres_store.get_incident("incident-rollback") is None
    page = await audit.query(
        AuditQuery(
            action="incident_evidence_aggregated", resource_id="incident-rollback"
        )
    )
    assert page.records == ()
