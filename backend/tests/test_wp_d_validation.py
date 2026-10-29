"""WP-D: единая диагностика всех моделей не повторяет значения запроса."""
import json

import pytest

MARKER = "СИНТЕТИЧЕСКОЕ_МЕДИЦИНСКОЕ_ЗНАЧЕНИЕ"


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/patients"), ("PATCH", "/api/patients/synthetic"),
    ("POST", "/api/auth/login"), ("POST", "/api/auth/change-password"),
    ("POST", "/api/import/commit"), ("POST", "/api/import/preview"),
    ("POST", "/api/migrate/localstorage/commit"), ("POST", "/api/migrate/localstorage/preview"),
    ("POST", "/api/users"), ("POST", "/api/departments"),
    ("POST", "/api/patients/synthetic/stage"),
    ("POST", "/api/patients/synthetic/history/0/events"),
    ("PATCH", "/api/patients/synthetic/history/0/checklist"),
    ("PUT", "/api/notifications/patients/synthetic/preferences"),
    ("PUT", "/api/notifications/templates/synthetic"),
    ("PUT", "/api/patients/synthetic/portal-access"),
])
def test_validation_does_not_echo_input(auth_client, method, path):
    response = auth_client.request(method, path, json={MARKER: MARKER}, headers=auth_client.csrf)
    assert response.status_code == 422
    assert MARKER not in response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["detail"]
    assert all(set(error) == {"type", "loc", "msg"} for error in body["detail"])


def test_validation_preserves_known_field_and_type(auth_client):
    from .conftest import make_patient_payload
    payload = make_patient_payload()
    payload["age"] = {"private": MARKER}
    response = auth_client.post("/api/patients", json=payload, headers=auth_client.csrf)
    assert response.status_code == 422
    assert MARKER not in response.text
    assert any(error["loc"] == ["body", "age"] and error["type"] == "int_type"
               for error in response.json()["detail"])


def test_import_preview_validation_does_not_echo_custom_messages(auth_client):
    raw = json.dumps({"patients": [{"name": {"private": MARKER}, MARKER: MARKER}]})
    response = auth_client.post("/api/import/preview", json={"raw": raw})
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert MARKER not in response.text


def test_safe_validation_excludes_custom_context_and_unknown_locations(client):
    from backend.app.validation import safe_validation_errors
    result = safe_validation_errors([{"type": "value_error", "loc": ("body", "name", MARKER),
        "msg": MARKER, "input": MARKER, "ctx": {"error": ValueError(MARKER)}}])
    assert MARKER not in json.dumps(result, ensure_ascii=False)
    assert result[0]["loc"] == ["body", "name", "unknown_field"]
