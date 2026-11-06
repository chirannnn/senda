import httpx
import pytest

from controller.clients.simulator import SimulatorClient
from controller.config import Settings
from controller.errors import AmbiguousPostError, SimulatorError, SimulatorHTTPError


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _settings(**kwargs):
    return Settings("http://simulator.test", **kwargs)


@pytest.mark.anyio
async def test_client_lifecycle_and_documented_get_endpoints():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        payloads = {
            "/health": {"ok": True},
            "/sim/state": {"finished": False, "ts": "D0 00:00"},
            "/workflows": [],
        }
        return httpx.Response(200, json=payloads[request.url.path])

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    assert not client.is_open
    async with client:
        assert client.is_open
        assert await client.get_health() == {"ok": True}
        assert (await client.get_state())["finished"] is False
        assert await client.list_workflows() == []
    assert not client.is_open
    assert seen == [
        ("GET", "/health"),
        ("GET", "/sim/state"),
        ("GET", "/workflows"),
    ]


@pytest.mark.anyio
async def test_get_retries_transient_status_with_bounded_backoff():
    attempts = 0
    delays = []

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    async def sleep(seconds):
        delays.append(seconds)

    client = SimulatorClient(
        _settings(),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
        random_value=lambda: 1.0,
    )
    async with client:
        assert await client.get_health() == {"ok": True}

    assert attempts == 2
    assert delays == [0.25]


@pytest.mark.anyio
async def test_get_honors_retry_after_with_configured_cap():
    attempts = 0
    delays = []

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "30"})
        return httpx.Response(200, json={"ok": True})

    async def sleep(seconds):
        delays.append(seconds)

    client = SimulatorClient(
        _settings(retry_max_backoff_seconds=2.0),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    )
    async with client:
        await client.get_health()

    assert attempts == 2
    assert delays == [2.0]


@pytest.mark.anyio
async def test_get_does_not_retry_non_transient_status():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(404)

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(SimulatorHTTPError) as exc_info:
            await client.get_state()

    assert exc_info.value.status_code == 404
    assert attempts == 1


@pytest.mark.anyio
async def test_advance_posts_once_and_returns_documented_response():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.read()))
        return httpx.Response(
            200,
            json={"ts": "D1 00:00", "finished": True, "new_chunks": []},
        )

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        result = await client.advance(1440)

    assert result["finished"] is True
    assert seen == [("POST", "/sim/advance", b'{"minutes":1440}')]


@pytest.mark.anyio
async def test_ambiguous_post_transport_failure_is_never_retried():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("simulated timeout", request=request)

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(AmbiguousPostError, match="do not retry it blindly"):
            await client.advance(15)

    assert attempts == 1


@pytest.mark.anyio
async def test_ambiguous_post_server_error_is_not_retried():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(500)

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(AmbiguousPostError, match="reconcile simulator state"):
            await client.advance(15)

    assert attempts == 1


@pytest.mark.anyio
async def test_methods_require_open_client_and_advance_requires_valid_tick():
    client = SimulatorClient(
        _settings(), transport=httpx.MockTransport(lambda request: None)
    )
    with pytest.raises(SimulatorError, match="not open"):
        await client.get_state()

    async with client:
        with pytest.raises(ValueError, match="multiple of 15"):
            await client.advance(10)
