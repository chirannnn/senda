"""Validated, stable approval proposal payloads."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from controller.errors import AmbiguousPostError, SimulatorHTTPError
from controller.governance.reconcile import (
    APPROVAL_SUBMISSION,
    _record_remote_approval,
    reconcile_approval_submission,
)
from controller.storage.audit import AuditEntry, AuditLog
from controller.storage.store import DatabaseStore


class ApprovalSubmitAPI(Protocol):
    async def submit_approval(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def list_approvals(
        self, *, workflow_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def get_approval(self, approval_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ProposalPayload:
    """An immutable request body accepted by the documented approval endpoint."""

    proposal_id: str
    workflow_id: str
    base_version: int
    proposed_config: dict[str, Any]
    change_summary: str
    diff: dict[str, Any]
    affected_leads: int
    dry_run: dict[str, Any]
    impact_score: float
    rationale: str
    evidence: dict[str, Any] | None = None
    expires_in_hours: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.proposal_id, str) or not self.proposal_id.strip():
            raise ValueError("proposal_id must be a non-empty stable string")
        if not isinstance(self.workflow_id, str) or not self.workflow_id.strip():
            raise ValueError("workflow_id must be a non-empty string")
        if (
            isinstance(self.base_version, bool)
            or not isinstance(self.base_version, int)
            or self.base_version < 0
        ):
            raise ValueError("base_version must be a nonnegative integer")
        if not isinstance(self.proposed_config, dict) or not self.proposed_config:
            raise ValueError("proposed_config must be a non-empty object")
        if not isinstance(self.change_summary, str) or not self.change_summary.strip():
            raise ValueError("change_summary must be non-empty")
        if not isinstance(self.diff, dict) or not self.diff:
            raise ValueError("diff must be a non-empty object")
        if (
            isinstance(self.affected_leads, bool)
            or not isinstance(self.affected_leads, int)
            or self.affected_leads < 0
        ):
            raise ValueError("affected_leads must be a nonnegative integer")
        if not isinstance(self.dry_run, dict) or not self.dry_run:
            raise ValueError("dry_run must be a non-empty object")
        if (
            isinstance(self.impact_score, bool)
            or not isinstance(self.impact_score, (int, float))
            or not math.isfinite(self.impact_score)
            or not 0 <= self.impact_score <= 1
        ):
            raise ValueError("impact_score must be a finite number in [0, 1]")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise ValueError("rationale must be non-empty")
        if self.evidence is not None and not isinstance(self.evidence, dict):
            raise ValueError("evidence must be an object when supplied")
        if self.expires_in_hours is not None and (
            isinstance(self.expires_in_hours, bool)
            or not isinstance(self.expires_in_hours, (int, float))
            or not math.isfinite(self.expires_in_hours)
            or self.expires_in_hours <= 0
        ):
            raise ValueError("expires_in_hours must be a finite positive number")
        object.__setattr__(self, "proposed_config", _freeze_json(self.proposed_config))
        object.__setattr__(self, "diff", _freeze_json(self.diff))
        object.__setattr__(self, "dry_run", _freeze_json(self.dry_run))
        if self.evidence is not None:
            object.__setattr__(self, "evidence", _freeze_json(self.evidence))
        # Fail at construction instead of later when persisting or hashing JSON.
        self._canonical_json()

    def to_api_payload(self) -> dict[str, Any]:
        """Return the exact documented approval request fields."""
        result: dict[str, Any] = {
            "proposal_id": self.proposal_id,
            "workflow_id": self.workflow_id,
            "base_version": self.base_version,
            "proposed_config": _thaw_json(self.proposed_config),
            "change_summary": self.change_summary,
            "diff": _thaw_json(self.diff),
            "affected_leads": self.affected_leads,
            "dry_run": _thaw_json(self.dry_run),
            "impact_score": self.impact_score,
            "rationale": self.rationale,
        }
        if self.evidence is not None:
            result["evidence"] = _thaw_json(self.evidence)
        if self.expires_in_hours is not None:
            result["expires_in_hours"] = self.expires_in_hours
        return result

    @property
    def payload_hash(self) -> str:
        """SHA-256 of canonical JSON, suitable for detecting ID/payload reuse."""
        return hashlib.sha256(self._canonical_json().encode("utf-8")).hexdigest()

    def _canonical_json(self) -> str:
        try:
            return json.dumps(
                self.to_api_payload(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("proposal payload must contain only JSON values") from exc


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


async def submit_proposal(
    simulator: ApprovalSubmitAPI,
    store: DatabaseStore,
    audit: AuditLog,
    proposal: ProposalPayload,
) -> dict[str, Any]:
    """Persist proposal and intent, then submit at most once and reconcile by ID."""
    existing = await store.get_governance_proposal(proposal.proposal_id)
    if existing is not None:
        if (
            existing.payload_hash != proposal.payload_hash
            or existing.payload != proposal.to_api_payload()
        ):
            raise ValueError("proposal ID already belongs to a different payload")
        return await reconcile_approval_submission(
            simulator, store, audit, proposal.proposal_id
        )

    operation_key = f"approval-submission:{proposal.proposal_id}"
    api_payload = proposal.to_api_payload()
    async with store.transaction() as session:
        durable = await store.create_governance_proposal(
            session,
            proposal_id=proposal.proposal_id,
            workflow_id=proposal.workflow_id,
            base_version=proposal.base_version,
            payload=api_payload,
            payload_hash=proposal.payload_hash,
        )
        operation = await store.create_outbox_operation(
            session,
            operation_type=APPROVAL_SUBMISSION,
            dedupe_key=operation_key,
            workflow_id=proposal.workflow_id,
            payload={
                "proposal_id": proposal.proposal_id,
                "payload_hash": proposal.payload_hash,
            },
        )
        if operation.status != "pending":
            should_submit = False
        else:
            await store.update_outbox_status(
                session, operation.id, "in_progress", increment_attempts=True
            )
            await store.update_governance_proposal(
                session, durable.proposal_id, status="submitted"
            )
            await audit.append(
                session,
                AuditEntry(
                    action="approval_submission_prepared",
                    actor="controller",
                    reason="Persisted proposal and outbox intent before approval request",
                    workflow_id=proposal.workflow_id,
                    resource_type="proposal",
                    resource_id=proposal.proposal_id,
                    evidence={
                        "proposal_id": proposal.proposal_id,
                        "payload_hash": proposal.payload_hash,
                        "base_version": proposal.base_version,
                        "diff": api_payload["diff"],
                        "rationale": proposal.rationale,
                        "proposal_evidence": api_payload.get("evidence", {}),
                    },
                ),
            )
            should_submit = True

    if not should_submit:
        return await reconcile_approval_submission(
            simulator, store, audit, proposal.proposal_id
        )

    try:
        approval = await simulator.submit_approval(proposal.to_api_payload())
    except AmbiguousPostError:
        return await reconcile_approval_submission(
            simulator, store, audit, proposal.proposal_id
        )
    except SimulatorHTTPError as exc:
        if exc.status_code == 409:
            return await reconcile_approval_submission(
                simulator, store, audit, proposal.proposal_id
            )
        async with store.transaction() as session:
            durable_operation = await store.get_outbox_operation(operation_key)
            if durable_operation is not None:
                await store.update_outbox_status(
                    session, durable_operation.id, "failed"
                )
            await audit.append(
                session,
                AuditEntry(
                    action="approval_submission_failed",
                    actor="controller",
                    reason="Simulator rejected the approval request",
                    workflow_id=proposal.workflow_id,
                    resource_type="proposal",
                    resource_id=proposal.proposal_id,
                    evidence={
                        "proposal_id": proposal.proposal_id,
                        "payload_hash": proposal.payload_hash,
                        "http_status": exc.status_code,
                    },
                ),
            )
        raise

    if (
        approval.get("proposal_id") != proposal.proposal_id
        or approval.get("workflow_id") != proposal.workflow_id
    ):
        return await reconcile_approval_submission(
            simulator, store, audit, proposal.proposal_id
        )
    durable = await store.get_governance_proposal(proposal.proposal_id)
    if durable is None:
        raise RuntimeError("durable proposal disappeared after successful submission")
    return await _record_remote_approval(store, audit, durable, approval)
