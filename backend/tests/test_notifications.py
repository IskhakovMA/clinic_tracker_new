"""Notifications MVP: consent, queue, worker, RBAC scope and PHI-safe audit."""
from __future__ import annotations
from backend.app import config

import json
import inspect
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from .conftest import create_account, create_department, get_conn, login, make_patient_payload, current_schema_version



def _future(month: int, day: int) -> str:
    """A distinct event date inside the reminder horizon (the queue skips
    past and far-future events)."""
    from datetime import date as _date, timedelta as _td
    from backend.app import clock
    return (clock.today_moscow() + _td(days=month * 31 + day)).isoformat()

def _now_plus(hours: int = 1) -> str:
    from backend.app.services import notifications
    return (notifications.utcnow() + timedelta(hours=hours)).isoformat()


def _now_minus(hours: int = 1) -> str:
    from backend.app.services import notifications
    return (notifications.utcnow() - timedelta(hours=hours)).isoformat()


def _create_patient_direct(name="Уведомлений Пациент", phone="+7 (999) 111-22-33", doctor_id=None, events=None):
    from backend.app import store
    from backend.app.models import PatientCreateIn

    payload = make_patient_payload(name=name, phone=phone, events=events or [], treatingDoctorUserId=doctor_id)
    return store.create_patient(get_conn(), PatientCreateIn(**payload), doctor_id)


def _grant(client, patient_id: str):
    r = client.put(
        f"/api/notifications/patients/{patient_id}/preferences",
        json={"smsEnabled": True, "consentStatus": "granted"},
        headers=client.csrf,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _manual(client, patient_id: str, scheduled_at: str | None = None, template="custom_reminder"):
    return client.post(
        "/api/notifications",
        json={"patientId": patient_id, "templateCode": template, "scheduledAt": scheduled_at or _now_plus(1)},
        headers=client.csrf,
    )


def _due_all(conn):
    with conn:
        conn.execute("UPDATE notifications SET next_attempt_at = ? WHERE status = 'pending'",
                     (_now_minus(1),))


def test_v2_like_database_migrates_to_v3_notifications_tables(auth_client):
    from backend.app import db, maintenance

    conn = get_conn()
    with conn:
        for table in ("notifications", "notification_templates", "patient_notification_preferences"):
            conn.execute(f"DROP TABLE {table}")
        from .conftest import strip_gq_v33
        strip_gq_v33(conn)  # Reconstruct only pre-GQ technical layout.
        conn.execute("UPDATE schema_version SET version = 2")

    db.init_db(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == current_schema_version()
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"notifications", "notification_templates", "patient_notification_preferences"} <= tables
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(notifications)").fetchall()}
    assert {"idempotency_key", "processing_started_at"} <= columns
    indexes = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_notifications_idempotency_key" in indexes
    assert conn.execute("SELECT COUNT(*) FROM notification_templates").fetchone()[0] >= 3
    db.init_db(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == current_schema_version()
    report = maintenance.check_database(Path(conn.execute("PRAGMA database_list").fetchone()["file"]))
    assert report["ok"] is True, report["problems"]


def test_consent_is_separate_from_phone_and_revoke_cancels_pending(auth_client):
    pid = _create_patient_direct()

    detail = auth_client.get(f"/api/notifications/patients/{pid}").json()
    assert detail["preference"]["consent_status"] == "not_recorded"
    assert detail["preference"]["sms_enabled"] is False

    no_consent = _manual(auth_client, pid)
    assert no_consent.status_code == 422
    assert "no_consent" in no_consent.text

    pref = _grant(auth_client, pid)
    assert pref["consent_status"] == "granted"
    created = _manual(auth_client, pid)
    assert created.status_code == 201, created.text

    revoke = auth_client.put(
        f"/api/notifications/patients/{pid}/preferences",
        json={"smsEnabled": False, "consentStatus": "revoked"},
        headers=auth_client.csrf,
    )
    assert revoke.status_code == 200
    row = get_conn().execute("SELECT status, error_code FROM notifications WHERE patient_id = ?", (pid,)).fetchone()
    assert dict(row) == {"status": "cancelled", "error_code": "no_consent"}


def test_manual_create_validation_template_and_csrf(auth_client, client):
    pid = _create_patient_direct()
    _grant(auth_client, pid)

    assert auth_client.post("/api/notifications", json={}).status_code == 403
    assert auth_client.post(
        "/api/notifications",
        json={"patientId": pid, "templateCode": "custom_reminder", "scheduledAt": _now_plus(1)},
    ).status_code == 403
    assert _manual(auth_client, pid, _now_minus(1)).status_code == 422

    bad_template = auth_client.put(
        "/api/notifications/templates/custom_reminder",
        json={"body": "Диагноз {diagnosis}", "enabled": True},
        headers=auth_client.csrf,
    )
    assert bad_template.status_code == 422


def test_automatic_generation_is_idempotent_and_reschedules(auth_client):
    event = {"date": _future(1, 2), "time": "10:00", "treatmentType": "", "actualDate": None, "actualTime": None}
    pid = _create_patient_direct(events=[event])
    _grant(auth_client, pid)

    first = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf)
    second = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["created"] == 1
    assert second.json()["duplicates"] == 1
    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (pid,)).fetchone()[0] == 1

    event_id = conn.execute("SELECT id FROM events").fetchone()["id"]
    with conn:
        conn.execute("UPDATE events SET date = '" + _future(1, 3) + "' WHERE id = ?", (event_id,))
    changed = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()
    assert changed["created"] == 1
    statuses = [r["status"] for r in conn.execute("SELECT status FROM notifications WHERE patient_id = ? ORDER BY created_at", (pid,))]
    assert statuses == ["cancelled", "pending"]


