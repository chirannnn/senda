import pytest

from controller.errors import ProposalReconciliationRequired
from controller.governance.state_machine import (
    ProposalStatus,
    poll_approval,
    reconcile_approval_event,
)
from controller.storage.audit import AuditLog, AuditQuery
from controller.storage.store import DatabaseStore


async def _stored_proposal(store, *, status="pending", proposal_id="proposal-state"):
    payload = {"proposal_id": proposal_id, "workflow_id": "approval-state-wf"}
    async with store.transaction() as session:
        await store.create_governance_proposal(
            session,
            proposal_id=proposal_id,
            workflow_id="approval-state-wf",
            base_version=2,
            payload=payload,
            payload_hash="f" * 64,
            status=status,
        )
        await store.update_governance_proposal(
            session,
            proposal_id,
            status=status,
            approval_id="apr_state_1",
            remote_status="pending" if status == "pending" else status,
        )
    return await store.get_governance_proposal(proposal_id)


@pytest.mark.anyio
async def test_polling_is_bounded_and_repeated_observations_are_idempotent(
    postgres_store,
):
    store: DatabaseStore = postgres_store
    proposal = await _stored_proposal(store)
    statuses = iter(("pending", "approved", "approved"))
    calls = 0
    delays = []

    class API:
        async def get_approval(self, approval_id):
            nonlocal calls
            calls += 1
            status = next(statuses)
            return {
                "approval_id": approval_id,
                "proposal_id": proposal.proposal_id,
                "workflow_id": proposal.workflow_id,
                "status": status,
                "base_version": 2,
                "changed_dims": ["voice"],
            }

    async def sleep(seconds):
        delays.append(seconds)

    audit = AuditLog(store)
    assert (
        await poll_approval(
            API(), store, audit, proposal.proposal_id, max_polls=3, sleep=sleep
        )
        == ProposalStatus.APPROVED
    )
    assert calls == 2
    assert delays == [0.25]

    await poll_approval(API(), store, audit, proposal.proposal_id, max_polls=1)
    page = await audit.query(AuditQuery(workflow_id=proposal.workflow_id, page_size=10))
    assert len(page.records) == 1
    assert page.records[0].evidence["outcome"] == "approved"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "initial,remote,expected",
    [
        ("submitted", "pending", ProposalStatus.PENDING),
        ("pending", "approved", ProposalStatus.APPROVED),
        ("pending", "rejected", ProposalStatus.REJECTED),
        ("pending", "expired", ProposalStatus.EXPIRED),
        ("applying", "applied", ProposalStatus.APPLIED),
        ("approved", "lapsed", ProposalStatus.LAPSED),
    ],
)
async def test_poll_handles_every_documented_remote_status(
    postgres_store, initial, remote, expected
):
    proposal = await _stored_proposal(
        postgres_store,
        status=initial,
        proposal_id=f"proposal-status-{remote}",
    )

    class API:
        async def get_approval(self, approval_id):
            return {
                "approval_id": approval_id,
                "proposal_id": proposal.proposal_id,
                "workflow_id": proposal.workflow_id,
                "status": remote,
                "base_version": 2,
                "changed_dims": ["voice"],
            }

    status = await poll_approval(
        API(), postgres_store, AuditLog(postgres_store), proposal.proposal_id
    )
    assert status == expected
    persisted = await postgres_store.get_governance_proposal(proposal.proposal_id)
    assert persisted is not None and persisted.status == expected.value


@pytest.mark.anyio
async def test_approval_decided_event_reconciles_known_id_idempotently(postgres_store):
    store: DatabaseStore = postgres_store
    proposal = await _stored_proposal(store, status="pending")
    event = {
        "type": "approval_decided",
        "approval_id": "apr_state_1",
        "workflow_id": proposal.workflow_id,
        "status": "rejected",
        "reasons": ["judgement call"],
    }
    audit = AuditLog(store)

    assert (
        await reconcile_approval_event(store, audit, event) == ProposalStatus.REJECTED
    )
    assert (
        await reconcile_approval_event(store, audit, event) == ProposalStatus.REJECTED
    )
    persisted = await store.get_governance_proposal(proposal.proposal_id)
    assert persisted is not None and persisted.status == "rejected"
    page = await audit.query(AuditQuery(workflow_id=proposal.workflow_id, page_size=10))
    assert len(page.records) == 1
    assert page.records[0].action == "approval_event_reconciled"


@pytest.mark.anyio
async def test_unknown_or_mismatched_approval_events_are_not_applied(postgres_store):
    store: DatabaseStore = postgres_store
    audit = AuditLog(store)
    assert (
        await reconcile_approval_event(
            store,
            audit,
            {
                "type": "approval_decided",
                "approval_id": "unknown",
                "status": "approved",
            },
        )
        is None
    )

    proposal = await _stored_proposal(store, status="pending")
    with pytest.raises(ProposalReconciliationRequired, match="does not match"):
        await reconcile_approval_event(
            store,
            audit,
            {
                "type": "approval_decided",
                "approval_id": "apr_state_1",
                "workflow_id": "another-workflow",
                "status": "approved",
            },
        )
    persisted = await store.get_governance_proposal(proposal.proposal_id)
    assert persisted is not None and persisted.status == "pending"
