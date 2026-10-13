"""Restoring encrypted backups taken by older supported schema versions.

The decrypted snapshot is migrated inside the private temp directory and
fully checked before the live database is replaced; a refusal at any step
leaves the live database byte-for-byte unchanged and no plaintext behind.
"""
import hashlib
import sqlite3

import pytest

from .conftest import PROJECT_ROOT, current_schema_version, make_patient_payload

TEST_BACKUP_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
V9_FIXTURE = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "stable_v9_synthetic.sql"
MAX_V17_FIXTURE = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "max_v17_synthetic.sql"
V9_NAMES = {"Вымышленов Первый Пациентович", "Вымышленова Вторая Пациентовна", "Вымышленов Третий Пациентович"}


@pytest.fixture(autouse=True)
def backup_key(monkeypatch):
    monkeypatch.setenv("CLINIC_BACKUP_KEY", TEST_BACKUP_KEY)
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "test-key")
    monkeypatch.delenv("CLINIC_BACKUP_KEY_FILE", raising=False)
    monkeypatch.delenv("CLINIC_BACKUP_DECRYPT_KEYS", raising=False)


def _sqlite_from_fixture(path, *, edit=None, fixture=V9_FIXTURE):
    conn = sqlite3.connect(str(path))
    conn.executescript(fixture.read_text(encoding="utf-8"))
    if edit:
        conn.execute("PRAGMA foreign_keys = OFF")
        edit(conn)
        conn.commit()
    conn.close()
    return path


