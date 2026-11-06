import httpx
import pytest

from controller.clients.simulator import SimulatorClient
from controller.config import Settings
from controller.ingestion.events import EventValidationError
from controller.ingestion.service import EventIngestionService


def _descriptor(chunk, kind):
    return {
        "chunk": chunk,
        "kind": kind,
        "events": 2,
        "first_event_id": 1,
        "last_event_id": 3,
        "until_minute": 0,
    }


@pytest.mark.anyio
async def test_history_listing_and_chunk_fetch_use_documented_http_endpoints():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path == "/events/chunks":
            return httpx.Response(200, json=[_descriptor(0, "history")])
        return httpx.Response(
            200,
            content=b'{"event_id":1,"type":"campaign_created"}\n'
            b'{"event_id":3,"type":"lead","features":{"raw":true}}\n',
            headers={"content-type": "application/x-ndjson"},
        )

    client = SimulatorClient(
        Settings("http://simulator.test"), transport=httpx.MockTransport(handler)
    )
    async with client:
        service = EventIngestionService(client)
        descriptors = await service.list_history_chunks()
        events = [event async for event in service.iter_chunk_events(descriptors[0])]

    assert [event.event_id for event in events] == [1, 3]
    assert events[1].payload["features"] == {"raw": True}
    assert seen == [
        ("GET", "/events/chunks", {"kind": "history", "after": "-1"}),
        ("GET", "/events/chunks/0", {}),
    ]


@pytest.mark.anyio
async def test_interrupted_download_retries_before_exposing_any_lines():
    attempts = 0

    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"event_id":1,"type":"lead"}\n'
            raise httpx.ReadError("interrupted")

        async def aclose(self):
            pass

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, stream=InterruptedStream())
        return httpx.Response(
            200,
            content=b'{"event_id":1,"type":"lead"}\n{"event_id":3,"type":"call"}\n',
        )

    delays = []

    async def sleep(delay):
        delays.append(delay)

    client = SimulatorClient(
        Settings("http://simulator.test"),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    )
    async with client, client.stream_chunk_lines(0) as lines:
        result = list(lines)

    assert attempts == 2
    assert len(delays) == 1
    assert len(result) == 2


@pytest.mark.anyio
async def test_history_list_rejects_live_descriptor():
    client = SimulatorClient(
        Settings("http://simulator.test"),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[_descriptor(0, "events")])
        ),
    )
    async with client:
        with pytest.raises(EventValidationError, match="non-history"):
            await EventIngestionService(client).list_history_chunks()


@pytest.mark.anyio
async def test_list_chunks_rejects_invalid_kind_and_cursor():
    client = SimulatorClient(Settings("http://simulator.test"))
    with pytest.raises(ValueError, match="kind"):
        await client.list_chunks("unknown")
    with pytest.raises(ValueError, match="cursor"):
        await client.list_chunks("history", after=True)
