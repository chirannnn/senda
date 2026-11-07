import httpx
import pytest

from controller.clients.simulator import SimulatorClient
from controller.config import Settings
from controller.errors import (
    ProposalReconciliationRequired,
)
from controller.governance.proposals import ProposalPayload, submit_proposal
from controller.storage.audit import AuditLog, AuditQuery


def _proposal(proposal_id="proposal-submit-1"):
    return ProposalPayload(
        proposal_id=proposal_id,
        workflow_id="governance-submit-wf",
        base_version=1,
        proposed_config={"voice": "V2"},
        change_summary="Try the alternate voice",
        diff={"voice": {"before": "V1", "after": "V2"}},
        affected_leads=25,
        dry_run={"method": "historical_replay", "sample": 25},
        impact_score=0.25,
        rationale="The observed evidence supports supervisor review",
    )


def _approval(proposal, *, status="pending"):
    return {
        "approval_id": "apr_test_001",
        "proposal_id": proposal.proposal_id,
        "workflow_id": proposal.workflow_id,
        "status": status,
        "base_version": proposal.base_version,
        "changed_dims": ["voice"],
    }


def _client(handler):
    return SimulatorClient(
        Settings("http://simulator.test", get_retries=0),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.anyio
async def test_submission_persists_before_post_and_records_remote_approval(
    postgres_store,
):
    proposal = _proposal()
    seen = []

    def handler(request):
        seen.append(request.method)
        assert awaitable_proposal_exists is True
        return httpx.Response(200, json=_approval(proposal))

    awaitable_proposal_exists = False
    client = _client(handler)
    audit = AuditLog(postgres_store)
    async with client:
        # The handler reads the flag changed by a wrapper at the POST boundary.
        original = client.submit_approval

        async def submit_after_persist(payload):
            nonlocal awaitable_proposal_exists
            stored = await postgres_store.get_governance_proposal(proposal.proposal_id)
            assert stored is not None
            assert stored.status == "submitted"
            awaitable_proposal_exists = True
            return await original(payload)

        client.submit_approval = submit_after_persist
        result = await submit_proposal(client, postgres_store, audit, proposal)

    stored = await postgres_store.get_governance_proposal(proposal.proposal_id)
    assert result["approval_id"] == "apr_test_001"
    assert stored is not None and stored.status == "pending"
    assert stored.approval_id == "apr_test_001"
    outbox = await postgres_store.get_outbox_operation(
        f"approval-submission:{proposal.proposal_id}"
    )
    assert outbox is not None and outbox.status == "succeeded" and outbox.attempts == 1
    page = await audit.query(AuditQuery(workflow_id=proposal.workflow_id, page_size=10))
    assert {record.action for record in page.records} == {
        "approval_submission_prepared",
        "approval_reconciled",
    }
    assert page.records[0].evidence["diff"] == proposal.diff
    assert seen == ["POST"]


@pytest.mark.anyio
async def test_lost_submission_response_recovers_by_proposal_id_without_second_post(
    postgres_store,
):
    proposal = _proposal("proposal-submit-lost")
    posts = 0

    def handler(request):
        nonlocal posts
        if request.method == "POST":
            posts += 1
            raise httpx.ReadTimeout("response lost after acceptance", request=request)
        return httpx.Response(200, json=[_approval(proposal)])

    client = _client(handler)
    async with client:
        result = await submit_proposal(
            client, postgres_store, AuditLog(postgres_store), proposal
        )

    assert result["proposal_id"] == proposal.proposal_id
    assert posts == 1
    stored = await postgres_store.get_governance_proposal(proposal.proposal_id)
    assert stored is not None and stored.status == "pending"


@pytest.mark.anyio
async def test_ambiguous_submission_without_lookup_match_requires_manual_review(
    postgres_store,
):
    proposal = _proposal("proposal-submit-unresolved")
    posts = 0

    def handler(request):
        nonlocal posts
        if request.method == "POST":
            posts += 1
            raise httpx.ReadTimeout("unknown outcome", request=request)
        return httpx.Response(200, json=[])

    client = _client(handler)
    async with client:
        with pytest.raises(ProposalReconciliationRequired, match="no retry was sent"):
            await submit_proposal(
                client, postgres_store, AuditLog(postgres_store), proposal
            )

    assert posts == 1
    stored = await postgres_store.get_governance_proposal(proposal.proposal_id)
    assert stored is not None and stored.status == "reconciliation_required"
    operation = await postgres_store.get_outbox_operation(
        f"approval-submission:{proposal.proposal_id}"
    )
    assert operation is not None and operation.status == "reconciliation_required"


@pytest.mark.anyio
async def test_duplicate_response_reconciles_existing_approval(postgres_store):
    proposal = _proposal("proposal-submit-duplicate")
    posts = 0

    def handler(request):
        nonlocal posts
        if request.method == "POST":
            posts += 1
            return httpx.Response(
                409, json={"error": "duplicate_proposal_id", "detail": "already exists"}
            )
        return httpx.Response(200, json=[_approval(proposal)])

    client = _client(handler)
    async with client:
        result = await submit_proposal(
            client, postgres_store, AuditLog(postgres_store), proposal
        )

    assert result["approval_id"] == "apr_test_001"
    assert posts == 1


@pytest.mark.anyio
async def test_unavailable_lookup_after_ambiguous_post_keeps_manual_review_state(
    postgres_store,
):
    proposal = _proposal("proposal-submit-api-down")

    def handler(request):
        if request.method == "POST":
            raise httpx.ReadTimeout("unknown outcome", request=request)
        raise httpx.ConnectError("lookup unavailable", request=request)

    client = _client(handler)
    async with client:
        with pytest.raises(ProposalReconciliationRequired):
            await submit_proposal(
                client, postgres_store, AuditLog(postgres_store), proposal
            )

    stored = await postgres_store.get_governance_proposal(proposal.proposal_id)
    assert stored is not None and stored.status == "reconciliation_required"
