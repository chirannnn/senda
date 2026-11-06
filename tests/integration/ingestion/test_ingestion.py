import httpx
import pytest

from controller.clients.simulator import SimulatorClient
from controller.config import Settings
from controller.ingestion.events import EventValidationError
from controller.ingestion.service import EventIngestionService


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

    client = SimulatorClient(
        Settings("http://simulator.test"), transport=httpx.MockTransport(handler)
    )
    async with client:
        committed, inserted = await EventIngestionService(
            client
        ).ingest_available_chunks(postgres_store, batch_size=1)

    assert (committed, inserted) == (2, 4)
    assert await postgres_store.get_checkpoint("simulator") == 3
    for event_id in (1, 4, 9, 12):
        assert await postgres_store.get_event(event_id) is not None


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
