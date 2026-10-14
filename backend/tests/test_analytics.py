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
    create_account("analytics.caop", "caop_head", "ЦАОП Аналитики")
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
    assert client.post("/api/analytics/export", json={"reportType": "unknown"}, headers=client.csrf).status_code == 422


def test_registered_analytics_routes_inventory():
    from backend.app.main import app

    routes = set()
    for route in app.routes:
        router = getattr(route, "original_router", None)
        for child in getattr(router, "routes", []) if router else [route]:
            path = getattr(child, "path", "")
            if path.startswith("/api/analytics"):
                routes.add((tuple(sorted(child.methods)), path, child.name))
    assert routes >= {
        (("GET",), "/api/analytics/summary", "summary"),
        (("GET",), "/api/analytics/dashboard", "dashboard"),
        (("GET",), "/api/analytics/stages", "stages"),
        (("GET",), "/api/analytics/overdue", "overdue"),
        (("GET",), "/api/analytics/stage-durations", "stage_durations"),
        (("GET",), "/api/analytics/workload", "workload"),
        (("GET",), "/api/analytics/data-quality", "data_quality"),
        (("GET",), "/api/analytics/checklists", "checklists"),
        (("GET",), "/api/analytics/notifications", "notifications"),
        (("GET",), "/api/analytics/details", "details"),
        (("POST",), "/api/analytics/export", "export"),
    }


def test_dashboard_empty_database_contract(client):
    create_account("analytics.empty.dev", "developer", "Разработчик Empty")
    _as_user(client, "analytics.empty.dev")
    body = client.get("/api/analytics/dashboard").json()
    cards = {card["code"]: card["value"] for card in body["summary"]["cards"]}
    assert cards["total_patients"] == 0
    assert cards["active_patients"] == 0
    assert cards["completed_patients"] == 0
    assert body["stages"]["rows"] == []
    assert body["profiles"]["rows"] == []
    route_rows = {row["code"]: row for row in body["route_state"]["rows"]}
    assert {"active", "completed", "dispensary_observation", "diagnosis_not_confirmed", "operational_signals"} <= set(route_rows)
    assert route_rows["operational_signals"]["meta"]["overlap"] is True
    assert body["attention"]["title"] == "Причины внимания"
    assert len(body["trend"]["rows"]) == 30
    capped = client.get("/api/analytics/dashboard?date_from=1900-01-01&date_to=2999-12-31").json()
    assert capped["trend"]["period_days"] == 90
    assert capped["trend"]["truncated"] is True


def test_dashboard_visual_aggregates_filters_and_scope(client):
    ids = _stand(client)
    completed = _create_patient(
        ids["doc_a"],
        name="Аналитика Завершён",
        phone="+7 (999) 303-30-30",
        diagnosis="C34.2 скрытый диагноз",
        nosology="lung34",
        stage="completed",
        events=[],
    )
    conn = get_conn()
    with conn:
        conn.execute("UPDATE patients SET arrival = '2026-08-02T10:00:00+00:00' WHERE id = ?", (completed,))
        conn.execute(
            """INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, checklist_json, note)
               VALUES ('analytics_dashboard_completed', ?, 1, 'completed', '2026-08-03T00:00:00+00:00', NULL, '')""",
            (completed,),
        )
    _as_user(client, "analytics.dev")
    body = client.get("/api/analytics/dashboard?date_from=2026-08-01&date_to=2026-08-30").json()
    cards = {card["code"]: card["value"] for card in body["summary"]["cards"]}
    assert cards["total_patients"] == 4
    assert cards["active_patients"] == 3
    assert cards["completed_patients"] == 1
    assert cards["unassigned_patients"] == 1
    assert cards["data_quality_issues"] >= 1
    stages = {row["stage"]: row for row in body["stages"]["rows"]}
    assert stages["completed"]["count"] == 1
    assert round(sum(row["percent"] for row in body["profiles"]["rows"]), 1) in (99.9, 100.0, 100.1)
    profile_rows = {row["meta"]["nosology"]: row for row in body["profiles"]["rows"]}
    assert profile_rows["lung12"]["count"] == 2
    assert profile_rows["lung12"]["meta"]["nosology_codes"] == ["lung12", "lung34"]
    route_rows = {row["code"]: row for row in body["route_state"]["rows"]}
    assert route_rows["operational_signals"]["meta"]["overlap"] is True
    assert route_rows["dispensary_observation"]["count"] == 0
    assert route_rows["diagnosis_not_confirmed"]["count"] == 0
    assert body["data_quality"]["rows"] == sorted(body["data_quality"]["rows"], key=lambda row: (-row["count"], row["code"]))
    assert sum(row["new_patients"] for row in body["trend"]["rows"]) >= 1
    assert sum(row["completed_patients"] for row in body["trend"]["rows"]) >= 1
    assert body["attention"]["rows"] == sorted(body["attention"]["rows"], key=lambda row: (-row["count"], row["label"]))

    filtered = client.get("/api/analytics/dashboard?nosology=lung34").json()
    assert filtered["summary"]["cards"][0]["value"] == 2

    _as_user(client, "analytics.doc.a")
    scoped = client.get(f"/api/analytics/dashboard?doctor_user_id={ids['doc_b']}").json()
    assert scoped["summary"]["cards"][0]["value"] == 0
    assert scoped["workload"]["rows"] == []


