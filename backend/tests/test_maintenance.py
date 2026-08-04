import sqlite3
import stat
import subprocess
import sys

import pytest

from .conftest import PROJECT_ROOT, make_patient_payload

TEST_BACKUP_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # 32 zero bytes, base64url without padding.


@pytest.fixture(autouse=True)
def backup_key(monkeypatch):
    monkeypatch.setenv("CLINIC_BACKUP_KEY", TEST_BACKUP_KEY)
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "test-key")
    monkeypatch.delenv("CLINIC_BACKUP_KEY_FILE", raising=False)
    monkeypatch.delenv("CLINIC_BACKUP_DECRYPT_KEYS", raising=False)


def test_check_db_passes_on_live_database(auth_client, tmp_path):
    from backend.app import maintenance
    report = maintenance.check_database(tmp_path / "clinic.db")
    assert report["ok"] is True
    assert report["integrity_check"] == ["ok"]
    assert report["foreign_key_violations"] == 0
    assert report["missing_tables"] == []
    assert report["schema_version"] == 3


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
    assert not maintenance.looks_like_sqlite(dest)
    assert maintenance.looks_like_encrypted_backup(dest)

    report = maintenance.verify_backup(dest)
    assert report["patient_count"] == 1
    assert report["authenticated"] is True
    assert report["key_id"] == "test-key"


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
    backup = maintenance.backup_database(db_path, tmp_path / "snapshot.cbk")

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
    with pytest.raises(maintenance.MaintenanceError, match="encrypted backup"):
        maintenance.restore_database(bad, tmp_path / "clinic.db")


def test_restore_rejects_incompatible_schema_version(auth_client, tmp_path):
    from backend.app import maintenance
    plaintext = tmp_path / "oldversion.sqlite"
    maintenance._sqlite_backup_to_file(tmp_path / "clinic.db", plaintext)
    conn = sqlite3.connect(str(plaintext))
    with conn:
        conn.execute("UPDATE schema_version SET version = 42")
    conn.close()
    backup = _encrypt_plaintext_for_test(maintenance, plaintext, tmp_path / "oldversion.cbk")

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


def _read_container(path):
    raw = path.read_bytes()
    header_len = int.from_bytes(raw[4:8], "big")
    header_start = 8
    header_end = header_start + header_len
    return raw, header_start, header_end


def _replace_header(path, mutator):
    import json
    raw, header_start, header_end = _read_container(path)
    header = json.loads(raw[header_start:header_end].decode("utf-8"))
    mutator(header)
    new_header = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw[:4] + len(new_header).to_bytes(4, "big") + new_header + raw[header_end:])


def _encrypt_plaintext_for_test(maintenance, plaintext, dest):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = maintenance._load_active_key()
    nonce = b"\x01" * maintenance.NONCE_BYTES
    plaintext_bytes = plaintext.read_bytes()
    header = maintenance._build_header(key=key, nonce=nonce, ciphertext_len=len(plaintext_bytes) + 16)
    raw_header = maintenance._header_bytes(header)
    ciphertext = AESGCM(key.key).encrypt(nonce, plaintext_bytes, maintenance._aad(raw_header))
    maintenance._write_container(dest, raw_header, ciphertext)
    return dest


def test_same_database_produces_different_ciphertext(auth_client, tmp_path):
    from backend.app import maintenance
    first = maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "one.cbk")
    second = maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "two.cbk")
    assert first.read_bytes() != second.read_bytes()


def test_wrong_key_unknown_key_tamper_and_truncation_rejected(auth_client, tmp_path, monkeypatch):
    from backend.app import maintenance
    backup = maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "snapshot.cbk")

    monkeypatch.setenv("CLINIC_BACKUP_KEY", "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE")
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "test-key")
    with pytest.raises(maintenance.MaintenanceError, match="подлинности"):
        maintenance.verify_backup(backup)

    monkeypatch.setenv("CLINIC_BACKUP_KEY", TEST_BACKUP_KEY)
    unknown = tmp_path / "unknown.cbk"
    unknown.write_bytes(backup.read_bytes())
    _replace_header(unknown, lambda h: h.__setitem__("key_id", "retired-key"))
    with pytest.raises(maintenance.MaintenanceError, match="key_id"):
        maintenance.verify_backup(unknown)

    tampered = tmp_path / "tampered.cbk"
    data = bytearray(backup.read_bytes())
    data[-20] ^= 1
    tampered.write_bytes(data)
    with pytest.raises(maintenance.MaintenanceError, match="подлинности"):
        maintenance.verify_backup(tampered)

    truncated = tmp_path / "truncated.cbk"
    truncated.write_bytes(backup.read_bytes()[:-10])
    with pytest.raises(maintenance.MaintenanceError, match="усечён|повреждён"):
        maintenance.verify_backup(truncated)


