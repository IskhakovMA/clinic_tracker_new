import json

from fastapi.routing import APIRoute

from .conftest import DEV_USERNAME, TEST_PASSWORD, create_account, login, make_patient_payload


def _dependency_names(dependant) -> set[str]:
    names = set()
    for dep in dependant.dependencies:
        call = getattr(dep, "call", None)
        names.add(getattr(call, "__name__", repr(call)))
        names |= _dependency_names(dep)
    return names


def _iter_api_routes(routes):
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        nested = getattr(route, "router", None) or getattr(route, "original_router", None)
        if nested is not None:
            yield from _iter_api_routes(nested.routes)


def test_write_endpoint_csrf_inventory_is_explicit():
    from backend.app.main import app

    csrf_na = {
        ("POST", "/api/auth/login"): "session creation uses credentials, not cookie auth",
        ("POST", "/api/import/preview"): "validated preview only; no database commit",
        ("POST", "/api/migrate/localstorage/preview"): "validated preview only; no database commit",
    }
    discovered = {}
    for route in _iter_api_routes(app.routes):
        for method in sorted(route.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            names = _dependency_names(route.dependant)
            discovered[(method, route.path)] = "require_csrf" in names

    assert len(discovered) >= 35
    missing = {key: has_csrf for key, has_csrf in discovered.items() if not has_csrf and key not in csrf_na}
    assert missing == {}
    for key in csrf_na:
        assert key in discovered


def test_logout_requires_csrf_and_active_session(auth_client):
    old_session = auth_client.cookies.get("clinic_session")
    old_csrf = auth_client.cookies.get("clinic_csrf")

    assert auth_client.get("/api/auth/logout").status_code == 405
    assert auth_client.post("/api/auth/logout").status_code == 403
    assert auth_client.post("/api/auth/logout", headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert auth_client.post(
        "/api/auth/logout",
        headers={**auth_client.csrf, "Origin": "https://evil.example"},
    ).status_code == 403
    assert auth_client.post(
        "/api/auth/logout",
        headers={**auth_client.csrf, "Referer": "https://evil.example/path"},
    ).status_code == 403

    # A token from a different live session must not authorize this session.
    assert login(auth_client, DEV_USERNAME, TEST_PASSWORD).status_code == 200
    assert auth_client.cookies.get("clinic_session") != old_session
    assert auth_client.post("/api/auth/logout", headers={"X-CSRF-Token": old_csrf}).status_code == 403

    good_csrf = {"X-CSRF-Token": auth_client.cookies.get("clinic_csrf")}
    live_session = auth_client.cookies.get("clinic_session")
    assert auth_client.post("/api/auth/logout", headers=good_csrf).status_code == 200

    auth_client.cookies.set("clinic_session", live_session)
    auth_client.cookies.set("clinic_csrf", good_csrf["X-CSRF-Token"])
    assert auth_client.post("/api/auth/logout", headers=good_csrf).status_code == 401


def test_csrf_cookie_and_header_must_be_present_together(client):
    create_account(DEV_USERNAME)
    assert login(client, DEV_USERNAME).status_code == 200
    csrf = client.cookies.get("clinic_csrf")
    session = client.cookies.get("clinic_session")

    client.cookies.clear()
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 401

    client.cookies.set("clinic_session", session)
    client.cookies.set("clinic_csrf", csrf)
    assert client.post("/api/auth/logout").status_code == 403


def test_wrong_content_type_does_not_create_patient(auth_client):
    before = auth_client.get("/api/patients").json()
    r = auth_client.post(
        "/api/patients",
        content=json.dumps(make_patient_payload(), ensure_ascii=False),
        headers={**auth_client.csrf, "Content-Type": "text/plain"},
    )
    assert 400 <= r.status_code < 500
    assert auth_client.get("/api/patients").json() == before


def test_request_models_forbid_unknown_fields(auth_client):
    assert auth_client.post(
        "/api/patients",
        json={**make_patient_payload(), "role": "developer"},
        headers=auth_client.csrf,
    ).status_code == 422
    assert auth_client.post(
        "/api/import/commit",
        json={"raw": "[]", "mode": "add", "created_by": "attacker"},
        headers=auth_client.csrf,
    ).status_code == 422
    assert auth_client.post(
        "/api/analytics/export",
        json={"reportType": "summary", "providerState": "sent"},
        headers=auth_client.csrf,
    ).status_code == 422


def test_nested_import_models_forbid_unknown_fields(auth_client):
    raw = json.dumps(
        {
            "schemaVersion": 3,
            "patients": [
                {
                    **make_patient_payload(),
                    "history": [
                        {
                            "stage": "exam",
                            "enteredAt": "2026-07-01T10:00:00+00:00",
                            "events": [{"date": "2026-07-10", "actor": "attacker"}],
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )
    r = auth_client.post("/api/import/preview", json={"raw": raw})
    assert r.status_code == 200
    assert r.json()["valid"] is False
    assert any("extra" in err["message"].lower() or "forbidden" in err["message"].lower() for err in r.json()["errors"])


def test_abuse_limits_return_429_with_retry_after(auth_client):
    for _ in range(20):
        r = auth_client.post("/api/analytics/export", json={"reportType": "summary"}, headers=auth_client.csrf)
        assert r.status_code == 200, r.text
    limited = auth_client.post("/api/analytics/export", json={"reportType": "summary"}, headers=auth_client.csrf)
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers


def test_data_quality_limit_is_bounded(auth_client):
    for _ in range(60):
        assert auth_client.get("/api/data-quality/issues").status_code == 200
    limited = auth_client.get("/api/data-quality/issues")
    assert limited.status_code == 429
    assert "Retry-After" in limited.headers
