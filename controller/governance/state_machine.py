"""Allowed proposal lifecycle states and idempotent transition checks."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from typing import Any, Protocol

from controller.errors import ProposalReconciliationRequired
from controller.storage.audit import AuditEntry, AuditLog
from controller.storage.models import GovernanceProposal
from controller.storage.store import DatabaseStore


class ProposalStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    APPLYING = "applying"
    APPLIED = "applied"
    LAPSED = "lapsed"
    STALE = "stale"
    RECONCILIATION_REQUIRED = "reconciliation_required"


_TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.DRAFT: frozenset(
        {ProposalStatus.SUBMITTED, ProposalStatus.RECONCILIATION_REQUIRED}
    ),
    ProposalStatus.SUBMITTED: frozenset(
        {
            ProposalStatus.PENDING,
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
            ProposalStatus.EXPIRED,
            ProposalStatus.RECONCILIATION_REQUIRED,
        }
    ),
    ProposalStatus.PENDING: frozenset(
        {
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
            ProposalStatus.EXPIRED,
            ProposalStatus.RECONCILIATION_REQUIRED,
        }
    ),
    ProposalStatus.APPROVED: frozenset(
        {
            ProposalStatus.APPLYING,
            ProposalStatus.LAPSED,
            ProposalStatus.STALE,
            ProposalStatus.RECONCILIATION_REQUIRED,
        }
    ),
    ProposalStatus.APPLYING: frozenset(
        {
            ProposalStatus.APPLIED,
            ProposalStatus.LAPSED,
            ProposalStatus.STALE,
            ProposalStatus.RECONCILIATION_REQUIRED,
        }
    ),
    ProposalStatus.RECONCILIATION_REQUIRED: frozenset(
        {
            ProposalStatus.PENDING,
            ProposalStatus.APPROVED,
            ProposalStatus.REJECTED,
            ProposalStatus.EXPIRED,
            ProposalStatus.APPLIED,
            ProposalStatus.LAPSED,
            ProposalStatus.STALE,
        }
    ),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.EXPIRED: frozenset(),
    ProposalStatus.APPLIED: frozenset(),
    ProposalStatus.LAPSED: frozenset(),
    ProposalStatus.STALE: frozenset(),
}


def transition(current: ProposalStatus, target: ProposalStatus) -> ProposalStatus:
    """Validate a state change; repeated observations of the same state are safe."""
    if current == target:
        return current
    if target not in _TRANSITIONS[current]:
        raise ValueError(
            f"invalid proposal transition: {current.value} -> {target.value}"
        )
    return target


class ApprovalReadAPI(Protocol):
    async def get_approval(self, approval_id: str) -> dict[str, Any]: ...


ApprovalSleep = Callable[[float], Awaitable[None]]


async def record_approval_observation(
    store: DatabaseStore,
    audit: AuditLog,
    proposal: GovernanceProposal,
    *,
    approval_id: str,
    remote_status: str,
    evidence: Mapping[str, object],
    action: str = "approval_status_observed",
) -> ProposalStatus:
    """Apply one remote/event observation exactly once to durable state."""
    remote_targets = {
        "pending": ProposalStatus.PENDING,
        "approved": ProposalStatus.APPROVED,
        "rejected": ProposalStatus.REJECTED,
        "expired": ProposalStatus.EXPIRED,
        "applied": ProposalStatus.APPLIED,
        "lapsed": ProposalStatus.LAPSED,
    }
    target = remote_targets.get(remote_status)
    if target is None:
        raise ProposalReconciliationRequired(
            f"approval {approval_id!r} has unknown status {remote_status!r}"
        )
    if proposal.approval_id not in (None, approval_id):
        raise ProposalReconciliationRequired(
            f"approval ID changed for proposal {proposal.proposal_id!r}"
        )
    current = ProposalStatus(proposal.status)
    try:
        target = transition(current, target)
    except ValueError as exc:
        raise ProposalReconciliationRequired(str(exc)) from exc
    changed = (
        target != current
        or proposal.approval_id != approval_id
        or proposal.remote_status != remote_status
    )
    async with store.transaction() as session:
        await store.update_governance_proposal(
            session,
            proposal.proposal_id,
            status=target.value,
            approval_id=approval_id,
            remote_status=remote_status,
            outcome={"approval_observation": dict(evidence)},
        )
        if changed:
            await audit.append(
                session,
                AuditEntry(
                    action=action,
                    actor="simulator_approver",
                    reason=f"Observed approval status {remote_status}",
                    workflow_id=proposal.workflow_id,
                    resource_type="proposal",
                    resource_id=proposal.proposal_id,
                    evidence={
                        "proposal_id": proposal.proposal_id,
                        "payload_hash": proposal.payload_hash,
                        "approval_id": approval_id,
                        "remote_status": remote_status,
                        "outcome": target.value,
                        "source": dict(evidence),
                    },
                ),
            )
    return target


async def poll_approval(
    simulator: ApprovalReadAPI,
    store: DatabaseStore,
    audit: AuditLog,
    proposal_id: str,
    *,
    max_polls: int = 3,
    interval_seconds: float = 0.25,
    request_timeout_seconds: float = 5.0,
    sleep: ApprovalSleep = asyncio.sleep,
) -> ProposalStatus:
    """Poll a known approval a bounded number of times with per-read timeout."""
    if not 1 <= max_polls <= 20:
        raise ValueError("max_polls must be between 1 and 20")
    if not math.isfinite(interval_seconds) or interval_seconds < 0:
        raise ValueError("poll interval must be finite and nonnegative")
    if not math.isfinite(request_timeout_seconds) or request_timeout_seconds <= 0:
        raise ValueError("request timeout must be finite and positive")
    proposal = await store.get_governance_proposal(proposal_id)
    if proposal is None or proposal.approval_id is None:
        raise ProposalReconciliationRequired(
            f"proposal {proposal_id!r} has no known approval ID to poll"
        )
    result = ProposalStatus(proposal.status)
    for attempt in range(max_polls):
        approval = await asyncio.wait_for(
            simulator.get_approval(proposal.approval_id),
            timeout=request_timeout_seconds,
        )
        if (
            approval.get("approval_id") != proposal.approval_id
            or approval.get("proposal_id") != proposal.proposal_id
            or approval.get("workflow_id") != proposal.workflow_id
        ):
            raise ProposalReconciliationRequired(
                f"approval polling returned an identity mismatch for {proposal_id!r}"
            )
        result = await record_approval_observation(
            store,
            audit,
            proposal,
            approval_id=proposal.approval_id,
            remote_status=approval["status"],
            evidence={"source": "approval_get", "approval": approval},
        )
        if result != ProposalStatus.PENDING or attempt + 1 == max_polls:
            return result
        await sleep(interval_seconds)
        proposal = await store.get_governance_proposal(proposal_id)
        if proposal is None:
            raise ProposalReconciliationRequired(
                f"proposal {proposal_id!r} disappeared while polling"
            )
    return result


async def reconcile_approval_event(
    store: DatabaseStore,
    audit: AuditLog,
    event: Mapping[str, object],
) -> ProposalStatus | None:
    """Apply a persisted approval_decided event when its approval is known."""
    if event.get("type") != "approval_decided":
        return None
    approval_id = event.get("approval_id")
    workflow_id = event.get("workflow_id")
    remote_status = event.get("status")
    if not all(
        isinstance(value, str) and value for value in (approval_id, workflow_id)
    ):
        return None
    if not isinstance(remote_status, str):
        return None
    proposal = await store.get_governance_proposal_by_approval_id(approval_id)
    if proposal is None:
        return None
    if proposal.workflow_id != workflow_id:
        raise ProposalReconciliationRequired(
            f"approval event workflow does not match proposal {proposal.proposal_id!r}"
        )
    return await record_approval_observation(
        store,
        audit,
        proposal,
        approval_id=approval_id,
        remote_status=remote_status,
        evidence={"source": "approval_decided_event", "event": dict(event)},
        action="approval_event_reconciled",
    )
