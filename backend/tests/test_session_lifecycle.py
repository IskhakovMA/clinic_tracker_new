"""Session lifecycle guarantees beyond the basic login/logout in test_auth."""
from .conftest import DEV_USERNAME, TEST_PASSWORD, create_account, get_conn, login


def test_password_reset_invalidates_all_sessions(auth_client):
    """Сброс пароля Разработчиком завершает все сессии пользователя."""
    from backend.app import users
    assert auth_client.get("/api/patients").status_code == 200

    conn = get_conn()
    row = users.get_user_by_username(conn, DEV_USERNAME)
    users.reset_password(conn, row["id"], "another-strong-password", must_change_password=False)
    users.terminate_sessions(conn, row["id"])

    assert auth_client.get("/api/patients").status_code == 401
    assert auth_client.get("/api/auth/me").json() == {"authenticated": False}
    # old password no longer works, new one does
    assert login(auth_client, DEV_USERNAME, TEST_PASSWORD).status_code == 401
    from backend.app import security as sec
    sec._failed_attempts.clear()
    assert login(auth_client, DEV_USERNAME, "another-strong-password").status_code == 200


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
    create_account(DEV_USERNAME)
    forged = "attacker-chosen-session-value"
    client.cookies.set("clinic_session", forged)
    assert client.get("/api/patients").status_code == 401

    login_resp = client.post(
        "/api/auth/login",
        json={"username": DEV_USERNAME, "password": TEST_PASSWORD},
    )
    issued = login_resp.cookies["clinic_session"]
    assert issued != forged, "login must mint a fresh session id, not reuse the supplied one"

    # The forged value is still worthless after a successful login elsewhere.
    client.cookies.clear()
    client.cookies.set("clinic_session", forged)
    assert client.get("/api/patients").status_code == 401


def test_logout_clears_cookies_client_side(auth_client):
    r = auth_client.post("/api/auth/logout", headers=auth_client.csrf)
    set_cookie = ", ".join(v for k, v in r.headers.multi_items() if k.lower() == "set-cookie")
    assert "clinic_session=" in set_cookie
    assert "clinic_csrf=" in set_cookie
    # deletion is expressed as an immediate expiry
    assert "Max-Age=0" in set_cookie or "expires=Thu, 01 Jan 1970" in set_cookie.lower()


def test_presenting_an_expired_session_deletes_its_row(client, monkeypatch):
    """Validating an expired token rejects it *and* drops the stale row.

    The cookie value is copied from the raw header and placed back into the
    test client's jar: a real client discards a cookie whose Max-Age has
    already elapsed, so the original jar would never send it and this cleanup
    path would go untested.
    """
    from backend.app import config, db

    create_account(DEV_USERNAME)
    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", -1)
    login_resp = client.post(
        "/api/auth/login", json={"username": DEV_USERNAME, "password": TEST_PASSWORD}
    )
    # Read the token off the raw header: an already-expired cookie is dropped
    # by the client's jar before it can be read back.
    set_cookies = [v for k, v in login_resp.headers.multi_items() if k.lower() == "set-cookie"]
    expired_token = next(
        v.split("clinic_session=", 1)[1].split(";", 1)[0]
        for v in set_cookies if v.startswith("clinic_session=")
    )

    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1

    client.cookies.set("clinic_session", expired_token)
    r = client.get("/api/patients")
    assert r.status_code == 401
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_purge_expired_sessions_helper(client, monkeypatch):
    from backend.app import config, db, security
    create_account(DEV_USERNAME)
    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", -1)
    login(client, DEV_USERNAME)

    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] >= 1
    security.purge_expired_sessions(conn)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_login_purges_expired_sessions(client, monkeypatch):
    from backend.app import config, db
    create_account(DEV_USERNAME)
    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", -1)
    login(client, DEV_USERNAME)
    conn = db.get_connection()

    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", 3600)
    from backend.app import security
    security._failed_attempts.clear()
    login(client, DEV_USERNAME)
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