def _encrypt(plaintext, dest, header_schema_version):
    """Same container as maintenance.backup_database, with the header claiming
    the schema version the snapshot was taken at (as old app code writes it)."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from backend.app import maintenance

    key = maintenance._load_active_key()
    nonce = b"\x02" * maintenance.NONCE_BYTES
    data = plaintext.read_bytes()
    header = maintenance._build_header(key=key, nonce=nonce, ciphertext_len=len(data) + 16)
    header["schema_version"] = header_schema_version
    raw = maintenance._header_bytes(header)
    maintenance._write_container(dest, raw, AESGCM(key.key).encrypt(nonce, data, maintenance._aad(raw)))
    return dest


def _v9_backup(tmp_path, name="stable-v9.cbk", *, edit=None, header=9, fixture=V9_FIXTURE):
    work = tmp_path / "fixture-work"
    work.mkdir(exist_ok=True)
    plaintext = _sqlite_from_fixture(work / f"{name}.sqlite", edit=edit, fixture=fixture)
    backup = _encrypt(plaintext, tmp_path / name, header)
    plaintext.unlink()
    return backup


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _live(auth_client):
    from backend.app import config, db
    auth_client.post("/api/patients", json=make_patient_payload(name="Живая База Текущая"), headers=auth_client.csrf)
    conn = db.get_connection()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.reset_singleton_for_tests()
    return config.DB_PATH


def _plaintext_leftovers(directory):
    from backend.app import maintenance
    return [
        p for p in directory.rglob("*")
        if p.is_file() and p.name != "clinic.db" and maintenance.looks_like_sqlite(p)
    ]


def test_fixture_is_a_real_v9_database(tmp_path):
    path = _sqlite_from_fixture(tmp_path / "v9.sqlite")
    conn = sqlite3.connect(str(path))
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 9
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "oncology_consilium_appointments" not in tables
    assert {r[0] for r in conn.execute("SELECT name FROM patients")} == V9_NAMES
    conn.close()


def test_verify_accepts_supported_v9_backup_without_touching_live_db(auth_client, tmp_path):
    from backend.app import maintenance
    live = _live(auth_client)
    before = _digest(live)
    report = maintenance.validate_backup(_v9_backup(tmp_path))
    assert report["migrated_from_schema_version"] == 9
    assert report["schema_version"] == 9
    assert report["patient_count"] == 3
    assert _digest(live) == before
    assert _plaintext_leftovers(tmp_path) == []


def test_restore_migrates_v9_backup_and_replaces_live_db(auth_client, tmp_path):
    from backend.app import maintenance
    live = _live(auth_client)
    result = maintenance.restore_database(_v9_backup(tmp_path), live, confirm_offline=True)
    assert result["migrated_from_schema_version"] == 9
    assert result["patient_count"] == 3

    report = maintenance.check_database(live)
    assert report["ok"], report["problems"]
    assert report["schema_version"] == current_schema_version()
    conn = sqlite3.connect(str(live))
    assert {r[0] for r in conn.execute("SELECT name FROM patients")} == V9_NAMES
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
    assert {"oncology_consilium_appointments", "consilium_schedule_slots", "trg_consilium_signed_immutable"} <= tables
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    conn.close()

    pre = result["pre_restore_backup"]
    assert pre and pre.endswith(".cbk")
    assert maintenance.looks_like_encrypted_backup(__import__("pathlib").Path(pre))
    assert _plaintext_leftovers(tmp_path) == []


@pytest.mark.parametrize("case", ["migration_fails", "fk_violation", "too_old", "too_new", "header_mismatch"])
def test_refused_old_backup_leaves_live_db_unchanged(auth_client, tmp_path, monkeypatch, case):
    from backend.app import config, maintenance
    live = _live(auth_client)
    before = _digest(live)
    backups_before = sorted(config.BACKUP_DIR.glob("*")) if config.BACKUP_DIR.exists() else []

    if case == "migration_fails":
        backup = _v9_backup(tmp_path)

        def broken_init(conn):
            raise sqlite3.OperationalError("simulated migration failure")
        monkeypatch.setattr(maintenance, "init_db", broken_init)
        expected = "не удалось обновить"
    elif case == "fk_violation":
        backup = _v9_backup(tmp_path, edit=lambda c: c.execute(
            "UPDATE history_entries SET patient_id = 'missing-patient' WHERE rowid = 1"))
        expected = "foreign key"
    elif case == "too_old":
        backup = _v9_backup(tmp_path, edit=lambda c: c.execute("UPDATE schema_version SET version = 8"), header=8)
        expected = "не поддерживается"
    elif case == "too_new":
        newer = current_schema_version() + 1
        backup = _v9_backup(tmp_path, edit=lambda c: c.execute(f"UPDATE schema_version SET version = {newer}"), header=newer)
        expected = "отклонена"
    else:
        backup = _v9_backup(tmp_path, header=current_schema_version())
        expected = "не совпадает"

    with pytest.raises(maintenance.MaintenanceError, match=expected):
        maintenance.restore_database(backup, live, confirm_offline=True)
    assert _digest(live) == before
    after = sorted(config.BACKUP_DIR.glob("*")) if config.BACKUP_DIR.exists() else []
    assert after == backups_before  # no pre-restore copy for a refused backup
    assert _plaintext_leftovers(tmp_path) == []


# --- проверенные сборки, а не номер схемы ---------------------------------

PORTAL_TABLE = """CREATE TABLE patient_portal_access (
    patient_id TEXT PRIMARY KEY REFERENCES patients(id) ON DELETE CASCADE,
    email_normalized TEXT NOT NULL UNIQUE COLLATE NOCASE, status TEXT NOT NULL,
    enrollment_state TEXT NOT NULL DEFAULT 'none', linked_at TEXT, linked_by_user_id TEXT,
    activated_at TEXT, last_login_at TEXT, revoked_at TEXT, revoked_by_user_id TEXT, updated_at TEXT)"""


def test_restore_migrates_verified_max_v17_backup_keeping_signed_decision(auth_client, tmp_path):
    from backend.app import maintenance
    live = _live(auth_client)
    backup = _v9_backup(tmp_path, "max-v17.cbk", header=17, fixture=MAX_V17_FIXTURE)
    report = maintenance.validate_backup(backup)
    assert (report["source_build"], report["migrated_from_schema_version"]) == ("clinic_tracker_new_Max", 17)
    result = maintenance.restore_database(backup, live, confirm_offline=True)
    assert result["migrated_from_schema_version"] == 17

    conn = sqlite3.connect(str(live))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM oncology_consilium_appointments ORDER BY scheduled_time")]
    assert [r["status"] for r in rows] == ["signed", "recorded"]
    assert rows[0]["treatment_type"] == "surgery"
    assert rows[0]["signed_treating_doctor_name"] == "Фикстурова Врач Тестовна"
    assert conn.execute("SELECT COUNT(*) FROM consilium_chair_favorites").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="consilium_signed_immutable"):
        conn.execute("UPDATE oncology_consilium_appointments SET status = 'recorded' WHERE id = ?", (rows[0]["id"],))
    conn.close()
    assert maintenance.check_database(live)["ok"]


def test_current_and_stable_backups_report_their_build(auth_client, tmp_path):
    from backend.app import config, maintenance
    _live(auth_client)
    assert maintenance.validate_backup(_v9_backup(tmp_path))["source_build"] == "v0.9.0-stable"
    current = maintenance.backup_database(config.DB_PATH, tmp_path / "current.cbk")
    assert maintenance.validate_backup(current)["source_build"] == "current"


@pytest.mark.parametrize("case", ["portal_v10", "max_v17_plus_portal", "v9_with_consilium", "max_v12_unverified"])
def test_unverified_builds_with_plausible_numbers_are_refused(auth_client, tmp_path, case):
    from backend.app import maintenance
    live = _live(auth_client)
    before = _digest(live)
    if case == "portal_v10":
        backup = _v9_backup(tmp_path, edit=lambda c: (c.execute(PORTAL_TABLE),
                            c.execute("UPDATE schema_version SET version = 10")), header=10)
    elif case == "max_v17_plus_portal":
        backup = _v9_backup(tmp_path, edit=lambda c: c.execute(PORTAL_TABLE), header=17, fixture=MAX_V17_FIXTURE)
    elif case == "v9_with_consilium":
        backup = _v9_backup(tmp_path, edit=lambda c: c.execute(
            "CREATE TABLE consilium_schedule_slots (id TEXT PRIMARY KEY)"))
    else:
        from .test_schema_intermediate_versions import _build
        work = tmp_path / "fixture-work"
        work.mkdir(exist_ok=True)
        plaintext = _build(work / "max-v12.sqlite", 12)
        backup = _encrypt(plaintext, tmp_path / "max-v12.cbk", 12)
        plaintext.unlink()
    with pytest.raises(maintenance.MaintenanceError, match="не совпадает ни с одной проверенной сборкой"):
        maintenance.restore_database(backup, live, confirm_offline=True)
    assert _digest(live) == before
    assert _plaintext_leftovers(tmp_path) == []
