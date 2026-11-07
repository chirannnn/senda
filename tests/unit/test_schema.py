from sqlalchemy import JSON

from controller.storage.models import (
    AuditRecord,
    Base,
    ExperimentDecision,
    GovernanceProposal,
    KpiFact,
    OutboxOperation,
    ProcessingCheckpoint,
    SimulatorEvent,
    VersionedCohortDefinition,
    WorkflowReference,
)


def test_durable_schema_contains_required_versioned_entities():
    assert {
        "controller_runs",
        "workflow_references",
        "simulator_events",
        "processing_checkpoints",
        "analytics_checkpoints",
        "kpi_facts",
        "cohort_definitions",
        "audit_records",
        "outbox_operations",
        "governance_proposals",
        "experiment_decisions",
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

    proposals = Base.metadata.tables[GovernanceProposal.__tablename__]
    assert proposals.primary_key.columns.keys() == ["proposal_id"]
    assert proposals.c.payload_hash.type.length == 64
    assert proposals.c.workflow_id.foreign_keys


def test_audit_records_reference_known_workflows():
    audit = Base.metadata.tables[AuditRecord.__tablename__]
    workflow = Base.metadata.tables[WorkflowReference.__tablename__]
    foreign_key = next(iter(audit.c.workflow_id.foreign_keys))
    assert foreign_key.column == workflow.c.workflow_id


def test_kpi_facts_are_unique_provenance_linked_and_indexed():
    facts = Base.metadata.tables[KpiFact.__tablename__]
    assert facts.primary_key.columns.keys() == ["source_event_id"]
    assert facts.c.source_event_id.foreign_keys
    assert any(
        index.name == "ix_kpi_facts_workflow_version_time" for index in facts.indexes
    )


def test_cohort_rules_have_a_versioned_stable_key_and_order():
    cohorts = Base.metadata.tables[VersionedCohortDefinition.__tablename__]
    assert cohorts.primary_key.columns.keys() == [
        "workflow_id",
        "config_version",
        "cohort_id",
    ]
    assert any(
        constraint.name == "uq_cohort_def_workflow_version_ordinal"
        for constraint in cohorts.constraints
    )


def test_experiment_decisions_are_immutable_identity_scoped_snapshots():
    table = Base.metadata.tables[ExperimentDecision.__tablename__]
    assert table.primary_key.columns.keys() == ["experiment_id"]
    assert table.c.input_hash.type.length == 64
    assert table.c.workflow_id.foreign_keys
    assert {"plan_snapshot", "input_snapshot", "decision"} <= set(table.columns.keys())
