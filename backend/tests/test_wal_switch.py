"""Opening a database that is not in WAL mode yet.

Switching a file to WAL needs exclusive access. When two processes switch
the same file at once (or another connection holds a write lock on it),
SQLite answers SQLITE_BUSY at once instead of waiting busy_timeout, since
waiting could deadlock. _connect has to wait for the other side within
the same busy timeout instead of failing the server start or a CLI
command, and still fail once that timeout is over. The data is synthetic.
"""
import sqlite3
import threading
import time

import pytest


def _rollback_journal_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (a INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    assert path.read_bytes()[18] == 1  # rollback journal, not WAL
    return path


def _write_lock(path):
    holder = sqlite3.connect(str(path), check_same_thread=False)  # released from a timer thread
    holder.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError):
        # Without a wait the switch fails immediately: the scenario under test.
        sqlite3.connect(str(path), timeout=5).execute("PRAGMA journal_mode = WAL")
    return holder


def test_wal_switch_waits_for_a_competing_lock(client, tmp_path):
    from backend.app import db

    path = _rollback_journal_db(tmp_path / "old.db")
    holder = _write_lock(path)
    release = threading.Timer(0.3, holder.rollback)
    release.start()
    started = time.monotonic()
    try:
        conn = db._connect(path)
    finally:
        release.join()
        holder.close()
    waited = time.monotonic() - started
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("SELECT a FROM t").fetchone()[0] == 1
    conn.close()
    assert waited >= 0.2


def test_wal_switch_gives_up_after_the_busy_timeout(client, tmp_path, monkeypatch):
    from backend.app import db

    monkeypatch.setattr(db.config, "BUSY_TIMEOUT_MS", 300)
    path = _rollback_journal_db(tmp_path / "busy.db")
    holder = _write_lock(path)
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            db._connect(path)
    finally:
        holder.rollback()
        holder.close()
    assert 0.25 <= time.monotonic() - started < 3
    assert path.read_bytes()[18] == 1  # left as it was, data untouched
