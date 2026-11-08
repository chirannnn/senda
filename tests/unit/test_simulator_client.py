import asyncio

import httpx
import pytest

from controller.clients.simulator import SimulatorClient
from controller.config import Settings
from controller.errors import AmbiguousPostError, SimulatorError, SimulatorHTTPError
from controller.telemetry import RuntimeTelemetry


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
async def test_client_records_each_api_attempt_without_exposing_resource_ids():
    telemetry = RuntimeTelemetry()
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts == 1 else 200, json={"ok": True})

    client = SimulatorClient(
        _settings(),
        transport=httpx.MockTransport(handler),
        sleep=lambda _: asyncio.sleep(0),
        telemetry=telemetry,
    )
    async with client:
        assert await client.get_health() == {"ok": True}

    payload = telemetry.export()
    assert payload["api"]["GET /health"]["requests"] == 2
    assert payload["api"]["GET /health"]["failures"] == 1
    assert payload["api"]["GET /health"]["status_codes"] == {
        "200": 1,
        "503": 1,
    }


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


@pytest.mark.anyio
async def test_suggestion_uses_documented_one_shot_endpoint_and_validates_schema():
    attempts = []

    def handler(request):
        attempts.append((request.method, request.url.path, request.read()))
        return httpx.Response(
            200,
            json={
                "suggestions": [
                    {"prompt_version": "P2", "summary": "Candidate prompt"}
                ],
                "credits_remaining": 7,
            },
        )

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        result = await client.suggest_fixes("wf-1", "Observed QA elevated")
    assert result == {
        "suggestions": [{"prompt_version": "P2", "summary": "Candidate prompt"}],
        "credits_remaining": 7.0,
    }
    assert attempts == [
        (
            "POST",
            "/llm/suggest-fixes",
            b'{"workflow_id":"wf-1","problem":"Observed QA elevated"}',
        )
    ]


@pytest.mark.anyio
async def test_suggestion_post_timeout_is_ambiguous_and_never_retried():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("suggestion response lost", request=request)

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(AmbiguousPostError):
            await client.suggest_fixes("wf-1", "Observed QA elevated")
    assert attempts == 1


@pytest.mark.anyio
async def test_suggestion_http_402_is_not_retried():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(402)

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(SimulatorHTTPError) as exc_info:
            await client.suggest_fixes("wf-1", "Observed QA elevated")
    assert exc_info.value.status_code == 402
    assert attempts == 1


@pytest.mark.anyio
async def test_suggestion_rejects_unexpected_candidate_fields():
    client = SimulatorClient(
        _settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "suggestions": [
                        {
                            "prompt_version": "P2",
                            "summary": "Candidate",
                            "approved": True,
                        }
                    ],
                    "credits_remaining": 7,
                },
            )
        ),
    )
    async with client:
        with pytest.raises(SimulatorError, match="unexpected schema"):
            await client.suggest_fixes("wf-1", "Observed QA elevated")


@pytest.mark.anyio
async def test_validation_posts_documented_candidate_and_validates_bound_result():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.read()))
        return httpx.Response(
            200,
            json={
                "validation_run_id": "val_00001",
                "workflow_id": "wf-1",
                "prompt_version": "P2",
                "n": 100,
                "failures": 5,
                "pass_rate": 0.95,
                "ts": "2027-01-04T01:00:00+05:30",
                "credits_remaining": 9,
            },
        )

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        result = await client.run_validation("wf-1", "P2", 100)
    assert result["validation_run_id"] == "val_00001"
    assert result["pass_rate"] == 0.95
    assert seen == [
        (
            "POST",
            "/validation/runs",
            b'{"workflow_id":"wf-1","prompt_version":"P2","n":100}',
        )
    ]


@pytest.mark.anyio
async def test_validation_post_timeout_is_ambiguous_and_never_retried():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("validation response lost", request=request)

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(AmbiguousPostError):
            await client.run_validation("wf-1", "P2", 100)
    assert attempts == 1


