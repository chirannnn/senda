"""Lookup-based recovery for approval submissions with uncertain responses."""

from __future__ import annotations

from typing import Any, Protocol

from controller.errors import (
    AmbiguousPostError,
    ProposalReconciliationRequired,
    SimulatorError,
    SimulatorHTTPError,
)
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

    async def get_workflow_config(self, workflow_id: str) -> dict[str, Any]: ...

    async def get_config_history(self, workflow_id: str) -> list[dict[str, Any]]: ...

    async def apply_approval(
        self, workflow_id: str, approval_id: str
    ) -> dict[str, Any]: ...


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


async def apply_approved_proposal(
    simulator: ApprovalAPI,
    store: DatabaseStore,
    audit: AuditLog,
    proposal_id: str,
) -> dict[str, Any]:
    """Recheck approval/version, persist apply intent, then apply once."""
    proposal = await store.get_governance_proposal(proposal_id)
    if proposal is None or proposal.approval_id is None:
        raise ProposalReconciliationRequired(
            f"proposal {proposal_id!r} has no persisted approval"
        )
    if proposal.status in {
        ProposalStatus.APPLIED.value,
    }:
        return {
            "workflow_id": proposal.workflow_id,
            "approval_id": proposal.approval_id,
            "proposal_id": proposal.proposal_id,
            "status": ProposalStatus.APPLIED.value,
        }
    if proposal.status in {
        ProposalStatus.APPLYING.value,
        ProposalStatus.RECONCILIATION_REQUIRED.value,
    }:
        return await reconcile_apply(simulator, store, audit, proposal_id)
    if proposal.status != ProposalStatus.APPROVED.value:
        raise ProposalReconciliationRequired(
            f"proposal {proposal_id!r} is {proposal.status}, not approved"
        )

    approval = await simulator.get_approval(proposal.approval_id)
    if (
        approval.get("approval_id") != proposal.approval_id
        or approval.get("proposal_id") != proposal.proposal_id
        or approval.get("workflow_id") != proposal.workflow_id
    ):
        await _mark_apply_reconciliation_required(store, audit, proposal)
        raise ProposalReconciliationRequired("approval identity changed before apply")
    if approval.get("status") != "approved":
        await _record_remote_approval(store, audit, proposal, approval)
        raise ProposalReconciliationRequired(
            f"approval status is {approval.get('status')!r}; apply was not sent"
        )
    if approval.get("base_version") != proposal.base_version:
        await _mark_proposal_stale(
            store, audit, proposal, current_version=approval.get("base_version")
        )
        raise ProposalReconciliationRequired(
            "approval base version does not match proposal"
        )

    current = await simulator.get_workflow_config(proposal.workflow_id)
    if current["version"] != proposal.base_version:
        await _mark_proposal_stale(
            store, audit, proposal, current_version=current["version"]
        )
        raise ProposalReconciliationRequired(
            "workflow config changed after approval; proposal is stale"
        )

    operation_key = f"approval-apply:{proposal.approval_id}"
    async with store.transaction() as session:
        operation = await store.create_outbox_operation(
            session,
            operation_type="approval_apply",
            dedupe_key=operation_key,
            workflow_id=proposal.workflow_id,
            payload={
                "proposal_id": proposal.proposal_id,
                "approval_id": proposal.approval_id,
                "payload_hash": proposal.payload_hash,
                "base_version": proposal.base_version,
            },
        )
        should_apply = operation.status == "pending"
        if should_apply:
            await store.update_outbox_status(
                session, operation.id, "in_progress", increment_attempts=True
            )
            await store.update_governance_proposal(
                session, proposal.proposal_id, status=ProposalStatus.APPLYING.value
            )
            await audit.append(
                session,
                AuditEntry(
                    action="approval_apply_prepared",
                    actor="controller",
                    reason="Approval and base config rechecked; apply intent persisted",
                    workflow_id=proposal.workflow_id,
                    resource_type="proposal",
                    resource_id=proposal.proposal_id,
                    evidence={
                        "proposal_id": proposal.proposal_id,
                        "approval_id": proposal.approval_id,
                        "payload_hash": proposal.payload_hash,
                        "base_version": proposal.base_version,
                        "current_version": current["version"],
                        "diff": proposal.payload.get("diff"),
                        "rationale": proposal.payload.get("rationale"),
                    },
                ),
            )
    if not should_apply:
        return await reconcile_apply(simulator, store, audit, proposal_id)
    try:
        await simulator.apply_approval(proposal.workflow_id, proposal.approval_id)
    except (AmbiguousPostError, SimulatorHTTPError):
        # Apply is not documented as idempotent after a lost response. Inspect
        # remote state and never issue a second POST from this recovery path.
        return await reconcile_apply(simulator, store, audit, proposal_id)
    return await reconcile_apply(simulator, store, audit, proposal_id)


