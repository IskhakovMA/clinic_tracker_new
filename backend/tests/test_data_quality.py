"""Data quality center: backend rules, API contract and RBAC scope."""
import json
from datetime import date

from .conftest import (
    TEST_PASSWORD,
    create_account,
    create_department,
    get_conn,
    login,
    make_patient_payload,
)


def _as_user(client, username: str):
    r = login(client, username)
    assert r.status_code == 200, r.text
    return client


def _create_patient(doctor_id=None, **overrides):
    from backend.app import store
    from backend.app.models import PatientCreateIn

    conn = get_conn()
    data = PatientCreateIn(**make_patient_payload(**overrides))
    return store.create_patient(conn, data, doctor_id)


def _issue_codes(payload):
    return {i["code"] for i in payload["issues"]}


def _issue(payload, code):
    return next(i for i in payload["issues"] if i["code"] == code)


def test_rules_detect_assignment_completeness_phone_dates_stage_and_checklist(auth_client):
    pid = _create_patient(
        None,
        phone="телефон неизвестен",
        nosology="lung34",
        stage="exam",
        events=[{"date": "2000-01-01"}],
    )
    conn = get_conn()
    with conn:
        conn.execute("UPDATE patients SET diagnosis = '', stage = '' WHERE id = ?", (pid,))
        conn.execute(
            """UPDATE events SET actual_date = '2999-01-01'
               WHERE history_entry_id IN (SELECT id FROM history_entries WHERE patient_id = ?)""",
            (pid,),
        )
        conn.execute(
            """INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, checklist_json, note)
               VALUES ('dq_bad_stage', ?, 1, 'primary-appointment', '1999-01-01T00:00:00+00:00', NULL, '')""",
            (pid,),
        )
    body = auth_client.get(f"/api/data-quality/patients/{pid}").json()
    codes = _issue_codes(body)
    assert {
        "missing_treating_doctor",
        "missing_stage",
        "missing_diagnosis",
        "invalid_phone",
        "actual_date_in_future",
        "stage_sequence_violation",
    } <= codes
    assert _issue(body, "missing_stage")["severity"] == "critical"
    assert _issue(body, "invalid_phone")["severity"] == "warning"


def test_recommended_completeness_fields_and_score(auth_client):
    pid = _create_patient(None, phone="", nosology="", diagnosis="C34.1", stage="exam")
    body = auth_client.get(f"/api/data-quality/patients/{pid}").json()
    assert body["patient"]["completeness"]["score"] == 57
    codes = _issue_codes(body)
    assert "missing_required_field" in codes  # phone
    assert "missing_client_path_profile" in codes
    assert "missing_treating_doctor" in codes


def test_overdue_event_and_future_plan(auth_client):
    pid = _create_patient(events=[{"date": "2000-01-01"}])
    auth_client.put(
        f"/api/patients/{pid}/future-plans/3",
        json={"field": "date", "value": "2000-02-01"},
        headers=auth_client.csrf,
    )
    body = auth_client.get(f"/api/data-quality/patients/{pid}").json()
    codes = _issue_codes(body)
    assert "overdue_event" in codes
    assert "planned_date_in_past" in codes
    assert _issue(body, "overdue_event")["details"]["days_overdue"] > 0


def test_date_rules_are_distinct_and_today_is_not_overdue(auth_client):
    today = date(2026, 8, 3)
    today_pid = _create_patient(events=[{"date": today.isoformat()}])
    completed_pid = _create_patient(stage="completed")
    completed_client_id = completed_pid
    auth_client.put(
        f"/api/patients/{completed_client_id}/future-plans/3",
        json={"field": "date", "value": "2000-02-01"},
        headers=auth_client.csrf,
    )
    actual_pid = _create_patient(events=[{"date": "2000-01-01", "actualDate": "2000-01-02"}])
    cancelled_plan_pid = _create_patient(events=[{"date": "2000-01-01", "noPlan": True}])

    conn = get_conn()
    from backend.app import policy
    from backend.app.services import data_quality

    current = policy.load_current_user(
        conn,
        conn.execute("SELECT id FROM users WHERE username = ?", ("dev.tester",)).fetchone()["id"],
    )
    result = data_quality.evaluate(conn, current, today=today)
    by_patient = {}
    for issue in result["issues"]:
        by_patient.setdefault(issue["patient_id"], set()).add(issue["code"])

    assert "overdue_event" not in by_patient.get(today_pid, set())
    assert "planned_date_in_past" not in by_patient.get(today_pid, set())
    assert "planned_date_in_past" not in by_patient.get(completed_pid, set())
    assert "overdue_event" not in by_patient.get(actual_pid, set())
    assert "overdue_event" not in by_patient.get(cancelled_plan_pid, set())


