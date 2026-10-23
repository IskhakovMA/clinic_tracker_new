"""WP-B: safe operation of the database (AUD-02, AUD-04, AUD-16, AUD-19, AUD-20).

Synthetic data only. Scenarios (task list, section 7):
 1. a v20 DB stays v20 (byte for byte) after check-db / check-db --no-migrate;
 2. diagnostics change nothing and never migrate, also on error;
 3. restore while the server runs is refused before any change;
 4. restore after a proper stop succeeds;
 5. two restores never run at the same time;
 6. a failed restore keeps the usable data and the backup;
 7. a damaged target follows the documented emergency procedure;
 8. an explicit --key-id is stored and used;
 9. historical backups and keys stay usable;
10. permissions, WAL/SHM and exceptions: no regressions.
"""
import base64
import hashlib
import json
import os
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .conftest import PROJECT_ROOT, app_stopped, current_schema_version, make_patient_payload

TEST_BACKUP_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"  # 32 zero bytes (synthetic test key)
V20_FIXTURE = PROJECT_ROOT / "backend" / "tests" / "fixtures" / "wp22a_v20_synthetic.sql"


@pytest.fixture(autouse=True)
def backup_key(monkeypatch):
    monkeypatch.setenv("CLINIC_BACKUP_KEY", TEST_BACKUP_KEY)
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "test-key")
    monkeypatch.delenv("CLINIC_BACKUP_KEY_FILE", raising=False)
    monkeypatch.delenv("CLINIC_BACKUP_DECRYPT_KEYS", raising=False)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Isolated data directory without a running application."""
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    monkeypatch.setenv("CLINIC_DATA_DIR", str(data))
    monkeypatch.setenv("CLINIC_AI_MODE", "disabled")
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]
    return data


def _digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _files(directory: Path) -> dict:
    """Content of the DB file and its sidecars (missing ones as None)."""
    return {name: _digest(directory / name) for name in ("clinic.db", "clinic.db-wal", "clinic.db-shm")}


def _v20_db(path: Path) -> Path:
    conn = sqlite3.connect(str(path))
    conn.executescript(V20_FIXTURE.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return path


def _schema_version(path: Path):
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        return conn.execute("SELECT version FROM schema_version").fetchone()[0]
    finally:
        conn.close()


def _cli(data: Path, *args, env_extra=None, timeout=120):
    env = dict(os.environ, CLINIC_DATA_DIR=str(data), PYTHONDONTWRITEBYTECODE="1", CLINIC_AI_MODE="disabled")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "backend.cli", *args], cwd=str(PROJECT_ROOT), env=env,
                          capture_output=True, text=True, timeout=timeout)


def _current_db(data: Path, patients=("Синтетик Первый",)):
    """A current-schema DB created the way the application creates it."""
    from backend.app import db
    conn = db.connect_live(data / "clinic.db")
    try:
        for i, name in enumerate(patients):
            conn.execute(
                "INSERT INTO patients (id, name, priority, stage, arrival, created_at, updated_at) "
                "VALUES (?, ?, 'normal', 'exam', ?, ?, ?)",
                (f"p-{i}", name, db.now_iso(), db.now_iso(), db.now_iso()),
            )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return data / "clinic.db"


def _patient_names(path: Path):
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return sorted(r[0] for r in conn.execute("SELECT name FROM patients"))
    finally:
        conn.close()


def _leftovers(data: Path):
    return sorted(p.name for p in data.iterdir()
                  if p.name.endswith(".restore-tmp") or p.name.startswith(("clinic-restore-", "clinic-check-",
                                                                           "clinic-migrate-", "clinic-backup-")))


# --- 1, 2: check-db is a diagnosis only (AUD-02) ---------------------------

@pytest.mark.parametrize("args", [["check-db"], ["check-db", "--no-migrate"]])
def test_check_db_keeps_v20_database_byte_identical(data_dir, args):
    live = _v20_db(data_dir / "clinic.db")
    before = _files(data_dir)
    result = _cli(data_dir, *args)
    assert result.returncode == 1
    assert "migrate-db" in result.stderr
    assert "База не изменена" in result.stderr
    assert "копия файла" in result.stdout
    assert _files(data_dir) == before                     # main file, no -wal, no -shm created
    assert before["clinic.db-wal"] is None and before["clinic.db-shm"] is None
    assert _schema_version(live) == 20


def test_check_db_never_writes_audit_or_migrates_on_failure(data_dir):
    """A failing check (FK violation) writes nothing: no audit event, no schema change."""
    live = _current_db(data_dir)
    conn = sqlite3.connect(str(live))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO history_entries (id, patient_id, seq, stage, entered_at) VALUES ('h1', 'missing', 1, 'exam', 't')")
    conn.commit()
    audit_before = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    conn.close()
    before = _files(data_dir)
    result = _cli(data_dir, "check-db")
    assert result.returncode == 1
    assert "foreign key" in result.stderr
    assert _files(data_dir) == before
    conn = sqlite3.connect(f"{live.as_uri()}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == audit_before
    conn.close()


def test_check_db_missing_database_creates_nothing(tmp_path):
    missing = tmp_path / "no-such-dir"
    result = _cli(missing, "check-db")
    assert result.returncode == 1
    assert "не найдена" in result.stderr
    assert not missing.exists()
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    result = _cli(empty_dir, "check-db")
    assert result.returncode == 1
    assert list(empty_dir.iterdir()) == []


@pytest.mark.parametrize("content", [b"plain text, not sqlite" * 50, bytes(range(256)) * 32, b""],
                         ids=["text", "binary", "empty"])
def test_check_db_reports_non_sqlite_and_empty_without_change(data_dir, content):
    (data_dir / "clinic.db").write_bytes(content)
    before = _files(data_dir)
    result = _cli(data_dir, "check-db")
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "Аварийное восстановление" in result.stderr
    assert _files(data_dir) == before


def test_check_db_detects_corruption_inside_valid_header(data_dir):
    live = _current_db(data_dir, patients=[f"Синтетик {i}" for i in range(200)])
    raw = bytearray(live.read_bytes())
    page = 4096
    raw[page * 3: page * 3 + 512] = os.urandom(512)       # damage a b-tree page, keep the header
    live.write_bytes(bytes(raw))
    before = _files(data_dir)
    result = _cli(data_dir, "check-db")
    assert result.returncode == 1
    assert _files(data_dir) == before


def test_check_db_while_app_runs_reads_live_file_read_only(auth_client, tmp_path):
    from backend.app import config, maintenance
    auth_client.post("/api/patients", json=make_patient_payload(name="Живой Пациент"), headers=auth_client.csrf)
    live = config.DB_PATH
    before = (_digest(live), _digest(live.with_name("clinic.db-wal")))
    report = maintenance.diagnose_database(live)
    assert report["method"] == "live-read-only"
    assert report["ok"] is True
    assert (_digest(live), _digest(live.with_name("clinic.db-wal"))) == before


def test_app_and_cli_refuse_older_schema_without_change(data_dir):
    live = _v20_db(data_dir / "clinic.db")
    before = _files(data_dir)
    from backend.app import db
    with pytest.raises(db.SchemaUpgradeRequiredError, match="migrate-db"):
        db.get_connection()
    for args in (["audit-tail"], ["purge-sessions"], ["backup"]):
        result = _cli(data_dir, *args)
        assert result.returncode == 1, args
    assert _files(data_dir) == before
    assert _schema_version(live) == 20

    from fastapi.testclient import TestClient
    from backend.app.main import app
    with pytest.raises(db.SchemaVersionError):
        with TestClient(app):
            pass
    assert _files(data_dir) == before


def test_migrate_db_is_explicit_and_backed_up(data_dir):
    from backend.app import maintenance
    live = _v20_db(data_dir / "clinic.db")
    before = _files(data_dir)
    assert _cli(data_dir, "migrate-db").returncode == 1                   # no --confirm-offline
    assert _files(data_dir) == before

    result = _cli(data_dir, "migrate-db", "--confirm-offline")
    assert result.returncode == 0, result.stderr
    assert "v20 → v" in result.stdout
    assert _schema_version(live) == current_schema_version()
    backups = sorted((data_dir / "backups").glob("pre_migrate_v20_*.cbk"))
    assert len(backups) == 1
    assert maintenance.inspect_backup(backups[0])["schema_version"] == 20
    assert maintenance.validate_backup(backups[0])["migrated_from_schema_version"] == 20
    assert _cli(data_dir, "check-db").returncode == 0
    again = _cli(data_dir, "migrate-db", "--confirm-offline")
    assert again.returncode == 0 and "уже текущая" in again.stdout
    assert _leftovers(data_dir) == []


def test_migrate_db_refuses_damaged_database_and_running_app(data_dir):
    live = data_dir / "clinic.db"
    live.write_bytes(os.urandom(8192))
    before = _files(data_dir)
    result = _cli(data_dir, "migrate-db", "--confirm-offline")
    assert result.returncode == 1 and "повреждена" in result.stderr
    assert _files(data_dir) == before
    assert not (data_dir / "backups").exists()


def test_migrate_db_refused_while_app_runs(auth_client):
    from backend.app import config, maintenance
    before = _files(config.DATA_DIR)
    with pytest.raises(maintenance.MaintenanceError, match="приложение запущено"):
        maintenance.migrate_database(config.DB_PATH, confirm_offline=True)
    assert not (config.BACKUP_DIR.exists() and list(config.BACKUP_DIR.glob("pre_migrate_*")))
    assert _files(config.DATA_DIR)["clinic.db"] == before["clinic.db"]


# --- 3, 4: restore and the running application (AUD-04) --------------------

def test_restore_refused_while_app_runs_before_any_change(auth_client, tmp_path):
    from backend.app import config, maintenance
    auth_client.post("/api/patients", json=make_patient_payload(name="Пациент До Копии"), headers=auth_client.csrf)
    backup = maintenance.backup_database(config.DB_PATH, tmp_path / "snap.cbk")
    auth_client.post("/api/patients", json=make_patient_payload(name="Пациент После Копии"), headers=auth_client.csrf)
    before = _digest(config.DB_PATH)
    with pytest.raises(maintenance.MaintenanceError, match="приложение запущено"):
        maintenance.restore_database(backup, config.DB_PATH, confirm_offline=True)
    assert _digest(config.DB_PATH) == before
    assert not list(config.BACKUP_DIR.glob("pre_restore_*"))
    assert _leftovers(config.DATA_DIR) == []
    # The application keeps working and keeps both patients.
    assert len(auth_client.get("/api/patients").json()) == 2

    with app_stopped():
        result = maintenance.restore_database(backup, config.DB_PATH, confirm_offline=True)
    assert result["previous_state"] == "healthy"
    assert result["pre_restore_backup"] is not None
    assert auth_client.get("/api/patients").status_code == 401           # sessions of the snapshot revoked


def test_app_cannot_start_and_cli_waits_while_maintenance_holds_the_lock(data_dir):
    from backend.app import db_guard
    _current_db(data_dir)
    from fastapi.testclient import TestClient
    from backend.app.main import app
    with db_guard.exclusive_maintenance("Restore"):
        with pytest.raises(Exception):
            with TestClient(app):
                pass
        result = _cli(data_dir, "purge-sessions")
        assert result.returncode == 1 and "обслуживание" in result.stderr
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.get("/api/auth/me").status_code == 200


def test_restore_refused_while_cli_command_uses_database(data_dir, tmp_path):
    from backend.app import maintenance
    _current_db(data_dir)
    backup = maintenance.backup_database(data_dir / "clinic.db", tmp_path / "b.cbk")
    holder = subprocess.Popen(
        [sys.executable, "-c", "import sys,time; sys.path.insert(0, sys.argv[1]);"
         "from backend.app import db_guard; fd = db_guard.acquire_shared(); print('ready', flush=True); time.sleep(30)",
         str(PROJECT_ROOT)],
        env=dict(os.environ, CLINIC_DATA_DIR=str(data_dir)), stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ready"
        before = _files(data_dir)
        with pytest.raises(maintenance.MaintenanceError, match="другой процесс"):
            maintenance.restore_database(backup, data_dir / "clinic.db", confirm_offline=True)
        assert _files(data_dir) == before
    finally:
        holder.kill()
        holder.wait()
    assert maintenance.restore_database(backup, data_dir / "clinic.db", confirm_offline=True)["previous_state"] == "healthy"


_SLOW_RESTORE = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from backend.app import config, maintenance
original = maintenance._sqlite_backup_to_file
def slow(src, dest):
    if str(dest).endswith(maintenance.RESTORE_STAGING_SUFFIX):
        original(src, dest)
        print("staging-written", flush=True)
        time.sleep(float(sys.argv[3]))
        return
    original(src, dest)
maintenance._sqlite_backup_to_file = slow
result = maintenance.restore_database(Path(sys.argv[2]), config.DB_PATH, confirm_offline=True)
print("done", result["previous_state"], flush=True)
"""


