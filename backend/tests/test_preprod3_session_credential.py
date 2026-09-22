"""Pre-production hardening, round 3: a login cannot outlive a password change.

Regression test for preprod-run-3 candidate
auth-session-not-bound-to-credential-version: a login that verified the old
password before a password change or admin reset committed could still
create its session after the revocation, and that session stayed valid for
the full TTL.
"""
from fastapi.testclient import TestClient

from .conftest import TEST_PASSWORD, create_account, get_conn

NEW_PASSWORD = "brand-new-strong-passphrase"


def _race_login(monkeypatch, username, during_verify):
    from backend.app import security
    from backend.app.main import app
    real = security.verify_password
    state = {"done": False}

    def verify_then_change(password, password_hash):
        ok = real(password, password_hash)
        if ok and not state["done"]:
            state["done"] = True
            during_verify()
        return ok

    monkeypatch.setattr(security, "verify_password", verify_then_change)
    attacker = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    r = attacker.post("/api/auth/login", json={"username": username, "password": TEST_PASSWORD})
    return attacker, r


def test_login_racing_a_password_change_gets_no_session(client, monkeypatch):
    from backend.app import users
    uid = create_account("doc.race", "doctor")

    def change():
        conn = get_conn()
        users.change_own_password(conn, uid, NEW_PASSWORD)
        users.terminate_sessions(conn, uid)

    attacker, r = _race_login(monkeypatch, "doc.race", change)
    assert r.status_code == 401
    assert attacker.get("/api/patients").status_code == 401
    assert get_conn().execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (uid,)).fetchone()[0] == 0


def test_login_racing_an_admin_block_gets_no_session(client, monkeypatch):
    from backend.app import users
    uid = create_account("doc.race2", "doctor")

    def block():
        conn = get_conn()
        users.update_user_fields(conn, uid, status="blocked")
        users.terminate_sessions(conn, uid)

    _attacker, r = _race_login(monkeypatch, "doc.race2", block)
    assert r.status_code == 401
    assert get_conn().execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (uid,)).fetchone()[0] == 0


def test_ordinary_login_still_works(client):
    from .conftest import login
    create_account("doc.plain", "doctor")
    assert login(client, "doc.plain").status_code == 200
    assert client.get("/api/auth/me").json()["authenticated"] is True
