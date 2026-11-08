import asyncio
import json
import os
import time
import tracemalloc

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

from controller.analytics.kpis import KpiCalculator
from controller.clients.simulator import SimulatorClient
from controller.config import Settings
from controller.errors import AmbiguousPostError, SimulatorError
from controller.ingestion.events import EventValidationError
from controller.ingestion.runner import (
    ADVANCE_OPERATION,
    AdvanceReconciliationError,
    advance_and_ingest,
    reconcile_pending_advances,
)
from controller.ingestion.service import EventIngestionService
from controller.runtime.lifecycle import run_controller
from controller.storage.store import DatabaseStore
from controller.telemetry import RuntimeTelemetry


def _descriptor(chunk, kind, first, last):
    return {
        "chunk": chunk,
        "kind": kind,
        "events": 2,
        "first_event_id": first,
        "last_event_id": last,
        "until_minute": chunk * 15,
    }


def _body(*events):
    return b"".join(
        (httpx.Response(200, json=event).text + "\n").encode() for event in events
    )


@pytest.mark.anyio
async def test_history_and_live_chunks_persist_with_global_order_and_id_gaps(
    postgres_store,
):
    descriptors = [
        _descriptor(0, "history", 1, 4),
        _descriptor(3, "events", 9, 12),
    ]
    contents = {
        0: _body(
            {"event_id": 1, "type": "campaign_created"},
            {"event_id": 4, "type": "lead"},
        ),
        3: _body(
            {"event_id": 9, "type": "call"},
            {"event_id": 12, "type": "call"},
        ),
    }

    def handler(request):
        if request.url.path == "/events/chunks":
            kind = request.url.params["kind"]
            return httpx.Response(
                200, json=[item for item in descriptors if item["kind"] == kind]
            )
        return httpx.Response(
            200, content=contents[int(request.url.path.rsplit("/", 1)[1])]
        )

    telemetry = RuntimeTelemetry()
    client = SimulatorClient(
        Settings("http://simulator.test"),
        transport=httpx.MockTransport(handler),
        telemetry=telemetry,
    )
    async with client:
        committed, inserted = await EventIngestionService(
            client, telemetry=telemetry
        ).ingest_available_chunks(postgres_store, batch_size=1)

    assert (committed, inserted) == (2, 4)
    assert await postgres_store.get_checkpoint("simulator") == 3
    for event_id in (1, 4, 9, 12):
        assert await postgres_store.get_event(event_id) is not None
    metrics = telemetry.export()
    assert metrics["counters"]["chunks_committed"] == 2
    assert metrics["counters"]["events_observed"] == 4
    assert metrics["counters"]["events_inserted"] == 4
    assert metrics["latest"]["pending_chunks"] == 0
    assert metrics["api"]["GET /events/chunks/{chunk_number}"]["requests"] == 2
    assert metrics["histograms"]["event_insert_execute_seconds"]["count"] == 4
    assert metrics["histograms"]["event_checkpoint_update_seconds"]["count"] == 2
    assert metrics["histograms"]["event_chunk_commit_seconds"]["count"] == 2
    for metric_name in (
        "event_insert_execute_seconds",
        "event_stage_create_seconds",
        "event_stage_reset_seconds",
        "event_stage_payload_build_seconds",
        "event_copy_execute_seconds",
        "event_stage_merge_execute_seconds",
        "event_checkpoint_update_seconds",
        "event_chunk_commit_seconds",
    ):
        histogram = metrics["histograms"][metric_name]
        assert histogram["sum_seconds"] >= 0
        assert histogram["max_seconds"] >= 0


@pytest.mark.anyio
async def test_replaying_committed_descriptor_is_idempotent(postgres_store):
    descriptors = [_descriptor(2, "history", 21, 24)]
    payload = _body(
        {"event_id": 21, "type": "lead"},
        {"event_id": 24, "type": "call"},
    )

    def handler(request):
        if request.url.path == "/events/chunks":
            kind = request.url.params["kind"]
            return httpx.Response(
                200, json=[item for item in descriptors if item["kind"] == kind]
            )
        return httpx.Response(200, content=payload)

    client = SimulatorClient(
        Settings("http://simulator.test"), transport=httpx.MockTransport(handler)
    )
    async with client:
        service = EventIngestionService(client)
        first = await service.ingest_available_chunks(postgres_store)
        descriptor = (await service.list_history_chunks())[0]
        replay = await service.ingest_descriptor(
            postgres_store, descriptor, checkpoint_key="simulator"
        )

    assert first == (1, 2)
    assert replay == 0
    assert await postgres_store.get_checkpoint("simulator") == 2


