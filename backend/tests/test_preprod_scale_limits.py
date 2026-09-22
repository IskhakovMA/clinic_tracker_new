"""Pre-production hardening: scope-wide reads do not depend on SQLite's
bound-variable limit, and init_db does not rescan the notification table.

Regression tests for audit leads NV-15 (data-quality evaluation bound one
SQL variable per history entry in the whole scope) and NV-14 (every
request re-read every notification idempotency key).
"""
import sqlite3

from .conftest import get_conn, make_patient_payload


def _seed_patients(n: int, extra_history: int = 2):
    from backend.app import store
    from backend.app.models import PatientCreateIn
    conn = get_conn()
    ids = []
    for i in range(n):
        pid = store.create_patient(conn, PatientCreateIn(**make_patient_payload(name=f"Масштабов Пациент {i}")), None)
        for s in range(extra_history):
            with conn:
                conn.execute(
                    "INSERT INTO history_entries (id, patient_id, seq, stage, entered_at) VALUES (?, ?, ?, 'exam', '2026-01-01T00:00:00+00:00')",
                    (store.new_id(), pid, s + 1),
                )
        ids.append(pid)
    return ids


def test_scope_wide_reads_survive_low_variable_limit(auth_client):
    from backend.app import db, policy
    from backend.app.services import data_quality, patient_attention
    ids = _seed_patients(30)
    conn = db._connect(db.config.DB_PATH)
    conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 40)
    user = policy.load_current_user(conn, conn.execute("SELECT id FROM users WHERE role_id = 'developer'").fetchone()[0])
    report = data_quality.evaluate(conn, user)
    assert isinstance(report, dict)
    assert patient_attention.bulk_summary(conn, ids) is not None
    assert patient_attention.active_patient_ids(conn, set(ids)) == set()
    assert patient_attention.active_reason_breakdown(conn, set(ids))["total"] == 0
    conn.close()


def test_init_db_skips_key_scan_when_nothing_needs_backfill(auth_client, monkeypatch):
    from backend.app import db
    statements = []
    conn = db._connect(db.config.DB_PATH)
    conn.set_trace_callback(statements.append)
    db.init_db(conn)
    conn.close()
    assert not any("SELECT idempotency_key FROM notifications" in s for s in statements)


def test_requests_do_not_rerun_init_db(auth_client, monkeypatch):
    from backend.app import deps
    calls = []
    real = deps.init_db
    monkeypatch.setattr(deps, "init_db", lambda conn: (calls.append(1), real(conn)))
    for _ in range(3):
        assert auth_client.get("/api/auth/me").status_code == 200
    assert calls == []
