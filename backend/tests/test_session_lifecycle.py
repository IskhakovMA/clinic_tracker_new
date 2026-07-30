"""Session lifecycle guarantees beyond the basic login/logout in test_auth."""
from .conftest import TEST_PASSWORD, make_patient_payload


def test_password_change_invalidates_all_sessions(auth_client):
    from backend.app import db, security
    assert auth_client.get("/api/patients").status_code == 200

    security.set_password(db.get_connection(), "another-strong-password")

    assert auth_client.get("/api/patients").status_code == 401
    assert auth_client.get("/api/auth/me").json() == {"authenticated": False}
    # old password no longer works, new one does
    assert auth_client.post("/api/auth/login", json={"password": TEST_PASSWORD}).status_code == 401
    from backend.app import security as sec
    sec._failed_attempts.clear()
    assert auth_client.post("/api/auth/login", json={"password": "another-strong-password"}).status_code == 200


def test_weak_password_rejected_by_policy():
    from backend.app import security
    import pytest
    for weak in ("password", "12345678", "short"):
        with pytest.raises(security.WeakPasswordError):
            security.validate_password_strength(weak)
    security.validate_password_strength("a-perfectly-fine-passphrase")


def test_no_session_fixation_attacker_supplied_cookie(client):
    """A cookie value the server never issued must not become a valid session,
    and logging in must not adopt a pre-set session id."""
    forged = "attacker-chosen-session-value"
    assert client.get("/api/patients", cookies={"clinic_session": forged}).status_code == 401

    login = client.post("/api/auth/login", json={"password": TEST_PASSWORD},
                        cookies={"clinic_session": forged})
    issued = login.cookies["clinic_session"]
    assert issued != forged, "login must mint a fresh session id, not reuse the supplied one"

    # The forged value is still worthless after a successful login elsewhere.
    client.cookies.clear()
    assert client.get("/api/patients", cookies={"clinic_session": forged}).status_code == 401


def test_logout_clears_cookies_client_side(auth_client):
    r = auth_client.post("/api/auth/logout")
    set_cookie = ", ".join(v for k, v in r.headers.multi_items() if k.lower() == "set-cookie")
    assert "clinic_session=" in set_cookie
    assert "clinic_csrf=" in set_cookie
    # deletion is expressed as an immediate expiry
    assert "Max-Age=0" in set_cookie or "expires=Thu, 01 Jan 1970" in set_cookie.lower()


def test_presenting_an_expired_session_deletes_its_row(client, monkeypatch):
    """Validating an expired token rejects it *and* drops the stale row.

    The cookie is passed explicitly per-request: a real client discards a
    cookie whose Max-Age has already elapsed, so the jar would never send it
    and this cleanup path would go untested.
    """
    from backend.app import config, db

    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", -1)
    login = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    # Read the token off the raw header: an already-expired cookie is dropped
    # by the client's jar before it can be read back.
    set_cookies = [v for k, v in login.headers.multi_items() if k.lower() == "set-cookie"]
    expired_token = next(
        v.split("clinic_session=", 1)[1].split(";", 1)[0]
        for v in set_cookies if v.startswith("clinic_session=")
    )

    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

    r = client.get("/api/patients", cookies={"clinic_session": expired_token})
    assert r.status_code == 401
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_purge_expired_sessions_helper(client, monkeypatch):
    from backend.app import config, db, security
    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", -1)
    client.post("/api/auth/login", json={"password": TEST_PASSWORD})

    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= 1
    security.purge_expired_sessions(conn)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_login_purges_expired_sessions(client, monkeypatch):
    from backend.app import config, db
    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", -1)
    client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    conn = db.get_connection()

    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", 3600)
    from backend.app import security
    security._failed_attempts.clear()
    client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    # only the fresh session remains
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_authenticated_api_is_not_cacheable(auth_client):
    for path in ("/api/patients", "/api/export", "/api/audit", "/api/auth/me"):
        r = auth_client.get(path)
        assert r.headers.get("cache-control") == "no-store", path


def test_raw_session_token_never_stored(auth_client, tmp_path):
    import sqlite3
    raw = auth_client.cookies.get("clinic_session")
    conn = sqlite3.connect(str(tmp_path / "clinic.db"))
    dump = "\n".join(conn.iterdump())
    conn.close()
    assert raw not in dump, "raw session token found in database dump"


def test_audit_actor_is_not_the_token(auth_client):
    raw = auth_client.cookies.get("clinic_session")
    events = auth_client.get("/api/audit").json()["events"]
    actors = {e["actor"] for e in events if e["actor"]}
    assert actors
    for actor in actors:
        assert actor not in raw
        assert raw not in actor
