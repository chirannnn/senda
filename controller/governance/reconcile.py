"""Lookup-based recovery for approval submissions with uncertain responses."""

from __future__ import annotations

from typing import Any, Protocol

from controller.errors import ProposalReconciliationRequired, SimulatorError
from controller.governance.state_machine import ProposalStatus, transition
from controller.storage.audit import AuditEntry, AuditLog
from controller.storage.models import GovernanceProposal
from controller.storage.store import DatabaseStore

APPROVAL_SUBMISSION = "approval_submission"
_REMOTE_TO_LOCAL = {
    "pending": ProposalStatus.PENDING,
    "approved": ProposalStatus.APPROVED,
    "rejected": ProposalStatus.REJECTED,
    "expired": ProposalStatus.EXPIRED,
    "applied": ProposalStatus.APPLIED,
    "lapsed": ProposalStatus.LAPSED,
}


class ApprovalAPI(Protocol):
    async def list_approvals(
        self, *, workflow_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def get_approval(self, approval_id: str) -> dict[str, Any]: ...


async def reconcile_approval_submission(
    simulator: ApprovalAPI,
    store: DatabaseStore,
    audit: AuditLog,
    proposal_id: str,
) -> dict[str, Any]:
    """Find the approval by persisted ID or proposal ID; never resubmit it."""
    proposal = await store.get_governance_proposal(proposal_id)
    if proposal is None:
        raise ProposalReconciliationRequired(
            f"proposal {proposal_id!r} has no durable local record"
        )

    approval: dict[str, Any] | None = None
    if proposal.approval_id:
        try:
            approval = await simulator.get_approval(proposal.approval_id)
        except SimulatorError:
            # A missing/unavailable direct lookup does not prove the POST failed.
            approval = None
    if approval is None:
        try:
            approvals = await simulator.list_approvals(workflow_id=proposal.workflow_id)
        except SimulatorError as exc:
            await _mark_reconciliation_required(store, audit, proposal)
            raise ProposalReconciliationRequired(
                f"approval lookup failed for proposal {proposal_id!r}; no retry was sent"
            ) from exc
        approval = next(
            (item for item in approvals if item.get("proposal_id") == proposal_id),
            None,
        )
    if approval is None:
        await _mark_reconciliation_required(store, audit, proposal)
        raise ProposalReconciliationRequired(
            f"approval for proposal {proposal_id!r} was not found; no retry was sent"
        )
    if (
        approval.get("proposal_id") != proposal_id
        or approval.get("workflow_id") != proposal.workflow_id
    ):
        await _mark_reconciliation_required(store, audit, proposal)
        raise ProposalReconciliationRequired(
            f"approval lookup returned an identity mismatch for {proposal_id!r}"
        )
    return await _record_remote_approval(store, audit, proposal, approval)


async def _record_remote_approval(
    store: DatabaseStore,
    audit: AuditLog,
    proposal: GovernanceProposal,
    approval: dict[str, Any],
) -> dict[str, Any]:
    remote_status = approval.get("status")
    target = _REMOTE_TO_LOCAL.get(remote_status)
    if target is None:
        await _mark_reconciliation_required(store, audit, proposal)
        raise ProposalReconciliationRequired(
            f"approval {approval.get('approval_id')!r} has an unknown status"
        )
    current = ProposalStatus(proposal.status)
    try:
        target = transition(current, target)
    except ValueError as exc:
        await _mark_reconciliation_required(store, audit, proposal)
        raise ProposalReconciliationRequired(str(exc)) from exc

    async with store.transaction() as session:
        await store.update_governance_proposal(
            session,
            proposal.proposal_id,
            status=target.value,
            approval_id=approval["approval_id"],
            remote_status=remote_status,
            outcome={"approval": approval},
        )
        operation = await store.get_outbox_operation(
            f"approval-submission:{proposal.proposal_id}"
        )
        if operation is not None:
            await store.update_outbox_status(session, operation.id, "succeeded")
        await audit.append(
            session,
            AuditEntry(
                action="approval_reconciled",
                actor="controller",
                reason="Matched simulator approval to the durable proposal ID",
                workflow_id=proposal.workflow_id,
                resource_type="proposal",
                resource_id=proposal.proposal_id,
                evidence={
                    "proposal_id": proposal.proposal_id,
                    "payload_hash": proposal.payload_hash,
                    "approval_id": approval["approval_id"],
                    "remote_status": remote_status,
                    "outcome": target.value,
                },
            ),
        )
    return approval


async def _mark_reconciliation_required(
    store: DatabaseStore, audit: AuditLog, proposal: GovernanceProposal
) -> None:
    current = ProposalStatus(proposal.status)
    if current != ProposalStatus.RECONCILIATION_REQUIRED:
        try:
            target = transition(current, ProposalStatus.RECONCILIATION_REQUIRED)
        except ValueError:
            target = current
    else:
        target = current
    async with store.transaction() as session:
        await store.update_governance_proposal(
            session,
            proposal.proposal_id,
            status=target.value,
            outcome={"reconciliation": "approval state not established"},
        )
        operation = await store.get_outbox_operation(
            f"approval-submission:{proposal.proposal_id}"
        )
        if operation is not None:
            await store.update_outbox_status(
                session,
                operation.id,
                "reconciliation_required",
                payload=operation.payload
                | {"reconciliation": "approval state not established"},
            )
        await audit.append(
            session,
            AuditEntry(
                action="approval_reconciliation_required",
                actor="controller",
                reason="Remote approval state could not be established safely",
                workflow_id=proposal.workflow_id,
                resource_type="proposal",
                resource_id=proposal.proposal_id,
                evidence={
                    "proposal_id": proposal.proposal_id,
                    "payload_hash": proposal.payload_hash,
                    "outcome": "manual_review_required",
                },
            ),
        )