def _slow_restore(data: Path, backup: Path, pause: float):
    env = dict(os.environ, CLINIC_DATA_DIR=str(data), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.Popen([sys.executable, "-c", _SLOW_RESTORE, str(PROJECT_ROOT), str(backup), str(pause)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_two_restores_never_run_at_the_same_time(data_dir, tmp_path):
    from backend.app import maintenance
    _current_db(data_dir, patients=("Синтетик Из Копии",))
    backup = maintenance.backup_database(data_dir / "clinic.db", tmp_path / "b.cbk")
    first = _slow_restore(data_dir, backup, 3)
    try:
        assert first.stdout.readline().strip() == "staging-written"
        second = _cli(data_dir, "restore", str(backup), "--confirm-offline", "--yes")
        assert second.returncode == 1
        assert "другой процесс" in second.stderr or "приложение запущено" in second.stderr
        out, err = first.communicate(timeout=60)
    finally:
        if first.poll() is None:
            first.kill()
    assert first.returncode == 0, err
    assert "done healthy" in out
    assert len(list((data_dir / "backups").glob("pre_restore_*"))) == 1     # only the first one ran
    assert _patient_names(data_dir / "clinic.db") == ["Синтетик Из Копии"]
    assert _leftovers(data_dir) == []


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM])
def test_killed_restore_leaves_live_db_and_releases_lock(data_dir, tmp_path, sig):
    from backend.app import maintenance
    _current_db(data_dir, patients=("Синтетик Рабочий",))
    backup_src = tmp_path / "src"
    backup_src.mkdir()
    backup = maintenance.backup_database(data_dir / "clinic.db", tmp_path / "b.cbk")
    conn = sqlite3.connect(str(data_dir / "clinic.db"))
    conn.execute("UPDATE patients SET name = 'Синтетик После Копии'")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    live_before = _digest(data_dir / "clinic.db")
    backup_before = _digest(backup)
    proc = _slow_restore(data_dir, backup, 60)
    assert proc.stdout.readline().strip() == "staging-written"
    proc.send_signal(sig)
    proc.wait(timeout=30)
    assert _digest(data_dir / "clinic.db") == live_before                   # never replaced
    assert _patient_names(data_dir / "clinic.db") == ["Синтетик После Копии"]
    assert _digest(backup) == backup_before
    assert _leftovers(data_dir)                                             # the interrupted staging file
    result = maintenance.restore_database(backup, data_dir / "clinic.db", confirm_offline=True)
    assert result["stale_files_removed"] >= 1
    assert _leftovers(data_dir) == []
    assert _patient_names(data_dir / "clinic.db") == ["Синтетик Рабочий"]


def test_restore_with_real_uvicorn_refused_then_succeeds_after_stop(data_dir, tmp_path):
    from backend.app import maintenance
    _current_db(data_dir, patients=("Синтетик Из Копии",))
    backup = maintenance.backup_database(data_dir / "clinic.db", tmp_path / "b.cbk")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    env = dict(os.environ, CLINIC_DATA_DIR=str(data_dir), PYTHONDONTWRITEBYTECODE="1", CLINIC_AI_MODE="disabled")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1",
                               "--port", str(port)], cwd=str(PROJECT_ROOT), env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import httpx
        for _ in range(200):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/api/auth/me", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            pytest.fail("uvicorn did not start")
        before = _digest(data_dir / "clinic.db")
        refused = _cli(data_dir, "restore", str(backup), "--confirm-offline", "--yes")
        assert refused.returncode == 1 and "приложение запущено" in refused.stderr
        assert _digest(data_dir / "clinic.db") == before
        assert not list((data_dir / "backups").glob("pre_restore_*"))
    finally:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=30)
    done = _cli(data_dir, "restore", str(backup), "--confirm-offline", "--yes")
    assert done.returncode == 0, done.stderr
    assert _cli(data_dir, "check-db").returncode == 0


