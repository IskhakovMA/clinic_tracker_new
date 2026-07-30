"""Fail-closed configuration: the app must not be usable in an insecure state."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_login_impossible_before_password_is_initialised(tmp_path, monkeypatch):
    """No env var, no stored hash → nobody can get in, and no default exists."""
    monkeypatch.setenv("CLINIC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CLINIC_ADMIN_PASSWORD", raising=False)
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]

    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import db, security

    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert security.get_password_hash(db.get_connection()) is None
        for attempt in ("password", "admin", "", "clinic"):
            r = client.post("/api/auth/login", json={"password": attempt or "x"})
            assert r.status_code == 503, f"unexpected status for {attempt!r}"
        assert client.get("/api/patients").status_code == 401


def test_weak_bootstrap_password_is_not_accepted(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLINIC_ADMIN_PASSWORD", "password")
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]

    from fastapi.testclient import TestClient
    from backend.app.main import app

    with TestClient(app, base_url="http://127.0.0.1") as client:
        r = client.post("/api/auth/login", json={"password": "password"})
        assert r.status_code == 503, "a weak bootstrap password must not become usable"


def test_no_admin_user_concept_in_backend():
    """The old client-side users table is gone; there is no per-user login."""
    for path in (PROJECT_ROOT / "clinic-v2.js", PROJECT_ROOT / "clinic-v2.html"):
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
    monkeypatch.setattr(store, "list_patients", lambda conn: (_ for _ in ()).throw(RuntimeError("boom secret detail")))
    from .conftest import TEST_PASSWORD
    client.post("/api/auth/login", json={"password": TEST_PASSWORD})
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