def test_analytics_localization_filter_groups_duplicate_profile_labels(client):
    ids = _stand(client)
    _create_patient(
        ids["doc_a"],
        name="Аналитика Пациент Лёгкое 12",
        phone="+7 (999) 404-40-40",
        diagnosis="C34.3 скрытый диагноз",
        nosology="lung12",
        events=[],
    )

    _as_user(client, "analytics.dev")
    body = client.get("/api/analytics/dashboard").json()
    lung_rows = [row for row in body["profiles"]["rows"] if row["label"] == "Лёгкое"]
    assert len(lung_rows) == 1
    assert lung_rows[0]["count"] == 2
    assert lung_rows[0]["meta"]["nosology"] == "lung12"
    assert lung_rows[0]["meta"]["nosology_codes"] == ["lung12", "lung34"]

    filtered_by_lung12 = client.get("/api/analytics/dashboard?nosology=lung12").json()
    filtered_by_lung34 = client.get("/api/analytics/dashboard?nosology=lung34").json()
    assert filtered_by_lung12["summary"]["cards"][0]["value"] == 2
    assert filtered_by_lung34["summary"]["cards"][0]["value"] == 2

    _as_user(client, "analytics.doc.a")
    outside_scope = client.get(f"/api/analytics/dashboard?nosology=lung12&doctor_user_id={ids['doc_b']}").json()
    assert outside_scope["summary"]["cards"][0]["value"] == 0


def test_summary_stages_overdue_and_details_respect_patient_scope(client):
    ids = _stand(client)

    _as_user(client, "analytics.dev")
    summary = client.get("/api/analytics/summary").json()
    assert _card(summary, "total_patients")["value"] == 3
    assert _card(summary, "unassigned_patients")["value"] == 1
    assert _card(summary, "patients_with_overdue")["value"] == 1
    assert _card(summary, "total_patients")["kind"] == "current"
    assert "локализации/клиентскому пути" in json.dumps(summary, ensure_ascii=False)
    assert client.get("/api/analytics/summary?date_from=2999-01-01&date_to=2999-01-31").json()["cards"][0]["value"] == 3

    stages = client.get("/api/analytics/stages").json()
    assert stages["total"] == 3
    assert sum(row["count"] for row in stages["rows"]) == 3
    assert all("percent" in row for row in stages["rows"])

    overdue = client.get("/api/analytics/overdue").json()
    assert overdue["total"] == 1
    assert overdue["items"][0]["patient_id"] == ids["p_a"]
    assert overdue["items"][0]["planned_date"] == "2000-01-01"
    assert overdue["items"][0]["days_overdue"] > 0
    assert client.get("/api/analytics/overdue?date_from=2000-01-01&date_to=2000-01-01").json()["total"] == 1
    assert client.get("/api/analytics/overdue?date_from=2000-01-02&date_to=2000-01-02").json()["total"] == 0

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


def test_analytics_user_facing_definitions_do_not_use_internal_english_terms(client):
    _stand(client)
    _as_user(client, "analytics.dev")
    body = client.get("/api/analytics/dashboard").json()
    visible = json.dumps({
        "summary": body["summary"]["cards"],
        "data_quality": body["data_quality"],
        "attention": body["attention"],
    }, ensure_ascii=False)
    for forbidden in ("critical findings", "warning findings", "planned events", "статусе failed", "статусе pending"):
        assert forbidden not in visible
    assert "Критические проблемы" in visible
    assert "Уведомления с ошибкой отправки" in visible


def test_analytics_readonly_roles_can_read_but_not_write(client):
    ids = _stand(client)
    for username in ("analytics.read", "analytics.caop"):
        _as_user(client, username)
        assert client.get("/api/analytics/data-quality").status_code == 200
        assert client.get("/api/analytics/checklists").status_code == 200
        assert client.post("/api/analytics/export", json={"reportType": "summary"}, headers=client.csrf).status_code == 200
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
        json={"reportType": "notifications", "detailLevel": "aggregate"},
        headers=client.csrf,
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=\"analytics_notifications_" in exported.headers["content-disposition"]
    assert exported.content.startswith("\ufeff".encode("utf-8"))
    text = exported.content.decode("utf-8-sig")
    for forbidden in (
        "+7 (999) 101-10-10",
        "9991011010",
        "C34.1 скрытый диагноз",
        "Напоминаем о запланированном",
    ):
        assert forbidden not in text
    audit_text = json.dumps(client.get("/api/audit?limit=20").json(), ensure_ascii=False)
    assert "analytics_report_exported" in audit_text
    assert "Аналитика Пациент А" not in audit_text
    assert "C34.1 скрытый диагноз" not in audit_text


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


def test_csv_formula_injection_protection_and_allowlisted_export(client):
    from backend.app.analytics import service

    for dangerous in ("=1+1", "+SUM(A1:A2)", "-2+3", "@cmd", "\t=1+1", "\r=1+1", "\n=1+1", "  =1+1"):
        assert service.csv_safe_cell(dangerous).startswith("'")

    dept = create_department("=1+1")
    create_account("analytics.formula.dev", "developer", "Разработчик CSV")
    doc = create_account("analytics.formula.doc", "doctor", "+SUM(A1:A2)", department_ids=[dept])
    _create_patient(doc, name="CSV Пациент", events=[])
    _as_user(client, "analytics.formula.dev")
    response = client.post(
        "/api/analytics/export",
        json={"reportType": "workload", "detailLevel": "details"},
        headers=client.csrf,
    )
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert "'+SUM(A1:A2)" in text
    assert "'=1+1" in text
    assert "\n+SUM(A1:A2)" not in text
    assert "\n=1+1" not in text


