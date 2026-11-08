"""Restart-safe derivation of KPI facts from raw persisted events."""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

from controller.storage.models import SimulatorEvent
from controller.storage.store import (
    MAX_KPI_FACT_PAGE_ROWS,
    DatabaseStore,
    KpiFactRecord,
)
from controller.telemetry import RuntimeTelemetry

PROCESSOR_NAME = "kpi-facts-v1"
_CONCLUDED_DISPOSITIONS = {"concluded_interested", "concluded_not_interested"}
_KNOWN_TYPES = {
    "campaign_created",
    "lead",
    "call",
    "config_changed",
    "approval_decided",
}


class KpiFactProcessor:
    """Project bounded pages of immutable raw events into unique fact rows."""

    def __init__(
        self,
        store: DatabaseStore,
        *,
        page_size: int = 1000,
        telemetry: RuntimeTelemetry | None = None,
    ) -> None:
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= MAX_KPI_FACT_PAGE_ROWS
        ):
            raise ValueError(
                f"page size must be between 1 and {MAX_KPI_FACT_PAGE_ROWS} rows"
            )
        self._store = store
        self._page_size = page_size
        self._telemetry = telemetry

    async def process_available(self) -> tuple[int, int]:
        """Return (source events scanned, facts newly inserted), safe across restarts."""
        cursor = await self._store.get_analytics_cursor(PROCESSOR_NAME)
        scanned = 0
        inserted = 0
        while True:
            page = await self._store.list_events_after(
                cursor, limit=self._page_size, telemetry=self._telemetry
            )
            if not page:
                return scanned, inserted
            projection_started = time.perf_counter()
            facts = [derive_fact(event) for event in page]
            if self._telemetry is not None:
                self._telemetry.observe(
                    "fact_projection_seconds", time.perf_counter() - projection_started
                )
            next_cursor = page[-1].event_id
            inserted += await self._store.commit_kpi_page(
                PROCESSOR_NAME,
                facts,
                next_cursor,
                telemetry=self._telemetry,
            )
            scanned += len(page)
            cursor = next_cursor


def derive_fact(event: SimulatorEvent) -> KpiFactRecord:
    """Derive only contract-backed additive counts; retain incomplete provenance."""
    payload: dict[str, Any] = event.payload
    event_type = payload.get("type")
    event_type = event_type if isinstance(event_type, str) else "unknown"
    workflow_id = _string(payload.get("workflow_id"))
    campaign_id = _string(payload.get("campaign_id"))
    lead_id = _string(payload.get("lead_id"))
    person_id = _string(payload.get("person_id"))
    cohort_id = _string(payload.get("cohort_id"))
    version_value = payload.get(
        "config_version", payload.get("config_version_at_creation")
    )
    config_version = (
        version_value
        if isinstance(version_value, int)
        and not isinstance(version_value, bool)
        and version_value >= 0
        else None
    )
    variant = payload.get("variant")
    variant = variant if isinstance(variant, dict) else None
    features = payload.get("features")
    features = features if isinstance(features, dict) else None
    quality = "valid"
    detail: str | None = None
    dials = connected = concluded = uploaded = 0
    qa_hallucination: bool | None = None
    qa_flow_adherence: float | None = None
    qa_quality = "not_applicable"

    if workflow_id is None:
        quality, detail = "invalid", "missing or invalid workflow_id"
    elif event_type not in _KNOWN_TYPES:
        quality, detail = "unsupported", f"unsupported event type: {event_type}"
    elif event_type == "campaign_created":
        leads = payload.get("leads")
        if isinstance(leads, int) and not isinstance(leads, bool) and leads >= 0:
            uploaded = leads
        else:
            quality, detail = "invalid", "campaign_created has invalid leads count"
    elif event_type == "lead":
        if lead_id is None:
            quality, detail = "incomplete", "lead event has no valid lead_id"
        elif features is None:
            quality, detail = "incomplete", "lead event has no feature object"
    elif event_type == "call":
        is_connected = payload.get("connected")
        disposition = payload.get("disposition")
        if not isinstance(is_connected, bool) or not isinstance(disposition, str):
            quality, detail = "invalid", "call lacks valid connected/disposition fields"
        elif disposition not in {
            "no_answer",
            "voicemail",
            "short_hangup",
            *_CONCLUDED_DISPOSITIONS,
        }:
            quality, detail = "invalid", "call has unknown disposition"
        elif disposition in _CONCLUDED_DISPOSITIONS and not is_connected:
            quality, detail = "invalid", "concluded disposition on disconnected call"
        else:
            dials = 1
            connected = int(is_connected)
            concluded = int(disposition in _CONCLUDED_DISPOSITIONS)
            qa = payload.get("qa")
            if is_connected:
                qa_quality = "incomplete"
                if isinstance(qa, dict):
                    hallucination = qa.get("hallucination_flag")
                    adherence = qa.get("flow_adherence")
                    if (
                        isinstance(hallucination, bool)
                        and isinstance(adherence, (int, float))
                        and not isinstance(adherence, bool)
                        and math.isfinite(adherence)
                        and 0 <= adherence <= 1
                    ):
                        qa_hallucination = hallucination
                        qa_flow_adherence = float(adherence)
                        qa_quality = "valid"
                    elif "hallucination_flag" in qa or "flow_adherence" in qa:
                        qa_quality = "invalid"
                elif qa is not None:
                    qa_quality = "invalid"
            if lead_id is None:
                quality, detail = "incomplete", "call event has no valid lead_id"
            elif config_version is None:
                quality, detail = "incomplete", "call has no valid config_version"

    occurred_at = _timestamp(payload.get("ts"))
    if occurred_at is None and quality == "valid":
        quality, detail = "incomplete", "event has no valid ISO timestamp"

    return KpiFactRecord(
        source_event_id=event.event_id,
        source_chunk=event.chunk_number,
        source_kind=event.kind,
        workflow_id=workflow_id,
        event_type=event_type,
        occurred_at=occurred_at,
        campaign_id=campaign_id,
        lead_id=lead_id,
        person_id=person_id,
        config_version=config_version,
        observed_cohort_id=cohort_id,
        variant=variant,
        features=features,
        quality=quality,
        quality_detail=detail,
        dials=dials,
        connected=connected,
        concluded=concluded,
        uploaded=uploaded,
        qa_hallucination=qa_hallucination,
        qa_flow_adherence=qa_flow_adherence,
        qa_quality=qa_quality,
    )


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