async def reconcile_apply(
    simulator: ApprovalAPI,
    store: DatabaseStore,
    audit: AuditLog,
    proposal_id: str,
) -> dict[str, Any]:
    """Resolve apply state only from approval status, config, and config history."""
    proposal = await store.get_governance_proposal(proposal_id)
    if proposal is None or proposal.approval_id is None:
        raise ProposalReconciliationRequired(
            f"proposal {proposal_id!r} has no persisted approval for apply recovery"
        )
    try:
        approval = await simulator.get_approval(proposal.approval_id)
        current = await simulator.get_workflow_config(proposal.workflow_id)
        history = await simulator.get_config_history(proposal.workflow_id)
    except SimulatorError as exc:
        await _mark_apply_reconciliation_required(store, audit, proposal)
        raise ProposalReconciliationRequired(
            f"could not establish apply outcome for {proposal_id!r}"
        ) from exc

    if (
        approval.get("approval_id") != proposal.approval_id
        or approval.get("proposal_id") != proposal.proposal_id
        or approval.get("workflow_id") != proposal.workflow_id
    ):
        await _mark_apply_reconciliation_required(store, audit, proposal)
        raise ProposalReconciliationRequired(
            "approval identity mismatch during apply recovery"
        )

    matching = next(
        (
            item
            for item in history
            if item.get("approval_id") == proposal.approval_id
            and item.get("proposal_id") == proposal.proposal_id
            and item.get("actor") == "controller_approved"
            and isinstance(item.get("version"), int)
            and not isinstance(item.get("version"), bool)
            and item["version"] > proposal.base_version
        ),
        None,
    )
    if matching is not None and current["version"] >= matching["version"]:
        await _record_apply_outcome(
            store,
            audit,
            proposal,
            status=ProposalStatus.APPLIED,
            operation_status="succeeded",
            evidence={
                "approval": approval,
                "config_version": current["version"],
                "matching_history": matching,
            },
        )
        return {
            "workflow_id": proposal.workflow_id,
            "approval_id": proposal.approval_id,
            "proposal_id": proposal.proposal_id,
            "status": ProposalStatus.APPLIED.value,
            "version": matching["version"],
        }

    remote_status = approval.get("status")
    if remote_status == "lapsed":
        await _record_apply_outcome(
            store,
            audit,
            proposal,
            status=ProposalStatus.LAPSED,
            operation_status="failed",
            evidence={"approval": approval, "config_version": current["version"]},
        )
        return {
            "status": ProposalStatus.LAPSED.value,
            "approval_id": proposal.approval_id,
        }

    if current["version"] != proposal.base_version:
        await _mark_proposal_stale(
            store, audit, proposal, current_version=current["version"]
        )
        raise ProposalReconciliationRequired(
            "config version changed without a matching approval history record"
        )

    await _mark_apply_reconciliation_required(store, audit, proposal)
    raise ProposalReconciliationRequired(
        "apply is not confirmed; retry is unsafe because simulator apply idempotency is undocumented"
    )


