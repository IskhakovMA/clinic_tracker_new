"""The backend runs as one process while the request limiters live in memory."""
import os
import subprocess
import sys

import pytest

from .conftest import PROJECT_ROOT

PROBE = (
    "from backend.app import process_guard\n"
    "try:\n"
    "    process_guard.release(process_guard.acquire())\n"
    "    print('acquired')\n"
    "except process_guard.MultipleProcessesError as exc:\n"
    "    print('refused:', exc)\n"
)


def _second_process(data_dir, **env):
    full_env = {k: v for k, v in os.environ.items() if k not in ("WEB_CONCURRENCY", "UVICORN_WORKERS")}
    full_env.update(CLINIC_DATA_DIR=str(data_dir), PYTHONDONTWRITEBYTECODE="1", **env)
    return subprocess.run([sys.executable, "-c", PROBE], cwd=PROJECT_ROOT, env=full_env,
                          capture_output=True, text=True, timeout=60).stdout.strip()


def test_second_process_on_the_same_data_cannot_start(client, tmp_path):
    # client holds the app (and the lock) for tmp_path
    out = _second_process(tmp_path)
    assert out.startswith("refused:") and "only one process" in out


def test_lock_is_released_on_shutdown(client, tmp_path):
    client.__exit__(None, None, None)
    assert _second_process(tmp_path) == "acquired"


@pytest.mark.parametrize("name", ["WEB_CONCURRENCY", "UVICORN_WORKERS"])
def test_multi_worker_setting_refuses_to_start(tmp_path, name):
    out = _second_process(tmp_path, **{name: "2"})
    assert out.startswith("refused:") and name in out


def test_single_worker_setting_is_accepted(tmp_path):
    assert _second_process(tmp_path, WEB_CONCURRENCY="1") == "acquired"


def test_app_startup_fails_closed_with_several_workers(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    for mod in [m for m in sys.modules if m.startswith("backend.")]:
        del sys.modules[mod]
    from fastapi.testclient import TestClient
    from backend.app.main import app
    with pytest.raises(Exception, match="single process only"):
        with TestClient(app):
            pass