def test_all_analytics_endpoints_apply_role_scope_and_details_pagination(client):
    ids = _stand(client)
    endpoints = [
        "/api/analytics/summary",
        "/api/analytics/dashboard",
        "/api/analytics/stages",
        "/api/analytics/overdue",
        "/api/analytics/stage-durations",
        "/api/analytics/workload",
        "/api/analytics/data-quality",
        "/api/analytics/checklists",
        "/api/analytics/notifications",
        "/api/analytics/details?type=patients&limit=1&offset=0",
    ]
    _as_user(client, "analytics.head.a")
    for endpoint in endpoints:
        assert client.get(endpoint).status_code == 200, endpoint
    assert client.get(f"/api/analytics/details?type=patients&doctor_user_id={ids['doc_b']}").json()["items"] == []
    assert client.post(
        f"/api/analytics/export?doctor_user_id={ids['doc_b']}",
        json={"reportType": "summary"},
        headers=client.csrf,
    ).content.decode("utf-8-sig").count("\n") >= 1

    _as_user(client, "analytics.doc.a")
    workload = client.get("/api/analytics/workload").json()
    assert [row["doctor_user_id"] for row in workload["rows"]] == [ids["doc_a"]]
    details = client.get("/api/analytics/details?type=patients&limit=1&offset=0").json()
    assert details["total"] == 1
    assert len(details["items"]) == 1
    assert details["items"][0]["patient_id"] == ids["p_a"]


def test_stage_duration_median_even_odd_and_negative_intervals_excluded(client):
    create_account("analytics.duration.dev", "developer", "Разработчик Длительности")
    conn = get_conn()
    for idx, days in enumerate((1, 3, 5, 7)):
        pid = _create_patient(name=f"Duration Even {idx}", events=[])
        with conn:
            conn.execute("UPDATE history_entries SET entered_at = '2026-01-01T00:00:00+00:00' WHERE patient_id = ?", (pid,))
            conn.execute(
                "INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, checklist_json, note) VALUES (?, ?, 1, 'consult', ?, NULL, '')",
                (f"duration_even_{idx}", pid, f"2026-01-{1 + days:02d}T00:00:00+00:00"),
            )
    bad = _create_patient(name="Duration Negative", events=[])
    with conn:
        conn.execute("UPDATE history_entries SET entered_at = '2026-01-10T00:00:00+00:00' WHERE patient_id = ?", (bad,))
        conn.execute(
            "INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, checklist_json, note) VALUES ('duration_bad', ?, 1, 'consult', '2026-01-01T00:00:00+00:00', NULL, '')",
            (bad,),
        )
    _as_user(client, "analytics.duration.dev")
    body = client.get("/api/analytics/stage-durations?date_from=2026-01-01&date_to=2026-01-31").json()
    exam = next(row for row in body["stats"] if row["stage"] == "exam")
    assert exam["observations"] == 4
    assert exam["median_days"] == 4.0
    assert exam["min_days"] == 1
    assert exam["max_days"] == 7


def test_checklist_aggregation_not_multiplied_by_events_or_notifications(client):
    ids = _stand(client)
    conn = get_conn()
    from backend.app.services import notifications

    latest = conn.execute("SELECT id FROM history_entries WHERE patient_id = ? ORDER BY seq DESC LIMIT 1", (ids["p_a"],)).fetchone()["id"]
    with conn:
        conn.execute("UPDATE history_entries SET checklist_json = ? WHERE id = ?", (json.dumps({"Направление на бронхоскопию": True}, ensure_ascii=False), latest))
        for seq in range(10, 13):
            conn.execute("INSERT INTO events (id, history_entry_id, seq, date, no_plan) VALUES (?, ?, ?, '2999-01-01', 0)", (f"check_join_event_{seq}", latest, seq))
        conn.execute(
            """INSERT OR REPLACE INTO patient_notification_preferences
               (patient_id, sms_enabled, consent_status, consent_recorded_at, consent_recorded_by_user_id, consent_revoked_at, updated_at)
               VALUES (?, 1, 'granted', '2026-08-04T00:00:00+00:00', NULL, NULL, '2026-08-04T00:00:00+00:00')""",
            (ids["p_a"],),
        )
    for idx in range(3):
        notifications.create_notification(
            conn,
            patient_id=ids["p_a"],
            template_code="appointment_reminder",
            scheduled_at=f"2026-08-0{idx + 5}T09:00:00+00:00",
            source_type="manual",
            source_id=f"manual-{idx}",
            created_by_user_id=None,
        )
    _as_user(client, "analytics.dev")
    body = client.get(f"/api/analytics/checklists?doctor_user_id={ids['doc_a']}").json()
    patient = next(row for row in body["patients"] if row["patient_id"] == ids["p_a"])
    assert patient["total_required"] == len(__import__("backend.app.services.data_quality", fromlist=["REQUIRED_CHECKLISTS"]).REQUIRED_CHECKLISTS["lung34"]["exam"])
    assert patient["completed_required"] == 1


def test_data_quality_bulk_evaluation_query_count_is_not_per_patient(client, monkeypatch):
    create_account("analytics.bulk.dev", "developer", "Разработчик Bulk")
    for idx in range(120):
        _create_patient(name=f"Bulk Analytics {idx}", phone=f"+7 900 000-{idx:04d}", events=[])
    _as_user(client, "analytics.bulk.dev")
    conn = get_conn()
    statements = []
    conn.set_trace_callback(lambda sql: statements.append(sql) if sql.lstrip().upper().startswith("SELECT") else None)
    try:
        assert client.get("/api/analytics/data-quality").status_code == 200
    finally:
        conn.set_trace_callback(None)
    assert len(statements) < 40


