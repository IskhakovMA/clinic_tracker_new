from .conftest import TEST_PASSWORD


def test_login_success_sets_cookies(client):
    r = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert r.status_code == 200
    assert r.json() == {"authenticated": True}
    assert client.cookies.get("clinic_session")
    assert client.cookies.get("clinic_csrf")
    set_cookie = ", ".join(v for k, v in r.headers.multi_items() if k.lower() == "set-cookie")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie or "SameSite=Lax" in set_cookie


def test_login_wrong_password(client):
    r = client.post("/api/auth/login", json={"password": "nope"})
    assert r.status_code == 401
    # Generic message, no hints about why it failed
    assert r.json()["detail"] == "Invalid password"
    assert client.cookies.get("clinic_session") is None


def test_login_rate_limited_after_repeated_failures(client):
    for _ in range(5):
        r = client.post("/api/auth/login", json={"password": "nope"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"password": "nope"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    # Even the CORRECT password is throttled while the window is hot
    r = client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert r.status_code == 429


def test_me_reflects_session_state(client):
    assert client.get("/api/auth/me").json() == {"authenticated": False}
    client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert client.get("/api/auth/me").json() == {"authenticated": True}


def test_logout_invalidates_session(auth_client):
    old_session = auth_client.cookies.get("clinic_session")
    r = auth_client.post("/api/auth/logout")
    assert r.status_code == 200
    # Re-presenting the old cookie must not work
    auth_client.cookies.set("clinic_session", old_session)
    assert auth_client.get("/api/patients").status_code == 401


def test_session_rotation_on_login(client):
    client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    first = client.cookies.get("clinic_session")
    client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    second = client.cookies.get("clinic_session")
    assert first != second


def test_api_requires_session(client):
    assert client.get("/api/patients").status_code == 401
    assert client.get("/api/export").status_code == 401
    assert client.post("/api/import/preview", json={"raw": "[]"}).status_code == 401


def test_expired_session_rejected(client, monkeypatch):
    from backend.app import security, config
    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", -1)
    client.post("/api/auth/login", json={"password": TEST_PASSWORD})
    assert client.get("/api/patients").status_code == 401


def test_no_password_hash_leaks_to_client(auth_client):
    r = auth_client.get("/api/auth/me")
    body = r.text.lower()
    assert "argon2" not in body
    assert "hash" not in body


def test_login_unconfigured_password(client, tmp_path, monkeypatch):
    from backend.app import db, security
    conn = db.get_connection()
    with conn:
        conn.execute("DELETE FROM app_login")
    r = client.post("/api/auth/login", json={"password": "anything"})
    assert r.status_code == 503
