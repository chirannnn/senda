import pytest
from sqlalchemy.exc import IntegrityError

from controller.storage.store import (
    DatabaseStore,
    GovernanceProposalConflictError,
)


@pytest.mark.anyio
async def test_proposal_and_submission_intent_share_transaction(postgres_store):
    store: DatabaseStore = postgres_store
    payload = {
        "proposal_id": "proposal-storage-1",
        "workflow_id": "governance-storage-wf",
        "base_version": 2,
        "proposed_config": {"voice": "V2"},
        "change_summary": "Try a voice change",
        "diff": {"voice": {"before": "V1", "after": "V2"}},
        "affected_leads": 10,
        "dry_run": {"method": "replay"},
        "impact_score": 0.2,
        "rationale": "A measured improvement is possible",
    }
    async with store.transaction() as session:
        proposal = await store.create_governance_proposal(
            session,
            proposal_id="proposal-storage-1",
            workflow_id="governance-storage-wf",
            base_version=2,
            payload=payload,
            payload_hash="a" * 64,
        )
        operation = await store.create_outbox_operation(
            session,
            operation_type="approval_submission",
            dedupe_key="approval-submission:proposal-storage-1",
            workflow_id="governance-storage-wf",
            payload={"proposal_id": proposal.proposal_id},
        )
        await store.update_governance_proposal(
            session,
            proposal.proposal_id,
            status="submitted",
            outcome={"outbox_id": str(operation.id)},
        )

    persisted = await store.get_governance_proposal("proposal-storage-1")
    assert persisted is not None
    assert persisted.workflow_id == "governance-storage-wf"
    assert persisted.payload == payload
    assert persisted.payload_hash == "a" * 64
    assert persisted.status == "submitted"
    assert persisted.outcome["outbox_id"] == str(operation.id)
    listed = await store.list_governance_proposals(
        workflow_id="governance-storage-wf", statuses=("submitted",)
    )
    assert [item.proposal_id for item in listed] == ["proposal-storage-1"]


@pytest.mark.anyio
async def test_proposal_id_is_immutable_and_duplicate_payload_is_idempotent(
    postgres_store,
):
    store: DatabaseStore = postgres_store
    args = {
        "proposal_id": "proposal-storage-duplicate",
        "workflow_id": "governance-duplicate-wf",
        "base_version": 1,
        "payload": {"proposed_config": {"voice": "V1"}},
        "payload_hash": "b" * 64,
    }
    async with store.transaction() as session:
        first = await store.create_governance_proposal(session, **args)
    async with store.transaction() as session:
        duplicate = await store.create_governance_proposal(session, **args)
    assert first.proposal_id == duplicate.proposal_id

    with pytest.raises(GovernanceProposalConflictError):
        async with store.transaction() as session:
            await store.create_governance_proposal(
                session, **(args | {"payload_hash": "c" * 64})
            )


@pytest.mark.anyio
async def test_proposal_and_outbox_roll_back_together(postgres_store):
    store: DatabaseStore = postgres_store
    with pytest.raises(RuntimeError, match="injected failure"):
        async with store.transaction() as session:
            await store.create_governance_proposal(
                session,
                proposal_id="proposal-storage-rollback",
                workflow_id="governance-rollback-wf",
                base_version=0,
                payload={"proposal_id": "proposal-storage-rollback"},
                payload_hash="d" * 64,
            )
            await store.create_outbox_operation(
                session,
                operation_type="approval_submission",
                dedupe_key="approval-submission:proposal-storage-rollback",
                workflow_id="governance-rollback-wf",
                payload={"proposal_id": "proposal-storage-rollback"},
            )
            raise RuntimeError("injected failure")

    assert await store.get_governance_proposal("proposal-storage-rollback") is None
    assert (
        await store.get_outbox_operation(
            "approval-submission:proposal-storage-rollback"
        )
        is None
    )


@pytest.mark.anyio
async def test_database_rejects_invalid_proposal_status(postgres_store):
    store: DatabaseStore = postgres_store
    with pytest.raises(IntegrityError):
        async with store.transaction() as session:
            await store.create_governance_proposal(
                session,
                proposal_id="proposal-storage-bad-status",
                workflow_id="governance-bad-status-wf",
                base_version=0,
                payload={},
                payload_hash="e" * 64,
                status="invented",
            )
            await session.flush()

    assert await store.get_governance_proposal("proposal-storage-bad-status") is None
