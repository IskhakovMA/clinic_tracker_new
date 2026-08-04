"""Management analytics API: scope, filters, aggregation and safe exports."""
from __future__ import annotations

import json
from datetime import timedelta

from .conftest import TEST_PASSWORD, create_account, create_department, get_conn, login, make_patient_payload


def _as_user(client, username: str):
    r = login(client, username, TEST_PASSWORD)
    assert r.status_code == 200, r.text


def _create_patient(doctor_id=None, **overrides):
    from backend.app import store
    from backend.app.models import PatientCreateIn

    return store.create_patient(get_conn(), PatientCreateIn(**make_patient_payload(**overrides)), doctor_id)


def _stand(client):
    dept_a = create_department("Аналитика А")
    dept_b = create_department("Аналитика Б")
    create_account("analytics.dev", "developer", "Разработчик Аналитики")
    create_account("analytics.read", "deputy_chief_oncology", "Глобальный Просмотр")
    head_a = create_account("analytics.head.a", "department_head", "Заведующий Аналитика А", department_ids=[dept_a])
    doc_a = create_account("analytics.doc.a", "doctor", "Врач Аналитика А", department_ids=[dept_a])
    doc_b = create_account("analytics.doc.b", "doctor", "Врач Аналитика Б", department_ids=[dept_b])
    p_a = _create_patient(
        doc_a,
        name="Аналитика Пациент А",
        phone="+7 (999) 101-10-10",
        diagnosis="C34.1 скрытый диагноз",
        nosology="lung34",
        events=[{"date": "2000-01-01"}],
    )
    p_b = _create_patient(
        doc_b,
        name="Аналитика Пациент Б",
        phone="+7 (999) 202-20-20",
        diagnosis="C50.1 скрытый диагноз",
        nosology="breast",
        events=[{"date": "2999-01-01"}],
    )
    p_unassigned = _create_patient(
        None,
        name="Аналитика Без Врача",
        phone="телефон неизвестен",
        diagnosis="C61 скрытый диагноз",
        nosology="prostate",
    )
    return {
        "dept_a": dept_a,
        "dept_b": dept_b,
        "head_a": head_a,
        "doc_a": doc_a,
        "doc_b": doc_b,
        "p_a": p_a,
        "p_b": p_b,
        "p_unassigned": p_unassigned,
    }


def _card(body, code):
    return next(card for card in body["cards"] if card["code"] == code)


def test_analytics_requires_authentication_and_validates_filters(client):
    assert client.get("/api/analytics/summary").status_code == 401
    _stand(client)
    _as_user(client, "analytics.dev")
    assert client.get("/api/analytics/summary?stage=unknown").status_code == 422
    assert client.get("/api/analytics/summary?date_from=2026-02-01&date_to=2026-01-01").status_code == 422
    assert client.get("/api/analytics/details?type=patients&limit=201").status_code == 422
    assert client.get("/api/analytics/details?type=unknown").status_code == 422


def test_summary_stages_overdue_and_details_respect_patient_scope(client):
    ids = _stand(client)

    _as_user(client, "analytics.dev")
    summary = client.get("/api/analytics/summary").json()
    assert _card(summary, "total_patients")["value"] == 3
    assert _card(summary, "unassigned_patients")["value"] == 1
    assert _card(summary, "patients_with_overdue")["value"] == 1
    assert "Профиль клиентского пути" in json.dumps(summary, ensure_ascii=False)

    stages = client.get("/api/analytics/stages").json()
    assert stages["total"] == 3
    assert sum(row["count"] for row in stages["rows"]) == 3
    assert all("percent" in row for row in stages["rows"])

    overdue = client.get("/api/analytics/overdue").json()
    assert overdue["total"] == 1
    assert overdue["items"][0]["patient_id"] == ids["p_a"]
    assert overdue["items"][0]["days_overdue"] > 0

    details = client.get("/api/analytics/details?type=patients&limit=2&offset=1").json()
    assert details["limit"] == 2
    assert details["offset"] == 1
    assert details["total"] == 3
    assert len(details["items"]) == 2

    _as_user(client, "analytics.doc.a")
    doctor_summary = client.get("/api/analytics/summary").json()
    assert _card(doctor_summary, "total_patients")["value"] == 1
    assert client.get(f"/api/analytics/summary?doctor_user_id={ids['doc_b']}").json()["cards"][0]["value"] == 0
    assert client.get(f"/api/analytics/details?type=patients&department_id={ids['dept_b']}").json()["items"] == []


