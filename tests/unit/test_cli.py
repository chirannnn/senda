import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _invoke(*args, extra_environment=None):
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.update(extra_environment or {})
    return subprocess.run(
        [sys.executable, "-m", "controller", *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_cli_requires_simulator_url():
    result = _invoke()
    assert result.returncode == 2
    assert "usage: controller" in result.stderr


def test_cli_rejects_url_with_embedded_credentials_without_echoing_them():
    result = _invoke("http://user:secret@127.0.0.1:8700")
    assert result.returncode == 2
    assert "must not contain credentials" in result.stderr
    assert "secret" not in result.stderr


def test_cli_fails_actionably_when_simulator_is_unavailable():
    result = _invoke(
        "http://127.0.0.1:1",
        extra_environment={
            "CONTROLLER_GET_RETRIES": "0",
            "CONTROLLER_CONNECT_TIMEOUT_SECONDS": "0.1",
        },
    )
    assert result.returncode == 1
    assert '"severity":"ERROR"' in result.stderr
    assert "127.0.0.1:1" not in result.stderr
