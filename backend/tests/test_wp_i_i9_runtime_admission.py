"""Release admission regressions on disposable synthetic v33 databases."""

import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from threading import Barrier

import pytest

from .test_wp_b_database_safety import data_dir


@pytest.fixture
def approved_database(data_dir):
    from backend.app import config, operations_state

    operations_state.bootstrap_database(confirm_new=True)
    operations_state.verify_database(confirm_offline=True)
    return config.DB_PATH


@pytest.fixture
def live_wal(approved_database):
    from backend.app import db

    # The app's long-lived connection owns the sidecars during release.
    db.get_connection()
    return approved_database


@pytest.fixture
def trace_sql(monkeypatch):
    """Capture SQL by connection so the live and reference DBs stay distinct."""
    real_connect = sqlite3.connect
    traces = []

    def traced_connect(database, *args, **kwargs):
        conn = real_connect(database, *args, **kwargs)
        if database != ":memory:":
            statements = []
            traces.append(statements)
            conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    return traces


@contextmanager
def _release_connection(path):
    from backend.app import db

    conn, admission = db.connect_release(path)
    with closing(conn):
        yield conn, admission


def _normalized(statements):
    return [" ".join(statement.upper().split()) for statement in statements]


def _schema_preparation(statements):
    return [
        statement for statement in _normalized(statements)
        if statement.startswith((
            "CREATE ", "ALTER ", "DROP ", "REINDEX ", "VACUUM",
            "PRAGMA JOURNAL_MODE =", "PRAGMA JOURNAL_MODE=",
            "INSERT OR IGNORE INTO ROLES", "INSERT OR IGNORE INTO NOTIFICATION_TEMPLATES",
        ))
    ]


def _files(path):
    return {
        file.name: file.read_bytes()
        for file in path.parent.glob(path.name + "*")
        if file.is_file()
    }


def test_cold_and_repeated_release_admission_keep_approved_wal_read_only(
    live_wal, trace_sql, monkeypatch
):
    from backend.app import config, db

    # Offline verification may prime this reference in the same process.
    monkeypatch.setattr(db, "_reference_triggers_cache", None)
    path = live_wal
    before = path.read_bytes()
    for _ in range(2):
        with _release_connection(path) as (conn, admission):
            assert admission is not None
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == config.BUSY_TIMEOUT_MS
            assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION
            assert not conn.in_transaction
    assert trace_sql
    for statements in trace_sql:
        assert len(_schema_preparation(statements)) == 0
        assert not any(sql.startswith("BEGIN IMMEDIATE") for sql in _normalized(statements))
    assert path.read_bytes() == before


def test_replaced_file_is_refused_after_warm_release_admission(live_wal):
    from backend.app import operations_state

    path = live_wal
    with _release_connection(path) as (_, admission):
        assert admission is not None
    replacement = path.with_name("replacement.db")
    replacement.write_bytes(path.read_bytes())
    replacement.chmod(0o600)
    os.replace(replacement, path)
    before = _files(path)
    with pytest.raises(operations_state.AdmissionRequired, match="database_identity_changed"):
        with _release_connection(path):
            pytest.fail("unverified replacement was admitted")
    assert _files(path) == before


def test_old_schema_stays_untouched_after_release_admission_denial(approved_database):
    from backend.app import config, db

    path = approved_database
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION - 1,))
        conn.commit()
    before = _files(path)
    with pytest.raises(db.SchemaUpgradeRequiredError):
        with _release_connection(path):
            pytest.fail("old schema was admitted")
    assert _files(path) == before


@pytest.mark.parametrize("drift", ["missing", "changed", "unknown"])
def test_release_preserves_protective_trigger_policy(approved_database, drift):
    from backend.app import db

    name = "trg_read_manifest_immutable"
    path = approved_database
    with closing(sqlite3.connect(path)) as conn:
        if drift != "unknown":
            conn.execute("DROP TRIGGER trg_read_manifest_immutable")
        if drift == "changed":
            conn.execute(
                "CREATE TRIGGER trg_read_manifest_immutable BEFORE UPDATE ON read_access_manifests "
                "BEGIN SELECT 1; END"
            )
        if drift == "unknown":
            conn.execute(
                "CREATE TRIGGER trg_test_observer AFTER INSERT ON audit_events BEGIN SELECT 1; END"
            )
        conn.commit()
    before = _files(path)
    if drift == "unknown":
        with _release_connection(path) as (conn, admission):
            assert admission.prepared
            assert db.trigger_drift(conn)["unknown"] == ["trg_test_observer"]
    else:
        with pytest.raises(db.SchemaDriftError, match=name):
            with _release_connection(path):
                pytest.fail("protective trigger drift was admitted")
        assert _files(path) == before