def test_incomplete_required_checklist(auth_client):
    pid = _create_patient(nosology="lung34", stage="exam")
    body = auth_client.get(f"/api/data-quality/patients/{pid}").json()
    item = _issue(body, "incomplete_required_checklist")
    assert item["details"]["missing_count"] >= 1
    assert item["suggested_action"]["tab"] == "checklist"


def test_duplicate_detection_by_phone_and_name_age(auth_client):
    _create_patient(name="Дублев Один", age=40, phone="+7 (999) 111-22-33")
    _create_patient(name="Другой Пациент", age=50, phone="8 999 111 22 33")
    _create_patient(name="Совпадов Имя", age=60, phone="+7 900 000-00-01")
    _create_patient(name="  совпадов   имя  ", age=60, phone="+7 900 000-00-02")

    body = auth_client.get("/api/data-quality/issues?code=possible_duplicate").json()
    reasons = {reason for g in body["duplicate_groups"] for reason in g["reasons"]}
    assert "Совпадает телефон" in reasons
    assert "Совпадают ФИО и возраст" in reasons
    assert body["total"] >= 4


def test_duplicate_group_merges_reasons_for_same_patient_pair(auth_client):
    first = _create_patient(
        name="Мульти Дубль",
        age=55,
        phone="+7 (999) 333-44-55",
        diagnosis="C50.1",
        nosology="lung34",
    )
    second = _create_patient(
        name="  мульти   дубль  ",
        age=55,
        phone="8 999 333 44 55",
        diagnosis="C50.1",
        nosology="breast",
    )

    body = auth_client.get("/api/data-quality/issues?code=possible_duplicate").json()
    groups = [
        g for g in body["duplicate_groups"]
        if set(g["patient_ids"]) == {first, second}
    ]
    assert len(groups) == 1
    group = groups[0]
    assert group["patient_ids"] == sorted({first, second})
    assert group["reason_codes"] == ["phone", "name_age", "name_diagnosis"]
    assert group["reasons"] == [
        "Совпадает телефон",
        "Совпадают ФИО и возраст",
        "Совпадают ФИО и диагноз",
    ]
    assert len(group["patient_ids"]) == len(set(group["patient_ids"]))


def test_no_false_duplicate_when_evidence_is_insufficient(auth_client):
    _create_patient(name="Только Имя", age=40, phone="+7 900 000-00-01")
    _create_patient(name="Только Имя", age=41, phone="+7 900 000-00-02", diagnosis="C50.1", nosology="")
    body = auth_client.get("/api/data-quality/issues?code=possible_duplicate").json()
    assert body["duplicate_groups"] == []
    assert body["issues"] == []


def _rbac_quality_stand(client):
    from backend.app import store
    from backend.app.models import PatientCreateIn

    ids = {}
    ids["dept_a"] = create_department("Качество А")
    ids["dept_b"] = create_department("Качество Б")
    ids["developer"] = create_account("dq.dev", "developer", "Разработчик Качество")
    ids["deputy"] = create_account("dq.deputy", "deputy_chief_oncology", "Просмотр Глобальный")
    ids["caop"] = create_account("dq.caop", "caop_head", "Просмотр ЦАОП")
    ids["head_a"] = create_account("dq.head.a", "department_head", "Заведующий А", department_ids=[ids["dept_a"]])
    ids["doc_a"] = create_account("dq.doc.a", "doctor", "Врач А", department_ids=[ids["dept_a"]])
    ids["doc_b"] = create_account("dq.doc.b", "doctor", "Врач Б", department_ids=[ids["dept_b"]])

    conn = get_conn()
    ids["p_a"] = store.create_patient(conn, PatientCreateIn(**make_patient_payload(name="Пациент А", phone="+7 999 222-22-22")), ids["doc_a"])
    ids["p_b"] = store.create_patient(conn, PatientCreateIn(**make_patient_payload(name="Пациент Б", phone="+7 999 222-22-22")), ids["doc_b"])
    ids["p_unresolved"] = store.create_patient(conn, PatientCreateIn(**make_patient_payload(name="Без Врача")), None)
    return ids