# --- 6: failures keep the usable data and the backup (AUD-16) --------------

@pytest.mark.parametrize("stage", ["staging", "replace", "recovery-copy"])
def test_failed_restore_keeps_live_db_and_backup(data_dir, tmp_path, monkeypatch, stage):
    import errno
    from backend.app import maintenance
    _current_db(data_dir, patients=("Синтетик Рабочий",))
    backup = maintenance.backup_database(data_dir / "clinic.db", tmp_path / "b.cbk")
    live_before, backup_before = _digest(data_dir / "clinic.db"), _digest(backup)

    def no_space(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    if stage == "staging":
        original = maintenance._sqlite_backup_to_file
        monkeypatch.setattr(maintenance, "_sqlite_backup_to_file", lambda src, dest: (
            no_space() if str(dest).endswith(maintenance.RESTORE_STAGING_SUFFIX) else original(src, dest)))
    elif stage == "replace":
        monkeypatch.setattr(maintenance.os, "replace", no_space)
    else:
        monkeypatch.setattr(maintenance, "_write_container", no_space)
    with pytest.raises(maintenance.MaintenanceError, match="не изменены|No space|OSError|Restore не выполнен"):
        maintenance.restore_database(backup, data_dir / "clinic.db", confirm_offline=True)
    monkeypatch.undo()
    assert _digest(data_dir / "clinic.db") == live_before
    assert _digest(backup) == backup_before
    assert _leftovers(data_dir) == []
    assert _patient_names(data_dir / "clinic.db") == ["Синтетик Рабочий"]
    assert not (data_dir / "quarantine").exists()


def test_failed_emergency_replace_puts_damaged_files_back(data_dir, tmp_path, monkeypatch):
    import errno
    from backend.app import maintenance
    _current_db(data_dir)
    backup = maintenance.backup_database(data_dir / "clinic.db", tmp_path / "b.cbk")
    (data_dir / "clinic.db").write_bytes(os.urandom(8192))
    before = _files(data_dir)

    def no_space(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(maintenance.os, "replace", no_space)
    with pytest.raises(maintenance.MaintenanceError):
        maintenance.restore_database(backup, data_dir / "clinic.db", confirm_offline=True, replace_damaged=True)
    assert _files(data_dir) == before


def test_restore_without_write_permission_changes_nothing(data_dir, tmp_path):
    from backend.app import maintenance
    _current_db(data_dir)
    backup = maintenance.backup_database(data_dir / "clinic.db", tmp_path / "b.cbk")
    before = _files(data_dir)
    os.chmod(data_dir, 0o500)
    try:
        if os.access(data_dir, os.W_OK):
            # root ignores directory permissions: the same refusal is forced at the
            # first write into the data directory.
            def denied(*_args, **_kwargs):
                raise PermissionError(13, "Permission denied")
            original = maintenance.tempfile.TemporaryDirectory
            maintenance.tempfile.TemporaryDirectory = denied
        else:
            original = None
        try:
            with pytest.raises(maintenance.MaintenanceError, match="не изменен|недоступен"):
                maintenance.restore_database(backup, data_dir / "clinic.db", confirm_offline=True)
        finally:
            if original is not None:
                maintenance.tempfile.TemporaryDirectory = original
    finally:
        os.chmod(data_dir, 0o700)
    assert _files(data_dir) == before


# --- 7: damaged target — documented emergency procedure (AUD-16) -----------

@pytest.mark.parametrize("damage", ["random-bytes", "text-file", "corrupt-pages"])
def test_damaged_target_needs_flag_and_is_quarantined_not_deleted(data_dir, tmp_path, damage):
    from backend.app import maintenance
    live = _current_db(data_dir, patients=[f"Синтетик {i}" for i in range(200)])
    backup = maintenance.backup_database(live, tmp_path / "b.cbk")
    if damage == "random-bytes":
        live.write_bytes(os.urandom(65536))
    elif damage == "text-file":
        live.write_text("это не база данных\n" * 100, encoding="utf-8")
    else:
        raw = bytearray(live.read_bytes())
        raw[4096 * 3: 4096 * 3 + 1024] = os.urandom(1024)
        live.write_bytes(bytes(raw))
    (data_dir / "clinic.db-wal").write_bytes(os.urandom(2048))
    before = _files(data_dir)

    refused = _cli(data_dir, "restore", str(backup), "--confirm-offline", "--yes")
    assert refused.returncode == 1
    assert "--replace-damaged" in refused.stderr and "База не изменена" in refused.stderr
    assert _files(data_dir) == before

    done = _cli(data_dir, "restore", str(backup), "--confirm-offline", "--yes", "--replace-damaged")
    assert done.returncode == 0, done.stderr
    quarantine = [p for p in (data_dir / "quarantine").iterdir()]
    assert len(quarantine) == 1
    assert _digest(quarantine[0] / "clinic.db") == before["clinic.db"]
    assert _digest(quarantine[0] / "clinic.db-wal") == before["clinic.db-wal"]
    assert stat.S_IMODE(quarantine[0].stat().st_mode) == 0o700
    assert _cli(data_dir, "check-db").returncode == 0
    assert len(_patient_names(live)) == 200
    assert _digest(backup) is not None


def test_missing_and_empty_target_restore(data_dir, tmp_path):
    from backend.app import maintenance
    live = _current_db(data_dir, patients=("Синтетик Из Копии",))
    backup = maintenance.backup_database(live, tmp_path / "b.cbk")
    live.unlink()
    result = maintenance.restore_database(backup, live, confirm_offline=True)
    assert result["previous_state"] == "missing" and result["pre_restore_backup"] is None
    assert _patient_names(live) == ["Синтетик Из Копии"]

    live.write_bytes(b"")
    result = maintenance.restore_database(backup, live, confirm_offline=True)
    assert result["previous_state"] == "empty"
    assert result["quarantine"] is not None
    assert _patient_names(live) == ["Синтетик Из Копии"]


def test_orphan_sidecars_of_missing_db_are_quarantined_not_applied(data_dir, tmp_path):
    from backend.app import maintenance
    live = _current_db(data_dir, patients=("Синтетик Из Копии",))
    backup = maintenance.backup_database(live, tmp_path / "b.cbk")
    live.unlink()
    (data_dir / "clinic.db-wal").write_bytes(os.urandom(4096))
    (data_dir / "clinic.db-shm").write_bytes(os.urandom(32768))
    result = maintenance.restore_database(backup, live, confirm_offline=True)
    assert result["quarantine"] is not None
    assert sorted(p.name for p in Path(result["quarantine"]).iterdir()) == ["clinic.db-shm", "clinic.db-wal"]
    assert _patient_names(live) == ["Синтетик Из Копии"]
    assert maintenance.check_database(live)["ok"] is True


@pytest.mark.parametrize("sidecar", ["-wal", "-shm"])
def test_broken_sidecar_of_healthy_db(data_dir, tmp_path, sidecar):
    from backend.app import maintenance
    live = _current_db(data_dir, patients=("Синтетик Из Копии",))
    backup = maintenance.backup_database(live, tmp_path / "b.cbk")
    live.with_name("clinic.db" + sidecar).write_bytes(os.urandom(32768))
    report = _cli(data_dir, "check-db")
    assert "Traceback" not in report.stderr
    result = maintenance.restore_database(backup, live, confirm_offline=True, replace_damaged=True)
    assert result["previous_state"] in ("healthy", "damaged")
    assert maintenance.check_database(live)["ok"] is True
    assert _patient_names(live) == ["Синтетик Из Копии"]


def test_restore_over_older_live_schema_keeps_verifiable_recovery_copy(data_dir, tmp_path):
    """A live v20 DB (not yet migrated) gets an encrypted v20 recovery copy that verifies."""
    from backend.app import maintenance
    live = _current_db(data_dir, patients=("Синтетик Из Копии",))
    backup = maintenance.backup_database(live, tmp_path / "b.cbk")
    live.unlink()
    _v20_db(live)
    result = maintenance.restore_database(backup, live, confirm_offline=True)
    assert result["previous_state"] == "healthy"
    pre = Path(result["pre_restore_backup"])
    assert maintenance.inspect_backup(pre)["schema_version"] == 20
    assert maintenance.validate_backup(pre)["migrated_from_schema_version"] == 20


# --- 8, 9: backup-keygen --key-id and historical keys (AUD-20) -------------

@pytest.fixture()
def key_dir(tmp_path):
    d = tmp_path / "keys"
    d.mkdir(mode=0o700)
    return d


def test_keygen_key_id_is_stored_and_used(data_dir, key_dir, monkeypatch):
    from backend.app import maintenance
    monkeypatch.delenv("CLINIC_BACKUP_KEY", raising=False)
    monkeypatch.delenv("CLINIC_BACKUP_KEY_ID", raising=False)
    key_file = key_dir / "clinic.key"
    result = maintenance.create_backup_key_file(key_file, key_id="clinic-2026-10")
    assert result["key_id"] == "clinic-2026-10"
    lines = key_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and lines[1] == "key_id=clinic-2026-10"
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    monkeypatch.setenv("CLINIC_BACKUP_KEY_FILE", str(key_file))
    live = _current_db(data_dir)
    backup = maintenance.backup_database(live, data_dir / "backups" / "b.cbk")
    assert maintenance.inspect_backup(backup)["key_id"] == "clinic-2026-10"
    assert maintenance.validate_backup(backup)["key_id"] == "clinic-2026-10"
    assert maintenance.restore_database(backup, live, confirm_offline=True)["key_id"] == "clinic-2026-10"

    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "clinic-2026-10")             # same value: fine
    maintenance.backup_database(live, data_dir / "backups" / "b2.cbk")
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "other")                      # different: refused
    with pytest.raises(maintenance.MaintenanceError, match="не совпадает"):
        maintenance.backup_database(live, data_dir / "backups" / "b3.cbk")
    assert not (data_dir / "backups" / "b3.cbk").exists()