# --- Сводка качества данных в аналитике (расхождение №27) -------------------
# Все величины сводки считаются по одной выборке: область доступа роли плюс
# фильтры пациентов. Эталон — пациентская карточка Центра качества данных.

def _quality_stand(client):
    dept_a = create_department("Качество А")
    dept_b = create_department("Качество Б")
    create_account("quality.dev", "developer", "Разработчик Качества")
    create_account("quality.deputy", "deputy_chief_oncology", "Заместитель Качества")
    create_account("quality.caop", "caop_head", "ЦАОП Качества")
    create_account("quality.head.a", "department_head", "Заведующий Качества А", department_ids=[dept_a])
    doc_a = create_account("quality.doc.a", "doctor", "Врач Качества А", department_ids=[dept_a])
    doc_b = create_account("quality.doc.b", "doctor", "Врач Качества Б", department_ids=[dept_b])
    clean = _create_patient(doc_a, name="Качество Полный Пациент", phone="+7 (999) 111-11-11",
                            policyNumber="7700000000000001", stage="appointment", events=[])
    multi = _create_patient(doc_a, name="Качество Много Проблем", phone="", policyNumber="",
                            stage="appointment", events=[{"date": "2000-01-01"}])
    info_only = _create_patient(doc_b, name="Качество Без Полиса", phone="+7 (999) 333-33-33",
                                policyNumber="", stage="appointment", events=[])
    unassigned = _create_patient(None, name="Качество Без Врача", phone="+7 (999) 444-44-44",
                                 policyNumber="7700000000000004", stage="appointment", events=[])
    conn = get_conn()
    with conn:
        # Критическая проблема: фактическая дата в будущем.
        conn.execute(
            """UPDATE events SET actual_date = '2999-01-01'
               WHERE history_entry_id IN (SELECT id FROM history_entries WHERE patient_id = ?)""",
            (multi,),
        )
    return {"dept_a": dept_a, "dept_b": dept_b, "doc_a": doc_a, "doc_b": doc_b,
            "clean": clean, "multi": multi, "info_only": info_only, "unassigned": unassigned}


def _expected_quality_summary(client, patient_ids):
    """Независимый расчёт по пациентским карточкам Центра качества (под разработчиком)."""
    _as_user(client, "quality.dev")
    scores, issues = [], []
    for pid in patient_ids:
        body = client.get(f"/api/data-quality/patients/{pid}").json()
        scores.append(body["patient"]["completeness"]["score"])
        issues.extend(body["issues"])
    severities = [i["severity"] for i in issues]
    return {
        "total_patients": len(patient_ids),
        "patients_with_issues": len({i["patient_id"] for i in issues}),
        "total_issues": len(issues),
        "critical_count": severities.count("critical"),
        "warning_count": severities.count("warning"),
        "info_count": severities.count("info"),
        "average_completeness": round(sum(scores) / len(scores)) if scores else None,
    }


_SUMMARY_KEYS = ("total_patients", "patients_with_issues", "total_issues", "critical_count",
                 "warning_count", "info_count", "average_completeness")


def _summary_subset(summary):
    return {key: summary[key] for key in _SUMMARY_KEYS}


def test_dashboard_quality_summary_empty_database_has_no_fake_completeness(client):
    create_account("quality.empty.dev", "developer", "Разработчик Пустой Базы")
    _as_user(client, "quality.empty.dev")
    summary = client.get("/api/analytics/dashboard").json()["data_quality"]["summary"]
    assert summary["total_patients"] == 0
    assert summary["average_completeness"] is None
    assert summary["total_issues"] == summary["patients_with_issues"] == 0
    assert client.get("/api/analytics/data-quality").json()["summary"]["average_completeness"] is None


def test_dashboard_quality_summary_matches_patient_level_quality(client):
    ids = _quality_stand(client)
    everyone = [ids["clean"], ids["multi"], ids["info_only"], ids["unassigned"]]
    expected = _expected_quality_summary(client, everyone)
    _as_user(client, "quality.dev")
    body = client.get("/api/analytics/dashboard").json()
    summary = body["data_quality"]["summary"]
    assert _summary_subset(summary) == expected
    # Несколько проблем у одного пациента: проблем больше, чем затронутых пациентов.
    assert summary["critical_count"] >= 1 and summary["warning_count"] >= 1 and summary["info_count"] >= 1
    assert summary["total_issues"] > summary["patients_with_issues"]
    assert 0 < summary["average_completeness"] < 100
    assert summary["unresolved_patients"] == 1
    # Сводка, разбивка и карточки — одна выборка.
    assert summary["total_issues"] == body["data_quality"]["total"]
    assert summary["total_issues"] == sum(row["count"] for row in body["data_quality"]["rows"])
    cards = {card["code"]: card["value"] for card in body["summary"]["cards"]}
    assert cards["data_quality_issues"] == summary["critical_count"] + summary["warning_count"]
    assert cards["total_patients"] == summary["total_patients"]
    # Пациент без проблем отдельно: 100 % и ноль проблем — реальные значения, а не заглушка.
    clean_only = _expected_quality_summary(client, [ids["clean"]])
    assert clean_only["average_completeness"] == 100 and clean_only["total_issues"] == 0