@pytest.mark.anyio
async def test_validation_rejects_inconsistent_stats_and_402_is_one_shot():
    attempts = 0

    def invalid(request):
        return httpx.Response(
            200,
            json={
                "validation_run_id": "val_00001",
                "workflow_id": "wf-1",
                "prompt_version": "P2",
                "n": 100,
                "failures": 5,
                "pass_rate": 0.5,
                "ts": "2027-01-04T01:00:00+05:30",
                "credits_remaining": 9,
            },
        )

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(invalid))
    async with client:
        with pytest.raises(SimulatorError, match="statistics"):
            await client.run_validation("wf-1", "P2", 100)

    def out_of_credits(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(402)

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(out_of_credits))
    async with client:
        with pytest.raises(SimulatorHTTPError) as exc_info:
            await client.run_validation("wf-1", "P2", 100)
    assert exc_info.value.status_code == 402
    assert attempts == 1


@pytest.mark.anyio
async def test_governance_reads_and_approval_submission_use_documented_routes():
    approval = {
        "approval_id": "apr_00001",
        "proposal_id": "proposal-1",
        "workflow_id": "wf-1",
        "status": "pending",
        "base_version": 2,
        "changed_dims": ["voice"],
    }
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.url.query))
        if request.url.path == "/workflows/wf-1/config":
            return httpx.Response(
                200, json={"workflow_id": "wf-1", "version": 2, "config": {}}
            )
        if request.url.path == "/workflows/wf-1/config/history":
            return httpx.Response(200, json=[])
        if request.url.path == "/approvals" and request.method == "POST":
            return httpx.Response(200, json=approval)
        if request.url.path == "/approvals/apr_00001":
            return httpx.Response(200, json=approval)
        return httpx.Response(200, json=[approval])

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        assert (await client.get_workflow_config("wf-1"))["version"] == 2
        assert await client.get_config_history("wf-1") == []
        posted = await client.submit_approval({"proposal_id": "proposal-1"})
        listed = await client.list_approvals(workflow_id="wf-1")
        fetched = await client.get_approval("apr_00001")

    assert posted["approval_id"] == "apr_00001"
    assert listed == [approval]
    assert fetched == approval
    assert [item[:2] for item in seen] == [
        ("GET", "/workflows/wf-1/config"),
        ("GET", "/workflows/wf-1/config/history"),
        ("POST", "/approvals"),
        ("GET", "/approvals"),
        ("GET", "/approvals/apr_00001"),
    ]
    assert b"workflow_id=wf-1" in seen[3][2]


@pytest.mark.anyio
async def test_approval_post_timeout_is_ambiguous_and_sent_once():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("response lost", request=request)

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(AmbiguousPostError, match="do not retry it blindly"):
            await client.submit_approval({"proposal_id": "proposal-1"})

    assert attempts == 1


@pytest.mark.anyio
async def test_approval_duplicate_is_a_definitive_http_error_not_retried():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(409, json={"error": "duplicate_proposal_id"})

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(SimulatorHTTPError) as exc_info:
            await client.submit_approval({"proposal_id": "proposal-1"})

    assert exc_info.value.status_code == 409
    assert attempts == 1


@pytest.mark.anyio
async def test_malformed_approval_response_fails_closed():
    client = SimulatorClient(
        _settings(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    async with client:
        with pytest.raises(SimulatorError, match="required identity fields"):
            await client.submit_approval({"proposal_id": "proposal-1"})


@pytest.mark.anyio
async def test_apply_posts_exact_approval_id_once_and_validates_result():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.read()))
        return httpx.Response(
            200,
            json={"workflow_id": "wf-1", "version": 3, "changed_dims": ["voice"]},
        )

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        result = await client.apply_approval("wf-1", "apr_00001")

    assert result["version"] == 3
    assert seen == [
        (
            "POST",
            "/workflows/wf-1/apply",
            b'{"approval_id":"apr_00001"}',
        )
    ]


@pytest.mark.anyio
async def test_ambiguous_apply_post_is_never_retried():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("response lost", request=request)

    client = SimulatorClient(_settings(), transport=httpx.MockTransport(handler))
    async with client:
        with pytest.raises(AmbiguousPostError, match="do not retry it blindly"):
            await client.apply_approval("wf-1", "apr_00001")

    assert attempts == 1