def test_modified_authenticated_header_and_unsupported_values_rejected(auth_client, tmp_path):
    from backend.app import maintenance
    backup = maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "snapshot.cbk")

    modified = tmp_path / "modified-header.cbk"
    modified.write_bytes(backup.read_bytes())
    _replace_header(modified, lambda h: h.__setitem__("created_at", "2099-01-01T00:00:00+00:00"))
    with pytest.raises(maintenance.MaintenanceError, match="подлинности"):
        maintenance.verify_backup(modified)

    unsupported_version = tmp_path / "future.cbk"
    unsupported_version.write_bytes(backup.read_bytes())
    _replace_header(unsupported_version, lambda h: h.__setitem__("version", 99))
    with pytest.raises(maintenance.MaintenanceError, match="версия"):
        maintenance.verify_backup(unsupported_version)

    unsupported_algorithm = tmp_path / "bad-alg.cbk"
    unsupported_algorithm.write_bytes(backup.read_bytes())
    _replace_header(unsupported_algorithm, lambda h: h.__setitem__("algorithm", "AES-256-CBC"))
    with pytest.raises(maintenance.MaintenanceError, match="алгоритм"):
        maintenance.verify_backup(unsupported_algorithm)


def test_backup_fails_closed_without_key(auth_client, tmp_path, monkeypatch):
    from backend.app import maintenance
    monkeypatch.delenv("CLINIC_BACKUP_KEY", raising=False)
    monkeypatch.delenv("CLINIC_BACKUP_KEY_FILE", raising=False)
    with pytest.raises(maintenance.MaintenanceError, match="ключ"):
        maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "no-key.cbk")
    assert not (tmp_path / "no-key.cbk").exists()


def test_key_not_present_in_backup_error_or_audit(auth_client, tmp_path):
    from backend.app import db, maintenance
    backup = maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "snapshot.cbk")
    assert TEST_BACKUP_KEY.encode("utf-8") not in backup.read_bytes()
    try:
        maintenance.verify_backup(tmp_path / "missing.cbk")
    except maintenance.MaintenanceError as exc:
        assert TEST_BACKUP_KEY not in str(exc)
    rows = db.get_connection().execute("SELECT detail FROM audit_events").fetchall()
    assert all(TEST_BACKUP_KEY not in (row["detail"] or "") for row in rows)


def test_backup_permissions_and_cleanup_after_success(auth_client, tmp_path):
    from backend.app import maintenance
    dest = maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "secure.cbk")
    assert stat.S_IMODE(dest.stat().st_mode) & 0o077 == 0
    assert not list(tmp_path.glob("clinic-backup-*"))
    assert not list(tmp_path.glob(".*.tmp"))


def test_cleanup_after_encryption_failure(auth_client, tmp_path, monkeypatch):
    from backend.app import maintenance
    class FailingAESGCM:
        def __init__(self, _key):
            pass
        def encrypt(self, *_args):
            raise ValueError("forced")
    monkeypatch.setattr(maintenance, "AESGCM", FailingAESGCM)
    with pytest.raises(ValueError):
        maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "failed.cbk")
    assert not (tmp_path / "failed.cbk").exists()
    assert not list(tmp_path.glob("clinic-backup-*"))
    assert not list(tmp_path.glob(".*.tmp"))


def test_symlink_path_traversal_static_and_overwrite_rejected(auth_client, tmp_path):
    from backend.app import maintenance
    real = tmp_path / "real.cbk"
    symlink = tmp_path / "link.cbk"
    symlink.symlink_to(real)
    with pytest.raises(maintenance.MaintenanceError, match="symlink"):
        maintenance.backup_database(tmp_path / "clinic.db", symlink)
    with pytest.raises(maintenance.MaintenanceError, match="Небезопасный путь"):
        maintenance.backup_database(tmp_path / "clinic.db", tmp_path / ".." / "escape.cbk")
    with pytest.raises(maintenance.MaintenanceError, match="static"):
        maintenance.backup_database(tmp_path / "clinic.db", PROJECT_ROOT / "static" / "bad.cbk")
    with pytest.raises(maintenance.MaintenanceError, match="Git repository"):
        maintenance.backup_database(tmp_path / "clinic.db", PROJECT_ROOT / "tracked-output.cbk")
    dest = maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "exists.cbk")
    with pytest.raises(maintenance.MaintenanceError, match="существует"):
        maintenance.backup_database(tmp_path / "clinic.db", dest)


def test_symlink_input_rejected(auth_client, tmp_path):
    from backend.app import maintenance
    backup = maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "snapshot.cbk")
    link = tmp_path / "input-link.cbk"
    link.symlink_to(backup)
    with pytest.raises(maintenance.MaintenanceError, match="symlink"):
        maintenance.verify_backup(link)


def test_failed_restore_cleans_temp_and_preserves_original(auth_client, tmp_path):
    from backend.app import maintenance
    db_path = tmp_path / "clinic.db"
    backup = maintenance.backup_database(db_path, tmp_path / "snapshot.cbk")
    _replace_header(backup, lambda h: h.__setitem__("schema_version", 99))
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.restore_database(backup, db_path)
    assert maintenance.check_database(db_path)["ok"] is True
    assert not list(tmp_path.glob("clinic-restore-*"))
    assert not list(tmp_path.glob("*.restore-tmp"))


