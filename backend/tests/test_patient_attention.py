import json

from .conftest import TEST_PASSWORD, create_account, create_department, get_conn, login, make_patient_payload


def _create_patient(client, **overrides):
    r = client.post("/api/patients", json=make_patient_payload(**overrides), headers=client.csrf)
    assert r.status_code == 201
    return r.json()


def _create_issue(client, patient_id, **overrides):
    payload = {
        "reasonCode": "doctor_decision_required",
        "comment": "Короткий тестовый комментарий",
        "eventDate": None,
    }
    payload.update(overrides)
    return client.post(f"/api/patients/{patient_id}/attention", json=payload, headers=client.csrf)


def test_attention_issue_lifecycle_patient_status_and_history(auth_client):
    patient = _create_patient(auth_client)
    pid = patient["id"]

    first = _create_issue(auth_client, pid, reasonCode="emergency_hospitalization", eventDate="2026-08-01")
    assert first.status_code == 201
    second = _create_issue(auth_client, pid, reasonCode="documents_or_data_issue", comment="Документы требуют проверки")
    assert second.status_code == 201

    listed = auth_client.get("/api/patients").json()[0]
    assert listed["attention"]["active"] is True
    assert listed["attention"]["activeCount"] == 2

    detail = auth_client.get(f"/api/patients/{pid}/attention").json()
    assert [i["reasonCode"] for i in detail["active"]] == ["documents_or_data_issue", "emergency_hospitalization"]

    issue_id = detail["active"][0]["id"]
    closed = auth_client.post(
        f"/api/patients/{pid}/attention/{issue_id}/close",
        json={"resolutionComment": "Закрыто после проверки"},
        headers=auth_client.csrf,
    )
    assert closed.status_code == 200
    assert auth_client.post(
        f"/api/patients/{pid}/attention/{issue_id}/close",
        json={"resolutionComment": ""},
        headers=auth_client.csrf,
    ).status_code == 409

    detail = auth_client.get(f"/api/patients/{pid}/attention").json()
    assert len(detail["active"]) == 1
    assert len(detail["history"]) == 1
    assert auth_client.get(f"/api/patients/{pid}").json()["attention"]["activeCount"] == 1


def test_attention_validation_csrf_and_strict_schema(auth_client):
    patient = _create_patient(auth_client)
    pid = patient["id"]

    assert auth_client.post(f"/api/patients/{pid}/attention", json={"reasonCode": "other"}).status_code == 403
    assert _create_issue(auth_client, pid, reasonCode="unknown").status_code == 422
    assert _create_issue(auth_client, pid, reasonCode="emergency_hospitalization", eventDate=None).status_code == 422
    assert auth_client.post(
        f"/api/patients/{pid}/attention",
        json={"reasonCode": "other", "comment": "", "eventDate": None, "unexpected": True},
        headers=auth_client.csrf,
    ).status_code == 422
    assert _create_issue(auth_client, pid, comment="x" * 801).status_code == 422


def test_attention_scope_rbac_and_patient_filters(client):
    dept_a = create_department("Тестовое отделение A")
    dept_b = create_department("Тестовое отделение B")
    doctor_a = create_account("doctor.a", "doctor", "Докторов A", department_ids=[dept_a])
    doctor_b = create_account("doctor.b", "doctor", "Докторов B", department_ids=[dept_b])
    create_account("head.a", "department_head", "Заведующий A", department_ids=[dept_a])

    create_account("dev.scope", "developer", "Разработчик Scope")
    assert login(client, "dev.scope", TEST_PASSWORD).status_code == 200
    other_id = _create_patient(client, name="Пациент B", treatingDoctorUserId=doctor_b)["id"]

    assert login(client, "doctor.a", TEST_PASSWORD).status_code == 200
    own = _create_patient(client, name="Пациент A", treatingDoctorUserId=doctor_a)
    assert _create_issue(client, own["id"], reasonCode="critical_overdue").status_code == 201
    assert _create_issue(client, other_id, reasonCode="critical_overdue").status_code == 404
    assert client.get(f"/api/patients/{other_id}/attention").status_code == 404

    active = client.get("/api/patients?attention=active").json()
    assert [p["id"] for p in active] == [own["id"]]
    none = client.get("/api/patients?attention=none").json()
    assert all(p["id"] != own["id"] for p in none)

    assert login(client, "head.a", TEST_PASSWORD).status_code == 200
    assert client.get(f"/api/patients/{own['id']}/attention").status_code == 200
    assert client.get(f"/api/patients/{other_id}/attention").status_code == 404