@pytest.mark.anyio
async def test_malformed_chunk_rolls_back_batches_and_remains_replayable(
    postgres_store,
):
    good = _descriptor(1, "history", 31, 34)
    malformed = _descriptor(4, "events", 40, 44)
    fixed = False

    def handler(request):
        nonlocal fixed
        if request.url.path == "/events/chunks":
            kind = request.url.params["kind"]
            after = int(request.url.params["after"])
            items = [good] if kind == "history" else [malformed]
            return httpx.Response(
                200, json=[item for item in items if item["chunk"] > after]
            )
        chunk_number = int(request.url.path.rsplit("/", 1)[1])
        if chunk_number == 1:
            return httpx.Response(
                200,
                content=_body(
                    {"event_id": 31, "type": "lead"},
                    {"event_id": 34, "type": "call"},
                ),
            )
        if fixed:
            return httpx.Response(
                200,
                content=_body(
                    {"event_id": 40, "type": "call"},
                    {"event_id": 44, "type": "call"},
                ),
            )
        # Declared event count/bounds do not match: first batch reaches SQL,
        # then trailer validation fails and the whole chunk transaction rolls back.
        return httpx.Response(200, content=_body({"event_id": 40, "type": "call"}))

    client = SimulatorClient(
        Settings("http://simulator.test"), transport=httpx.MockTransport(handler)
    )
    async with client:
        service = EventIngestionService(client)
        with pytest.raises(EventValidationError):
            await service.ingest_available_chunks(postgres_store, batch_size=1)

    assert await postgres_store.get_checkpoint("simulator") == 1
    assert await postgres_store.get_event(40) is None
    assert await postgres_store.get_event(31) is not None

    fixed = True
    async with client:
        committed, inserted = await EventIngestionService(
            client
        ).ingest_available_chunks(postgres_store, batch_size=1)

    assert (committed, inserted) == (1, 2)
    assert await postgres_store.get_checkpoint("simulator") == 4
    assert await postgres_store.get_event(31) is not None
    assert await postgres_store.get_event(40) is not None


@pytest.mark.anyio
async def test_live_advance_persists_intent_then_ingests_returned_chunks(
    postgres_store,
):
    descriptor = _descriptor(7, "events", 101, 105)
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/events/chunks":
            return httpx.Response(200, json=[])
        if request.url.path == "/sim/state":
            return httpx.Response(
                200,
                json={
                    "sim_minute": 0,
                    "ts": "D0 00:00",
                    "finished": False,
                    "chunks": 0,
                    "last_event_id": 0,
                },
            )
        if request.url.path == "/sim/advance":
            return httpx.Response(
                200,
                json={
                    "sim_minute": 15,
                    "ts": "D0 00:15",
                    "finished": True,
                    "new_chunks": [descriptor],
                },
            )
        return httpx.Response(
            200,
            content=_body(
                {"event_id": 101, "type": "call"},
                {"event_id": 105, "type": "call"},
            ),
        )

    client = SimulatorClient(
        Settings("http://simulator.test"), transport=httpx.MockTransport(handler)
    )
    async with client:
        result = await advance_and_ingest(client, postgres_store, 15)

    assert result.finished
    assert result.simulated_time == "D0 00:15"
    assert result.events_inserted == 2
    assert await postgres_store.get_checkpoint("simulator") == 7
    assert await postgres_store.get_event(101) is not None
    assert await postgres_store.get_event(105) is not None
    assert requests.index(("GET", "/sim/state")) < requests.index(
        ("POST", "/sim/advance")
    )
    assert requests.count(("POST", "/sim/advance")) == 1
    intents = await postgres_store.list_outbox_operations(ADVANCE_OPERATION)
    assert len(intents) == 1
    assert intents[0].status == "succeeded"
    assert intents[0].attempts == 1
    assert intents[0].payload["before"]["sim_minute"] == 0
    assert intents[0].payload["response"]["new_chunks"] == [descriptor]


