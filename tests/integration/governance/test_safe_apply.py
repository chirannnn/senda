import asyncio
import os

import httpx
import pytest

from controller.clients.simulator import SimulatorClient
from controller.config import Settings
from controller.errors import ProposalReconciliationRequired
from controller.governance.proposals import ProposalPayload, submit_proposal
from controller.governance.reconcile import apply_approved_proposal
from controller.governance.state_machine import ProposalStatus, poll_approval
from controller.storage.audit import AuditLog, AuditQuery
from controller.storage.store import DatabaseStore


async def _approved_proposal(
    store, *, proposal_id="proposal-apply-1", status="approved"
):
    payload = {
        "proposal_id": proposal_id,
        "workflow_id": "safe-apply-wf",
        "base_version": 1,
        "proposed_config": {"voice": "V2"},
        "change_summary": "Try the alternate voice",
        "diff": {"voice": {"before": "V1", "after": "V2"}},
        "affected_leads": 40,
        "dry_run": {"method": "replay"},
        "impact_score": 0.3,
        "rationale": "Evidence supports the approved proposal",
    }
    async with store.transaction() as session:
        await store.create_governance_proposal(
            session,
            proposal_id=proposal_id,
            workflow_id="safe-apply-wf",
            base_version=1,
            payload=payload,
            payload_hash="9" * 64,
            status=status,
        )
        await store.update_governance_proposal(
            session,
            proposal_id,
            status=status,
            approval_id=f"apr-{proposal_id}",
            remote_status="approved",
        )
    return await store.get_governance_proposal(proposal_id)


