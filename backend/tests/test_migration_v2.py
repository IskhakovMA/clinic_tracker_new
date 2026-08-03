"""Миграция старой схемы на актуальную на фиктивной базе (реальные данные не используются).

Проверяется: сохранность пациентов и legacy-текста врача, появление
nullable-связи, повторный запуск, backup/restore и check-db после миграции,
совместимость старого JSON-формата импорта.
"""
import json
import sqlite3
from pathlib import Path

import pytest

from .conftest import DEV_USERNAME, create_account, login, make_patient_payload

# Схема v1 — как она выглядела до RBAC (упрощённо, но с теми же таблицами).
V1_SCHEMA = """
CREATE TABLE schema_version (version INTEGER NOT NULL);
CREATE TABLE app_login (id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE patients (id TEXT PRIMARY KEY, name TEXT NOT NULL, age INTEGER,
    priority TEXT NOT NULL, stage TEXT NOT NULL, phone TEXT, complaint TEXT,
    diagnosis TEXT, nosology TEXT, disease_stage TEXT, arrival TEXT NOT NULL,
    doctor TEXT, head TEXT, created_at TEXT NOT NULL, updated_at TEXT);
CREATE TABLE history_entries (id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, stage TEXT NOT NULL, entered_at TEXT NOT NULL,
    checklist_json TEXT, note TEXT, UNIQUE (patient_id, seq));
CREATE TABLE events (id TEXT PRIMARY KEY,
    history_entry_id TEXT NOT NULL REFERENCES history_entries(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, date TEXT, time TEXT, treatment_type TEXT, actual_date TEXT,
    actual_time TEXT, matched_plan INTEGER, no_plan INTEGER NOT NULL DEFAULT 0,
    set_at TEXT, UNIQUE (history_entry_id, seq));
CREATE TABLE future_plans (patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    template_index INTEGER NOT NULL, date TEXT, time TEXT, treatment_type TEXT,
    PRIMARY KEY (patient_id, template_index));
CREATE TABLE audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
    event_type TEXT NOT NULL, actor TEXT, patient_id TEXT, result TEXT NOT NULL,
    request_id TEXT, detail TEXT);
"""


def build_v1_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(V1_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.execute(
        "INSERT INTO app_login (id, password_hash, updated_at) VALUES (1, '$argon2id$fake', '2026-01-01')"
    )
    conn.execute(
        """INSERT INTO patients (id, name, age, priority, stage, arrival, doctor, head, created_at)
           VALUES ('legacy1', 'Стародавнев Пациент', 60, 'normal', 'exam',
                   '2026-01-01T00:00:00+00:00', 'Легасиев Врач Текстович', 'Старший Зав', '2026-01-01')"""
    )
    conn.execute(
        "INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, note) "
        "VALUES ('h1', 'legacy1', 0, 'exam', '2026-01-01T00:00:00+00:00', 'заметка')"
    )
    conn.execute(
        "INSERT INTO sessions (token_hash, csrf_token, created_at, last_seen_at, expires_at) "
        "VALUES ('oldhash', 'oldcsrf', '2026-01-01', '2026-01-01', '2099-01-01')"
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def migrated(tmp_path, monkeypatch):
    """v1-база, прошедшая init_db (миграцию) при старте приложения."""
    import sys
    build_v1_db(tmp_path / "clinic.db")
    monkeypatch.setenv("CLINIC_DATA_DIR", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import security
    security._failed_attempts.clear()
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        yield c, tmp_path


def test_migration_preserves_patients_and_legacy_doctor(migrated):
    client, tmp_path = migrated
    from backend.app import db
    conn = db.get_connection()
    # версия поднята
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    # медицинские данные не потеряны
    row = conn.execute("SELECT * FROM patients WHERE id = 'legacy1'").fetchone()
    assert row["name"] == "Стародавнев Пациент"
    assert row["doctor"] == "Легасиев Врач Текстович"  # legacy-текст сохранён
    assert row["treating_doctor_user_id"] is None      # nullable-связь появилась пустой
    assert conn.execute("SELECT COUNT(*) FROM history_entries").fetchone()[0] == 1
    # сессии общей модели входа удалены
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    # RBAC-таблицы созданы и роли посеяны
    roles = {r["id"] for r in conn.execute("SELECT id FROM roles").fetchall()}
    assert roles == {"developer", "deputy_chief_oncology", "caop_head", "department_head", "doctor"}


def test_migration_is_idempotent(migrated):
    client, tmp_path = migrated
    from backend.app import db
    conn = db.get_connection()
    # повторный запуск миграции ничего не ломает
    db.init_db(conn)
    db.init_db(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 1


def test_unresolved_patient_visibility_after_migration(migrated):
    client, tmp_path = migrated
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    # Разработчик видит legacy-пациента и очередь сопоставления
    assert {p["name"] for p in client.get("/api/patients").json()} == {"Стародавнев Пациент"}
    unresolved = client.get("/api/patients/unresolved").json()["patients"]
    assert unresolved == [{"id": "legacy1", "name": "Стародавнев Пациент",
                           "legacyDoctor": "Легасиев Врач Текстович"}]

    # глобальная read-only роль видит
    create_account("ro.user", "deputy_chief_oncology", "Прочтёнов Только")
    login(client, "ro.user")
    assert len(client.get("/api/patients").json()) == 1

    # обычный врач и заведующий — не видят, пока врач не сопоставлен
    from .conftest import create_department
    dept = create_department("Постмиграционное")
    create_account("mig.doc", "doctor", "Мигратов Врач", department_ids=[dept])
    create_account("mig.head", "department_head", "Мигратов Зав", department_ids=[dept])
    login(client, "mig.doc")
    assert client.get("/api/patients").json() == []
    assert client.get("/api/patients/legacy1").status_code == 404
    login(client, "mig.head")
    assert client.get("/api/patients").json() == []


def test_foreign_keys_hold_after_migration(migrated):
    client, tmp_path = migrated
    from backend.app import db
    conn = db.get_connection()
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # нельзя привязать пациента к несуществующему пользователю
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE patients SET treating_doctor_user_id = 'ghost' WHERE id = 'legacy1'")


def test_check_db_ok_after_migration(migrated):
    client, tmp_path = migrated
    from backend.app import maintenance
    report = maintenance.check_database(tmp_path / "clinic.db")
    assert report["ok"] is True, report["problems"]
    assert report["schema_version"] == 3
    assert report["missing_tables"] == []


def test_backup_restore_after_migration(migrated):
    client, tmp_path = migrated
    from backend.app import maintenance
    dest = maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "after_migration.db")
    report = maintenance.validate_backup(dest)
    assert report["ok"] is True
    assert report["patient_count"] == 1


def test_v1_export_format_still_imports(migrated):
    """Старый экспорт (schemaVersion: 1, без treatingDoctorUserId) принимается."""
    client, tmp_path = migrated
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    old_doc = json.dumps({
        "schemaVersion": 1,
        "patients": [make_patient_payload(name="Старофайлов Импорт")],
    }, ensure_ascii=False)
    r = client.post("/api/import/commit", json={"raw": old_doc, "mode": "add"},
                    headers=client.csrf)
    assert r.status_code == 200
    assert r.json()["added"] == 1
    # запись попала в очередь несопоставленных, а не потерялась
    names = {p["name"] for p in client.get("/api/patients/unresolved").json()["patients"]}
    assert "Старофайлов Импорт" in names