def test_dashboard_quality_summary_follows_filters_and_period_does_not_change_it(client):
    ids = _quality_stand(client)
    cases = {
        f"doctor_user_id={ids['doc_a']}": [ids["clean"], ids["multi"]],
        f"department_id={ids['dept_b']}": [ids["info_only"]],
        f"department_id={ids['dept_a']}&doctor_user_id={ids['doc_a']}&stage=appointment": [ids["clean"], ids["multi"]],
        f"department_id={ids['dept_a']}&doctor_user_id={ids['doc_b']}": [],
        "stage=completed": [],
    }
    for query, patient_ids in cases.items():
        expected = _expected_quality_summary(client, patient_ids)
        _as_user(client, "quality.dev")
        dashboard = client.get(f"/api/analytics/dashboard?{query}").json()["data_quality"]["summary"]
        state = client.get(f"/api/analytics/data-quality?{query}").json()["summary"]
        assert _summary_subset(dashboard) == expected, query
        assert _summary_subset(state) == expected, query
    _as_user(client, "quality.dev")
    without_period = client.get("/api/analytics/dashboard").json()["data_quality"]["summary"]
    with_period = client.get("/api/analytics/dashboard?date_from=2026-01-01&date_to=2026-01-31").json()["data_quality"]["summary"]
    assert _summary_subset(with_period) == _summary_subset(without_period)


def test_dashboard_quality_summary_respects_role_scope(client):
    ids = _quality_stand(client)
    everyone = [ids["clean"], ids["multi"], ids["info_only"], ids["unassigned"]]
    scopes = {
        "quality.dev": everyone,
        "quality.deputy": everyone,
        "quality.caop": everyone,
        "quality.head.a": [ids["clean"], ids["multi"]],
        "quality.doc.a": [ids["clean"], ids["multi"]],
        "quality.doc.b": [ids["info_only"]],
    }
    for username, patient_ids in scopes.items():
        expected = _expected_quality_summary(client, patient_ids)
        _as_user(client, username)
        summary = client.get("/api/analytics/dashboard").json()["data_quality"]["summary"]
        assert _summary_subset(summary) == expected, username
    # Фильтр не расширяет область доступа: чужой врач даёт пустую выборку, а не его пациентов.
    _as_user(client, "quality.doc.a")
    foreign = client.get(f"/api/analytics/dashboard?doctor_user_id={ids['doc_b']}").json()["data_quality"]
    assert foreign["summary"]["total_patients"] == 0
    assert foreign["summary"]["average_completeness"] is None
    assert foreign["rows"] == []
    body = client.get("/api/analytics/dashboard").text
    assert "Качество Без Полиса" not in body and "Качество Без Врача" not in body


def test_dashboard_other_sections_unchanged_by_quality_summary(client):
    ids = _quality_stand(client)
    _as_user(client, "quality.dev")
    body = client.get("/api/analytics/dashboard").json()
    assert set(body) == {"meta", "summary", "stages", "profiles", "route_state", "data_quality", "trend", "workload", "attention", "operations"}
    assert set(body["data_quality"]) == {"title", "total", "rows", "summary"}
    assert body["data_quality"]["title"] == "Самые частые проблемы качества данных"
    assert sum(row["count"] for row in body["stages"]["rows"]) == 4
    assert body["workload"]["rows"]
    # Детализация и экспорт качества работают как прежде.
    assert client.get("/api/analytics/details?type=quality_warning").status_code == 200
    export = client.post("/api/analytics/export", json={"reportType": "data_quality", "detailLevel": "details"}, headers=client.csrf)
    assert export.status_code == 200


def test_dashboard_quality_summary_duplicate_groups_touching_selection_within_scope(client):
    dept_a = create_department("Дубли А")
    dept_b = create_department("Дубли Б")
    create_account("dup.dev", "developer", "Разработчик Дублей")
    doc_a = create_account("dup.doc.a", "doctor", "Врач Дублей А", department_ids=[dept_a])
    doc_b = create_account("dup.doc.b", "doctor", "Врач Дублей Б", department_ids=[dept_b])
    # Пара внутри одного врача и пара через границу врачей (совпадает телефон).
    _create_patient(doc_a, name="Дубль Один", phone="+7 (900) 555-11-11", stage="appointment")
    _create_patient(doc_a, name="Дубль Два", phone="+7 (900) 555-11-11", stage="appointment")
    _create_patient(doc_a, name="Дубль Три", phone="+7 (900) 555-22-22", stage="exam")
    _create_patient(doc_b, name="Дубль Четыре", phone="+7 (900) 555-22-22", stage="exam")

    def groups(query=""):
        dashboard = client.get(f"/api/analytics/dashboard{query}").json()["data_quality"]["summary"]
        state = client.get(f"/api/analytics/data-quality{query}").json()["summary"]
        assert dashboard["possible_duplicate_groups"] == state["possible_duplicate_groups"]
        return dashboard["possible_duplicate_groups"]

    _as_user(client, "dup.dev")
    assert groups() == 2
    assert groups(f"?doctor_user_id={doc_a}") == 2                 # обе пары затрагивают выборку
    assert groups(f"?doctor_user_id={doc_a}&stage=exam") == 1      # в выборке один пациент пары
    assert groups("?stage=completed") == 0                         # обе пары вне выборки
    # Пара с пациентом другого врача для роли врача не обнаруживается и не раскрывается.
    _as_user(client, "dup.doc.a")
    assert groups() == 1


