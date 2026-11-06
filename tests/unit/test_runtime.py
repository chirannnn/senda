import asyncio

import pytest

from controller.config import Settings
from controller.errors import AmbiguousPostError, SimulatorHTTPError
from controller.runtime import run_controller


class FakeSimulator:
    def __init__(self, states, *, advance_error=None, on_advance=None):
        self.states = list(states)
        self.advance_error = advance_error
        self.on_advance = on_advance
        self.calls = []

    async def get_health(self):
        self.calls.append("health")
        return {"ok": True}

    async def list_workflows(self):
        self.calls.append("workflows")
        return [{"id": "wf"}]

    async def get_state(self):
        self.calls.append("state")
        return self.states.pop(0)

    async def advance(self, minutes):
        self.calls.append(("advance", minutes))
        if self.on_advance:
            await self.on_advance()
        if self.advance_error:
            raise self.advance_error
        return self.states.pop(0)


def _settings():
    return Settings("http://simulator.test", advance_minutes=15)


@pytest.mark.anyio
async def test_runtime_checks_readiness_and_returns_when_already_finished():
    simulator = FakeSimulator([{"finished": True}])
    outcome = await run_controller(_settings(), simulator, asyncio.Event())
    assert outcome.completed and not outcome.stopped
    assert simulator.calls == ["health", "workflows", "state"]


@pytest.mark.anyio
async def test_runtime_advances_until_simulator_reports_finished():
    simulator = FakeSimulator(
        [
            {"finished": False},
            {"finished": False},
            {"finished": True, "ts": "D1"},
        ]
    )
    outcome = await run_controller(_settings(), simulator, asyncio.Event())
    assert outcome.completed and not outcome.stopped
    assert simulator.calls == [
        "health",
        "workflows",
        "state",
        ("advance", 15),
        ("advance", 15),
    ]


@pytest.mark.anyio
async def test_finished_conflict_is_reconciled_by_reading_state():
    simulator = FakeSimulator(
        [{"finished": False}, {"finished": True}],
        advance_error=SimulatorHTTPError("POST", "/sim/advance", 409),
    )
    outcome = await run_controller(_settings(), simulator, asyncio.Event())
    assert outcome.completed
    assert simulator.calls[-2:] == [("advance", 15), "state"]


@pytest.mark.anyio
async def test_ambiguous_advance_fails_closed_without_retry():
    simulator = FakeSimulator(
        [{"finished": False}],
        advance_error=AmbiguousPostError("/sim/advance", "timeout"),
    )
    with pytest.raises(AmbiguousPostError):
        await run_controller(_settings(), simulator, asyncio.Event())
    assert simulator.calls.count(("advance", 15)) == 1


@pytest.mark.anyio
async def test_stop_during_inflight_advance_prevents_another_advance():
    stop_event = asyncio.Event()

    async def request_stop():
        stop_event.set()

    simulator = FakeSimulator(
        [{"finished": False}, {"finished": False}], on_advance=request_stop
    )
    outcome = await run_controller(_settings(), simulator, stop_event)
    assert outcome.stopped and not outcome.completed
    assert simulator.calls.count(("advance", 15)) == 1