def test_analytics_readonly_roles_can_read_but_not_write(client):
    ids = _stand(client)
    _as_user(client, "analytics.read")
    assert client.get("/api/analytics/data-quality").status_code == 200
    assert client.get("/api/analytics/checklists").status_code == 200
    assert client.post("/api/notifications/process-due", headers=client.csrf).status_code == 403
    assert client.get(f"/api/patients/{ids['p_a']}").status_code == 200


def test_checklists_workload_notifications_and_export_do_not_expose_phi(client):
    ids = _stand(client)
    _as_user(client, "analytics.dev")
    conn = get_conn()
    from backend.app.services import notifications

    with conn:
        conn.execute(
            """INSERT OR REPLACE INTO patient_notification_preferences
               (patient_id, sms_enabled, consent_status, consent_recorded_at,
                consent_recorded_by_user_id, consent_revoked_at, updated_at)
               VALUES (?, 1, 'granted', '2026-08-04T00:00:00+00:00', NULL, NULL, '2026-08-04T00:00:00+00:00')""",
            (ids["p_a"],),
        )
    scheduled = (notifications.utcnow() + timedelta(days=1)).isoformat()
    notification, reason = notifications.create_notification(
        conn,
        patient_id=ids["p_a"],
        template_code="appointment_reminder",
        scheduled_at=scheduled,
        source_type="manual",
        source_id=notifications.MANUAL_SOURCE_ID,
        created_by_user_id=None,
    )
    assert notification and reason is None

    checklist = client.get("/api/analytics/checklists").json()
    assert checklist["formula"] == "completed_required_items / total_required_items * 100; необязательные пункты не включаются"
    assert checklist["total_required_items"] >= checklist["incomplete_required_items"]

    workload = client.get("/api/analytics/workload").json()
    assert workload["title"] == "Распределение рабочей нагрузки"
    assert "рейтинг" not in json.dumps(workload, ensure_ascii=False).lower()
    assert "эффектив" not in json.dumps(workload, ensure_ascii=False).lower()

    notification_stats = client.get("/api/analytics/notifications").json()
    assert notification_stats["provider"] == "mock"
    assert notification_stats["status_counts"]["pending"] == 1
    assert "Обработано тестовым провайдером" not in json.dumps(notification_stats, ensure_ascii=False)

    exported = client.post(
        "/api/analytics/export",
        json={"sections": ["summary", "notifications", "data_quality"]},
        headers=client.csrf,
    ).json()
    text = json.dumps(exported, ensure_ascii=False)
    for forbidden in (
        "+7 (999) 101-10-10",
        "9991011010",
        "C34.1 скрытый диагноз",
        "Напоминаем о запланированном",
    ):
        assert forbidden not in text


def test_stage_durations_are_based_on_completed_history_intervals(client):
    ids = _stand(client)
    conn = get_conn()
    with conn:
        conn.execute(
            """INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, checklist_json, note)
               VALUES ('analytics_duration_next', ?, 1, 'consult', '2026-08-04T00:00:00+00:00', NULL, '')""",
            (ids["p_a"],),
        )
    _as_user(client, "analytics.dev")
    body = client.get("/api/analytics/stage-durations?date_from=2026-08-01&date_to=2026-08-31").json()
    assert body["available"] is True
    assert all(row["observations"] > 0 for row in body["stats"])
    assert "current_stage_age" in body
