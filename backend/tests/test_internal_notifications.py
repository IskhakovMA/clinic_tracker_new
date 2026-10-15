"""Internal notification center: scoped computed inbox and read state."""
from __future__ import annotations

from datetime import date
import json

from .conftest import create_account, create_department, get_conn, login, make_patient_payload


def _as_user(client, username: str):
    r = login(client, username)
    assert r.status_code == 200, r.text
    return client


def _create_patient(doctor_id=None, **overrides):
    from backend.app import store
    from backend.app.models import PatientCreateIn

    return store.create_patient(get_conn(), PatientCreateIn(**make_patient_payload(**overrides)), doctor_id)


def _stand(client):
    dept_a = create_department("Inbox A")
    dept_b = create_department("Inbox B")
    dev = create_account("in.dev", "developer", "Разработчик Inbox")
    deputy = create_account("in.deputy", "deputy_chief_oncology", "Глобальный Просмотр")
    head_a = create_account("in.head.a", "department_head", "Заведующий Inbox A", department_ids=[dept_a])
    doc_a = create_account("in.doc.a", "doctor", "Врач Inbox A", department_ids=[dept_a])
    doc_b = create_account("in.doc.b", "doctor", "Врач Inbox B", department_ids=[dept_b])
    patient_a = _create_patient(
        doc_a,
        name="Пациент Inbox A",
        diagnosis="C34.1",
        policyNumber="7700000000000201",
        nosology="lung34",
        events=[{"date": "2026-08-01", "time": "10:00"}],
    )
    patient_b = _create_patient(
        doc_b,
        name="Пациент Inbox B",
        events=[{"date": "2026-08-12", "time": "10:00"}],
    )
    return {
        "dev": dev,
        "deputy": deputy,
        "head_a": head_a,
        "doc_a": doc_a,
        "doc_b": doc_b,
        "patient_a": patient_a,
        "patient_b": patient_b,
    }


def test_summary_and_list_are_scoped_by_doctor_head_and_global(client, monkeypatch):
    from backend.app.services import internal_notifications

    ids = _stand(client)
    monkeypatch.setattr(internal_notifications, "today_local", lambda: date(2026, 8, 12))

    _as_user(client, "in.doc.a")
    own = client.get("/api/internal-notifications").json()
    assert own["total"] >= 1
    assert {item["patient_id"] for item in own["items"]} == {ids["patient_a"]}
    assert all("7700000000000201" not in str(item) and "C34.1" not in str(item) for item in own["items"])
    summary = client.get("/api/internal-notifications/summary").json()
    assert summary["unread_count"] == own["total"]
    assert summary["overdue_count"] >= 1

    _as_user(client, "in.head.a")
    head = client.get("/api/internal-notifications").json()
    assert {item["patient_id"] for item in head["items"]} == {ids["patient_a"]}

    _as_user(client, "in.deputy")
    global_items = client.get("/api/internal-notifications").json()["items"]
    assert {ids["patient_a"], ids["patient_b"]} <= {item["patient_id"] for item in global_items}


def test_categories_read_state_csrf_idempotency_and_lifecycle(client, monkeypatch):
    from backend.app import store
    from backend.app.services import internal_notifications

    dept = create_department("Inbox Lifecycle")
    doc = create_account("in.life.doc", "doctor", "Врач Lifecycle", department_ids=[dept])
    _create_patient(
        doc,
        name="Просрочка Lifecycle",
        events=[{"date": "2026-08-01", "time": "09:00"}],
        nosology="lung34",
    )
    today_pid = _create_patient(doc, name="Сегодня Lifecycle", events=[{"date": "2026-08-12"}], nosology="")
    _create_patient(doc, name="Скоро Lifecycle", events=[{"date": "2026-08-14"}], nosology="lung34")

    _as_user(client, "in.life.doc")
    monkeypatch.setattr(internal_notifications, "today_local", lambda: date(2026, 8, 12))
    summary = client.get("/api/internal-notifications/summary").json()
    assert summary["overdue_count"] >= 1
    assert summary["today_count"] >= 1
    assert summary["upcoming_count"] >= 1
    assert summary["checklist_count"] >= 1
    assert summary["quality_count"] >= 1

    first = client.get("/api/internal-notifications?drawer_filter=overdue").json()["items"][0]
    assert client.post(f"/api/internal-notifications/{first['id']}/read").status_code == 403
    read = client.post(f"/api/internal-notifications/{first['id']}/read", json={}, headers=client.csrf)
    assert read.status_code == 200, read.text
    again = client.post(f"/api/internal-notifications/{first['id']}/read", json={}, headers=client.csrf)
    assert again.status_code == 200
    assert client.get("/api/internal-notifications/summary").json()["overdue_count"] >= 1

    all_read = client.post("/api/internal-notifications/read-all", json={}, headers=client.csrf)
    assert all_read.status_code == 200
    assert client.get("/api/internal-notifications/summary").json()["unread_count"] == 0
    first_seen = get_conn().execute(
        "SELECT COUNT(*) AS c FROM internal_notification_state WHERE user_id = ?", (doc,)
    ).fetchone()["c"]
    client.get("/api/internal-notifications")
    second_seen = get_conn().execute(
        "SELECT COUNT(*) AS c FROM internal_notification_state WHERE user_id = ?", (doc,)
    ).fetchone()["c"]
    assert second_seen == first_seen

    conn = get_conn()
    event_id = conn.execute(
        """SELECT e.id FROM events e
           JOIN history_entries h ON h.id = e.history_entry_id
           WHERE h.patient_id = ?""",
        (today_pid,),
    ).fetchone()["id"]
    version = conn.execute("SELECT version FROM events WHERE id = ?", (event_id,)).fetchone()["version"]
    store.update_event(conn, today_pid, event_id, "actualDate", "2026-08-12", version)
    today = client.get("/api/internal-notifications?drawer_filter=today").json()["items"]
    assert today == []


