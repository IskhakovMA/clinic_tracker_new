"""Fail-closed configuration: the app must not be usable in an insecure state."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_login_impossible_before_first_developer(tmp_path, monkeypatch):
    """Нет учётных записей → войти нельзя; встроенных паролей не существует,
    прежний общий пароль (CLINIC_ADMIN_PASSWORD) больше не действует."""
    monkeypatch.setenv("CLINIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLINIC_ADMIN_PASSWORD", "legacy-shared-password")
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]

    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import db

    with TestClient(app, base_url="http://127.0.0.1") as client:
        conn = db.get_connection()
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        for username, password in (
            ("admin", "legacy-shared-password"),
            ("admin", "password"),
            ("root", "clinic123!"),
        ):
            r = client.post("/api/auth/login", json={"username": username, "password": password})
            assert r.status_code == 401, f"unexpected status for {username!r}"
        assert client.get("/api/patients").status_code == 401


def test_shared_password_fallback_is_gone():
    """Ни модуль security, ни auth-роут не содержат пути входа по общему
    паролю: вход возможен только через таблицу users."""
    from backend.app import security
    assert not hasattr(security, "get_password_hash")
    assert not hasattr(security, "ensure_bootstrap_password")
    from backend.app import config
    assert not hasattr(config, "BOOTSTRAP_PASSWORD")


def test_no_admin_user_concept_in_backend():
    """The old client-side users table is gone; there is no per-user login."""
    for path in (PROJECT_ROOT / "static" / "app.js", PROJECT_ROOT / "index.html"):
        text = path.read_text(encoding="utf-8")
        assert "doctor1" not in text
        assert "nurse1" not in text
        assert "normalizedPassword" not in text


def test_debug_off_by_default():
    from backend.app import config
    assert config.DEBUG is False
    assert config.HTTPS is False
    assert config.HOST == "127.0.0.1"


def test_docs_and_openapi_hidden_without_debug(client):
    assert client.get("/api/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_no_cors_middleware_installed():
    from backend.app.main import app
    names = [m.cls.__name__ for m in app.user_middleware]
    assert not any("CORS" in n for n in names), names


def test_no_wildcard_cors_header(client):
    r = client.get("/api/auth/me", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_traceback_not_returned_to_client(client, monkeypatch):
    from backend.app import store
    monkeypatch.setattr(
        store, "list_patients",
        lambda conn, scope=None: (_ for _ in ()).throw(RuntimeError("boom secret detail")),
    )
    from .conftest import DEV_USERNAME, create_account, login
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    r = client.get("/api/patients")
    assert r.status_code == 500
    assert "boom secret detail" not in r.text
    assert "Traceback" not in r.text
    assert r.json() == {"error": "Internal server error"}


def test_sensitive_paths_not_served_over_http(client):
    for path in (
        "/backend/data/clinic.db",
        "/data/clinic.db",
        "/clinic.db",
        "/backend/.env",
        "/.env",
        "/backend/data/backups/clinic.db",
        "/backend/app/config.py",
    ):
        assert client.get(path).status_code == 404, path


def test_env_example_contains_no_real_secret():
    """Every value must be blank or a non-secret default. Mentions of Argon2id
    in comments are documentation, not secrets — an actual hash would start
    with the `$argon2id$` marker."""
    example = (PROJECT_ROOT / "backend" / ".env.example").read_text(encoding="utf-8")
    assert "$argon2id$" not in example
    for line in example.splitlines():
        stripped = line.strip()
        if stripped.startswith("CLINIC_ADMIN_PASSWORD"):
            assert stripped == "CLINIC_ADMIN_PASSWORD=", f"a value is filled in: {line}"


def test_gitignore_covers_sensitive_artifacts():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "*.db", "*.db-wal", "*.db-shm", "backend/data/", "backups/", "logs/", "__pycache__/"):
        assert pattern in gitignore, f"{pattern} missing from .gitignore"
    assert "!.env.example" in gitignore
