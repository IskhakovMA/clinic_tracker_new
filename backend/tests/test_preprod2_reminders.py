"""Pre-production hardening, round 2: event reminders follow their event.

Regression tests for preprod-run-2 leads NV2-07 (a queued event reminder was
still sent after its event was deleted, rescheduled, completed or the
patient was closed), NV2-09 (event reminders bypassed the queue limits via
past- or far-dated events) and the event count caps (NV2-08).
"""
from datetime import datetime, timedelta, timezone

from .conftest import get_conn, make_patient_payload, event_delete, event_patch, stage_body, event_add_body
from .test_notifications import _create_patient_direct, _due_all, _grant


def _event(days=2, hour="10:00"):
    d = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
    return {"date": d, "time": hour, "treatmentType": "", "actualDate": None, "actualTime": None}


def _queued(conn, pid):
    return [dict(r) for r in conn.execute(
        "SELECT id, status, error_code FROM notifications WHERE patient_id = ? AND source_type = 'event'", (pid,))]


def _setup(auth_client):
    pid = _create_patient_direct(events=[_event()])
    _grant(auth_client, pid)
    r = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf)
    assert r.json()["created"] == 1
    return pid


def _process(conn):
    from backend.app.notifications.mock_provider import MockNotificationProvider
    from backend.app.services import notifications
    _due_all(conn)
    return notifications.process_due(conn, limit=10, provider=MockNotificationProvider("success"))


def _sent(conn, pid):
    return [q for q in _queued(conn, pid) if q["status"] == "sent"]


def test_deleted_event_reminder_is_not_sent(auth_client):
    pid = _setup(auth_client)
    assert event_delete(auth_client, pid).status_code == 200
    conn = get_conn()
    _process(conn)
    assert _sent(conn, pid) == []


def test_rescheduled_event_old_reminder_is_not_sent(auth_client):
    pid = _setup(auth_client)
    r = event_patch(auth_client, pid, "date", _event(days=20)["date"])
    assert r.status_code == 200
    conn = get_conn()
    _process(conn)
    assert _sent(conn, pid) == []


def test_completed_event_and_closed_patient_reminders_are_not_sent(auth_client):
    pid = _setup(auth_client)
    r = event_patch(auth_client, pid, "actualDate", _clinic_today())
    assert r.status_code == 200
    conn = get_conn()
    _process(conn)
    assert _sent(conn, pid) == []

    pid2 = _setup(auth_client)
    r = auth_client.post(f"/api/patients/{pid2}/stage", json=stage_body(auth_client, pid2, {"newStage": "completed"}), headers=auth_client.csrf)
    assert r.status_code == 200
    _process(conn)
    assert _sent(conn, pid2) == []


def test_unchanged_event_reminder_is_sent_with_the_appointment_time(auth_client, monkeypatch):
    from backend.app.notifications import mock_provider
    bodies = []
    real = mock_provider.MockNotificationProvider.send

    def capture(self, ctx):
        bodies.append(ctx.body)
        return real(self, ctx)

    monkeypatch.setattr(mock_provider.MockNotificationProvider, "send", capture)
    ev = _event(days=2, hour="10:00")
    pid = _create_patient_direct(events=[ev])
    _grant(auth_client, pid)
    auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf)
    conn = get_conn()
    _process(conn)
    assert len(_sent(conn, pid)) == 1
    assert bodies and "10:00" in bodies[0]
    d = datetime.fromisoformat(ev["date"])
    assert d.strftime("%Y-%m-%d") in bodies[0] or d.strftime("%d.%m.%Y") in bodies[0], bodies[0]


def test_past_and_far_future_events_do_not_enter_the_queue(auth_client):
    pid = _create_patient_direct(events=[_event(days=-3), {**_event(days=500)}, {"date": "0001-01-02", "time": "09:00"}])
    _grant(auth_client, pid)
    r = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf)
    assert r.json()["created"] == 0
    assert _queued(get_conn(), pid) == []


def test_event_count_caps_are_enforced(auth_client, monkeypatch):
    from backend.app import config
    monkeypatch.setattr(config, "MAX_EVENTS_PER_HISTORY_ENTRY", 3)
    r = auth_client.post("/api/patients", json=make_patient_payload(events=[_event()] * 4), headers=auth_client.csrf)
    assert r.status_code == 422
    pid = auth_client.post("/api/patients", json=make_patient_payload(events=[_event()] * 3), headers=auth_client.csrf).json()["id"]
    r = auth_client.post(f"/api/patients/{pid}/history/0/events", json=event_add_body(auth_client, pid, 0), headers=auth_client.csrf)
    assert r.status_code == 422


def test_history_entry_cap_is_enforced(auth_client, monkeypatch):
    from backend.app import config
    monkeypatch.setattr(config, "MAX_HISTORY_ENTRIES_PER_PATIENT", 2)
    pid = auth_client.post("/api/patients", json=make_patient_payload(stage="exam"), headers=auth_client.csrf).json()["id"]
    assert auth_client.post(f"/api/patients/{pid}/stage", json=stage_body(auth_client, pid, {"newStage": "consult"}), headers=auth_client.csrf).status_code == 200
    r = auth_client.post(f"/api/patients/{pid}/stage", json=stage_body(auth_client, pid, {"newStage": "exam"}), headers=auth_client.csrf)
    assert r.status_code == 422


def _clinic_today() -> str:
    """WP-C: факт «событие выполнено сегодня» — дата учреждения (в тестах часы учреждения
    закреплены, реальная дата может быть позже: такой «факт» был бы в будущем)."""
    from backend.app import clock
    return clock.today_moscow().isoformat()