def test_attention_analytics_notifications_and_read_is_not_close(auth_client):
    patient = _create_patient(auth_client)
    pid = patient["id"]
    issue = _create_issue(auth_client, pid, reasonCode="route_change").json()["issue"]

    dashboard = auth_client.get("/api/analytics/dashboard").json()
    cards = {c["code"]: c for c in dashboard["summary"]["cards"]}
    assert cards["patients_need_attention"]["value"] == 1
    assert dashboard["attention"]["rows"][0]["code"] == "route_change"
    assert dashboard["operations"]["title"] == "Операционные сигналы"

    notifications = auth_client.get("/api/internal-notifications?type=attention").json()
    assert notifications["total"] == 1
    item = notifications["items"][0]
    assert item["source_id"] == issue["id"]
    assert item["read"] is False
    assert issue["comment"] not in json.dumps(item, ensure_ascii=False)

    auth_client.post(f"/api/internal-notifications/{item['id']}/read", headers=auth_client.csrf)
    assert auth_client.get(f"/api/patients/{pid}").json()["attention"]["active"] is True

    auth_client.post(
        f"/api/patients/{pid}/attention/{issue['id']}/close",
        json={"resolutionComment": ""},
        headers=auth_client.csrf,
    )
    assert auth_client.get("/api/internal-notifications?type=attention").json()["total"] == 0


def test_attention_audit_contains_no_phi_or_comments(auth_client):
    patient = _create_patient(
        auth_client,
        name="Секретов Пациент",
        phone="+7 900 111-22-33",
        diagnosis="C50.1",
        nosology="breast50",
        policyNumber="7700000000001234",
    )
    pid = patient["id"]
    created = _create_issue(
        auth_client,
        pid,
        reasonCode="patient_unavailable",
        comment="Свободный комментарий с чувствительным текстом",
    ).json()["issue"]
    auth_client.post(
        f"/api/patients/{pid}/attention/{created['id']}/close",
        json={"resolutionComment": "Закрывающий чувствительный текст"},
        headers=auth_client.csrf,
    )
    text = json.dumps(auth_client.get("/api/audit").json(), ensure_ascii=False)
    for marker in (
        "Секретов Пациент",
        "+7 900 111-22-33",
        "C50.1",
        "breast50",
        "7700000000001234",
        "Свободный комментарий",
        "Закрывающий чувствительный текст",
        "patient_unavailable",
    ):
        assert marker not in text
    assert "attention_issue_created" in text
    assert "attention_issue_closed" in text


def test_schema_v5_to_v6_preserves_internal_notification_state_and_patients(auth_client):
    from backend.app import db

    patient = _create_patient(auth_client, name="Миграционный Пациент")
    conn = get_conn()
    user_id = conn.execute("SELECT id FROM users WHERE username = 'dev.tester'").fetchone()["id"]
    with conn:
        conn.execute(
            """INSERT INTO internal_notification_state
               (id, user_id, stable_key, patient_id, notification_type, source_type, source_id,
                first_seen_at, last_seen_at, read_at, resolved_at)
               VALUES ('legacy_state', ?, 'today:event:legacy', ?, 'today', 'event', 'legacy',
                       '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00',
                       '2026-01-01T00:01:00+00:00', NULL)""",
            (user_id, patient["id"]),
        )
        conn.execute("UPDATE schema_version SET version = 5")
    db.init_db(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 6
    assert conn.execute("SELECT COUNT(*) FROM patient_attention_issues").fetchone()[0] == 0
    row = conn.execute("SELECT read_at FROM internal_notification_state WHERE id = 'legacy_state'").fetchone()
    assert row["read_at"] == "2026-01-01T00:01:00+00:00"
    assert conn.execute("SELECT policy_number FROM patients WHERE id = ?", (patient["id"],)).fetchone()[0] == "7700000000000000"