@pytest.mark.anyio
async def test_runtime_bootstraps_history_then_finishes_after_live_ingestion(
    postgres_store,
):
    history = _descriptor(0, "history", 1, 3)
    live = _descriptor(4, "events", 9, 11)
    state = {
        "sim_minute": 0,
        "ts": "D0 00:00",
        "finished": False,
        "chunks": 1,
        "last_event_id": 3,
    }
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/health":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/workflows":
            return httpx.Response(200, json=[])
        if request.url.path == "/sim/state":
            return httpx.Response(200, json=state)
        if request.url.path == "/events/chunks":
            kind = request.url.params["kind"]
            after = int(request.url.params["after"])
            if kind == "history":
                return httpx.Response(
                    200, json=[history] if history["chunk"] > after else []
                )
            return httpx.Response(
                200, json=[live] if state["finished"] and live["chunk"] > after else []
            )
        if request.url.path == "/events/chunks/0":
            return httpx.Response(
                200,
                content=_body(
                    {
                        "event_id": 1,
                        "type": "campaign_created",
                        "ts": "2027-01-04T00:00:00+05:30",
                        "workflow_id": "wf-runtime",
                        "campaign_id": "campaign-1",
                        "leads": 2,
                        "config_version_at_creation": 1,
                    },
                    {
                        "event_id": 3,
                        "type": "lead",
                        "ts": "2027-01-04T00:01:00+05:30",
                        "workflow_id": "wf-runtime",
                        "campaign_id": "campaign-1",
                        "lead_id": "lead-1",
                        "person_id": "person-1",
                        "features": {"lang": "hi", "age_band": "46-60"},
                    },
                ),
            )
        if request.url.path == "/events/chunks/4":
            return httpx.Response(
                200,
                content=_body(
                    {
                        "event_id": 9,
                        "type": "call",
                        "ts": "2027-01-04T00:15:00+05:30",
                        "workflow_id": "wf-runtime",
                        "campaign_id": "campaign-1",
                        "lead_id": "lead-1",
                        "person_id": "person-1",
                        "config_version": 1,
                        "cohort_id": "default",
                        "variant": {"voice": "V1"},
                        "connected": True,
                        "engaged": False,
                        "disposition": "short_hangup",
                    },
                    {
                        "event_id": 11,
                        "type": "call",
                        "ts": "2027-01-04T00:30:00+05:30",
                        "workflow_id": "wf-runtime",
                        "campaign_id": "campaign-1",
                        "lead_id": "lead-2",
                        "person_id": "person-2",
                        "config_version": 1,
                        "cohort_id": "default",
                        "variant": {"voice": "V1"},
                        "connected": True,
                        "engaged": True,
                        "disposition": "concluded_interested",
                    },
                ),
            )
        if request.url.path == "/sim/advance":
            state.update(
                sim_minute=15,
                ts="D0 00:15",
                finished=True,
                chunks=2,
                last_event_id=11,
            )
            return httpx.Response(
                200,
                json={
                    "sim_minute": 15,
                    "ts": "D0 00:15",
                    "finished": True,
                    "new_chunks": [live],
                },
            )
        raise AssertionError(
            f"unexpected HTTP request {request.method} {request.url.path}"
        )

    client = SimulatorClient(
        Settings("http://simulator.test"), transport=httpx.MockTransport(handler)
    )
    async with client:
        outcome = await run_controller(
            Settings("http://simulator.test"),
            client,
            asyncio.Event(),
            postgres_store,
        )

    assert outcome.completed and not outcome.stopped
    assert await postgres_store.get_checkpoint("simulator") == 4
    stored = [await postgres_store.get_event(event_id) for event_id in (1, 3, 9, 11)]
    assert all(stored)
    assert requests.count(("POST", "/sim/advance")) == 1
    assert await postgres_store.get_analytics_cursor("kpi-facts-v1") == 11
    summary = await KpiCalculator(postgres_store).calculate("wf-runtime")
    assert (summary.dials, summary.connected, summary.concluded, summary.uploaded) == (
        2,
        2,
        1,
        2,
    )


@pytest.mark.anyio
async def test_restart_reconciles_advance_that_applied_before_response_was_lost(
    postgres_store,
):
    descriptor = _descriptor(12, "events", 201, 205)
    state = {
        "sim_minute": 0,
        "ts": "D0 00:00",
        "finished": False,
        "chunks": 0,
        "last_event_id": 0,
    }
    applied = False
    post_count = 0

    def handler(request):
        nonlocal applied, post_count
        if request.url.path == "/events/chunks":
            kind = request.url.params["kind"]
            items = [descriptor] if applied and kind == "events" else []
            return httpx.Response(200, json=items)
        if request.url.path == "/sim/state":
            return httpx.Response(200, json=state)
        if request.url.path == "/sim/advance":
            post_count += 1
            applied = True
            state.update(
                sim_minute=15,
                ts="D0 00:15",
                chunks=1,
                last_event_id=205,
            )
            raise httpx.ReadTimeout("response lost after application", request=request)
        return httpx.Response(
            200,
            content=_body(
                {"event_id": 201, "type": "call"},
                {"event_id": 205, "type": "call"},
            ),
        )

    client = SimulatorClient(
        Settings("http://simulator.test"), transport=httpx.MockTransport(handler)
    )
    async with client:
        with pytest.raises(AmbiguousPostError):
            await advance_and_ingest(client, postgres_store, 15)

    # A fresh store instance represents a controller process restart.
    database_url = os.environ["CONTROLLER_TEST_DATABASE_URL"]
    await postgres_store.close()
    restarted_store = DatabaseStore(database_url)
    await restarted_store.initialize()
    try:
        recovery_client = SimulatorClient(
            Settings("http://simulator.test"),
            transport=httpx.MockTransport(handler),
        )
        async with recovery_client:
            outcomes = await reconcile_pending_advances(
                recovery_client, restarted_store
            )

        assert post_count == 1
        assert len(outcomes) == 1 and outcomes[0].finished is False
        assert await restarted_store.get_checkpoint("simulator") == 12
        assert await restarted_store.get_event(201) is not None
        assert await restarted_store.get_event(205) is not None
        intents = await restarted_store.list_outbox_operations(ADVANCE_OPERATION)
        assert len(intents) == 1 and intents[0].status == "succeeded"
        assert (
            intents[0].payload["reconciliation"]["result"]
            == "simulator_progress_observed"
        )
    finally:
        await restarted_store.close()


