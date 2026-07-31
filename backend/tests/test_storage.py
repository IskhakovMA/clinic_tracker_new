import sqlite3

from .conftest import make_patient_payload


def test_empty_db_boots_and_serves(client):
    # login + empty list on a brand new database
    from .conftest import DEV_USERNAME, create_account, login
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    assert client.get("/api/patients").json() == []


def test_data_survives_reconnect(auth_client, tmp_path):
    auth_client.post("/api/patients", json=make_patient_payload(), headers=auth_client.csrf)

    # Open the same file with a second, independent connection
    conn = sqlite3.connect(str(tmp_path / "clinic.db"))
    count = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    conn.close()
    assert count == 1


def test_foreign_keys_cascade(auth_client, tmp_path):
    p = auth_client.post("/api/patients", json=make_patient_payload(), headers=auth_client.csrf).json()
    auth_client.post(f"/api/patients/{p['id']}/history/0/events", headers=auth_client.csrf)
    auth_client.delete(f"/api/patients/{p['id']}", headers=auth_client.csrf)

    conn = sqlite3.connect(str(tmp_path / "clinic.db"))
    assert conn.execute("SELECT COUNT(*) FROM history_entries").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    conn.close()


def test_schema_version_row_present(auth_client, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "clinic.db"))
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 2
    conn.close()


def test_import_rollback_on_failure(auth_client, monkeypatch):
    import json
    from backend.app import importexport

    original = importexport._insert_full_patient
    calls = {"n": 0}

    def failing_insert(conn, p, pid):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom mid-import")
        original(conn, p, pid)

    monkeypatch.setattr(importexport, "_insert_full_patient", failing_insert)

    doc = json.dumps({
        "schemaVersion": 1,
        "patients": [
            {"name": "Первый", "priority": "normal", "stage": "exam"},
            {"name": "Второй", "priority": "normal", "stage": "exam"},
        ],
    }, ensure_ascii=False)
    r = auth_client.post("/api/import/commit", json={"raw": doc, "mode": "add"}, headers=auth_client.csrf)
    assert r.status_code == 500
    # Transaction rolled back: neither patient persisted
    assert auth_client.get("/api/patients").json() == []


def test_db_not_reachable_over_http(auth_client):
    for path in ("/backend/data/clinic.db", "/data/clinic.db", "/clinic.db"):
        assert auth_client.get(path).status_code == 404


def test_session_tokens_stored_hashed(auth_client, tmp_path):
    raw_token = auth_client.cookies.get("clinic_session")
    conn = sqlite3.connect(str(tmp_path / "clinic.db"))
    stored = [row[0] for row in conn.execute("SELECT token_hash FROM sessions").fetchall()]
    conn.close()
    assert stored
    assert raw_token not in stored  # only the hash is at rest
