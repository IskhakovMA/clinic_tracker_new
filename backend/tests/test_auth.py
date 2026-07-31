from .conftest import DEV_USERNAME, TEST_PASSWORD, create_account, login


def test_login_success_sets_cookies(client):
    create_account(DEV_USERNAME)
    r = login(client, DEV_USERNAME)
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["user"]["username"] == DEV_USERNAME
    assert body["user"]["roleId"] == "developer"
    assert client.cookies.get("clinic_session")
    assert client.cookies.get("clinic_csrf")
    set_cookie = ", ".join(v for k, v in r.headers.multi_items() if k.lower() == "set-cookie")
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie or "SameSite=Lax" in set_cookie


def test_login_wrong_password(client):
    create_account(DEV_USERNAME)
    r = login(client, DEV_USERNAME, "nope-nope")
    assert r.status_code == 401
    # Generic message, no hints about why it failed
    assert r.json()["detail"] == "Invalid credentials"
    assert client.cookies.get("clinic_session") is None


def test_login_unknown_username_same_response(client):
    create_account(DEV_USERNAME)
    wrong_pw = login(client, DEV_USERNAME, "nope-nope")
    unknown = login(client, "no.such.user", "nope-nope")
    # «Нет такого логина» неотличим от «неверный пароль»
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json() == unknown.json()


def test_login_rate_limited_after_repeated_failures(client):
    create_account(DEV_USERNAME)
    for _ in range(5):
        r = login(client, DEV_USERNAME, "nope-nope")
        assert r.status_code == 401
    r = login(client, DEV_USERNAME, "nope-nope")
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    # Even the CORRECT password is throttled while the window is hot
    r = login(client, DEV_USERNAME, TEST_PASSWORD)
    assert r.status_code == 429


def test_me_reflects_session_state(client):
    create_account(DEV_USERNAME)
    assert client.get("/api/auth/me").json() == {"authenticated": False}
    login(client, DEV_USERNAME)
    me = client.get("/api/auth/me").json()
    assert me["authenticated"] is True
    assert me["user"]["username"] == DEV_USERNAME
    assert me["user"]["permissions"]["manageUsers"] is True


def test_logout_invalidates_session(auth_client):
    old_session = auth_client.cookies.get("clinic_session")
    r = auth_client.post("/api/auth/logout")
    assert r.status_code == 200
    # Re-presenting the old cookie must not work
    auth_client.cookies.set("clinic_session", old_session)
    assert auth_client.get("/api/patients").status_code == 401


def test_session_rotation_on_login(client):
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    first = client.cookies.get("clinic_session")
    login(client, DEV_USERNAME)
    second = client.cookies.get("clinic_session")
    assert first != second


def test_api_requires_session(client):
    assert client.get("/api/patients").status_code == 401
    assert client.get("/api/export").status_code == 401
    assert client.post("/api/import/preview", json={"raw": "[]"}).status_code == 401


def test_expired_session_rejected(client, monkeypatch):
    from backend.app import config
    create_account(DEV_USERNAME)
    monkeypatch.setattr(config, "SESSION_TTL_SECONDS", -1)
    login(client, DEV_USERNAME)
    assert client.get("/api/patients").status_code == 401


def test_no_password_hash_leaks_to_client(auth_client):
    r = auth_client.get("/api/auth/me")
    body = r.text.lower()
    assert "argon2" not in body
    assert "password_hash" not in body


def test_login_impossible_without_accounts(client):
    """Fail-closed: пока create-developer не выполнен, войти нельзя —
    встроенных паролей и общего пароля не существует."""
    r = login(client, "anyone", "anything-at-all")
    assert r.status_code == 401


def test_blocked_user_cannot_login(client):
    from backend.app import users
    from .conftest import get_conn
    uid = create_account("blocked.user")
    users.update_user_fields(get_conn(), uid, status="blocked")
    r = login(client, "blocked.user")
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid credentials"


def test_must_change_password_gates_working_endpoints(client):
    create_account("temp.user", must_change_password=True)
    r = login(client, "temp.user")
    assert r.status_code == 200
    assert r.json()["user"]["mustChangePassword"] is True
    # Рабочие endpoints закрыты до смены временного пароля
    assert client.get("/api/patients").status_code == 403
    # Смена пароля разрешена
    r = client.post(
        "/api/auth/change-password",
        json={"currentPassword": TEST_PASSWORD, "newPassword": "brand-new-secret-1"},
        headers=client.csrf,
    )
    assert r.status_code == 200
    assert client.get("/api/patients").status_code == 200