# --- Детализация проблем качества данных -----------------------------------
# Инвариант: значение показателя равно total его детализации при тех же фильтрах.

def _details(client, detail_type, query="", **params):
    extra = "".join(f"&{key}={value}" for key, value in params.items())
    response = client.get(f"/api/analytics/details?type={detail_type}{query and '&' + query}{extra}")
    assert response.status_code == 200, response.text
    return response.json()


def _all_details(client, detail_type, query="", **params):
    items, offset = [], 0
    while True:
        page = _details(client, detail_type, query, limit=50, offset=offset, **params)
        items.extend(page["items"])
        offset += len(page["items"])
        if not page["items"] or offset >= page["total"]:
            return items, page["total"]


def _detail_stand(client):
    dept_a = create_department("Детализация А")
    dept_b = create_department("Детализация Б")
    create_account("detail.dev", "developer", "Разработчик Детализации")
    create_account("detail.deputy", "deputy_chief_oncology", "Заместитель Детализации")
    create_account("detail.caop", "caop_head", "ЦАОП Детализации")
    create_account("detail.head.a", "department_head", "Заведующий Детализации А", department_ids=[dept_a])
    create_account("detail.chair", "consilium_chair", "Председатель Детализации")
    doc_crit = create_account("detail.doc.crit", "doctor", "Врач Критических", department_ids=[dept_a])
    doc_warn = create_account("detail.doc.warn", "doctor", "Врач Предупреждений", department_ids=[dept_a])
    doc_info = create_account("detail.doc.info", "doctor", "Врач Информации", department_ids=[dept_b])
    doc_mix = create_account("detail.doc.mix", "doctor", "Врач Смешанный", department_ids=[dept_b])
    doc_clean = create_account("detail.doc.clean", "doctor", "Врач Без Проблем", department_ids=[dept_b])
    ok = {"policyNumber": "7700000000000009", "stage": "appointment", "events": []}
    p = {}
    # Только критическая проблема: фактическая дата в будущем.
    p["crit"] = _create_patient(doc_crit, name="Детали Критическая", phone="+7 (900) 610-00-01", **{**ok, "events": [{"date": "2000-01-01"}]})
    # Только предупреждения, три разных кода одного уровня.
    p["warn_phone"] = _create_patient(doc_warn, name="Детали Без Телефона", phone="", **ok)
    p["warn_bad_phone"] = _create_patient(doc_warn, name="Детали Плохой Телефон", phone="телефон неизвестен", **ok)
    # Только информационные: два пациента с одним кодом.
    p["info_1"] = _create_patient(doc_info, name="Детали Без Полиса Один", phone="+7 (900) 610-00-04", **{**ok, "policyNumber": ""})
    p["info_2"] = _create_patient(doc_info, name="Детали Без Полиса Два", phone="+7 (900) 610-00-05", **{**ok, "policyNumber": ""})
    # Несколько проблем у одного пациента: все три уровня.
    p["multi"] = _create_patient(doc_mix, name="Детали Много Проблем", phone="", **{**ok, "policyNumber": "", "events": [{"date": "2000-01-01"}]})
    p["clean"] = _create_patient(doc_clean, name="Детали Без Проблем", phone="+7 (900) 610-00-07", **ok)
    # Без лечащего врача: видна только глобальным ролям.
    p["unassigned"] = _create_patient(None, name="Детали Без Врача", phone="+7 (900) 610-00-08", **ok)
    conn = get_conn()
    with conn:
        conn.execute(
            """UPDATE events SET actual_date = '2999-01-01'
               WHERE history_entry_id IN (SELECT id FROM history_entries WHERE patient_id IN (?, ?))""",
            (p["crit"], p["multi"]),
        )
    docs = {"crit": doc_crit, "warn": doc_warn, "info": doc_info, "mix": doc_mix, "clean": doc_clean}
    return {"dept_a": dept_a, "dept_b": dept_b, "docs": docs, "patients": p}


def _assert_quality_invariants(client, query=""):
    body = client.get(f"/api/analytics/dashboard{'?' + query if query else ''}").json()
    summary = body["data_quality"]["summary"]
    cards = {card["code"]: card for card in body["summary"]["cards"]}
    actionable = _details(client, cards["data_quality_issues"]["detail_type"], query)
    assert cards["data_quality_issues"]["detail_type"] == "quality_actionable"
    assert cards["data_quality_issues"]["value"] == actionable["total"]
    assert {item["severity"] for item in actionable["items"]} <= {"critical", "warning"}
    everything = _details(client, "quality_all", query)
    assert summary["total_issues"] == everything["total"]
    assert cards["critical_quality"]["value"] == _details(client, cards["critical_quality"]["detail_type"], query)["total"]
    assert cards["warning_quality"]["value"] == _details(client, cards["warning_quality"]["detail_type"], query)["total"]
    patients_total = _details(client, "quality_patients", query)["total"]
    assert patients_total == summary["patients_with_issues"]
    route = {row["code"]: row for row in body["route_state"]["rows"]}
    assert route["operational_signals"]["detail_type"] == "quality_patients"
    assert route["operational_signals"]["count"] == patients_total
    operations = {row["code"]: row for row in body["operations"]["rows"]}
    assert operations["quality"]["count"] == _details(client, operations["quality"]["detail_type"], query)["total"]
    assert operations["critical"]["count"] == _details(client, operations["critical"]["detail_type"], query)["total"]
    for row in body["data_quality"]["rows"]:
        assert row["detail_type"] == "quality_all" and row["filter"] == {"code": row["code"]}
        detail = _details(client, row["detail_type"], query, code=row["code"])
        assert detail["total"] == row["count"], row
        assert {item["code"] for item in detail["items"]} == {row["code"]}
        assert {item["code_label"] for item in detail["items"]} == {row["label"]}
    return body, summary


