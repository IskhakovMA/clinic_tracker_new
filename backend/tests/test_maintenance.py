import sqlite3

import pytest

from .conftest import TEST_PASSWORD, make_patient_payload


def test_check_db_passes_on_live_database(auth_client, tmp_path):
    from backend.app import maintenance
    report = maintenance.check_database(tmp_path / "clinic.db")
    assert report["ok"] is True
    assert report["integrity_check"] == ["ok"]
    assert report["foreign_key_violations"] == 0
    assert report["missing_tables"] == []
    assert report["schema_version"] == 1


def test_check_db_reports_missing_file(tmp_path):
    from backend.app import maintenance
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.check_database(tmp_path / "nope.db")


def test_check_db_detects_missing_table(auth_client, tmp_path):
    from backend.app import db, maintenance
    conn = db.get_connection()
    with conn:
        conn.execute("DROP TABLE audit_events")
    report = maintenance.check_database(tmp_path / "clinic.db")
    assert report["ok"] is False
    assert "audit_events" in report["missing_tables"]


def test_check_db_detects_wrong_schema_version(auth_client, tmp_path):
    from backend.app import db, maintenance
    conn = db.get_connection()
    with conn:
        conn.execute("UPDATE schema_version SET version = 99")
    report = maintenance.check_database(tmp_path / "clinic.db")
    assert report["ok"] is False
    assert any("schema version" in p for p in report["problems"])


def test_backup_creates_consistent_copy(auth_client, tmp_path):
    from backend.app import maintenance
    auth_client.post("/api/patients", json=make_patient_payload(), headers=auth_client.csrf)

    dest = maintenance.backup_database(tmp_path / "clinic.db")
    assert dest.exists()
    assert maintenance.looks_like_sqlite(dest)

    conn = sqlite3.connect(str(dest))
    assert conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 1
    conn.close()


def test_backup_refuses_to_overwrite_without_flag(auth_client, tmp_path):
    from backend.app import maintenance
    dest = tmp_path / "explicit.db"
    maintenance.backup_database(tmp_path / "clinic.db", dest)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.backup_database(tmp_path / "clinic.db", dest)
    # with the flag it succeeds
    maintenance.backup_database(tmp_path / "clinic.db", dest, overwrite=True)


def test_restore_round_trip_preserves_data(auth_client, tmp_path):
    from backend.app import db, maintenance
    db_path = tmp_path / "clinic.db"

    auth_client.post("/api/patients", json=make_patient_payload(name="Сохранённый Пациент"), headers=auth_client.csrf)
    backup = maintenance.backup_database(db_path, tmp_path / "snapshot.db")

    # Change the live DB after the snapshot, then roll back to it.
    auth_client.post("/api/patients", json=make_patient_payload(name="Лишний Пациент"), headers=auth_client.csrf)
    assert len(auth_client.get("/api/patients").json()) == 2

    db.reset_singleton_for_tests()
    result = maintenance.restore_database(backup, db_path)
    assert result["patient_count"] == 1
    assert result["pre_restore_backup"] is not None

    conn = sqlite3.connect(str(db_path))
    names = [row[0] for row in conn.execute("SELECT name FROM patients").fetchall()]
    conn.close()
    assert names == ["Сохранённый Пациент"]


def test_restore_rejects_non_sqlite_file(auth_client, tmp_path):
    from backend.app import maintenance
    bad = tmp_path / "notadb.db"
    bad.write_text("plain text, definitely not sqlite")
    with pytest.raises(maintenance.MaintenanceError, match="SQLite"):
        maintenance.restore_database(bad, tmp_path / "clinic.db")


def test_restore_rejects_incompatible_schema_version(auth_client, tmp_path):
    from backend.app import maintenance
    backup = maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "oldversion.db")
    conn = sqlite3.connect(str(backup))
    with conn:
        conn.execute("UPDATE schema_version SET version = 42")
    conn.close()

    with pytest.raises(maintenance.MaintenanceError, match="отклонена"):
        maintenance.restore_database(backup, tmp_path / "clinic.db")


def test_rejected_restore_leaves_live_db_untouched(auth_client, tmp_path):
    from backend.app import maintenance
    db_path = tmp_path / "clinic.db"
    auth_client.post("/api/patients", json=make_patient_payload(name="Неприкосновенный"), headers=auth_client.csrf)

    bad = tmp_path / "corrupt.db"
    bad.write_text("garbage")
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.restore_database(bad, db_path)

    conn = sqlite3.connect(str(db_path))
    names = [row[0] for row in conn.execute("SELECT name FROM patients").fetchall()]
    conn.close()
    assert names == ["Неприкосновенный"]


def test_busy_timeout_is_set(auth_client):
    from backend.app import config, db
    conn = db.get_connection()
    timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert timeout == config.BUSY_TIMEOUT_MS


def test_foreign_keys_enabled_on_connection(auth_client):
    from backend.app import db
    conn = db.get_connection()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_indexes_exist(auth_client):
    from backend.app import db
    conn = db.get_connection()
    names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    for expected in ("idx_history_patient", "idx_events_history", "idx_patients_head", "idx_sessions_expires", "idx_audit_created"):
        assert expected in names, f"missing index {expected}"
