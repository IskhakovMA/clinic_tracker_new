import json
import sqlite3
from pathlib import Path

from .conftest import get_conn, make_patient_payload


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _create_patient(auth_client, **overrides):
    response = auth_client.post(
        "/api/patients",
        json=make_patient_payload(**overrides),
        headers=auth_client.csrf,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_schema_v6_to_v7_adds_codes_and_diagnosis_history(tmp_path):
    from backend.app import config, db, maintenance

    path = tmp_path / "clinic.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (6)")
        conn.execute(
            """CREATE TABLE patients (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                age INTEGER,
                priority TEXT NOT NULL,
                stage TEXT NOT NULL,
                phone TEXT,
                policy_number TEXT,
                complaint TEXT,
                diagnosis TEXT,
                nosology TEXT,
                disease_stage TEXT,
                arrival TEXT NOT NULL,
                doctor TEXT,
                head TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                treating_doctor_user_id TEXT
            )"""
        )
        conn.execute(
            """INSERT INTO patients
               (id, name, age, priority, stage, phone, policy_number, complaint, diagnosis,
                nosology, disease_stage, arrival, doctor, head, created_at, updated_at, treating_doctor_user_id)
               VALUES ('legacy_v6', 'Пациент V6', 50, 'normal', 'exam', '+7 900 000-00-00',
                       '7700000000000000', 'жалобы', 'D44.0', 'thyroidSuspected', 'II',
                       '2026-01-01T00:00:00+00:00', 'Врач V6', 'Заведующий V6',
                       '2026-01-01T00:00:00+00:00', NULL, NULL)"""
        )
    db.init_db(conn)

    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 7
    patient_columns = {row["name"] for row in conn.execute("PRAGMA table_info(patients)").fetchall()}
    assert {"consilium_code", "pgi_code"} <= patient_columns
    row = conn.execute("SELECT diagnosis, policy_number FROM patients WHERE id = 'legacy_v6'").fetchone()
    assert row["diagnosis"] == "D44.0"
    assert row["policy_number"] == "7700000000000000"
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "patient_diagnosis_history" in tables
    report = maintenance.check_database(path)
    assert report["ok"] is True, report["problems"]
    assert report["schema_version"] == config.SCHEMA_VERSION


def test_d37_d48_dictionary_and_search_ui_are_local_and_safe():
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    index = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")

    for code in ("D37.4", "D44.0", "D48.9"):
        assert f"code: '{code}'" in app_js
    assert "filterDiagnosisOptions" in app_js
    assert "diagnosisSearch" in index
    assert "innerHTML" not in app_js
    assert "onchange=" not in index.lower()


def test_diagnosis_change_history_and_audit_without_phi(auth_client):
    created = _create_patient(auth_client, diagnosis="D44.0")
    response = auth_client.patch(
        f"/api/patients/{created['id']}",
        json={"field": "diagnosis", "value": "C73"},
        headers=auth_client.csrf,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["diagnosis"] == "C73"
    assert body["diagnosisHistory"][-1]["oldDiagnosis"] == "D44.0"
    assert body["diagnosisHistory"][-1]["newDiagnosis"] == "C73"

    audit = auth_client.get("/api/audit").json()["events"]
    diagnosis_events = [event for event in audit if event["event_type"] == "patient_diagnosis_changed"]
    assert diagnosis_events
    assert diagnosis_events[-1]["detail"] is None
    audit_text = json.dumps(audit, ensure_ascii=False)
    assert "D44.0" not in audit_text
    assert "C73" not in audit_text


def test_consilium_and_pgi_codes_roundtrip_and_audit_without_values(auth_client):
    created = _create_patient(auth_client, consiliumCode="OK-123", pgiCode="PGI-456")
    assert created["consiliumCode"] == "OK-123"
    assert created["pgiCode"] == "PGI-456"

    updated = auth_client.patch(
        f"/api/patients/{created['id']}",
        json={"field": "consiliumCode", "value": "OK-999"},
        headers=auth_client.csrf,
    )
    assert updated.status_code == 200, updated.text
    updated = auth_client.patch(
        f"/api/patients/{created['id']}",
        json={"field": "pgiCode", "value": "PGI-999"},
        headers=auth_client.csrf,
    )
    assert updated.status_code == 200, updated.text

    exported = json.loads(auth_client.get("/api/export").text)
    patient = exported["patients"][0]
    assert patient["consiliumCode"] == "OK-999"
    assert patient["pgiCode"] == "PGI-999"
    audit_text = json.dumps(auth_client.get("/api/audit").json()["events"], ensure_ascii=False)
    assert "consilium_code_changed" in audit_text
    assert "pgi_code_changed" in audit_text
    assert "OK-999" not in audit_text
    assert "PGI-999" not in audit_text


def test_terminal_diagnosis_not_confirmed_has_no_operational_notifications_or_overdue(auth_client):
    patient = _create_patient(
        auth_client,
        stage="diagnosis-not-confirmed",
        events=[{"date": "2000-01-01"}],
    )
    quality = auth_client.get(f"/api/data-quality/patients/{patient['id']}").json()
    codes = {issue["code"] for issue in quality["issues"]}
    assert "overdue_event" not in codes
    assert "planned_date_in_past" not in codes

    from backend.app import policy
    from backend.app.services import internal_notifications, notifications

    conn = get_conn()
    user_id = conn.execute("SELECT id FROM users WHERE username = ?", ("dev.tester",)).fetchone()["id"]
    user = policy.load_current_user(conn, user_id)
    inbox = internal_notifications.list_items(conn, user)
    operational = {"overdue", "today", "upcoming", "checklist"}
    assert all(
        item["patient_id"] != patient["id"] or item["type"] not in operational
        for item in inbox["items"]
    )

    notifications.generate_due_reminders(conn, user)
    rows = conn.execute("SELECT COUNT(*) FROM notifications WHERE patient_id = ?", (patient["id"],)).fetchone()[0]
    assert rows == 0


def test_frontend_transition_question_removed_and_first_visit_sort_present():
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "состоялся по плану" not in app_js
    assert "first-visit-oldest" in app_js
    assert "firstVisitTime" in app_js
