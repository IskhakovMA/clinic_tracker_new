import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TEST_PASSWORD = "correct-horse-battery"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with an isolated temp database per test."""
    monkeypatch.setenv("CLINIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLINIC_ADMIN_PASSWORD", TEST_PASSWORD)

    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]

    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import security

    security._failed_attempts.clear()

    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def auth_client(client):
    """Client with an active session; also carries a helper for CSRF headers."""
    r = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert r.status_code == 200
    client.csrf = {"X-CSRF-Token": client.cookies.get("clinic_csrf")}
    return client


def make_patient_payload(**overrides):
    payload = {
        "name": "Тестов Тест Тестович",
        "age": 50,
        "phone": "+7 (999) 000-00-00",
        "priority": "normal",
        "stage": "exam",
        "diagnosis": "C34.1",
        "nosology": "lung34",
        "diseaseStage": "II",
        "doctor": "Др. Проверяющий",
        "head": "Шашолин Михаил Аркадьевич",
        "complaint": "Жалобы для теста",
        "events": [],
    }
    payload.update(overrides)
    return payload