def test_approved_rollback_journal_uses_full_compatibility_path(approved_database):
    from backend.app import operations_state

    path = approved_database
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"
    operations_state.verify_database(confirm_offline=True)
    with _release_connection(path) as (conn, admission):
        assert admission.prepared
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_approved_wal_without_live_sidecars_uses_full_compatibility_path(approved_database):
    path = approved_database
    assert not path.with_name(path.name + "-wal").exists()
    assert not path.with_name(path.name + "-shm").exists()
    with _release_connection(path) as (conn, admission):
        assert admission.prepared
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_live_wal_with_missing_seed_is_refused_without_repair(live_wal):
    from backend.app import db

    path = live_wal
    query = "SELECT scope_strategy FROM roles WHERE id = 'doctor'"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("DELETE FROM roles WHERE id = 'doctor'")
        conn.commit()
        assert conn.execute(query).fetchone() is None
    wal = path.with_name(path.name + "-wal")
    before = path.read_bytes(), wal.read_bytes()
    with pytest.raises(db.ReleaseAdmissionError, match="seed identities are missing"):
        with _release_connection(path):
            pytest.fail("database with a missing required seed was admitted")
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute(query).fetchone() is None
    assert (path.read_bytes(), wal.read_bytes()) == before


def test_live_wal_with_unknown_trigger_uses_guarded_compatibility_path(live_wal, trace_sql):
    from backend.app import db

    with closing(sqlite3.connect(live_wal)) as writer:
        writer.execute(
            "CREATE TRIGGER trg_test_observer AFTER INSERT ON audit_events BEGIN SELECT 1; END"
        )
        writer.commit()
    trace_sql.clear()
    with _release_connection(live_wal) as (conn, admission):
        assert admission is not None
        assert db.trigger_drift(conn)["unknown"] == ["trg_test_observer"]
        assert any("BEGIN IMMEDIATE" in _normalized(statements) for statements in trace_sql)
        conn.execute("BEGIN IMMEDIATE")
        try:
            db.require_release_admission(conn, admission)
        finally:
            conn.rollback()


def test_legacy_preparation_does_not_reseed_a_row_lost_after_preflight(live_wal, trace_sql, monkeypatch):
    from backend.app import db

    with closing(sqlite3.connect(live_wal)) as writer:
        writer.execute("CREATE TRIGGER trg_test_observer AFTER INSERT ON audit_events BEGIN SELECT 1; END")
        writer.commit()
    connect_live = db.connect_live

    def connect_after_seed_loss(path, **kwargs):
        with closing(sqlite3.connect(path)) as writer:
            writer.execute("DELETE FROM roles WHERE id = 'doctor'")
            writer.commit()
        return connect_live(path, **kwargs)

    monkeypatch.setattr(db, "connect_live", connect_after_seed_loss)
    trace_sql.clear()
    with pytest.raises(db.ReleaseAdmissionError, match="seed identities are missing"):
        db.connect_release(live_wal)
    with closing(sqlite3.connect(live_wal)) as observer:
        assert observer.execute("SELECT id FROM roles WHERE id = 'doctor'").fetchone() is None
    assert not any(_schema_preparation(statements) for statements in trace_sql)


@pytest.mark.parametrize("change", ["main_trigger", "temporary_trigger", "seed", "permit"])
def test_release_recheck_rejects_changes_after_fast_admission(live_wal, trace_sql, change):
    from backend.app import db, operations_state

    path = live_wal
    with _release_connection(path) as (conn, admission):
        assert admission is not None
        release_sql = trace_sql[-1]
        if change == "main_trigger":
            with closing(sqlite3.connect(path)) as writer:
                writer.execute(
                    "CREATE TRIGGER trg_test_observer AFTER INSERT ON audit_events BEGIN SELECT 1; END"
                )
                writer.commit()
        elif change == "temporary_trigger":
            conn.execute(
                "CREATE TEMP TRIGGER trg_test_observer AFTER INSERT ON main.audit_events BEGIN SELECT 1; END"
            )
        elif change == "seed":
            with closing(sqlite3.connect(path)) as writer:
                writer.execute("DELETE FROM roles WHERE id = 'doctor'")
                writer.commit()
        else:
            (path.parent / operations_state.PERMIT).unlink()
        conn.execute("BEGIN IMMEDIATE")
        before_recheck = len(release_sql)
        try:
            with pytest.raises((db.ReleaseAdmissionError, operations_state.AdmissionRequired)):
                db.require_release_admission(conn, admission)
        finally:
            conn.rollback()
        if change == "seed":
            assert conn.execute("SELECT 1 FROM roles WHERE id = 'doctor'").fetchone() is None
            assert len(_schema_preparation(release_sql[before_recheck:])) == 0