def test_quality_details_match_metrics_for_each_severity_mix(client):
    ids = _detail_stand(client)
    _as_user(client, "detail.dev")
    docs = ids["docs"]
    expected = {  # врач -> (critical, warning, info)
        "crit": (1, 0, 0),
        "warn": (0, 2, 0),
        "info": (0, 0, 2),
    }
    for key, (critical, warning, info) in expected.items():
        _, summary = _assert_quality_invariants(client, f"doctor_user_id={docs[key]}")
        assert (summary["critical_count"], summary["warning_count"], summary["info_count"]) == (critical, warning, info)
        severities = {item["severity"] for item in _details(client, "quality_all", f"doctor_user_id={docs[key]}")["items"]}
        assert severities == {sev for sev, n in zip(("critical", "warning", "info"), (critical, warning, info)) if n}
    _, mixed = _assert_quality_invariants(client, f"doctor_user_id={docs['mix']}")
    assert mixed["critical_count"] and mixed["warning_count"] and mixed["info_count"]
    _assert_quality_invariants(client)


def test_quality_details_multiple_issues_same_code_and_same_severity(client):
    ids = _detail_stand(client)
    _as_user(client, "detail.dev")
    docs, p = ids["docs"], ids["patients"]
    # Несколько проблем у одного пациента: проблем больше, чем пациентов.
    issues = _details(client, "quality_all", f"doctor_user_id={docs['mix']}")
    assert issues["total"] >= 3 and {item["patient_id"] for item in issues["items"]} == {p["multi"]}
    patients = _details(client, "quality_patients", f"doctor_user_id={docs['mix']}")
    assert patients["total"] == 1 and patients["items"][0]["issue_count"] == issues["total"]
    # Несколько проблем одного кода у разных пациентов.
    same_code = _details(client, "quality_all", f"doctor_user_id={docs['info']}", code="missing_policy_number")
    assert same_code["total"] == 2
    assert {item["patient_id"] for item in same_code["items"]} == {p["info_1"], p["info_2"]}
    # Разные коды одного уровня не смешиваются при фильтре по коду.
    warnings = _details(client, "quality_warning", f"doctor_user_id={docs['warn']}")
    assert {item["code"] for item in warnings["items"]} == {"missing_required_field", "invalid_phone"}
    only_phone = _details(client, "quality_all", f"doctor_user_id={docs['warn']}", code="invalid_phone")
    assert only_phone["total"] == 1 and only_phone["items"][0]["patient_id"] == p["warn_bad_phone"]
    # Понятные названия и уровень у каждой записи.
    for item in _details(client, "quality_all")["items"]:
        assert item["title"] and item["code_label"] and item["severity"] in {"critical", "warning", "info"}
        assert item["patient_name"] and "stage_label" in item and "treating_doctor_name" in item


def test_quality_details_empty_selection_clean_patient_and_filters(client):
    ids = _detail_stand(client)
    _as_user(client, "detail.dev")
    docs = ids["docs"]
    for query in ("stage=completed", f"department_id={ids['dept_a']}&doctor_user_id={docs['info']}"):
        _, summary = _assert_quality_invariants(client, query)
        assert summary["total_patients"] == 0 and summary["average_completeness"] is None
        for detail_type in ("quality_all", "quality_actionable", "quality_patients", "quality_critical", "quality_warning"):
            assert _details(client, detail_type, query) ["total"] == 0
    _, clean = _assert_quality_invariants(client, f"doctor_user_id={docs['clean']}")
    assert clean["total_patients"] == 1 and clean["average_completeness"] == 100 and clean["total_issues"] == 0
    for query in (
        f"department_id={ids['dept_a']}",
        f"department_id={ids['dept_b']}",
        "stage=appointment",
        f"department_id={ids['dept_b']}&doctor_user_id={docs['mix']}&stage=appointment",
        f"department_id={ids['dept_a']}&stage=appointment&date_from=2026-01-01&date_to=2026-12-31",
    ):
        _assert_quality_invariants(client, query)


def test_quality_details_respect_role_scope(client):
    ids = _detail_stand(client)
    p, docs = ids["patients"], ids["docs"]
    everyone = set(p.values())
    dept_a_patients = {p["crit"], p["warn_phone"], p["warn_bad_phone"]}
    scopes = {
        "detail.dev": everyone,
        "detail.deputy": everyone,
        "detail.caop": everyone,
        "detail.head.a": dept_a_patients,
        "detail.doc.warn": {p["warn_phone"], p["warn_bad_phone"]},
        "detail.chair": set(),
    }
    for username, allowed in scopes.items():
        _as_user(client, username)
        for detail_type in ("quality_all", "quality_actionable", "quality_patients"):
            items, total = _all_details(client, detail_type)
            seen = {item["patient_id"] for item in items}
            assert seen <= allowed, (username, detail_type, seen - allowed)
            assert total == len(items)
        _assert_quality_invariants(client)
    # Фильтры не расширяют область: чужой врач и чужой код дают пустую выборку.
    _as_user(client, "detail.doc.warn")
    assert _details(client, "quality_all", f"doctor_user_id={docs['info']}")["total"] == 0
    assert _details(client, "quality_all", code="missing_policy_number")["total"] == 0
    assert _details(client, "quality_patients", f"department_id={ids['dept_b']}")["total"] == 0


