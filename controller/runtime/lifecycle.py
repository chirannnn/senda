"""Minimal simulator lifecycle for the runtime foundation chunk.

Event ingestion and durable advance-intent reconciliation are deliberately
deferred to their planned chunks. An ambiguous advance therefore fails closed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from controller.config import Settings
from controller.errors import AmbiguousPostError, SimulatorError, SimulatorHTTPError

logger = logging.getLogger(__name__)


class SimulatorAPI(Protocol):
    """The documented simulator operations required by this runtime loop."""

    async def get_health(self) -> dict[str, Any]: ...

    async def get_state(self) -> dict[str, Any]: ...

    async def list_workflows(self) -> list[dict[str, Any]]: ...

    async def advance(self, minutes: int) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class RuntimeOutcome:
    """How the runtime loop stopped."""

    completed: bool
    stopped: bool


async def run_controller(
    settings: Settings,
    simulator: SimulatorAPI,
    stop_event: asyncio.Event,
) -> RuntimeOutcome:
    """Check readiness, then advance until finished or asked to stop."""
    await simulator.get_health()
    workflows = await simulator.list_workflows()
    state = await simulator.get_state()
    logger.info(
        "simulator ready",
        extra={"event": "startup_ready", "workflow_count": len(workflows)},
    )
    if _is_finished(state):
        return RuntimeOutcome(completed=True, stopped=False)

    while not stop_event.is_set():
        try:
            result = await simulator.advance(settings.advance_minutes)
        except AmbiguousPostError:
            # Until the durable intent/reconciliation chunk is implemented, no
            # subsequent advance is safe after an ambiguous remote outcome.
            logger.exception("advance response is ambiguous; stopping safely")
            raise
        except SimulatorHTTPError as exc:
            if (
                exc.method == "POST"
                and exc.path == "/sim/advance"
                and exc.status_code == 409
            ):
                state = await simulator.get_state()
                if _is_finished(state):
                    return RuntimeOutcome(completed=True, stopped=False)
            raise

        finished = _is_finished(result)
        logger.info(
            "simulator advanced",
            extra={
                "event": "advance_completed",
                "advance_minutes": settings.advance_minutes,
                "simulated_time": result.get("ts"),
                "finished": finished,
            },
        )
        if finished:
            return RuntimeOutcome(completed=True, stopped=False)

    return RuntimeOutcome(completed=False, stopped=True)


def _is_finished(payload: dict[str, Any]) -> bool:
    value = payload.get("finished")
    if not isinstance(value, bool):
        raise SimulatorError("simulator response is missing boolean 'finished' status")
    return value