def test_keygen_cli_prints_the_stored_id(data_dir, key_dir):
    result = _cli(data_dir, "backup-keygen", str(key_dir / "k.key"), "--key-id", "clinic-2026-11",
                  env_extra={"CLINIC_BACKUP_KEY": "", "CLINIC_BACKUP_KEY_ID": ""})
    assert result.returncode == 0, result.stderr
    assert "clinic-2026-11" in result.stdout
    assert (key_dir / "k.key").read_text(encoding="utf-8").splitlines()[1] == "key_id=clinic-2026-11"
    assert TEST_BACKUP_KEY not in result.stdout


def test_keygen_refuses_an_id_already_in_use(data_dir, key_dir, monkeypatch):
    from backend.app import maintenance
    monkeypatch.setenv("CLINIC_BACKUP_DECRYPT_KEYS", json.dumps({"retired-2025": TEST_BACKUP_KEY.replace("A", "B")}))
    with pytest.raises(maintenance.MaintenanceError, match="уже используется"):
        maintenance.create_backup_key_file(key_dir / "a.key", key_id="retired-2025")
    with pytest.raises(maintenance.MaintenanceError, match="уже используется"):
        maintenance.create_backup_key_file(key_dir / "b.key", key_id="test-key")   # the active key's id
    assert not (key_dir / "a.key").exists() and not (key_dir / "b.key").exists()
    assert maintenance.create_backup_key_file(key_dir / "c.key", key_id="clinic-2026-12")["key_id"] == "clinic-2026-12"