def test_summary_respects_scope_for_developer_readonly_head_and_doctor(client):
    ids = _rbac_quality_stand(client)

    _as_user(client, "dq.dev")
    assert client.get("/api/data-quality/summary").json()["total_patients"] == 3
    assert client.get("/api/data-quality/summary").json()["unresolved_patients"] == 1

    _as_user(client, "dq.deputy")
    body = client.get("/api/data-quality/issues").json()
    assert {p["patient_id"] for p in body["patients"]} == {ids["p_a"], ids["p_b"], ids["p_unresolved"]}
    assert all(p["can_edit"] is False for p in body["patients"])
    assert client.get(f"/api/data-quality/patients/{ids['p_a']}").status_code == 200

    _as_user(client, "dq.caop")
    body = client.get("/api/data-quality/issues").json()
    assert {p["patient_id"] for p in body["patients"]} == {ids["p_a"], ids["p_b"], ids["p_unresolved"]}
    assert all(p["can_edit"] is False for p in body["patients"])
    assert client.get(f"/api/data-quality/patients/{ids['p_a']}").status_code == 200

    _as_user(client, "dq.head.a")
    body = client.get("/api/data-quality/issues").json()
    assert {p["patient_id"] for p in body["patients"]} == {ids["p_a"]}
    assert client.get(f"/api/data-quality/patients/{ids['p_b']}").status_code == 404
    assert client.get(f"/api/data-quality/patients/{ids['p_unresolved']}").status_code == 404

    _as_user(client, "dq.doc.a")
    body = client.get("/api/data-quality/issues?code=possible_duplicate").json()
    assert body["duplicate_groups"] == []  # duplicate with B is outside scope
    assert {p["patient_id"] for p in client.get("/api/data-quality/issues").json()["patients"]} == {ids["p_a"]}


def test_query_params_cannot_escape_scope(client):
    ids = _rbac_quality_stand(client)
    _as_user(client, "dq.doc.a")
    body = client.get(f"/api/data-quality/issues?patient_id={ids['p_b']}").json()
    assert body["issues"] == []
    assert body["patients"] == []
    assert client.get("/api/data-quality/issues?treating_doctor=Врач Б").json()["patients"] == []
    assert client.get("/api/data-quality/issues?department=Качество Б").json()["patients"] == []


def test_reading_quality_and_crud_correction_do_not_add_audit_phi_and_readonly_cannot_write(client):
    ids = _rbac_quality_stand(client)
    conn = get_conn()
    with conn:
        conn.execute(
            "UPDATE patients SET name = ?, phone = ?, diagnosis = ?, nosology = ? WHERE id = ?",
            ("PHI Audit Пациент", "+7 999 222-22-22", "C99.PHI-AUDIT", "lung34", ids["p_a"]),
        )
    _as_user(client, "dq.deputy")
    client.get("/api/data-quality/summary")
    client.get("/api/data-quality/issues")
    assert client.patch(
        f"/api/patients/{ids['p_a']}",
        json={"field": "phone", "value": "+7 900 111-11-11"},
        headers=client.csrf,
    ).status_code == 403

    _as_user(client, "dq.dev")
    assert client.patch(
        f"/api/patients/{ids['p_a']}",
        json={"field": "phone", "value": "+7 900 111-11-11"},
        headers=client.csrf,
    ).status_code == 200
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)
    forbidden_markers = (
        "PHI Audit Пациент",
        "+7 999 222-22-22",
        "+7 900 111-11-11",
        "C99.PHI-AUDIT",
        "lung34",
        "Профиль клиентского пути",
        "Пациент не сопоставлен с учётной записью лечащего врача",
        "Совпадает телефон",
        "Совпадают ФИО и диагноз",
    )
    for marker in forbidden_markers:
        assert marker not in audit_text


def test_issues_filters_pagination_empty_and_ordering(auth_client):
    pid = _create_patient(None, phone="", nosology="")
    conn = get_conn()
    with conn:
        conn.execute("UPDATE patients SET diagnosis = '' WHERE id = ?", (pid,))
    empty = auth_client.get("/api/data-quality/issues?severity=info&code=invalid_phone").json()
    assert empty["issues"] == []
    assert empty["total"] == 0

    page = auth_client.get("/api/data-quality/issues?severity=critical&limit=1&offset=0").json()
    assert page["limit"] == 1
    assert page["total"] >= 1
    assert len(page["issues"]) == 1
    assert page["issues"][0]["patient_id"] == pid