def _api_client(proposal, *, initial_version=1, apply_behavior="success"):
    state = {
        "version": initial_version,
        "approval_status": "approved",
        "history": [],
        "posts": 0,
    }

    def handler(request):
        if request.method == "GET" and request.url.path.startswith("/approvals/"):
            return httpx.Response(
                200,
                json={
                    "approval_id": f"apr-{proposal.proposal_id}",
                    "proposal_id": proposal.proposal_id,
                    "workflow_id": proposal.workflow_id,
                    "status": state["approval_status"],
                    "base_version": 1,
                    "changed_dims": ["voice"],
                },
            )
        if request.method == "GET" and request.url.path.endswith("/config/history"):
            return httpx.Response(200, json=state["history"])
        if request.method == "GET" and request.url.path.endswith("/config"):
            return httpx.Response(
                200,
                json={
                    "workflow_id": proposal.workflow_id,
                    "version": state["version"],
                    "config": {"voice": "V2" if state["version"] > 1 else "V1"},
                },
            )
        if request.method == "POST" and request.url.path.endswith("/apply"):
            state["posts"] += 1
            assert (
                request.read()
                == f'{{"approval_id":"apr-{proposal.proposal_id}"}}'.encode()
            )
            if apply_behavior != "no_effect":
                state["version"] = 2
                state["approval_status"] = "applied"
                state["history"].append(
                    {
                        "version": 2,
                        "actor": "controller_approved",
                        "proposal_id": proposal.proposal_id,
                        "approval_id": f"apr-{proposal.proposal_id}",
                        "changed_dims": ["voice"],
                    }
                )
            if apply_behavior in {"lost_after_apply", "no_effect"}:
                raise httpx.ReadTimeout("apply response lost", request=request)
            return httpx.Response(
                200,
                json={
                    "workflow_id": proposal.workflow_id,
                    "version": 2,
                    "changed_dims": ["voice"],
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    client = SimulatorClient(
        Settings("http://simulator.test", get_retries=0),
        transport=httpx.MockTransport(handler),
    )
    return client, state


@pytest.mark.anyio
async def test_apply_rechecks_version_persists_intent_and_audits_exact_diff(
    postgres_store,
):
    proposal = await _approved_proposal(postgres_store)
    client, state = _api_client(proposal)
    audit = AuditLog(postgres_store)
    async with client:
        result = await apply_approved_proposal(
            client, postgres_store, audit, proposal.proposal_id
        )

    assert result["status"] == "applied"
    assert state["posts"] == 1
    stored = await postgres_store.get_governance_proposal(proposal.proposal_id)
    assert stored is not None and stored.status == "applied"
    intent = await postgres_store.get_outbox_operation(
        f"approval-apply:{proposal.approval_id}"
    )
    assert intent is not None and intent.status == "succeeded" and intent.attempts == 1
    page = await audit.query(AuditQuery(workflow_id=proposal.workflow_id, page_size=10))
    assert {entry.action for entry in page.records} == {
        "approval_apply_prepared",
        "approval_apply_reconciled",
    }
    prepared = next(
        entry for entry in page.records if entry.action == "approval_apply_prepared"
    )
    assert prepared.evidence["diff"] == proposal.payload["diff"]
    assert prepared.actor == "controller"
    assert prepared.reason
    reconciled = next(
        entry for entry in page.records if entry.action == "approval_apply_reconciled"
    )
    assert reconciled.evidence["outcome"] == "applied"


@pytest.mark.anyio
async def test_lost_apply_response_is_reconciled_from_config_history(postgres_store):
    proposal = await _approved_proposal(
        postgres_store, proposal_id="proposal-apply-lost"
    )
    client, state = _api_client(proposal, apply_behavior="lost_after_apply")
    async with client:
        result = await apply_approved_proposal(
            client, postgres_store, AuditLog(postgres_store), proposal.proposal_id
        )

    assert result["status"] == "applied"
    assert result["version"] == 2
    assert state["posts"] == 1
    stored = await postgres_store.get_governance_proposal(proposal.proposal_id)
    assert stored is not None and stored.status == "applied"


@pytest.mark.anyio
async def test_ambiguous_apply_without_history_halts_without_retry(postgres_store):
    proposal = await _approved_proposal(
        postgres_store, proposal_id="proposal-apply-unknown"
    )
    client, state = _api_client(proposal, apply_behavior="no_effect")
    async with client:
        with pytest.raises(ProposalReconciliationRequired, match="retry is unsafe"):
            await apply_approved_proposal(
                client, postgres_store, AuditLog(postgres_store), proposal.proposal_id
            )

    assert state["posts"] == 1
    stored = await postgres_store.get_governance_proposal(proposal.proposal_id)
    assert stored is not None and stored.status == "reconciliation_required"
    intent = await postgres_store.get_outbox_operation(
        f"approval-apply:{proposal.approval_id}"
    )
    assert intent is not None and intent.status == "reconciliation_required"
    database_url = os.environ["CONTROLLER_TEST_DATABASE_URL"]
    await postgres_store.close()
    restarted = DatabaseStore(database_url)
    await restarted.initialize()
    await restarted.migrate()
    async with client:
        with pytest.raises(ProposalReconciliationRequired, match="retry is unsafe"):
            await apply_approved_proposal(
                client,
                restarted,
                AuditLog(restarted),
                proposal.proposal_id,
            )
    assert state["posts"] == 1
    intent = await restarted.get_outbox_operation(
        f"approval-apply:{proposal.approval_id}"
    )
    assert intent is not None and intent.status == "reconciliation_required"
    await restarted.close()


@pytest.mark.anyio
async def test_stale_config_prevents_apply_post_and_marks_proposal(postgres_store):
    proposal = await _approved_proposal(
        postgres_store,
        proposal_id="proposal-apply-stale",
    )
    client, state = _api_client(proposal, initial_version=2)
    audit = AuditLog(postgres_store)
    async with client:
        with pytest.raises(ProposalReconciliationRequired, match="proposal is stale"):
            await apply_approved_proposal(
                client, postgres_store, audit, proposal.proposal_id
            )

    assert state["posts"] == 0
    stored = await postgres_store.get_governance_proposal(proposal.proposal_id)
    assert stored is not None and stored.status == ProposalStatus.STALE.value
    assert (
        await postgres_store.get_outbox_operation(
            f"approval-apply:{proposal.approval_id}"
        )
        is None
    )


@pytest.mark.anyio
async def test_repeated_apply_after_success_does_not_post_again(postgres_store):
    proposal = await _approved_proposal(
        postgres_store, proposal_id="proposal-apply-repeat"
    )
    client, state = _api_client(proposal)
    audit = AuditLog(postgres_store)
    async with client:
        first = await apply_approved_proposal(
            client, postgres_store, audit, proposal.proposal_id
        )
        second = await apply_approved_proposal(
            client, postgres_store, audit, proposal.proposal_id
        )

    assert first["status"] == second["status"] == "applied"
    assert state["posts"] == 1
    page = await audit.query(AuditQuery(workflow_id=proposal.workflow_id, page_size=10))
    assert (
        sum(entry.action == "approval_apply_reconciled" for entry in page.records) == 1
    )


@pytest.mark.anyio
async def test_restart_with_apply_intent_reconciles_without_reposting(postgres_store):
    proposal = await _approved_proposal(
        postgres_store,
        proposal_id="proposal-apply-restart",
        status="applying",
    )
    async with postgres_store.transaction() as session:
        operation = await postgres_store.create_outbox_operation(
            session,
            operation_type="approval_apply",
            dedupe_key=f"approval-apply:{proposal.approval_id}",
            workflow_id=proposal.workflow_id,
            payload={"proposal_id": proposal.proposal_id},
        )
        await postgres_store.update_outbox_status(session, operation.id, "in_progress")
    client, state = _api_client(proposal)
    # The remote apply already happened, but the controller crashed before its
    # local commit; expose the corresponding API state before restart recovery.
    state["version"] = 2
    state["approval_status"] = "applied"
    state["history"].append(
        {
            "version": 2,
            "actor": "controller_approved",
            "proposal_id": proposal.proposal_id,
            "approval_id": proposal.approval_id,
            "changed_dims": ["voice"],
        }
    )
    async with client:
        result = await apply_approved_proposal(
            client, postgres_store, AuditLog(postgres_store), proposal.proposal_id
        )

    assert result["status"] == "applied"
    assert state["posts"] == 0
    stored = await postgres_store.get_governance_proposal(proposal.proposal_id)
    assert stored is not None and stored.status == "applied"


@pytest.mark.anyio
async def test_restart_before_apply_post_halts_without_repeating_uncertain_write(
    postgres_store,
):
    proposal = await _approved_proposal(
        postgres_store,
        proposal_id="proposal-apply-before-post",
        status="applying",
    )
    async with postgres_store.transaction() as session:
        operation = await postgres_store.create_outbox_operation(
            session,
            operation_type="approval_apply",
            dedupe_key=f"approval-apply:{proposal.approval_id}",
            workflow_id=proposal.workflow_id,
            payload={"proposal_id": proposal.proposal_id},
        )
        await postgres_store.update_outbox_status(session, operation.id, "in_progress")
    client, state = _api_client(proposal, apply_behavior="no_effect")
    async with client:
        with pytest.raises(ProposalReconciliationRequired, match="retry is unsafe"):
            await apply_approved_proposal(
                client,
                postgres_store,
                AuditLog(postgres_store),
                proposal.proposal_id,
            )

    assert state["posts"] == 0
    stored = await postgres_store.get_governance_proposal(proposal.proposal_id)
    assert stored is not None and stored.status == "reconciliation_required"


@pytest.mark.anyio
async def test_pending_approved_applied_lifecycle_end_to_end(postgres_store):
    proposal = ProposalPayload(
        proposal_id="proposal-full-lifecycle",
        workflow_id="safe-apply-wf",
        base_version=1,
        proposed_config={"voice": "V2"},
        change_summary="Try the alternate voice",
        diff={"voice": {"before": "V1", "after": "V2"}},
        affected_leads=40,
        dry_run={"method": "replay"},
        impact_score=0.3,
        rationale="Evidence supports the approved proposal",
    )
    remote = {"status": "pending", "version": 1, "history": [], "posts": []}
    status_reads = 0

    def approval_payload():
        return {
            "approval_id": "apr-proposal-full-lifecycle",
            "proposal_id": proposal.proposal_id,
            "workflow_id": proposal.workflow_id,
            "status": remote["status"],
            "base_version": 1,
            "changed_dims": ["voice"],
        }

    def handler(request):
        nonlocal status_reads
        if request.method == "POST":
            remote["posts"].append((request.method, request.url.path))
        if request.method == "POST" and request.url.path == "/approvals":
            return httpx.Response(200, json=approval_payload())
        if (
            request.method == "GET"
            and request.url.path == "/approvals/apr-proposal-full-lifecycle"
        ):
            status_reads += 1
            if status_reads >= 2 and remote["status"] == "pending":
                remote["status"] = "approved"
            return httpx.Response(200, json=approval_payload())
        if request.method == "GET" and request.url.path.endswith("/config"):
            return httpx.Response(
                200,
                json={
                    "workflow_id": proposal.workflow_id,
                    "version": remote["version"],
                    "config": {"voice": "V2" if remote["version"] > 1 else "V1"},
                },
            )
        if request.method == "GET" and request.url.path.endswith("/config/history"):
            return httpx.Response(200, json=remote["history"])
        if request.method == "POST" and request.url.path.endswith("/apply"):
            remote["status"] = "applied"
            remote["version"] = 2
            remote["history"].append(
                {
                    "version": 2,
                    "actor": "controller_approved",
                    "proposal_id": proposal.proposal_id,
                    "approval_id": "apr-proposal-full-lifecycle",
                    "changed_dims": ["voice"],
                }
            )
            return httpx.Response(
                200,
                json={
                    "workflow_id": proposal.workflow_id,
                    "version": 2,
                    "changed_dims": ["voice"],
                },
            )
        raise AssertionError(f"unexpected request {request.method} {request.url.path}")

    client = SimulatorClient(
        Settings("http://simulator.test", get_retries=0),
        transport=httpx.MockTransport(handler),
    )
    audit = AuditLog(postgres_store)
    async with client:
        submitted = await submit_proposal(client, postgres_store, audit, proposal)
        assert submitted["status"] == "pending"
        approval_status = await poll_approval(
            client,
            postgres_store,
            audit,
            proposal.proposal_id,
            max_polls=3,
            interval_seconds=0,
            sleep=asyncio.sleep,
        )
        assert approval_status == ProposalStatus.APPROVED
        # Simulate a process crash after approval is durable but before apply.
        await postgres_store.close()
        restarted_store = DatabaseStore(os.environ["CONTROLLER_TEST_DATABASE_URL"])
        await restarted_store.initialize()
        await restarted_store.migrate()
        try:
            result = await apply_approved_proposal(
                client,
                restarted_store,
                AuditLog(restarted_store),
                proposal.proposal_id,
            )
            stored = await restarted_store.get_governance_proposal(proposal.proposal_id)
            assert result["status"] == "applied"
            assert stored is not None and stored.status == "applied"
            assert remote["history"][0]["approval_id"] == stored.approval_id
        finally:
            await restarted_store.close()

    assert remote["posts"] == [
        ("POST", "/approvals"),
        ("POST", "/workflows/safe-apply-wf/apply"),
    ]