async def _record_apply_outcome(
    store: DatabaseStore,
    audit: AuditLog,
    proposal: GovernanceProposal,
    *,
    status: ProposalStatus,
    operation_status: str,
    evidence: dict[str, object],
) -> None:
    current = ProposalStatus(proposal.status)
    target = transition(current, status)
    async with store.transaction() as session:
        await store.update_governance_proposal(
            session,
            proposal.proposal_id,
            status=target.value,
            remote_status=evidence.get("approval", {}).get("status")
            if isinstance(evidence.get("approval"), dict)
            else None,
            outcome={"apply_reconciliation": evidence},
        )
        operation = await store.get_outbox_operation(
            f"approval-apply:{proposal.approval_id}"
        )
        if operation is not None:
            await store.update_outbox_status(session, operation.id, operation_status)
        if target != current:
            await audit.append(
                session,
                AuditEntry(
                    action="approval_apply_reconciled",
                    actor="controller",
                    reason=f"Reconciled approved configuration change as {target.value}",
                    workflow_id=proposal.workflow_id,
                    resource_type="proposal",
                    resource_id=proposal.proposal_id,
                    evidence={
                        "proposal_id": proposal.proposal_id,
                        "approval_id": proposal.approval_id,
                        "payload_hash": proposal.payload_hash,
                        "diff": proposal.payload.get("diff"),
                        "outcome": target.value,
                        **evidence,
                    },
                ),
            )


async def _mark_proposal_stale(
    store: DatabaseStore,
    audit: AuditLog,
    proposal: GovernanceProposal,
    *,
    current_version: object,
) -> None:
    current = ProposalStatus(proposal.status)
    target = transition(current, ProposalStatus.STALE)
    async with store.transaction() as session:
        await store.update_governance_proposal(
            session,
            proposal.proposal_id,
            status=target.value,
            outcome={"stale": True, "current_version": current_version},
        )
        if target != current:
            await audit.append(
                session,
                AuditEntry(
                    action="proposal_stale",
                    actor="controller",
                    reason="Current workflow config no longer matches proposal base version",
                    workflow_id=proposal.workflow_id,
                    resource_type="proposal",
                    resource_id=proposal.proposal_id,
                    evidence={
                        "proposal_id": proposal.proposal_id,
                        "approval_id": proposal.approval_id,
                        "payload_hash": proposal.payload_hash,
                        "base_version": proposal.base_version,
                        "current_version": current_version,
                        "diff": proposal.payload.get("diff"),
                    },
                ),
            )


async def _mark_apply_reconciliation_required(
    store: DatabaseStore, audit: AuditLog, proposal: GovernanceProposal
) -> None:
    current = ProposalStatus(proposal.status)
    target = (
        transition(current, ProposalStatus.RECONCILIATION_REQUIRED)
        if current != ProposalStatus.RECONCILIATION_REQUIRED
        else current
    )
    async with store.transaction() as session:
        await store.update_governance_proposal(
            session,
            proposal.proposal_id,
            status=target.value,
            outcome={"apply_reconciliation": "manual review required"},
        )
        operation = await store.get_outbox_operation(
            f"approval-apply:{proposal.approval_id}"
        )
        if operation is not None:
            await store.update_outbox_status(
                session,
                operation.id,
                "reconciliation_required",
                payload=operation.payload
                | {"reconciliation": "apply outcome not established"},
            )
        if target != current:
            await audit.append(
                session,
                AuditEntry(
                    action="approval_apply_reconciliation_required",
                    actor="controller",
                    reason="Remote apply outcome could not be established safely",
                    workflow_id=proposal.workflow_id,
                    resource_type="proposal",
                    resource_id=proposal.proposal_id,
                    evidence={
                        "proposal_id": proposal.proposal_id,
                        "approval_id": proposal.approval_id,
                        "payload_hash": proposal.payload_hash,
                        "outcome": "manual_review_required",
                    },
                ),
            )