def test_encrypted_non_sqlite_and_fk_violation_rejected(auth_client, tmp_path):
    from backend.app import maintenance
    bad_plain = tmp_path / "bad.txt"
    bad_plain.write_text("not sqlite", encoding="utf-8")
    encrypted_bad = _encrypt_plaintext_for_test(maintenance, bad_plain, tmp_path / "bad.cbk")
    with pytest.raises(maintenance.MaintenanceError, match="SQLite"):
        maintenance.verify_backup(encrypted_bad)

    fk_db = tmp_path / "fk.sqlite"
    maintenance._sqlite_backup_to_file(tmp_path / "clinic.db", fk_db)
    conn = sqlite3.connect(str(fk_db))
    conn.execute("PRAGMA foreign_keys = OFF")
    with conn:
        conn.execute(
            "INSERT INTO events (id, history_entry_id, seq, date, treatment_type) "
            "VALUES ('evt-orphan', 'missing-history', 1, '2026-01-01', 'exam')"
        )
    conn.close()
    encrypted_fk = _encrypt_plaintext_for_test(maintenance, fk_db, tmp_path / "fk.cbk")
    with pytest.raises(maintenance.MaintenanceError, match="foreign key"):
        maintenance.verify_backup(encrypted_fk)


def test_key_file_source_requires_owner_only_permissions(auth_client, tmp_path, monkeypatch):
    from backend.app import maintenance
    key_file = tmp_path / "backup.key"
    key_file.write_text(TEST_BACKUP_KEY, encoding="utf-8")
    key_file.chmod(0o644)
    monkeypatch.delenv("CLINIC_BACKUP_KEY", raising=False)
    monkeypatch.setenv("CLINIC_BACKUP_KEY_FILE", str(key_file))
    with pytest.raises(maintenance.MaintenanceError, match="только владельцу"):
        maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "bad-perms.cbk")
    key_file.chmod(0o600)
    assert maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "key-file.cbk").exists()


def test_successful_restore_creates_encrypted_recovery_and_removes_wal_shm(auth_client, tmp_path):
    from backend.app import db, maintenance
    db_path = tmp_path / "clinic.db"
    auth_client.post("/api/patients", json=make_patient_payload(name="До"), headers=auth_client.csrf)
    backup = maintenance.backup_database(db_path, tmp_path / "snapshot.cbk")
    auth_client.post("/api/patients", json=make_patient_payload(name="После"), headers=auth_client.csrf)
    db.reset_singleton_for_tests()
    result = maintenance.restore_database(backup, db_path)
    assert result["pre_restore_backup"].endswith(".cbk")
    assert maintenance.looks_like_encrypted_backup(result["pre_restore_backup"])
    assert not (tmp_path / "clinic.db-wal").exists()
    assert not (tmp_path / "clinic.db-shm").exists()


def test_cli_create_inspect_verify_and_restore(tmp_path):
    key = TEST_BACKUP_KEY
    env = {
        **os_environ_without_clinic(),
        "CLINIC_DATA_DIR": str(tmp_path),
        "CLINIC_BACKUP_KEY": key,
        "CLINIC_BACKUP_KEY_ID": "cli-key",
        "PYTHONPATH": str(PROJECT_ROOT),
    }
    check = subprocess.run(
        [sys.executable, "-m", "backend.cli", "check-db"],
        cwd=PROJECT_ROOT, env=env, text=True, capture_output=True,
    )
    assert check.returncode == 0
    backup = tmp_path / "cli.cbk"
    create = subprocess.run(
        [sys.executable, "-m", "backend.cli", "backup", "--dest", str(backup)],
        cwd=PROJECT_ROOT, env=env, text=True, capture_output=True,
    )
    assert create.returncode == 0, create.stderr
    assert "encrypted v1" in create.stdout
    inspect = subprocess.run(
        [sys.executable, "-m", "backend.cli", "backup-inspect", str(backup)],
        cwd=PROJECT_ROOT, env=env, text=True, capture_output=True,
    )
    assert inspect.returncode == 0
    assert "cli-key" in inspect.stdout
    verify = subprocess.run(
        [sys.executable, "-m", "backend.cli", "backup-verify", str(backup)],
        cwd=PROJECT_ROOT, env=env, text=True, capture_output=True,
    )
    assert verify.returncode == 0
    restore = subprocess.run(
        [sys.executable, "-m", "backend.cli", "restore", str(backup), "--yes"],
        cwd=PROJECT_ROOT, env=env, text=True, capture_output=True,
    )
    assert restore.returncode == 0, restore.stderr
    assert key not in create.stdout + create.stderr + inspect.stdout + verify.stdout + restore.stdout


def os_environ_without_clinic():
    import os
    return {k: v for k, v in os.environ.items() if not k.startswith("CLINIC_")}


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
    for expected in (
        "idx_history_patient",
        "idx_events_history",
        "idx_patients_head",
        "idx_sessions_expires",
        "idx_audit_created",
        "idx_notifications_status_due",
        "idx_notifications_patient",
        "idx_notifications_idempotency_key",
    ):
        assert expected in names, f"missing index {expected}"
