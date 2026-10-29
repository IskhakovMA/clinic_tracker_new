"""Owner decision 1: per-treating-doctor queue cap and fair claiming.

Regression tests for preprod-run-3 finding
backend/app/routes/notifications.py:create_manual:unbounded-manual-queue-rows:
event reminders were capped only per patient and claimed strictly by
scheduled_at, so one doctor could fill the shared queue and push every other
doctor's reminders behind their backlog.
"""
from datetime import datetime, timedelta, timezone

from .conftest import create_account, get_conn
from .test_notifications import _create_patient_direct, _grant


def _notification_now():
    from backend.app.services import notifications
    return notifications.utcnow()


def _iso(delta):
    return (_notification_now() + delta).isoformat()


def _event(days=2, hour="10:00"):
    d = (_notification_now() + timedelta(days=days)).date().isoformat()
    return {"date": d, "time": hour, "treatmentType": "", "actualDate": None, "actualTime": None}


def _patient_for(auth_client, doctor_id, *, days, hour="10:00", name="Очередь Тест"):
    pid = _create_patient_direct(name=name, events=[_event(days=days, hour=hour)])
    conn = get_conn()
    with conn:
        conn.execute("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (doctor_id, pid))
    _grant(auth_client, pid)
    return pid


def _generate(auth_client):
    r = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf)
    assert r.status_code == 200, r.text
    return r.json()


def _pending_for(doctor_id):
    return get_conn().execute(
        """SELECT COUNT(*) FROM notifications n JOIN patients p ON p.id = n.patient_id
           WHERE n.status = 'pending' AND n.source_type = 'event' AND p.treating_doctor_user_id = ?""",
        (doctor_id,),
    ).fetchone()[0]


def test_pending_event_rows_are_capped_per_treating_doctor(auth_client, monkeypatch):
    from backend.app import config
    monkeypatch.setattr(config, "NOTIFICATION_MAX_PENDING_EVENT_PER_DOCTOR", 3)
    doc = create_account("doc.queue.cap", "doctor")
    for i in range(5):
        _patient_for(auth_client, doc, days=2 + i, name=f"Пациент Один {i}")
    out = _generate(auth_client)
    assert _pending_for(doc) == 3, out
    assert out["skipped"] >= 2


def test_cap_is_per_doctor_so_another_doctor_is_unaffected(auth_client, monkeypatch):
    from backend.app import config
    monkeypatch.setattr(config, "NOTIFICATION_MAX_PENDING_EVENT_PER_DOCTOR", 2)
    a = create_account("doc.queue.a", "doctor")
    b = create_account("doc.queue.b", "doctor")
    for i in range(4):
        _patient_for(auth_client, a, days=2 + i, name=f"Пациент А {i}")
    _patient_for(auth_client, b, days=3, name="Пациент Б")
    _generate(auth_client)
    assert _pending_for(a) == 2
    assert _pending_for(b) == 1


def test_claiming_alternates_between_doctors(auth_client):
    """One doctor's older backlog must not be drained before another doctor's
    single due reminder."""
    from backend.app.services import notifications
    a = create_account("doc.fair.a", "doctor")
    b = create_account("doc.fair.b", "doctor")
    pa = [_patient_for(auth_client, a, days=2 + i, name=f"Фэйр А {i}") for i in range(3)]
    pb = _patient_for(auth_client, b, days=9, name="Фэйр Б")
    _generate(auth_client)
    conn = get_conn()
    # Every row is due; doctor A's rows are scheduled earlier than doctor B's.
    with conn:
        for i, pid in enumerate(pa):
            conn.execute(
                """UPDATE notifications SET scheduled_at = ?, next_attempt_at = ?
                   WHERE patient_id = ?""",
                (_iso(timedelta(hours=-10 + i)), _iso(timedelta(minutes=-5)), pid),
            )
        conn.execute(
            """UPDATE notifications SET scheduled_at = ?, next_attempt_at = ?
               WHERE patient_id = ?""",
            (_iso(timedelta(hours=-1)), _iso(timedelta(minutes=-5)), pb),
        )
    first = notifications.claim_next(conn)
    second = notifications.claim_next(conn)
    assert first is not None and second is not None
    doctors = []
    for row in (first, second):
        doctors.append(conn.execute(
            "SELECT treating_doctor_user_id FROM patients WHERE id = ?", (row["patient_id"],)
        ).fetchone()[0])
    assert set(doctors) == {a, b}, doctors


def test_ordinary_single_doctor_queue_keeps_earliest_first(auth_client):
    from backend.app.services import notifications
    doc = create_account("doc.order", "doctor")
    early = _patient_for(auth_client, doc, days=2, name="Раньше")
    late = _patient_for(auth_client, doc, days=5, name="Позже")
    _generate(auth_client)
    conn = get_conn()
    with conn:
        conn.execute("UPDATE notifications SET next_attempt_at = ?", (_iso(timedelta(minutes=-5)),))
    first = notifications.claim_next(conn)
    assert first["patient_id"] == early
    second = notifications.claim_next(conn)
    assert second["patient_id"] == late


def test_backlog_of_one_doctor_does_not_starve_a_drain(auth_client):
    """A short drain must still deliver another doctor's reminder."""
    from backend.app.notifications.mock_provider import MockNotificationProvider
    from backend.app.services import notifications
    a = create_account("doc.starve.a", "doctor")
    b = create_account("doc.starve.b", "doctor")
    backlog = [_patient_for(auth_client, a, days=2 + i, name=f"Бэклог {i}") for i in range(5)]
    victim = _patient_for(auth_client, b, days=8, name="Жертва")
    _generate(auth_client)
    conn = get_conn()
    with conn:
        for i, pid in enumerate(backlog):
            conn.execute("UPDATE notifications SET scheduled_at = ?, next_attempt_at = ? WHERE patient_id = ?",
                         (_iso(timedelta(hours=-20 + i)), _iso(timedelta(minutes=-5)), pid))
        conn.execute("UPDATE notifications SET scheduled_at = ?, next_attempt_at = ? WHERE patient_id = ?",
                     (_iso(timedelta(hours=-1)), _iso(timedelta(minutes=-5)), victim))
    notifications.process_due(conn, limit=2, provider=MockNotificationProvider("success"))
    sent = conn.execute(
        "SELECT status FROM notifications WHERE patient_id = ?", (victim,)
    ).fetchone()[0]
    assert sent == "sent"
