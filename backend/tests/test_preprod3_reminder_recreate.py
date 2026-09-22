"""Pre-production hardening, round 3: a cancelled reminder does not block its event.

Regression tests for preprod-run-3 candidate
store.cancel-event-reminders/notifications.idempotency-key:cancelled-event-reminder-never-recreated:
round-2 cancellation left the cancelled row holding the idempotency key, so an
event that returned to the same time (or was edited without changing its
time) never got a reminder again.
"""
from datetime import datetime, timedelta, timezone

from .conftest import get_conn
from .test_preprod2_reminders import _event, _queued, _setup


def _pending(pid):
    return [q for q in _queued(get_conn(), pid) if q["status"] == "pending"]


def _patch(auth_client, pid, field, value):
    r = auth_client.patch(f"/api/patients/{pid}/history/0/events/0",
                          json={"field": field, "value": value}, headers=auth_client.csrf)
    assert r.status_code == 200, r.text


def _generate(auth_client):
    r = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf)
    assert r.status_code == 200
    return r.json()


def test_event_moved_away_and_back_gets_a_reminder_again(auth_client):
    pid = _setup(auth_client)
    original = _event()["date"]
    _patch(auth_client, pid, "date", _event(days=20)["date"])
    _patch(auth_client, pid, "date", original)
    _generate(auth_client)
    assert len(_pending(pid)) == 1


def test_cleared_actual_date_restores_the_reminder(auth_client):
    pid = _setup(auth_client)
    _patch(auth_client, pid, "actualDate", datetime.now(timezone.utc).date().isoformat())
    _generate(auth_client)
    assert _pending(pid) == []
    _patch(auth_client, pid, "actualDate", "")
    _generate(auth_client)
    assert len(_pending(pid)) == 1


def test_editing_without_changing_the_reminder_time_keeps_it(auth_client):
    pid = _setup(auth_client)
    _patch(auth_client, pid, "date", _event()["date"])
    _patch(auth_client, pid, "actualTime", "10:00")
    _generate(auth_client)
    assert len(_pending(pid)) == 1


def test_revoked_then_regranted_consent_queues_the_reminder_again(auth_client):
    from .test_notifications import _grant
    pid = _setup(auth_client)
    r = auth_client.put(f"/api/notifications/patients/{pid}/preferences",
                        json={"consentStatus": "revoked", "smsEnabled": False}, headers=auth_client.csrf)
    assert r.status_code == 200, r.text
    assert _pending(pid) == []
    _grant(auth_client, pid)
    _generate(auth_client)
    assert len(_pending(pid)) == 1


def test_sent_reminder_is_still_deduplicated(auth_client):
    from .test_preprod2_reminders import _process
    pid = _setup(auth_client)
    conn = get_conn()
    _process(conn)
    assert [q["status"] for q in _queued(conn, pid)] == ["sent"]
    out = _generate(auth_client)
    assert out["created"] == 0
    assert len(_queued(conn, pid)) == 1


def test_manually_cancelled_reminder_stays_cancelled(auth_client):
    pid = _setup(auth_client)
    nid = get_conn().execute("SELECT id FROM notifications WHERE patient_id = ?", (pid,)).fetchone()["id"]
    assert auth_client.post(f"/api/notifications/{nid}/cancel", headers=auth_client.csrf).status_code == 200
    assert _generate(auth_client)["created"] == 0
    assert _pending(pid) == []


def test_existing_database_index_is_migrated(auth_client):
    from backend.app import db
    conn = get_conn()
    conn.execute("DROP INDEX idx_notifications_idempotency_key")
    conn.execute("CREATE UNIQUE INDEX idx_notifications_idempotency_key ON notifications(idempotency_key)")
    conn.commit()
    db.init_db(conn)
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'idx_notifications_idempotency_key'").fetchone()[0]
    assert "error_code" in sql


def test_reminder_for_an_appointment_already_past_is_not_sent(auth_client):
    """preprod-run-3 create_manual:unbounded-manual-queue-rows (send side): a
    backlog drained after the appointment must not still send the reminder."""
    from datetime import datetime, timedelta, timezone
    from .test_preprod2_reminders import _process
    pid = _setup(auth_client)
    conn = get_conn()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    conn.execute("UPDATE events SET date = ? WHERE id IN (SELECT source_id FROM notifications WHERE patient_id = ?)",
                 (past, pid))
    conn.commit()
    _process(conn)
    rows = _queued(conn, pid)
    assert [r["status"] for r in rows] == ["skipped"]
    assert rows[0]["error_code"] == "event_passed"