def _legacy_key_file(path: Path, raw: bytes) -> Path:
    """Key file as written before WP-B: one line with the key, no id."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") + "\n")
    return path


def test_legacy_one_line_key_file_and_its_backups_still_work(data_dir, key_dir, monkeypatch):
    from backend.app import maintenance
    raw = bytes(range(32))
    key_file = _legacy_key_file(key_dir / "legacy.key", raw)
    monkeypatch.delenv("CLINIC_BACKUP_KEY", raising=False)
    monkeypatch.delenv("CLINIC_BACKUP_KEY_ID", raising=False)
    monkeypatch.setenv("CLINIC_BACKUP_KEY_FILE", str(key_file))
    derived = hashlib.sha256(b"clinic-backup-key-id:" + raw).hexdigest()[:16]
    live = _current_db(data_dir)
    old_backup = maintenance.backup_database(live, data_dir / "backups" / "old.cbk")
    assert maintenance.inspect_backup(old_backup)["key_id"] == derived           # unchanged behaviour

    # The operator later sets the id that keygen once printed (the AUD-20 situation):
    # new backups use it, the earlier ones under the derived id still verify and restore.
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "clinic-2026-10")
    new_backup = maintenance.backup_database(live, data_dir / "backups" / "new.cbk")
    assert maintenance.inspect_backup(new_backup)["key_id"] == "clinic-2026-10"
    assert maintenance.validate_backup(old_backup)["key_id"] == derived
    assert maintenance.restore_database(old_backup, live, confirm_offline=True)["key_id"] == derived


def test_rotation_keeps_old_backups_restorable(data_dir, key_dir, monkeypatch):
    from backend.app import maintenance
    live = _current_db(data_dir, patients=("Синтетик Старый Ключ",))
    old_backup = maintenance.backup_database(live, data_dir / "backups" / "old.cbk")  # test-key (env)
    new_file = key_dir / "new.key"
    maintenance.create_backup_key_file(new_file, key_id="clinic-2026-q4")
    monkeypatch.delenv("CLINIC_BACKUP_KEY", raising=False)
    monkeypatch.delenv("CLINIC_BACKUP_KEY_ID", raising=False)
    monkeypatch.setenv("CLINIC_BACKUP_KEY_FILE", str(new_file))
    monkeypatch.setenv("CLINIC_BACKUP_DECRYPT_KEYS", json.dumps({"test-key": TEST_BACKUP_KEY}))
    assert maintenance.inspect_backup(maintenance.backup_database(live, data_dir / "backups" / "n.cbk"))["key_id"] == "clinic-2026-q4"
    assert maintenance.restore_database(old_backup, live, confirm_offline=True)["key_id"] == "test-key"
    assert _patient_names(live) == ["Синтетик Старый Ключ"]


@pytest.mark.parametrize("content", ["", "key_id=x\n", "{key}\nsomething=else\n", "{key}\nkey_id=a\nkey_id=b\n", "{key}\nkey_id=\n"])
def test_malformed_key_file_is_rejected(data_dir, key_dir, monkeypatch, content):
    from backend.app import maintenance
    path = key_dir / "bad.key"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content.format(key=TEST_BACKUP_KEY))
    monkeypatch.delenv("CLINIC_BACKUP_KEY", raising=False)
    monkeypatch.delenv("CLINIC_BACKUP_KEY_ID", raising=False)
    monkeypatch.setenv("CLINIC_BACKUP_KEY_FILE", str(path))
    with pytest.raises(maintenance.MaintenanceError) as exc:
        maintenance._load_active_key()
    assert TEST_BACKUP_KEY not in str(exc.value)


# --- AUD-19: configuration comes from the process environment only ---------

def test_env_file_with_values_the_process_lacks_is_refused(tmp_path):
    from backend.app import config
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nexport CLINIC_HTTPS=true\nCLINIC_ENV='production'\nCLINIC_AI_API_KEY=synthetic-secret-value\n",
                        encoding="utf-8")
    with pytest.raises(config.ConfigError) as exc:
        config.check_env_files([env_file], environ={})
    message = str(exc.value)
    assert "CLINIC_HTTPS" in message and "CLINIC_ENV" in message and "CLINIC_AI_API_KEY" in message
    assert "synthetic-secret-value" not in message and "production" not in message
    config.check_env_files([env_file], environ={"CLINIC_HTTPS": "true", "CLINIC_ENV": "production",
                                                "CLINIC_AI_API_KEY": "synthetic-secret-value"})
    with pytest.raises(config.ConfigError, match="CLINIC_HTTPS"):
        config.check_env_files([env_file], environ={"CLINIC_HTTPS": "false", "CLINIC_ENV": "production",
                                                    "CLINIC_AI_API_KEY": "synthetic-secret-value"})
    config.check_env_files([tmp_path / "absent.env"], environ={})
    bad = tmp_path / "bad.env"
    bad.write_text("CLINIC_HTTPS true\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="строка 1"):
        config.check_env_files([bad], environ={})


def test_app_and_cli_refuse_unapplied_env_file(data_dir, tmp_path, monkeypatch):
    from backend.app import config
    env_file = tmp_path / ".env"
    env_file.write_text("CLINIC_HTTPS=true\n", encoding="utf-8")
    monkeypatch.delenv("CLINIC_HTTPS", raising=False)
    monkeypatch.setattr(config, "ENV_FILE_CANDIDATES", (env_file,))
    from fastapi.testclient import TestClient
    from backend.app.main import app
    with pytest.raises(config.ConfigError):
        with TestClient(app):
            pass
    assert not (data_dir / "clinic.db").exists()
    monkeypatch.setenv("CLINIC_HTTPS", "true")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.get("/api/auth/me").status_code == 200


_CLI_WITH_ENV_FILE = r"""
import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from backend.app import config
config.ENV_FILE_CANDIDATES = (Path(sys.argv[2]),)
from backend import cli
sys.argv = ["backend.cli", "check-db"]
raise SystemExit(cli.main())
"""


def test_cli_refuses_unapplied_env_file(data_dir, tmp_path):
    """Same check as for backend/.env, with the candidate path pointed at a temporary
    file (the repository is never written by the tests)."""
    env_file = tmp_path / ".env"
    env_file.write_text("CLINIC_DEBUG=true\n", encoding="utf-8")
    base = dict(os.environ, CLINIC_DATA_DIR=str(data_dir), PYTHONDONTWRITEBYTECODE="1")
    run = lambda debug: subprocess.run(
        [sys.executable, "-c", _CLI_WITH_ENV_FILE, str(PROJECT_ROOT), str(env_file)],
        env=dict(base, CLINIC_DEBUG=debug), capture_output=True, text=True, timeout=60)
    refused = run("false")
    assert refused.returncode == 2 and "CLINIC_DEBUG" in refused.stderr
    assert "CLINIC_DEBUG=true" not in refused.stderr and "=true" not in refused.stderr   # names only, no values
    assert not (data_dir / "clinic.db").exists()
    applied = run("true")
    assert "CLINIC_DEBUG" not in applied.stderr          # loaded: check-db runs (DB missing -> 1)


def test_env_file_candidates_are_backend_and_repo_root():
    from backend.app import config
    assert config.ENV_FILE_CANDIDATES == (PROJECT_ROOT / "backend" / ".env", PROJECT_ROOT / ".env")


# --- 10: locks, permissions and exceptions ---------------------------------

def test_lock_files_are_private_and_released(data_dir):
    from backend.app import db_guard, process_guard
    with db_guard.exclusive_maintenance("Restore"):
        for name in (db_guard.DB_LOCK_NAME, process_guard.LOCK_NAME):
            assert stat.S_IMODE((data_dir / name).stat().st_mode) == 0o600
        with pytest.raises(db_guard.DatabaseBusyError):
            with db_guard.exclusive_maintenance("Restore"):
                pass
        with pytest.raises(db_guard.DatabaseBusyError):
            db_guard.acquire_shared()
    with db_guard.shared():
        with db_guard.shared():                       # several users share the database
            with pytest.raises(db_guard.DatabaseBusyError, match="другой процесс"):
                with db_guard.exclusive_maintenance("Restore"):
                    pass
    with db_guard.exclusive_maintenance("Restore"):
        pass


def test_restored_database_permissions(data_dir, tmp_path):
    from backend.app import maintenance
    live = _current_db(data_dir)
    backup = maintenance.backup_database(live, tmp_path / "b.cbk")
    os.chmod(live, 0o644)
    maintenance.restore_database(backup, live, confirm_offline=True)
    assert stat.S_IMODE(live.stat().st_mode) == 0o600
    pre = list((data_dir / "backups").glob("pre_restore_*"))
    assert pre and stat.S_IMODE(pre[0].stat().st_mode) == 0o600


def test_check_db_while_app_runs_without_open_wal_uses_unchanged_copy(auth_client):
    """App running but no connection open (no WAL/SHM): mode=ro would create them,
    so a copy taken while the file did not change is checked instead."""
    from backend.app import config, db, maintenance
    db.reset_singleton_for_tests()                        # last connection closed: WAL/SHM removed
    assert not config.DB_PATH.with_name("clinic.db-wal").exists()
    before = _files(config.DATA_DIR)
    report = maintenance.diagnose_database(config.DB_PATH)
    assert report["method"] == "copy-while-idle"
    assert report["ok"] is True
    assert _files(config.DATA_DIR) == before
    assert _leftovers(config.DATA_DIR) == []


# --- review fix: failures after the commit point --------------------------
# Restore commits at os.replace(staging, clinic.db); migrate-db commits inside init_db.
# A later failure must never be reported as "база не изменена" or as success.

def _fail_after_replace(monkeypatch, *, fsync=False, chmod=False):
    """Inject EIO into os.fsync / EPERM into os.chmod for clinic.db once clinic.db was replaced."""
    import errno
    from backend.app import maintenance
    real_replace, real_fsync, real_chmod = os.replace, os.fsync, os.chmod
    replaced = {"done": False}

    def replace(src, dst):
        real_replace(src, dst)
        if str(dst).endswith("clinic.db"):
            replaced["done"] = True

    def failing_fsync(fd):
        if replaced["done"]:
            raise OSError(errno.EIO, "Input/output error")
        real_fsync(fd)

    def failing_chmod(path, mode, *args, **kwargs):
        if replaced["done"] and str(path).endswith("clinic.db"):
            raise OSError(errno.EPERM, "Operation not permitted")
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(maintenance.os, "replace", replace)
    if fsync:
        monkeypatch.setattr(maintenance.os, "fsync", failing_fsync)
    if chmod:
        monkeypatch.setattr(maintenance.os, "chmod", failing_chmod)
    return replaced


def test_fsync_failure_after_replace_is_not_reported_as_unchanged(data_dir, tmp_path, monkeypatch):
    from backend.app import maintenance
    live = _current_db(data_dir, patients=("Синтетик Из Копии",))
    backup = maintenance.backup_database(live, tmp_path / "b.cbk")
    conn = sqlite3.connect(str(live))
    conn.execute("UPDATE patients SET name = 'Синтетик После Копии'")
    conn.commit()
    conn.close()
    backup_before = _digest(backup)
    with monkeypatch.context() as m:
        replaced = _fail_after_replace(m, fsync=True)
        with pytest.raises(maintenance.CommittedButUnverifiedError) as exc:
            maintenance.restore_database(backup, live, confirm_offline=True)
    assert replaced["done"] is True
    err = exc.value
    assert err.operation == "restore" and err.state == "replaced_not_durable"
    message = str(err)
    assert "ЗАМЕНЕНА" in message and "долговечность записи не подтверждена" in message
    assert "не изменен" not in message
    assert "автоматически не выполняется" in message
    # Actual state: the restored content is installed, nothing was retried or rolled back.
    assert _patient_names(live) == ["Синтетик Из Копии"]
    # The verified copy of the previous DB and the source backup are kept.
    pre = Path(err.details["pre_restore_backup"])
    assert pre.exists() and maintenance.validate_backup(pre)["patient_count"] == 1
    assert _digest(backup) == backup_before
    assert _leftovers(data_dir) == []
    # Locks are released: the operator can check and, if needed, restore again by hand.
    assert maintenance.diagnose_database(live)["ok"] is True
    assert maintenance.restore_database(backup, live, confirm_offline=True)["previous_state"] == "healthy"


def test_fsync_failure_after_replace_damaged_keeps_quarantine(data_dir, tmp_path, monkeypatch):
    from backend.app import maintenance
    live = _current_db(data_dir, patients=("Синтетик Из Копии",))
    backup = maintenance.backup_database(live, tmp_path / "b.cbk")
    live.write_bytes(os.urandom(16384))
    (data_dir / "clinic.db-wal").write_bytes(os.urandom(2048))
    damaged = _files(data_dir)
    with monkeypatch.context() as m:
        _fail_after_replace(m, fsync=True)
        with pytest.raises(maintenance.CommittedButUnverifiedError) as exc:
            maintenance.restore_database(backup, live, confirm_offline=True, replace_damaged=True)
    err = exc.value
    assert err.state == "replaced_not_durable"
    assert "не изменен" not in str(err)
    quarantine = Path(err.details["quarantine"])
    assert _digest(quarantine / "clinic.db") == damaged["clinic.db"]
    assert _digest(quarantine / "clinic.db-wal") == damaged["clinic.db-wal"]
    assert str(quarantine) in str(err)
    assert _patient_names(live) == ["Синтетик Из Копии"]


def test_chmod_failure_after_replace_is_reported(data_dir, tmp_path, monkeypatch):
    from backend.app import maintenance
    live = _current_db(data_dir, patients=("Синтетик Из Копии",))
    backup = maintenance.backup_database(live, tmp_path / "b.cbk")
    with monkeypatch.context() as m:
        _fail_after_replace(m, chmod=True)
        with pytest.raises(maintenance.CommittedButUnverifiedError) as exc:
            maintenance.restore_database(backup, live, confirm_offline=True)
    assert exc.value.state == "replaced_permissions_unconfirmed"
    assert "права доступа 0600" in str(exc.value) and "не изменен" not in str(exc.value)
    assert Path(exc.value.details["pre_restore_backup"]).exists()
    assert _patient_names(live) == ["Синтетик Из Копии"]


def test_failure_before_replace_still_reports_unchanged(data_dir, tmp_path, monkeypatch):
    """Control: os.replace itself fails -> nothing installed, the old files are back."""
    import errno
    from backend.app import maintenance
    live = _current_db(data_dir, patients=("Синтетик Рабочий",))
    backup = maintenance.backup_database(live, tmp_path / "b.cbk")
    before = _files(data_dir)

    def no_space(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as m:
        m.setattr(maintenance.os, "replace", no_space)
        with pytest.raises(maintenance.MaintenanceError) as exc:
            maintenance.restore_database(backup, live, confirm_offline=True)
    assert not isinstance(exc.value, maintenance.CommittedButUnverifiedError)
    assert "не изменены" in str(exc.value)
    assert _files(data_dir) == before


def test_failed_put_back_after_failed_replace_is_not_reported_as_unchanged(data_dir, tmp_path, monkeypatch):
    import errno
    from backend.app import maintenance
    live = _current_db(data_dir)
    backup = maintenance.backup_database(live, tmp_path / "b.cbk")
    live.write_bytes(os.urandom(8192))
    real_rename = os.rename

    def no_space(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    def rename(src, dst):
        if "quarantine" in str(src):                  # putting the damaged file back fails
            raise OSError(errno.EIO, "Input/output error")
        return real_rename(src, dst)

    with monkeypatch.context() as m:
        m.setattr(maintenance.os, "replace", no_space)
        m.setattr(maintenance.os, "rename", rename)
        with pytest.raises(maintenance.MaintenanceError) as exc:
            maintenance.restore_database(backup, live, confirm_offline=True, replace_damaged=True)
    message = str(exc.value)
    assert "не изменены" not in message.split("Резервная копия")[0]
    assert "остались там" in message and "clinic.db" in message
    quarantine = next((data_dir / "quarantine").iterdir())
    assert (quarantine / "clinic.db").exists()               # the damaged file is kept, not deleted


def test_migration_check_failure_after_commit_is_reported_as_committed(data_dir, monkeypatch):
    import errno
    from backend.app import maintenance
    live = _v20_db(data_dir / "clinic.db")

    def broken(_path):
        raise OSError(errno.EIO, "Input/output error")

    with monkeypatch.context() as m:
        m.setattr(maintenance, "_diagnose_copy", broken)
        with pytest.raises(maintenance.CommittedButUnverifiedError) as exc:
            maintenance.migrate_database(live, confirm_offline=True)
    err = exc.value
    assert err.operation == "migrate-db" and err.state == "migrated_check_incomplete"
    message = str(err)
    assert "ЗАФИКСИРОВАНА" in message and f"v{current_schema_version()}" in message
    assert "откатана" not in message and "не изменена" not in message
    assert "check-db" in message and "restore" in message
    assert err.details["file_schema_version"] == current_schema_version()
    assert _schema_version(live) == current_schema_version()           # the migration is in the file
    backup = Path(err.details["pre_migration_backup"])
    assert maintenance.inspect_backup(backup)["schema_version"] == 20   # verified copy kept
    assert maintenance.validate_backup(backup)["migrated_from_schema_version"] == 20
    assert maintenance.diagnose_database(live)["ok"] is True            # the operator's next step works


def test_migration_check_problems_after_commit_are_reported_as_committed(data_dir, monkeypatch):
    from backend.app import maintenance
    live = _v20_db(data_dir / "clinic.db")
    real = maintenance._diagnose_copy
    calls = {"n": 0}

    def failing_after_commit(path):
        report = real(path)
        report["ok"] = False
        report["problems"] = ["synthetic post-commit problem"]
        calls["n"] += 1
        return report

    with monkeypatch.context() as m:
        m.setattr(maintenance, "_diagnose_copy", failing_after_commit)
        with pytest.raises(maintenance.CommittedButUnverifiedError) as exc:
            maintenance.migrate_database(live, confirm_offline=True)
    assert exc.value.state == "migrated_check_failed"
    assert "synthetic post-commit problem" in str(exc.value) and "откатана" not in str(exc.value)
    assert _schema_version(live) == current_schema_version()


def test_migration_failure_before_commit_reports_the_real_old_version(data_dir, monkeypatch):
    from backend.app import maintenance
    live = _v20_db(data_dir / "clinic.db")
    before = _digest(live)

    real_init = maintenance.init_db

    def broken_init(conn, **kwargs):
        files = [Path(r[2]).resolve() for r in conn.execute("PRAGMA database_list") if r[2]]
        if live.resolve() not in files:
            return real_init(conn, **kwargs)          # trial migration of the decrypted copy: normal
        conn.execute("BEGIN IMMEDIATE")              # live file: change, then fail before COMMIT
        conn.execute("UPDATE schema_version SET version = 99")
        conn.rollback()
        raise RuntimeError("synthetic migration failure")

    with monkeypatch.context() as m:
        m.setattr(maintenance, "init_db", broken_init)
        with pytest.raises(maintenance.MaintenanceError) as exc:
            maintenance.migrate_database(live, confirm_offline=True)
    assert not isinstance(exc.value, maintenance.CommittedButUnverifiedError)
    assert "откатана" in str(exc.value) and "прежняя схема v20" in str(exc.value)
    assert _schema_version(live) == 20 and _digest(live) == before


_CLI_FAULT = r"""
import errno, os, sys
sys.path.insert(0, sys.argv[1])
from backend.app import maintenance
real_replace, real_fsync = os.replace, os.fsync
state = {"replaced": False}
def replace(src, dst):
    real_replace(src, dst)
    if str(dst).endswith("clinic.db"):
        state["replaced"] = True