def test_quality_details_pagination_more_than_fifty_without_duplicates(client):
    dept = create_department("Детализация Много")
    create_account("detail.many.dev", "developer", "Разработчик Многих")
    doctor = create_account("detail.many.doc", "doctor", "Врач Многих", department_ids=[dept])
    for idx in range(63):
        _create_patient(doctor, name=f"Много Пациент {idx:03d}", phone=f"+7 (900) 7{idx:02d}-00-00",
                        policyNumber="", stage="appointment", events=[])
    _as_user(client, "detail.many.dev")
    first = _details(client, "quality_all", code="missing_policy_number", limit=50, offset=0)
    second = _details(client, "quality_all", code="missing_policy_number", limit=50, offset=50)
    assert first["total"] == second["total"] == 63
    assert len(first["items"]) == 50 and len(second["items"]) == 13
    keys = [(item["patient_id"], item["code"]) for item in first["items"] + second["items"]]
    assert len(keys) == len(set(keys)) == 63
    # Стабильный порядок: повторный запрос той же страницы даёт те же записи.
    assert _details(client, "quality_all", code="missing_policy_number", limit=50, offset=0)["items"] == first["items"]
    items, total = _all_details(client, "quality_patients", f"doctor_user_id={doctor}")
    assert total == 63 and len({item["patient_id"] for item in items}) == 63
    small = [_details(client, "quality_all", limit=7, offset=offset)["items"] for offset in range(0, 63, 7)]
    assert [item["patient_id"] for page in small for item in page] == [item["patient_id"] for item in first["items"] + second["items"]]


def test_quality_details_validation_and_legacy_types(client):
    _detail_stand(client)
    _as_user(client, "detail.dev")
    assert client.get("/api/analytics/details?type=quality_all&code=unknown_code").status_code == 422
    assert client.get("/api/analytics/details?type=patients&code=missing_policy_number").status_code == 422
    assert client.get("/api/analytics/details?type=quality_patients&code=missing_policy_number").status_code == 422
    assert client.get("/api/analytics/details?type=quality_everything").status_code == 422
    assert client.get(f"/api/analytics/details?type=quality_all&code={'x' * 61}").status_code == 422
    critical = _details(client, "quality_critical")
    warning = _details(client, "quality_warning")
    assert critical["total"] >= 1 and {item["severity"] for item in critical["items"]} == {"critical"}
    assert warning["total"] >= 1 and {item["severity"] for item in warning["items"]} == {"warning"}
    for item in critical["items"] + warning["items"]:
        assert {"code", "severity", "title", "patient_id", "patient_name", "stage_label"} <= set(item)
    for detail_type in ("patients", "unassigned_patients", "attention_patients", "overdue_events", "incomplete_checklists",
                        "notifications_pending", "notifications_failed"):
        assert client.get(f"/api/analytics/details?type={detail_type}").status_code == 200
    for report in ("summary", "data_quality"):
        export = client.post("/api/analytics/export", json={"reportType": report, "detailLevel": "details"}, headers=client.csrf)
        assert export.status_code == 200 and export.content.startswith("﻿".encode())


def test_quality_details_same_code_issues_have_distinguishing_context(client):
    create_account("context.dev", "developer", "Разработчик Контекста")
    _create_patient(None, name="Контекст События", stage="appointment",
                    events=[{"date": "2000-01-01"}, {"date": "2001-02-03"}])
    route = _create_patient(None, name="Контекст Маршрута", phone="+7 (900) 111-22-33", stage="appointment", events=[])
    conn = get_conn()
    with conn:
        conn.execute("UPDATE history_entries SET entered_at = '2020-01-01T00:00:00+00:00' WHERE patient_id = ?", (route,))
        for seq, stage in ((1, "treatment"), (2, "exam"), (3, "appointment")):
            conn.execute(
                """INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, checklist_json, note)
                   VALUES (?, ?, ?, ?, ?, NULL, '')""",
                (f"context-route-{seq}", route, seq, stage, f"2020-0{seq + 1}-01T00:00:00+00:00"),
            )
    _as_user(client, "context.dev")
    events = _details(client, "quality_all", code="overdue_event")
    assert events["total"] == 2
    assert len({item["patient_id"] for item in events["items"]}) == 1
    assert [item["context"] for item in events["items"]] == [
        {"history_number": 1, "event_number": 1, "planned_date": "2000-01-01"},
        {"history_number": 1, "event_number": 2, "planned_date": "2001-02-03"},
    ]
    history = _details(client, "quality_all", code="stage_sequence_violation")
    assert history["total"] == 2
    assert [item["context"] for item in history["items"]] == [{"history_number": 3}, {"history_number": 4}]
    # Наружу — только разрешённые ключи контекста, без объекта details.
    for item in events["items"] + history["items"]:
        assert set(item["context"]) <= {"history_number", "event_number", "planned_date"}
        assert "details" not in item
    # Проблемы, не связанные с событиями и маршрутом, — без контекста.
    unassigned = _details(client, "quality_all", code="missing_treating_doctor")
    assert unassigned["total"] == 2 and all(item["context"] == {} for item in unassigned["items"])