def test_foreign_notification_cannot_be_read_and_audit_has_no_phi(client, monkeypatch):
    from backend.app.services import internal_notifications

    ids = _stand(client)
    monkeypatch.setattr(internal_notifications, "today_local", lambda: date(2026, 8, 12))

    _as_user(client, "in.doc.b")
    foreign_id = client.get("/api/internal-notifications").json()["items"][0]["id"]

    _as_user(client, "in.doc.a")
    assert client.post(f"/api/internal-notifications/{foreign_id}/read", json={}, headers=client.csrf).status_code == 404
    own = client.get("/api/internal-notifications").json()["items"][0]
    assert client.post(f"/api/internal-notifications/{own['id']}/read", json={}, headers=client.csrf).status_code == 200

    rows = get_conn().execute("SELECT event_type, detail FROM audit_events ORDER BY id").fetchall()
    text = "\n".join(f"{r['event_type']} {r['detail'] or ''}" for r in rows)
    assert "internal_notification_read" in text
    for forbidden in ("Пациент Inbox", "7700000000000201", "C34.1", "Контрольное действие"):
        assert forbidden not in text


def test_empty_and_validation(client):
    create_account("in.empty", "doctor", "Пустой Врач")
    _as_user(client, "in.empty")
    empty = client.get("/api/internal-notifications").json()
    assert empty["items"] == []
    assert empty["summary"]["unread_count"] == 0
    assert client.get("/api/internal-notifications?type=bad").status_code == 422
    assert client.get("/api/internal-notifications?limit=101").status_code == 422
    assert client.post("/api/internal-notifications/read-all", json={"extra": True}, headers=client.csrf).status_code == 422


def test_internal_notifications_smoke_1000_fake_patients(client, monkeypatch):
    from backend.app.services import data_quality, internal_notifications

    dept = create_department("Inbox Perf")
    doc = create_account("in.perf.doc", "doctor", "Врач Perf", department_ids=[dept])
    conn = get_conn()
    now = "2026-08-12T08:00:00+00:00"
    patients = []
    history = []
    events = []
    for i in range(1000):
        pid = f"in-perf-{i:04d}"
        hid = f"in-perf-h-{i:04d}"
        patients.append((
            pid,
            f"Пациент Perf {i:04d}",
            50,
            "normal",
            "exam",
            f"+7900000{i:04d}",
            f"7700000000{i:06d}",
            "",
            "C34.1",
            "lung34",
            "",
            now,
            "",
            "",
            now,
            now,
            doc,
        ))
        history.append((hid, pid, 0, "exam", now, json.dumps({}), ""))
        events.append((f"in-perf-e-{i:04d}", hid, 0, "2026-08-11", "09:00", "", "", "", 0, 0, now))
    conn.executemany(
        """INSERT INTO patients
           (id, name, age, priority, stage, phone, policy_number, complaint, diagnosis,
            nosology, disease_stage, arrival, doctor, head, created_at, updated_at,
            treating_doctor_user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        patients,
    )
    conn.executemany(
        """INSERT INTO history_entries
           (id, patient_id, seq, stage, entered_at, checklist_json, note)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        history,
    )
    conn.executemany(
        """INSERT INTO events
           (id, history_entry_id, seq, date, time, treatment_type, actual_date,
            actual_time, matched_plan, no_plan, set_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        events,
    )
    conn.commit()

    _as_user(client, "in.perf.doc")
    monkeypatch.setattr(internal_notifications, "today_local", lambda: date(2026, 8, 12))
    summary = client.get("/api/internal-notifications/summary")
    page = client.get("/api/internal-notifications?limit=100")
    assert summary.status_code == 200
    assert page.status_code == 200
    assert summary.json()["overdue_count"] == 1000
    assert len(page.json()["items"]) == 100

    before = conn.execute("SELECT COUNT(*) AS c FROM internal_notification_state").fetchone()["c"]
    from backend.app.policy import load_current_user

    current_user = load_current_user(conn, doc)
    quality = data_quality.evaluate(conn, current_user, today=date(2026, 8, 12))
    after = conn.execute("SELECT COUNT(*) AS c FROM internal_notification_state").fetchone()["c"]
    assert quality["summary"]["total_patients"] == 1000
    assert after == before
