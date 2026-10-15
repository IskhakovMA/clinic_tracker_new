"""Pre-production hardening: one malformed event cannot abort reminder generation.

Regression tests for audit lead NV-05: a doctor could store an event time
such as "25:00" (or a date of 0001-01-01) through the event PATCH route;
the next global generate-due run then raised inside event_dt and stopped
before reaching other doctors' patients.
"""
from .conftest import event_patch
from .test_notifications import _create_patient_direct, _grant


def _future(month: int, day: int) -> str:
    """A distinct event date inside the reminder horizon (the queue skips
    past and far-future events)."""
    from datetime import date as _date, timedelta as _td
    return (_date.today() + _td(days=month * 31 + day)).isoformat()

GOOD_EVENT = {"date": _future(1, 2), "time": "10:00", "treatmentType": "", "actualDate": None, "actualTime": None}


def test_event_patch_rejects_out_of_range_time(auth_client):
    pid = _create_patient_direct(events=[dict(GOOD_EVENT)])
    for bad in ("25:00", "-1:00", "12:60", "7pm"):
        r = event_patch(auth_client, pid, "time", bad)
        assert r.status_code == 422, (bad, r.status_code)
    r = event_patch(auth_client, pid, "date", "not-a-date")
    assert r.status_code == 422
    ok = event_patch(auth_client, pid, "time", "23:59")
    assert ok.status_code == 200


def test_create_rejects_out_of_range_event_time(auth_client):
    from .conftest import make_patient_payload
    bad = dict(GOOD_EVENT, time="25:00")
    r = auth_client.post("/api/patients", json=make_patient_payload(events=[bad]), headers=auth_client.csrf)
    assert r.status_code == 422


def test_generation_skips_poisoned_rows_and_continues(auth_client):
    from .conftest import get_conn
    conn = get_conn()
    poisoned = []
    for _ in range(2):
        pid = _create_patient_direct(events=[dict(GOOD_EVENT)])
        _grant(auth_client, pid)
        poisoned.append(pid)
    # Rows written before validation existed (or by any other path).
    ev_ids = [r["id"] for r in conn.execute("SELECT id FROM events ORDER BY id")]
    with conn:
        conn.execute("UPDATE events SET time = '25:00' WHERE id = ?", (ev_ids[0],))
        conn.execute("UPDATE events SET date = '0001-01-01', time = '09:00' WHERE id = ?", (ev_ids[1],))
    healthy = _create_patient_direct(events=[dict(GOOD_EVENT)])
    _grant(auth_client, healthy)

    r = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    assert r.json()["skipped"] >= 2
    assert conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (healthy,)
    ).fetchone()[0] == 1


def test_event_dt_rejects_out_of_range_values():
    from backend.app.services.notifications import event_dt
    assert event_dt("2099-01-02", "25:00") is None
    assert event_dt("2099-01-02", "10:61") is None
    assert event_dt("2099-01-02", "10:30").hour == 10
