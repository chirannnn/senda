"""Allowed proposal lifecycle states and idempotent transition checks."""

from __future__ import annotations

from enum import StrEnum


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