def test_issues_filters_validate_params_scope_pagination_and_ordering(client):
    from backend.app import store
    from backend.app.models import PatientCreateIn

    dept_a = create_department("Фильтры А")
    dept_b = create_department("Фильтры Б")
    create_account("dq.filter.dev", "developer", "Разработчик Фильтров")
    doc_a = create_account("dq.filter.doc.a", "doctor", "Врач Фильтр А", department_ids=[dept_a])
    doc_b = create_account("dq.filter.doc.b", "doctor", "Врач Фильтр Б", department_ids=[dept_b])

    conn = get_conn()
    alpha = store.create_patient(
        conn,
        PatientCreateIn(**make_patient_payload(
            name="Альфа Фильтр",
            phone="",
            nosology="",
        )),
        doc_a,
    )
    with conn:
        conn.execute("UPDATE patients SET diagnosis = '' WHERE id = ?", (alpha,))
    beta = store.create_patient(
        conn,
        PatientCreateIn(**make_patient_payload(
            name="Бета Фильтр",
            phone="телефон",
            diagnosis="C50.1",
            nosology="breast",
        )),
        doc_b,
    )

    _as_user(client, "dq.filter.dev")
    cases = {
        "severity=critical": alpha,
        "category=completeness": alpha,
        "code=invalid_phone": beta,
        f"patient_id={alpha}": alpha,
        "treating_doctor=Врач Фильтр А": alpha,
        "department=Фильтры А": alpha,
        "search=Альфа": alpha,
    }
    for query, expected_patient_id in cases.items():
        body = client.get(f"/api/data-quality/issues?{query}").json()
        assert body["total"] >= 1, query
        assert {issue["patient_id"] for issue in body["issues"]} == {expected_patient_id}
        assert {patient["patient_id"] for patient in body["patients"]} == {expected_patient_id}

    first = client.get("/api/data-quality/issues?limit=1&offset=0").json()
    second = client.get("/api/data-quality/issues?limit=1&offset=1").json()
    full = client.get("/api/data-quality/issues?limit=2&offset=0").json()
    assert first["limit"] == 1 and second["offset"] == 1
    assert [first["issues"][0], second["issues"][0]] == full["issues"][:2]
    assert client.get("/api/data-quality/issues?limit=2&offset=0").json()["issues"] == full["issues"]

    for query in (
        "severity=bad",
        "category=bad",
        "code=bad",
        "sort=bad",
        "limit=0",
        "limit=201",
        "offset=-1",
    ):
        assert client.get(f"/api/data-quality/issues?{query}").status_code == 422, query

    _as_user(client, "dq.filter.doc.a")
    assert client.get("/api/data-quality/issues?department=Фильтры Б").json()["patients"] == []
    assert client.get("/api/data-quality/issues?treating_doctor=Врач Фильтр Б").json()["patients"] == []
    hidden = client.get(f"/api/data-quality/issues?patient_id={beta}").json()
    assert hidden["issues"] == []
    assert hidden["patients"] == []


def test_direct_evaluator_has_deterministic_completeness(auth_client):
    from backend.app import policy
    from backend.app.services import data_quality

    pid = _create_patient(None, phone="", nosology="")
    conn = get_conn()
    with conn:
        conn.execute("UPDATE patients SET diagnosis = '' WHERE id = ?", (pid,))
    conn = get_conn()
    user = policy.load_current_user(conn, "missing")  # prove no accidental global
    assert user is None
    current = policy.load_current_user(conn, conn.execute("SELECT id FROM users WHERE username = ?", ("dev.tester",)).fetchone()["id"])
    first = data_quality.evaluate(conn, current, today=date(2026, 8, 3))
    second = data_quality.evaluate(conn, current, today=date(2026, 8, 3))
    assert first["patients"][0]["completeness"] == second["patients"][0]["completeness"]


def test_quality_summary_and_issues_handle_1000_fake_patients_without_n_plus_one(auth_client):
    for i in range(1000):
        _create_patient(
            name="Нагрузочный Дубль" if i in (0, 1) else f"Нагрузочный Пациент {i}",
            phone="+7 900 199-00-00" if i in (0, 1) else f"+7 900 1{i:06d}",
            diagnosis="C34.1",
            nosology="lung34",
        )
    summary = auth_client.get("/api/data-quality/summary").json()
    issues_page = auth_client.get("/api/data-quality/issues?limit=200").json()
    duplicates_page = auth_client.get("/api/data-quality/issues?code=possible_duplicate").json()
    assert summary["total_patients"] == 1000
    assert "average_completeness" in summary
    assert issues_page["limit"] == 200
    assert issues_page["total"] >= 1000
    assert duplicates_page["duplicate_groups"]

    from backend.app import policy
    from backend.app.services import data_quality

    conn = get_conn()
    current = policy.load_current_user(
        conn,
        conn.execute("SELECT id FROM users WHERE username = ?", ("dev.tester",)).fetchone()["id"],
    )
    selects = []

    def trace(sql):
        if sql.lstrip().upper().startswith("SELECT"):
            selects.append(sql)

    conn.set_trace_callback(trace)
    try:
        result = data_quality.evaluate(conn, current, today=date(2026, 8, 3))
    finally:
        conn.set_trace_callback(None)
    assert result["summary"]["total_patients"] == 1000
    assert result["summary"]["possible_duplicate_groups"] >= 1
    assert len(selects) <= 4
