"""Validated, stable approval proposal payloads."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any


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
        # Fail at construction instead of later when persisting or hashing JSON.
        self._canonical_json()

    def to_api_payload(self) -> dict[str, Any]:
        """Return the exact documented approval request fields."""
        result: dict[str, Any] = {
            "proposal_id": self.proposal_id,
            "workflow_id": self.workflow_id,
            "base_version": self.base_version,
            "proposed_config": self.proposed_config,
            "change_summary": self.change_summary,
            "diff": self.diff,
            "affected_leads": self.affected_leads,
            "dry_run": self.dry_run,
            "impact_score": self.impact_score,
            "rationale": self.rationale,
        }
        if self.evidence is not None:
            result["evidence"] = self.evidence
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