def test_generation_idempotent_after_sent_cancelled_and_failed(auth_client):
    from backend.app.notifications.mock_provider import MockNotificationProvider
    from backend.app.services import notifications

    conn = get_conn()

    sent_pid = _create_patient_direct(name="Sent Event", events=[{"date": _future(3, 1), "time": "10:00"}])
    _grant(auth_client, sent_pid)
    assert auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()["created"] == 1
    _due_all(conn)
    notifications.process_due(conn, limit=1, provider=MockNotificationProvider("success"))
    sent_again = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()
    assert sent_again["created"] == 0
    assert sent_again["duplicates"] >= 1
    assert conn.execute("SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (sent_pid,)).fetchone()[0] == 1

    cancelled_pid = _create_patient_direct(name="Cancelled Event", events=[{"date": _future(3, 2), "time": "10:00"}])
    _grant(auth_client, cancelled_pid)
    assert auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()["created"] >= 1
    cancelled_id = conn.execute("SELECT id FROM notifications WHERE patient_id = ?", (cancelled_pid,)).fetchone()["id"]
    assert auth_client.post(f"/api/notifications/{cancelled_id}/cancel", headers=auth_client.csrf).status_code == 200
    cancelled_again = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()
    assert cancelled_again["created"] == 0
    assert conn.execute("SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (cancelled_pid,)).fetchone()[0] == 1

    failed_pid = _create_patient_direct(name="Failed Event", events=[{"date": _future(3, 3), "time": "10:00"}])
    _grant(auth_client, failed_pid)
    assert auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()["created"] >= 1
    _due_all(conn)
    notifications.process_due(conn, limit=1, provider=MockNotificationProvider("permanent_failure"))
    failed_again = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()
    assert failed_again["created"] == 0
    assert conn.execute("SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (failed_pid,)).fetchone()[0] == 1


def test_generation_repeated_runs_and_parallel_create_share_business_key(auth_client):
    from backend.app.services import notifications

    pid = _create_patient_direct(events=[{"date": _future(4, 1), "time": "10:00"}])
    _grant(auth_client, pid)
    results = [auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json() for _ in range(10)]
    assert results[0]["created"] == 1
    assert sum(r["created"] for r in results) == 1
    assert get_conn().execute("SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (pid,)).fetchone()[0] == 1

    scheduled = _now_plus(10)
    first, first_reason = notifications.create_notification(
        get_conn(),
        patient_id=pid,
        template_code="custom_reminder",
        scheduled_at=scheduled,
        source_type="manual",
        source_id="parallel",
        created_by_user_id=None,
    )
    second, second_reason = notifications.create_notification(
        get_conn(),
        patient_id=pid,
        template_code="custom_reminder",
        scheduled_at=scheduled,
        source_type="manual",
        source_id="parallel",
        created_by_user_id=None,
    )
    assert first is not None
    assert first_reason is None
    assert second_reason == "duplicate"
    assert second["id"] == first["id"]
    key = first["idempotency_key"]
    assert get_conn().execute("SELECT COUNT(*) FROM notifications WHERE idempotency_key = ?", (key,)).fetchone()[0] == 1


def test_generation_skips_finished_cancelled_invalid_phone_no_consent_and_disabled_template(auth_client):
    _create_patient_direct(name="Нет Согласия", events=[{"date": _future(2, 1), "time": "09:00"}])
    invalid = _create_patient_direct(name="Плохой Телефон", phone="not-a-phone", events=[{"date": _future(2, 1), "time": "09:00"}])
    completed = _create_patient_direct(name="Завершённый", events=[{"date": _future(2, 1), "time": "09:00", "actualDate": _future(2, 1)}])
    cancelled = _create_patient_direct(name="Без Плана", events=[{"date": _future(2, 1), "time": "09:00", "noPlan": True}])
    for pid in (invalid, completed, cancelled):
        _grant(auth_client, pid)

    result = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()
    assert result["created"] == 0
    assert result["skipped"] >= 2

    auth_client.put(
        "/api/notifications/templates/appointment_reminder",
        json={"enabled": False},
        headers=auth_client.csrf,
    )
    ok = _create_patient_direct(name="Шаблон Отключён", events=[{"date": _future(2, 2), "time": "09:00"}])
    _grant(auth_client, ok)
    disabled = auth_client.post("/api/notifications/generate-due", headers=auth_client.csrf).json()
    assert disabled["skipped"] >= 1


def test_worker_success_failures_retry_and_cancelled_not_sent(auth_client):
    from backend.app.notifications.mock_provider import MockNotificationProvider
    from backend.app.services import notifications

    pid = _create_patient_direct()
    _grant(auth_client, pid)
    assert _manual(auth_client, pid).status_code == 201
    conn = get_conn()
    _due_all(conn)

    result = notifications.process_due(conn, limit=1, provider=MockNotificationProvider("success"))
    assert result["processed"] == 1
    sent = conn.execute("SELECT status, provider_status FROM notifications WHERE patient_id = ?", (pid,)).fetchone()
    assert sent["status"] == "sent"
    assert sent["provider_status"] == "processed_by_mock"

    assert _manual(auth_client, pid, _now_plus(2)).status_code == 201
    _due_all(conn)
    temp = notifications.process_due(conn, limit=1, provider=MockNotificationProvider("temporary_failure"))
    assert temp["processed"] == 1
    row = conn.execute("SELECT id, status, attempt_count, error_code FROM notifications WHERE status != 'sent'").fetchone()
    assert row["status"] == "pending"
    assert row["attempt_count"] == 1
    assert row["error_code"] == "mock_temporary"

    with conn:
        conn.execute("UPDATE notifications SET status = 'failed' WHERE id = ?", (row["id"],))
    retry = auth_client.post(f"/api/notifications/{row['id']}/retry", headers=auth_client.csrf)
    assert retry.status_code == 200

    assert _manual(auth_client, pid, _now_plus(3)).status_code == 201
    pending_id = conn.execute("SELECT id FROM notifications WHERE status = 'pending' ORDER BY created_at DESC").fetchone()["id"]
    cancel = auth_client.post(f"/api/notifications/{pending_id}/cancel", headers=auth_client.csrf)
    assert cancel.status_code == 200
    assert notifications.process_due(conn, limit=10, provider=MockNotificationProvider("success"))["processed"] >= 0
    assert conn.execute("SELECT status FROM notifications WHERE id = ?", (pending_id,)).fetchone()["status"] == "cancelled"


def test_processing_lease_recovery_and_provider_call_outside_write_transaction(auth_client):
    from backend.app.notifications.mock_provider import MockNotificationProvider
    from backend.app.services import notifications

    class CountingProvider(MockNotificationProvider):
        def __init__(self):
            super().__init__("success")
            self.calls = 0

        def send(self, notification_context):
            assert not get_conn().in_transaction
            self.calls += 1
            return super().send(notification_context)

    pid = _create_patient_direct()
    _grant(auth_client, pid)
    assert _manual(auth_client, pid).status_code == 201
    conn = get_conn()
    _due_all(conn)

    claimed = notifications.claim_next(conn)
    assert claimed is not None
    fresh = notifications.process_due(conn, limit=10, provider=CountingProvider())
    assert fresh["restored"] == 0
    assert fresh["processed"] == 0
    assert conn.execute("SELECT status FROM notifications WHERE id = ?", (claimed["id"],)).fetchone()["status"] == "processing"

    old = _now_minus(1)
    with conn:
        conn.execute("UPDATE notifications SET processing_started_at = ? WHERE id = ?", (old, claimed["id"]))
    provider = CountingProvider()
    recovered = notifications.process_due(conn, limit=10, provider=provider)
    assert recovered["restored"] == 1
    assert recovered["processed"] == 1
    assert provider.calls == 1
    assert conn.execute("SELECT status FROM notifications WHERE id = ?", (claimed["id"],)).fetchone()["status"] == "sent"
    again = notifications.process_due(conn, limit=10, provider=provider)
    assert again["processed"] == 0
    assert provider.calls == 1


def test_processing_recovery_respects_max_attempts_and_terminal_statuses(auth_client):
    from backend.app.services import notifications

    pid = _create_patient_direct()
    _grant(auth_client, pid)
    assert _manual(auth_client, pid, _now_plus(1)).status_code == 201
    assert _manual(auth_client, pid, _now_plus(2)).status_code == 201
    assert _manual(auth_client, pid, _now_plus(3)).status_code == 201
    conn = get_conn()
    ids = [r["id"] for r in conn.execute("SELECT id FROM notifications ORDER BY scheduled_at").fetchall()]
    old = _now_minus(1)
    with conn:
        conn.execute(
            "UPDATE notifications SET status = 'processing', processing_started_at = ?, attempt_count = max_attempts WHERE id = ?",
            (old, ids[0]),
        )
        conn.execute(
            "UPDATE notifications SET status = 'sent', processing_started_at = ? WHERE id = ?",
            (old, ids[1]),
        )
        conn.execute(
            "UPDATE notifications SET status = 'cancelled', processing_started_at = ? WHERE id = ?",
            (old, ids[2]),
        )
    result = notifications.restore_stale_processing(conn)
    assert result == {"restored": 0, "failed": 1}
    statuses = {
        r["id"]: r["status"]
        for r in conn.execute("SELECT id, status FROM notifications WHERE id IN (?, ?, ?)", ids).fetchall()
    }
    assert statuses[ids[0]] == "failed"
    assert statuses[ids[1]] == "sent"
    assert statuses[ids[2]] == "cancelled"


def test_worker_rechecks_revoked_consent_and_unknown_provider_fails_closed(auth_client, monkeypatch):
    from backend.app.services import notifications

    pid = _create_patient_direct()
    _grant(auth_client, pid)
    assert _manual(auth_client, pid).status_code == 201
    conn = get_conn()
    _due_all(conn)
    auth_client.put(
        f"/api/notifications/patients/{pid}/preferences",
        json={"smsEnabled": False, "consentStatus": "revoked"},
        headers=auth_client.csrf,
    )
    result = notifications.process_due(conn, limit=10)
    assert result["processed"] == 0
    assert conn.execute("SELECT status FROM notifications WHERE patient_id = ?", (pid,)).fetchone()["status"] == "cancelled"

    monkeypatch.setattr("backend.app.config.NOTIFICATION_PROVIDER", "real_sms")
    with pytest.raises(RuntimeError):
        notifications.generate_due_reminders(conn)


def test_mock_provider_fail_closed_no_network_and_no_phi_in_logs(auth_client, monkeypatch, capsys, caplog):
    from backend.app.notifications.mock_provider import MockNotificationProvider
    from backend.app.services import notifications
    import backend.app.notifications.mock_provider as mock_provider

    source = inspect.getsource(mock_provider)
    for forbidden in ("requests", "urllib", "http.client", "socket", "aiohttp"):
        assert forbidden not in source

    pid = _create_patient_direct(phone="+7 (999) 777-66-55")
    _grant(auth_client, pid)
    assert _manual(auth_client, pid).status_code == 201
    conn = get_conn()
    _due_all(conn)
    with caplog.at_level(logging.INFO):
        notifications.process_due(conn, limit=1, provider=MockNotificationProvider("success"))
    captured = capsys.readouterr()
    logs = "\n".join(record.getMessage() for record in caplog.records)
    for forbidden in ("999", "777-66-55", "Напоминаем о запланированном"):
        assert forbidden not in captured.out
        assert forbidden not in captured.err
        assert forbidden not in logs

    monkeypatch.setattr("backend.app.config.NOTIFICATION_PROVIDER", "sms_real")
    with pytest.raises(RuntimeError):
        notifications.process_due(conn, limit=1)


def test_notifications_api_filters_pagination_and_scope(client):
    dept_a = create_department("Уведомления А")
    dept_b = create_department("Уведомления Б")
    doc_a = create_account("notif.doc.a", "doctor", "Доктор А", department_ids=[dept_a])
    doc_b = create_account("notif.doc.b", "doctor", "Доктор Б", department_ids=[dept_b])
    create_account("notif.dev", "developer", "Разработчик")
    p_a = _create_patient_direct(name="Пациент А", doctor_id=doc_a)
    p_b = _create_patient_direct(name="Пациент Б", doctor_id=doc_b)

    login(client, "notif.dev")
    _grant(client, p_a)
    _grant(client, p_b)
    assert _manual(client, p_a, _now_plus(1), "custom_reminder").status_code == 201
    assert _manual(client, p_b, _now_plus(2), "examination_reminder").status_code == 201
    n_b = get_conn().execute("SELECT id FROM notifications WHERE patient_id = ?", (p_b,)).fetchone()["id"]

    assert client.get("/api/notifications?status=pending").json()["total"] == 2
    assert client.get("/api/notifications?template_code=custom_reminder").json()["total"] == 1
    assert client.get(f"/api/notifications?patient_id={p_a}").json()["total"] == 1
    from urllib.parse import quote
    assert client.get("/api/notifications", headers={"X-Search": quote("Пациент А")}).json()["total"] == 1
    page = client.get("/api/notifications?limit=1&offset=0&sort=scheduled_asc").json()
    assert page["limit"] == 1
    assert len(page["notifications"]) == 1
    assert client.get("/api/notifications?status=bad").status_code == 422

    create_account("notif.head.a", "department_head", "Заведующий А", department_ids=[dept_a])
    login(client, "notif.head.a")
    assert client.get("/api/notifications").json()["total"] == 1
    assert client.get(f"/api/notifications?patient_id={p_b}").json()["total"] == 0
    assert client.get(f"/api/notifications/{n_b}").status_code == 404


def test_read_only_roles_can_view_but_cannot_write(client):
    create_account("notif.dev2", "developer", "Разработчик")
    login(client, "notif.dev2")
    pid = _create_patient_direct()
    _grant(client, pid)
    assert _manual(client, pid).status_code == 201
    notification_id = get_conn().execute("SELECT id FROM notifications WHERE patient_id = ?", (pid,)).fetchone()["id"]

    for role in ("deputy_chief_oncology", "caop_head"):
        create_account(f"notif.{role}", role, f"Роль {role}")
        login(client, f"notif.{role}")
        assert client.get("/api/notifications").status_code == 200
        assert client.get(f"/api/notifications/patients/{pid}").json()["can_write"] is False
        assert _manual(client, pid).status_code == 403
        assert client.put(
            f"/api/notifications/patients/{pid}/preferences",
            json={"smsEnabled": True, "consentStatus": "granted"},
            headers=client.csrf,
        ).status_code == 403
        assert client.post(f"/api/notifications/{notification_id}/cancel", headers=client.csrf).status_code == 403
        assert client.post(f"/api/notifications/{notification_id}/retry", headers=client.csrf).status_code == 403
        assert client.post("/api/notifications/process-due", headers=client.csrf).status_code == 403


def test_notification_audit_excludes_phi(auth_client):
    from backend.app.services import notifications

    pid = _create_patient_direct(
        name="Фамилия Секретная",
        phone="+7 (999) 444-55-66",
    )
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE patients SET diagnosis = ?, nosology = ?, complaint = ? WHERE id = ?",
            ("C34.1 секретный диагноз", "lung34", "секретная жалоба", pid),
        )
    _grant(auth_client, pid)
    assert _manual(auth_client, pid).status_code == 201
    _due_all(conn)
    notifications.process_due(conn, limit=10)

    audit_text = json.dumps(auth_client.get("/api/audit?limit=200").json(), ensure_ascii=False)
    for forbidden in (
        "Фамилия Секретная",
        "+7 (999) 444-55-66",
        "9994445566",
        "444-55-66",
        "C34.1",
        "lung34",
        "секретная жалоба",
        "Напоминаем о запланированном",
        "Локализация/клиентский путь",
    ):
        assert forbidden not in audit_text
    assert "notification_created" in audit_text
    assert "patient_id" in audit_text or pid in audit_text


def test_templates_endpoint_alias_and_no_full_phone_in_list(auth_client):
    pid = _create_patient_direct()
    _grant(auth_client, pid)
    assert _manual(auth_client, pid).status_code == 201

    assert auth_client.get("/api/notification-templates").status_code == 200
    text = auth_client.get("/api/notifications").text
    assert "+7 (999) 111-22-33" not in text
    assert "9991112233" not in text
    assert "phone_masked" in text


def test_notifications_smoke_1000_patients_5000_notifications(auth_client):
    from backend.app import store
    from backend.app.db import now_iso
    from backend.app.models import PatientCreateIn
    from backend.app.services import notifications

    conn = get_conn()
    template = conn.execute("SELECT id, code, version FROM notification_templates WHERE code = 'custom_reminder'").fetchone()
    patient_ids = []
    for i in range(1000):
        pid = store.create_patient(
            conn,
                PatientCreateIn(**make_patient_payload(
                    name=f"Фиктивный Пациент {i:04d}",
                    phone=f"+7 (900) {i % 1000:03d}-00-00",
                )),
            None,
        )
        patient_ids.append(pid)
    with conn:
        conn.executemany(
            """INSERT INTO patient_notification_preferences
               (patient_id, sms_enabled, consent_status, consent_recorded_at, consent_recorded_by_user_id, updated_at)
               VALUES (?, 1, 'granted', ?, NULL, ?)""",
            [(pid, now_iso(), now_iso()) for pid in patient_ids],
        )
        rows = []
        for i in range(5000):
            pid = patient_ids[i % len(patient_ids)]
            scheduled = _now_minus(1)
            source_id = f"manual-{i}"
            rows.append((
                f"smoke-{i}",
                pid,
                "sms",
                template["id"],
                template["code"],
                template["version"],
                "manual",
                source_id,
                scheduled,
                notifications.notification_idempotency_key(pid, "manual", source_id, template["code"], scheduled),
                "pending",
                0,
                3,
                _now_minus(1),
                None,
                now_iso(),
                now_iso(),
            ))
        conn.executemany(
            """INSERT INTO notifications
               (id, patient_id, channel, template_id, template_code, template_version, source_type, source_id,
                scheduled_at, idempotency_key, status, attempt_count, max_attempts, next_attempt_at, created_by_user_id,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )

    summary = auth_client.get("/api/notifications/summary")
    page = auth_client.get("/api/notifications?limit=200")
    batch = notifications.process_due(conn, limit=25)
    assert summary.status_code == 200
    assert page.status_code == 200
    assert len(page.json()["notifications"]) == 200
    assert batch["processed"] == 25
    assert batch["restored"] == 0