def test_release_refuses_missing_anchored_sidecar_before_sqlite_open(live_wal, trace_sql):
    from backend.app import operations_state

    sidecar = live_wal.with_name(live_wal.name + "-shm")
    displaced = sidecar.with_name(sidecar.name + ".test-displaced")
    sidecar.rename(displaced)
    try:
        with pytest.raises(operations_state.AdmissionRequired, match="database_identity_changed"):
            with _release_connection(live_wal):
                pytest.fail("database with missing anchored sidecar was admitted")
        assert not trace_sql
        assert not sidecar.exists()
    finally:
        displaced.rename(sidecar)


def test_release_recheck_refuses_sidecar_identity_change(live_wal):
    from backend.app import db, operations_state

    sidecar = live_wal.with_name(live_wal.name + "-shm")
    displaced = sidecar.with_name(sidecar.name + ".test-displaced")
    with _release_connection(live_wal) as (conn, admission):
        assert admission is not None
        conn.execute("BEGIN IMMEDIATE")
        sidecar.rename(displaced)
        try:
            with pytest.raises(operations_state.AdmissionRequired, match="database_identity_changed"):
                db.require_release_admission(conn, admission)
        finally:
            displaced.rename(sidecar)
            conn.rollback()


def test_concurrent_release_setup_does_not_request_writer_lock(live_wal, monkeypatch):
    from backend.app import config

    monkeypatch.setattr(config, "BUSY_TIMEOUT_MS", 0)
    start = Barrier(3, timeout=5)

    def admit():
        start.wait()
        with _release_connection(live_wal) as (conn, admission):
            assert admission is not None
            return conn.execute("SELECT version FROM schema_version").fetchone()[0]

    with closing(sqlite3.connect(live_wal)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(admit)
                second = pool.submit(admit)
                start.wait()
                assert [first.result(timeout=5), second.result(timeout=5)] == [config.SCHEMA_VERSION] * 2
        finally:
            writer.rollback()


def test_medical_read_persists_one_audit_transaction_without_schema_preparation(auth_client, trace_sql):
    from .conftest import get_conn

    # Warm the ordinary dependency path, which retains its existing initialization.
    assert auth_client.get("/api/auth/me").status_code == 200
    trace_sql.clear()
    response = auth_client.get("/api/patients?view=page&limit=50")
    assert response.status_code == 200
    response_trace = _normalized(sql for trace in trace_sql for sql in trace)
    assert len(_schema_preparation(response_trace)) == 0
    assert sum(sql == "BEGIN IMMEDIATE" for sql in response_trace) == 1

    audit_rows = get_conn().execute(
        """SELECT m.snapshot_sha256
           FROM audit_events e JOIN read_access_manifests m ON m.audit_event_id = e.id
           WHERE e.event_type='medical_read_release' AND e.detail='operation=GET /api/patients'"""
    ).fetchall()
    assert [row[0] for row in audit_rows] == [hashlib.sha256(response.content).hexdigest()]


def test_post_admission_drift_withholds_response_and_leaves_no_audit_pair(auth_client, monkeypatch):
    from backend.app import db
    from .conftest import get_conn

    connect_release = db.connect_release
    admissions = []

    def connect_with_catalog_change(path):
        conn, admission = connect_release(path)
        admissions.append(admission)
        conn.execute("CREATE TEMP TABLE test_observer (id INTEGER)")
        return conn, admission

    def release_counts():
        return tuple(get_conn().execute(
            """SELECT (SELECT COUNT(*) FROM audit_events WHERE event_type='medical_read_release'),
                      (SELECT COUNT(*) FROM read_access_manifests)"""
        ).fetchone())

    before = release_counts()
    monkeypatch.setattr(db, "connect_release", connect_with_catalog_change)
    response = auth_client.get("/api/patients?view=page&limit=50")
    assert len(admissions) == 1 and admissions[0] is not None
    assert response.status_code == 503
    assert response.json() == {
        "detail": "Read audit unavailable",
        "code": "READ_ADMISSION_UNAVAILABLE",
    }
    assert release_counts() == before


def test_parallel_scoped_reads_serialize_their_own_audits(auth_client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from .conftest import get_conn

    assert auth_client.get("/api/auth/me").status_code == 200
    start = Barrier(5)

    def read():
        start.wait()
        return auth_client.get("/api/patients?view=page&limit=50")

    with ThreadPoolExecutor(max_workers=4) as workers:
        futures = [workers.submit(read) for _ in range(4)]
        start.wait()
        replies = [future.result(timeout=10) for future in futures]
    assert [reply.status_code for reply in replies] == [200] * 4
    audits = get_conn().execute(
        """SELECT m.audit_event_id, m.snapshot_sha256
           FROM audit_events e JOIN read_access_manifests m ON m.audit_event_id = e.id
           WHERE e.event_type='medical_read_release' AND e.detail='operation=GET /api/patients'"""
    ).fetchall()
    assert len({row[0] for row in audits}) == 4
    assert sorted(row[1] for row in audits) == sorted(hashlib.sha256(reply.content).hexdigest() for reply in replies)
