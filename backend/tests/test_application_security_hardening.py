import json
import sys
from pathlib import Path

from fastapi.routing import APIRoute

from .conftest import DEV_USERNAME, TEST_PASSWORD, create_account, get_conn, login, make_patient_payload


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


def test_docs_and_openapi_registration_by_debug_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLINIC_DEBUG", "true")
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]
    from fastapi.testclient import TestClient
    from backend.app.main import app as debug_app

    debug_paths = {getattr(route, "path", None) for route in debug_app.routes}
    assert "/openapi.json" in debug_paths
    assert "/api/docs" in debug_paths
    assert "/redoc" not in debug_paths
    with TestClient(debug_app, base_url="http://127.0.0.1", raise_server_exceptions=False) as debug_client:
        assert debug_client.get("/api/docs").status_code == 200
        assert debug_client.get("/docs").status_code == 404
        assert debug_client.get("/redoc").status_code == 404
        assert debug_client.get("/openapi.json").status_code == 200

    monkeypatch.setenv("CLINIC_DATA_DIR", str(tmp_path / "prod"))
    monkeypatch.setenv("CLINIC_DEBUG", "false")
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]
    from backend.app.main import app as prod_app

    prod_paths = {getattr(route, "path", None) for route in prod_app.routes}
    assert "/openapi.json" not in prod_paths
    assert "/api/docs" not in prod_paths
    assert "/docs" not in prod_paths
    assert "/redoc" not in prod_paths
    with TestClient(prod_app, base_url="http://127.0.0.1", raise_server_exceptions=False) as prod_client:
        assert prod_client.get("/api/docs").status_code == 404
        assert prod_client.get("/docs").status_code == 404
        assert prod_client.get("/redoc").status_code == 404
        assert prod_client.get("/openapi.json").status_code == 404


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
    for field in ("role", "is_admin", "actor_user_id", "created_by", "status", "attempt_count", "idempotency_key"):
        assert auth_client.post(
            "/api/patients",
            json={**make_patient_payload(), field: "attacker"},
            headers=auth_client.csrf,
        ).status_code == 422, field
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


def test_preview_endpoints_are_side_effect_free(auth_client):
    conn = get_conn()
    raw = json.dumps({"schemaVersion": 3, "patients": [make_patient_payload(name="Превью Тест")]}, ensure_ascii=False)
    db_path = Path(conn.execute("PRAGMA database_list").fetchone()["file"])

    def snapshot():
        return {
            "patients": conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0],
            "audit": conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
            "sessions": [
                dict(row) for row in conn.execute("SELECT * FROM sessions ORDER BY token_hash").fetchall()
            ],
            "notifications": conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0],
            "files": sorted(p.name for p in db_path.parent.iterdir()),
        }

    before = snapshot()
    assert auth_client.post("/api/import/preview", json={"raw": raw}).status_code == 200
    assert auth_client.post("/api/migrate/localstorage/preview", json={"raw": raw}).status_code == 200
    after = snapshot()
    assert after == before


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


def test_proxy_headers_do_not_change_rate_limit_identity(auth_client):
    for idx in range(20):
        r = auth_client.post(
            "/api/analytics/export",
            json={"reportType": "summary"},
            headers={
                **auth_client.csrf,
                "X-Forwarded-For": f"1.2.3.{idx}",
                "X-Real-IP": f"5.6.7.{idx}",
                "Forwarded": f"for=8.8.8.{idx};proto=https",
            },
        )
        assert r.status_code == 200, r.text
    limited = auth_client.post(
        "/api/analytics/export",
        json={"reportType": "summary"},
        headers={
            **auth_client.csrf,
            "X-Forwarded-For": "9.9.9.9, 10.0.0.1, malformed",
            "X-Real-IP": "9.9.9.9",
            "Forwarded": "for=unknown",
        },
    )
    assert limited.status_code == 429


def test_rate_limit_buckets_are_bounded_and_pruned(monkeypatch):
    from backend.app import config, security

    monkeypatch.setattr(config, "REQUEST_RATE_LIMIT_MAX_BUCKETS", 3)
    with security._rate_limit_lock:
        security._request_attempts.clear()
    for i in range(10):
        assert security.check_request_rate_limit(f"bucket-{i}", 1, 60) == 0
    assert len(security._request_attempts) <= 3

    with security._rate_limit_lock:
        for values in security._request_attempts.values():
            values[:] = [-10_000.0]
    assert security.check_request_rate_limit("fresh", 1, 60) == 0
    assert list(security._request_attempts) == ["fresh"]


def test_login_rate_limit_keys_do_not_store_plaintext_username(client):
    from backend.app import security

    username = "CaseSensitive.User"
    create_account(username)
    login(client, username.upper(), "wrong-password")
    keys = "\n".join(security._failed_attempts.keys()).lower()
    assert "casesensitive" not in keys
    assert "user" not in keys
    assert security.rate_limit_subject(username.upper()) in keys