@pytest.mark.anyio
async def test_ambiguous_advance_retries_only_after_state_and_descriptors_prove_no_progress(
    postgres_store,
):
    descriptor = _descriptor(5, "events", 301, 303)
    state = {
        "sim_minute": 0,
        "ts": "D0 00:00",
        "finished": False,
        "chunks": 0,
        "last_event_id": 0,
    }
    post_count = 0
    requests = []

    def handler(request):
        nonlocal post_count
        requests.append((request.method, request.url.path))
        if request.url.path == "/events/chunks":
            return httpx.Response(200, json=[])
        if request.url.path == "/sim/state":
            return httpx.Response(200, json=state)
        if request.url.path == "/sim/advance":
            post_count += 1
            if post_count == 1:
                raise httpx.ReadTimeout("request outcome unclear", request=request)
            state.update(
                sim_minute=15,
                ts="D0 00:15",
                chunks=1,
                last_event_id=303,
            )
            return httpx.Response(
                200,
                json={
                    **state,
                    "new_chunks": [descriptor],
                },
            )
        return httpx.Response(
            200,
            content=_body(
                {"event_id": 301, "type": "call"},
                {"event_id": 303, "type": "call"},
            ),
        )

    client = SimulatorClient(
        Settings("http://simulator.test"), transport=httpx.MockTransport(handler)
    )
    async with client:
        with pytest.raises(AmbiguousPostError):
            await advance_and_ingest(client, postgres_store, 15)

    await postgres_store.close()
    restarted_store = DatabaseStore(os.environ["CONTROLLER_TEST_DATABASE_URL"])
    await restarted_store.initialize()
    try:
        recovery_client = SimulatorClient(
            Settings("http://simulator.test"),
            transport=httpx.MockTransport(handler),
        )
        async with recovery_client:
            outcomes = await reconcile_pending_advances(
                recovery_client, restarted_store
            )

        assert len(outcomes) == 1 and outcomes[0].finished is False
        assert post_count == 2
        second_post = [
            i for i, item in enumerate(requests) if item == ("POST", "/sim/advance")
        ][1]
        state_read_before_retry = max(
            i
            for i, item in enumerate(requests[:second_post])
            if item == ("GET", "/sim/state")
        )
        assert state_read_before_retry < second_post
        assert await restarted_store.get_checkpoint("simulator") == 5
        intents = await restarted_store.list_outbox_operations(ADVANCE_OPERATION)
        assert intents[0].attempts == 2 and intents[0].status == "succeeded"
    finally:
        await restarted_store.close()


@pytest.mark.anyio
async def test_unavailable_reconciliation_get_never_retries_advance(postgres_store):
    state = {
        "sim_minute": 0,
        "ts": "D0 00:00",
        "finished": False,
        "chunks": 0,
        "last_event_id": 0,
    }
    post_count = 0
    fail_state = False

    def handler(request):
        nonlocal post_count
        if request.url.path == "/events/chunks":
            return httpx.Response(200, json=[])
        if request.url.path == "/sim/state":
            if fail_state:
                raise httpx.ConnectError("unavailable", request=request)
            return httpx.Response(200, json=state)
        if request.url.path == "/sim/advance":
            post_count += 1
            raise httpx.ReadTimeout("ambiguous", request=request)

    settings = Settings("http://simulator.test", get_retries=0)
    first = SimulatorClient(settings, transport=httpx.MockTransport(handler))
    async with first:
        with pytest.raises(AmbiguousPostError):
            await advance_and_ingest(first, postgres_store, 15)

    fail_state = True
    recovery = SimulatorClient(settings, transport=httpx.MockTransport(handler))
    async with recovery:
        with pytest.raises(SimulatorError):
            await reconcile_pending_advances(recovery, postgres_store)

    assert post_count == 1
    unresolved = await postgres_store.list_outbox_operations(
        ADVANCE_OPERATION, statuses=("in_progress",)
    )
    assert len(unresolved) == 1


