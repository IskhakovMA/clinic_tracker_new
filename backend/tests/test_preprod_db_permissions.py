"""Pre-production hardening: the live database is private regardless of umask.

Regression test for audit lead NV-06: DATA_DIR, clinic.db and its WAL/SHM
sidecars were created with umask-default modes (0755/0644 under umask 022).
"""
import os
import stat
import sys


def test_live_database_files_are_private_under_permissive_umask(tmp_path, monkeypatch):
    data_dir = tmp_path / "fresh-data"
    monkeypatch.setenv("CLINIC_DATA_DIR", str(data_dir))
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]
    old = os.umask(0o022)
    try:
        from backend.app import db
        conn = db._connect(data_dir / "clinic.db")
        db.init_db(conn)
        with conn:
            conn.execute("INSERT OR IGNORE INTO departments (id, name, created_at, updated_at) VALUES ('d1','Отделение','x','x')")
        mode = lambda p: stat.S_IMODE(os.stat(p).st_mode)
        assert mode(data_dir) == 0o700
        for name in ("clinic.db", "clinic.db-wal", "clinic.db-shm"):
            path = data_dir / name
            if path.exists():
                assert mode(path) == 0o600, (name, oct(mode(path)))
        conn.close()
    finally:
        os.umask(old)


def test_existing_world_readable_database_is_tightened(tmp_path, monkeypatch):
    data_dir = tmp_path / "old-install"
    data_dir.mkdir(mode=0o755)
    monkeypatch.setenv("CLINIC_DATA_DIR", str(data_dir))
    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]
    import sqlite3
    sqlite3.connect(str(data_dir / "clinic.db")).close()
    os.chmod(data_dir / "clinic.db", 0o644)
    os.chmod(data_dir, 0o755)
    from backend.app import db
    db._connect(data_dir / "clinic.db").close()
    assert stat.S_IMODE(os.stat(data_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(data_dir / "clinic.db").st_mode) == 0o600