def fsync(fd):
    if state["replaced"]:
        raise OSError(errno.EIO, "Input/output error")
    real_fsync(fd)
os.replace, os.fsync = replace, fsync
if sys.argv[2] == "migrate-db":
    def broken(_path):
        raise OSError(errno.EIO, "Input/output error")
    maintenance._diagnose_copy = broken
from backend import cli
sys.argv = ["backend.cli", *sys.argv[2:]]
raise SystemExit(cli.main())
"""


@pytest.mark.parametrize("operation", ["restore", "restore-damaged", "migrate-db"])
def test_cli_reports_committed_unverified_state_without_false_success(data_dir, tmp_path, operation):
    from backend.app import maintenance
    if operation == "migrate-db":
        live = _v20_db(data_dir / "clinic.db")
        args = ["migrate-db", "--confirm-offline"]
    else:
        live = _current_db(data_dir, patients=("Синтетик Из Копии",))
        backup = maintenance.backup_database(live, tmp_path / "b.cbk")
        args = ["restore", str(backup), "--confirm-offline", "--yes"]
        if operation == "restore-damaged":
            live.write_bytes(os.urandom(8192))
            args.append("--replace-damaged")
    env = dict(os.environ, CLINIC_DATA_DIR=str(data_dir), PYTHONDONTWRITEBYTECODE="1", CLINIC_AI_MODE="disabled")
    result = subprocess.run([sys.executable, "-c", _CLI_FAULT, str(PROJECT_ROOT), *args], env=env,
                            capture_output=True, text=True, timeout=120)
    out = result.stdout + result.stderr
    assert result.returncode == 3, out
    assert "ОПЕРАЦИЯ НЕ ПОДТВЕРЖДЕНА" in result.stderr
    assert "Восстановление завершено" not in out and "Миграция выполнена" not in out
    assert "не изменен" not in result.stderr
    conn = sqlite3.connect(f"{live.as_uri()}?mode=ro", uri=True)
    row = conn.execute("SELECT event_type, result, detail FROM audit_events ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    expected = "schema_migrated" if operation == "migrate-db" else "restore_performed"
    assert row[0] == expected and row[1] == "error" and "state=" in row[2]