@pytest.mark.anyio
async def test_repeated_no_progress_ambiguity_is_bounded(postgres_store):
    state = {
        "sim_minute": 0,
        "ts": "D0 00:00",
        "finished": False,
        "chunks": 0,
        "last_event_id": 0,
    }
    post_count = 0

    def handler(request):
        nonlocal post_count
        if request.url.path == "/events/chunks":
            return httpx.Response(200, json=[])
        if request.url.path == "/sim/state":
            return httpx.Response(200, json=state)
        if request.url.path == "/sim/advance":
            post_count += 1
            raise httpx.ReadTimeout("response unavailable", request=request)

    client = SimulatorClient(
        Settings("http://simulator.test"), transport=httpx.MockTransport(handler)
    )
    async with client:
        with pytest.raises(AmbiguousPostError):
            await advance_and_ingest(client, postgres_store, 15)
        with pytest.raises(AmbiguousPostError):
            await reconcile_pending_advances(client, postgres_store)
        with pytest.raises(AdvanceReconciliationError, match="retry limit"):
            await reconcile_pending_advances(client, postgres_store)

    assert post_count == 2
    intents = await postgres_store.list_outbox_operations(ADVANCE_OPERATION)
    assert len(intents) == 1
    assert intents[0].attempts == 2
    assert intents[0].status == "reconciliation_required"


@pytest.mark.anyio
async def test_database_write_failure_after_download_keeps_chunk_replayable(
    postgres_store, monkeypatch
):
    descriptor = _descriptor(8, "history", 401, 405)

    def handler(request):
        if request.url.path == "/events/chunks":
            kind = request.url.params["kind"]
            return httpx.Response(
                200,
                json=[descriptor] if kind == "history" else [],
            )
        return httpx.Response(
            200,
            content=_body(
                {"event_id": 401, "type": "lead"},
                {"event_id": 405, "type": "call"},
            ),
        )

    async def fail_write(*args, **kwargs):
        raise SQLAlchemyError("injected database write failure")

    monkeypatch.setattr(postgres_store, "commit_chunk_stream", fail_write)
    client = SimulatorClient(
        Settings("http://simulator.test"), transport=httpx.MockTransport(handler)
    )
    async with client:
        with pytest.raises(SQLAlchemyError, match="injected"):
            await EventIngestionService(client).ingest_available_chunks(postgres_store)

    assert await postgres_store.get_checkpoint("simulator") is None
    assert await postgres_store.get_event(401) is None


@pytest.mark.anyio
async def test_large_chunk_throughput_and_memory_are_measured(postgres_store):
    event_count = 30_000
    first_id = 1_000_001
    last_id = first_id + (event_count - 1) * 2
    descriptor = {
        "chunk": 26,
        "kind": "history",
        "events": event_count,
        "first_event_id": first_id,
        "last_event_id": last_id,
        "until_minute": 0,
    }

    class GeneratedChunk(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(event_count):
                payload = {
                    "event_id": first_id + offset * 2,
                    "type": "call",
                    "opaque": "x" * 48,
                }
                yield (json.dumps(payload, separators=(",", ":")) + "\n").encode()

        async def aclose(self):
            pass

    def handler(request):
        if request.url.path == "/events/chunks":
            kind = request.url.params["kind"]
            return httpx.Response(
                200,
                json=[descriptor] if kind == "history" else [],
            )
        return httpx.Response(200, stream=GeneratedChunk())

    client = SimulatorClient(
        Settings("http://simulator.test"), transport=httpx.MockTransport(handler)
    )
    tracemalloc.start()
    started = time.perf_counter()
    async with client:
        committed, inserted = await EventIngestionService(
            client
        ).ingest_available_chunks(postgres_store, batch_size=500)
    elapsed = time.perf_counter() - started
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert (committed, inserted) == (1, event_count)
    assert await postgres_store.get_checkpoint("simulator") == 26
    assert peak_bytes < 16 * 1024 * 1024
    print(
        f"synthetic streamed ingestion: {event_count} events, "
        f"{event_count / elapsed:.0f} events/s, {peak_bytes / 1024 / 1024:.2f} MiB traced peak"
    )
