from sqlalchemy import JSON

from controller.storage.models import (
    AuditRecord,
    Base,
    OutboxOperation,
    ProcessingCheckpoint,
    SimulatorEvent,
    WorkflowReference,
)


def test_durable_schema_contains_required_versioned_entities():
    assert {
        "controller_runs",
        "workflow_references",
        "simulator_events",
        "processing_checkpoints",
        "audit_records",
        "outbox_operations",
    } == set(Base.metadata.tables)


def test_event_id_is_unique_and_raw_payload_has_no_simulator_schema_assumption():
    event = Base.metadata.tables[SimulatorEvent.__tablename__]
    assert event.primary_key.columns.keys() == ["event_id"]
    assert "payload" in event.columns
    assert isinstance(event.columns["payload"].type, JSON)
    assert any(index.name == "ix_simulator_events_chunk" for index in event.indexes)


def test_checkpoint_and_outbox_have_safety_constraints():
    checkpoint = Base.metadata.tables[ProcessingCheckpoint.__tablename__]
    assert "checkpoint_key" in checkpoint.primary_key.columns
    assert checkpoint.columns["chunk_number"].nullable

    outbox = Base.metadata.tables[OutboxOperation.__tablename__]
    assert "dedupe_key" in outbox.columns
    assert outbox.columns["dedupe_key"].unique
    assert any(
        "attempts >= 0" in str(item.sqltext)
        for item in outbox.constraints
        if hasattr(item, "sqltext")
    )


def test_audit_records_reference_known_workflows():
    audit = Base.metadata.tables[AuditRecord.__tablename__]
    workflow = Base.metadata.tables[WorkflowReference.__tablename__]
    foreign_key = next(iter(audit.c.workflow_id.foreign_keys))
    assert foreign_key.column == workflow.c.workflow_id
