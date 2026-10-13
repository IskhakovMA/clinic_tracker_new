"""Regression tests for the consilium security fixes (integration stage 2)."""
from datetime import date

from .conftest import DEV_USERNAME, create_account, get_conn, login
from .test_consilium_appointments import appointment_payload, create_patient


PATIENT_NAME = "Чужаков Скрытый Пациентович"
PATIENT_POLICY = "7700000000000777"


def _slot(slots, scheduled_date, scheduled_time, group="thoracic"):
    return next(
        slot for slot in slots
        if slot["scheduledDate"] == scheduled_date
        and slot["scheduledTime"] == scheduled_time
        and slot["consiliumGroup"] == group
    )


# --- Б1: /api/consilium/schedule ------------------------------------------

def _book_for_doctor_a(client):
    """Doctor A books their own patient on a chair grid slot and on a
    default current-week slot. Returns (doctor_a_id, patient, today)."""
    doctor_a = create_account("doctor.a", "doctor", full_name="Врачова Анна Первая")
    create_account("doctor.b", "doctor", full_name="Врачов Борис Второй")
    create_account("chair.schedule", "consilium_chair")
    create_account(DEV_USERNAME)

    login(client, "chair.schedule")
    generated = client.post(
        "/api/consilium/schedule/generate",
        json={
            "scheduledDate": "2026-11-12", "consiliumGroup": "thoracic",
            "startTime": "10:00", "endTime": "11:00", "intervalMinutes": 30,
        },
        headers=client.csrf,
    )
    assert generated.status_code == 200, generated.text

    login(client, DEV_USERNAME)
    patient = create_patient(
        client, name=PATIENT_NAME, policyNumber=PATIENT_POLICY, treatingDoctorUserId=doctor_a,
    )
    second = create_patient(
        client, name="Второй Скрытый Пациент", policyNumber="7700000000000778",
        treatingDoctorUserId=doctor_a,
    )

    login(client, "doctor.a")
    booked = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"], policyNumber=PATIENT_POLICY),
        headers=client.csrf,
    )
    assert booked.status_code == 201, booked.text
    today = date.today().isoformat()
    booked_week = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(second["id"], scheduledDate=today, scheduledTime="09:00"),
        headers=client.csrf,
    )
    assert booked_week.status_code == 201, booked_week.text
    return doctor_a, patient, today


def test_schedule_hides_other_doctors_appointments(client):
    _, patient, today = _book_for_doctor_a(client)

    login(client, "doctor.b")
    assert client.get(f"/api/patients/{patient['id']}").status_code == 404
    response = client.get("/api/consilium/schedule")
    assert response.status_code == 200
    slots = response.json()["slots"]

    for occupied in (_slot(slots, "2026-11-12", "10:30"), _slot(slots, today, "09:00")):
        assert occupied["occupied"] is True
        assert occupied["appointment"] is None

    body = response.text
    for secret in (
        PATIENT_NAME, "Второй Скрытый Пациент", PATIENT_POLICY, "7700000000000778",
        "C34.1", "Врачова Анна Первая", patient["id"],
    ):
        assert secret not in body


def test_schedule_shows_own_appointment_and_full_grid_to_chair(client):
    _, patient, today = _book_for_doctor_a(client)

    login(client, "doctor.a")
    own = _slot(client.get("/api/consilium/schedule").json()["slots"], "2026-11-12", "10:30")
    assert own["appointment"]["isMine"] is True
    assert own["appointment"]["patientName"] == PATIENT_NAME
    assert own["appointment"]["policyNumber"] == PATIENT_POLICY

    login(client, "chair.schedule")
    chair_slots = client.get("/api/consilium/schedule").json()["slots"]
    for scheduled_date, scheduled_time in (("2026-11-12", "10:30"), (today, "09:00")):
        details = _slot(chair_slots, scheduled_date, scheduled_time)["appointment"]
        assert details is not None
        assert details["isMine"] is False
    assert _slot(chair_slots, "2026-11-12", "10:30")["appointment"]["patientName"] == PATIENT_NAME


def test_schedule_hides_own_record_once_patient_leaves_scope(client):
    _, patient, _ = _book_for_doctor_a(client)
    doctor_b = get_conn().execute("SELECT id FROM users WHERE username = 'doctor.b'").fetchone()["id"]
    conn = get_conn()
    conn.execute("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (doctor_b, patient["id"]))
    conn.commit()

    login(client, "doctor.a")
    response = client.get("/api/consilium/schedule")
    occupied = _slot(response.json()["slots"], "2026-11-12", "10:30")
    assert occupied["occupied"] is True
    assert occupied["appointment"] is None
    assert PATIENT_NAME not in response.text


# --- Б2: избранное председателя -------------------------------------------

FAVORITE_NAME = "Избранов Закладка Консилиумович"


def _chair_with_favorite(client):
    """A doctor's patient is booked; the chair bookmarks that patient.
    Returns (patient, chair_user_id, snapshots_before_favorite)."""
    doctor_a = create_account("doctor.a", "doctor", full_name="Врачова Анна Первая")
    chair_id = create_account("chair.favorite", "consilium_chair")
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(
        client, name=FAVORITE_NAME, policyNumber="7700000000000999",
        treatingDoctorUserId=doctor_a, phone="+7 (999) 111-22-33",
    )
    login(client, "doctor.a")
    booked = client.post(
        "/api/consilium/appointments", json=appointment_payload(patient["id"]), headers=client.csrf,
    )
    assert booked.status_code == 201, booked.text

    login(client, "chair.favorite")
    before = {path: client.get(path).json() for path in SCOPED_LISTS}
    added = client.post(f"/api/consilium/favorites/{patient['id']}", json={}, headers=client.csrf)
    assert added.status_code == 200, added.text
    return patient, chair_id, before


def _stable(value):
    """Drop computation timestamps so two snapshots compare by content."""
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items() if k not in ("computed_at", "generated_at", "generatedAt")}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


SCOPED_LISTS = (
    "/api/patients",
    "/api/data-quality/issues",
    "/api/data-quality/summary",
    "/api/notifications",
    "/api/notifications/summary",
    "/api/internal-notifications",
    "/api/internal-notifications/summary",
    "/api/analytics/summary",
    "/api/analytics/dashboard",
)


def test_favorite_does_not_open_patient_card_or_its_subresources(client):
    patient, _, _ = _chair_with_favorite(client)
    pid = patient["id"]
    for path in (
        f"/api/patients/{pid}",
        f"/api/data-quality/patients/{pid}",
        f"/api/notifications/patients/{pid}",
    ):
        response = client.get(path)
        assert response.status_code == 404, (path, response.status_code)
        assert FAVORITE_NAME not in response.text
    edit = client.patch(
        f"/api/patients/{pid}", json={"field": "complaint", "value": "x"}, headers=client.csrf,
    )
    assert edit.status_code == 404


def test_favorite_does_not_change_scoped_lists_and_analytics(client):
    patient, _, before = _chair_with_favorite(client)
    for path in SCOPED_LISTS:
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code)
        assert FAVORITE_NAME not in response.text, path
        assert patient["id"] not in response.text, path
        assert _stable(response.json()) == _stable(before[path]), path


def test_favorite_does_not_reach_ai_tools(client):
    from backend.app import policy
    from backend.app.ai import tools

    patient, chair_id, _ = _chair_with_favorite(client)
    conn = get_conn()
    chair = policy.load_current_user(conn, chair_id)
    found = tools.execute_tool("search_accessible_patients", {"query": "Избранов"}, conn, chair)
    assert found == {"patients": []}
    for name in ("get_patient_summary", "get_patient_timeline", "get_patient_attention"):
        try:
            tools.execute_tool(name, {"patientId": patient["id"]}, conn, chair)
        except tools.ToolPermissionError:
            continue
        raise AssertionError(f"{name} returned data for a favorite outside scope")
    analytics = tools.execute_tool("get_analytics_summary", {}, conn, chair)
    assert FAVORITE_NAME not in str(analytics)
    assert patient["id"] not in str(analytics)


def test_chair_still_sees_minimal_consilium_record_for_favorite(client):
    patient, _, _ = _chair_with_favorite(client)
    listed = client.get("/api/consilium/appointments").json()["appointments"]
    assert [(row["patientId"], row["isFavorite"]) for row in listed] == [(patient["id"], True)]
    assert listed[0]["patientName"] == FAVORITE_NAME
    # Телефон и прочие поля карточки в запись консилиума не входят.
    assert "phone" not in listed[0]
    assert "+7 (999) 111-22-33" not in str(listed[0])


# --- Б3: переходы состояний ----------------------------------------------

import sqlite3  # noqa: E402

import pytest  # noqa: E402

DECISION = {"treatmentType": "surgery", "treatmentScheme": "", "treatmentSchemeCode": ""}
STATES = ("recorded", "in_review", "needs_revision", "revised", "signed", "signed_archived")
ACTIONS = ("review", "sign", "return", "update", "delete")
ALLOWED = {
    "review": {"recorded", "revised"},
    "sign": {"in_review"},
    "return": {"in_review"},
    "update": {"recorded", "needs_revision", "revised"},
    "delete": {"recorded", "needs_revision", "revised"},
}


def _post(client, appointment_id, action, body=None):
    return client.post(
        f"/api/consilium/appointments/{appointment_id}/{action}", json=body or {}, headers=client.csrf,
    )


def _make_state(client, state):
    """Return an appointment id in `state`; the session ends logged in as the chair."""
    create_account(DEV_USERNAME)
    create_account("chair.states", "consilium_chair")
    login(client, DEV_USERNAME)
    patient = create_patient(client, name=f"Состояний {state}")
    created = client.post(
        "/api/consilium/appointments", json=appointment_payload(patient["id"]), headers=client.csrf,
    )
    assert created.status_code == 201, created.text
    aid = created.json()["id"]
    login(client, "chair.states")
    if state in ("in_review", "signed", "signed_archived"):
        assert _post(client, aid, "review").status_code == 200
    if state in ("signed", "signed_archived"):
        assert _post(client, aid, "sign", DECISION).status_code == 200
    if state == "signed_archived":
        login(client, DEV_USERNAME)
        assert _post(client, aid, "archive").status_code == 200
        login(client, "chair.states")
    if state in ("needs_revision", "revised"):
        assert _post(client, aid, "review").status_code == 200
        assert _post(client, aid, "return", {"comment": "Уточните данные"}).status_code == 200
    if state == "revised":
        login(client, DEV_USERNAME)
        resubmitted = client.put(
            f"/api/consilium/appointments/{aid}",
            json=appointment_payload(patient["id"], comment="Исправлено"), headers=client.csrf,
        )
        assert resubmitted.status_code == 200, resubmitted.text
        login(client, "chair.states")
    status = get_conn().execute(
        "SELECT status FROM oncology_consilium_appointments WHERE id = ?", (aid,)
    ).fetchone()["status"]
    assert status in ("recorded", "in_review", "signed")
    return aid, patient


def _row(aid):
    row = get_conn().execute("SELECT * FROM oncology_consilium_appointments WHERE id = ?", (aid,)).fetchone()
    return dict(row) if row else None


def _consilium_audit_count():
    return get_conn().execute(
        "SELECT COUNT(*) AS c FROM audit_events WHERE event_type LIKE 'consilium_%'"
    ).fetchone()["c"]


def _act(client, action, aid, patient):
    if action in ("review", "sign", "return"):
        body = DECISION if action == "sign" else ({"comment": "Ещё раз"} if action == "return" else None)
        return _post(client, aid, action, body)
    login(client, DEV_USERNAME)
    if action == "update":
        return client.put(
            f"/api/consilium/appointments/{aid}",
            json=appointment_payload(patient["id"], comment="Правка"), headers=client.csrf,
        )
    return client.delete(f"/api/consilium/appointments/{aid}", headers=client.csrf)


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("action", ACTIONS)
def test_consilium_transition_matrix(client, state, action):
    aid, patient = _make_state(client, state)
    before_row, before_audit = _row(aid), _consilium_audit_count()
    response = _act(client, action, aid, patient)
    if state in ALLOWED[action]:
        assert response.status_code == 200, (state, action, response.text)
        assert _consilium_audit_count() == before_audit + 1
    else:
        assert response.status_code == 409, (state, action, response.status_code, response.text)
        assert _row(aid) == before_row
        assert _consilium_audit_count() == before_audit


def test_resubmission_waits_for_explicit_review(client):
    aid, _ = _make_state(client, "revised")
    listed = client.get("/api/consilium/appointments").json()["appointments"][0]
    assert listed["status"] == "revised"
    assert _row(aid)["status"] == "recorded"
    assert _post(client, aid, "sign", DECISION).status_code == 409
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "sign", DECISION).status_code == 200


def test_signed_decision_is_immutable_in_the_database(client):
    aid, _ = _make_state(client, "signed")
    conn = get_conn()
    for column, value in (
        ("treatment_type", "radiotherapy"), ("status", "recorded"), ("signed_by_user_id", None),
        ("primary_diagnosis", "C50.1"), ("revision_comment", "тихий возврат"),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="consilium_signed_immutable"):
            conn.execute(f"UPDATE oncology_consilium_appointments SET {column} = ? WHERE id = ?", (value, aid))
        conn.rollback()
    conn.execute(
        "UPDATE oncology_consilium_appointments SET archived_at = ?, updated_at = ? WHERE id = ?",
        ("2026-11-13T10:00:00+00:00", "2026-11-13T10:00:00+00:00", aid),
    )
    conn.rollback()


# --- Б4: XLSX ------------------------------------------------------------

from io import BytesIO  # noqa: E402
from xml.etree import ElementTree  # noqa: E402
from zipfile import ZipFile  # noqa: E402

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
DANGEROUS = ("=HYPERLINK(\"http://evil.example/\",\"x\")", "+7 999", "-2+3", "@SUM(A1)", "=cmd|' /C calc'!A0")


def _sheet(content: bytes):
    with ZipFile(BytesIO(content)) as workbook:
        sheet = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        styles = ElementTree.fromstring(workbook.read("xl/styles.xml"))
    return sheet, styles


def test_xlsx_writes_dangerous_values_as_text():
    from backend.app.services.xlsx_export import workbook_bytes

    sheet, styles = _sheet(workbook_bytes("Проверка", [list(DANGEROUS)]))
    assert sheet.findall(".//m:f", NS) == []
    cells = sheet.findall(".//m:c", NS)
    assert [c.get("t") for c in cells] == ["inlineStr"] * len(DANGEROUS)
    assert [c.find("m:is/m:t", NS).text for c in cells] == list(DANGEROUS)
    quote_styles = {
        str(index) for index, xf in enumerate(styles.find("m:cellXfs", NS)) if xf.get("quotePrefix") == "1"
    }
    assert {c.get("s") for c in cells} <= quote_styles


def test_xlsx_strips_xml_illegal_characters():
    from backend.app.services.xlsx_export import workbook_bytes

    value = "Схема\x01 с\x1b упр\x0bавляющими￾ символами\ud800 <&>\t\n"
    sheet, _ = _sheet(workbook_bytes('Лист"\x02', [[value]]))  # parses => valid XML
    text = sheet.find(".//m:c/m:is/m:t", NS).text
    assert text == "Схема с управляющими символами <&>\t\n"


def test_registry_export_keeps_formula_like_scheme_as_text(client):
    aid, _ = _make_state(client, "in_review")
    scheme = "=HYPERLINK(\"http://evil.example/\",\"Открыть\")"
    signed = _post(client, aid, "sign", {"treatmentType": "chemotherapy", "treatmentScheme": scheme})
    assert signed.status_code == 200, signed.text
    exported = client.get("/api/consilium/registries/chemotherapy.xlsx", headers=client.csrf)
    assert exported.status_code == 200
    sheet, _ = _sheet(exported.content)
    assert sheet.findall(".//m:f", NS) == []
    texts = [t.text for t in sheet.findall(".//m:t", NS)]
    assert scheme in texts


# --- Б4: аудит выгрузок и ограничения частоты -----------------------------

EXPORTS = (
    "/api/consilium/registries/chemotherapy.xlsx",
    "/api/consilium/registries/goc.xlsx",
    "/api/consilium/registries/goc-65-plus.xlsx",
)


def _audit_rows(event_type):
    return [dict(r) for r in get_conn().execute(
        "SELECT * FROM audit_events WHERE event_type = ? ORDER BY id", (event_type,)
    ).fetchall()]


def test_registry_export_is_audited_without_personal_data(client):
    aid, patient = _make_state(client, "in_review")
    signed = _post(client, aid, "sign", {
        "treatmentType": "chemotherapy", "treatmentScheme": "FOLFOX", "goc": True, "goc65Plus": True,
    })
    assert signed.status_code == 200, signed.text
    for path in EXPORTS:
        assert client.get(f"{path}?date=2026-11-12", headers=client.csrf).status_code == 200
    rows = _audit_rows("consilium_registry_exported")
    assert [r["detail"] for r in rows] == [
        "registry=chemotherapy;date=2026-11-12;row_count=1;format=xlsx",
        "registry=goc;date=2026-11-12;row_count=1;format=xlsx",
        "registry=goc_65_plus;date=2026-11-12;row_count=1;format=xlsx",
    ]
    dump = str(rows)
    for secret in (patient["name"], patient["policyNumber"], "C34.1", "FOLFOX"):
        assert secret not in dump
    assert all(r["patient_id"] is None and r["actor_role"] == "consilium_chair" for r in rows)


def test_registry_export_needs_csrf_header_and_chair_role(client):
    _make_state(client, "recorded")
    for path in EXPORTS:
        assert client.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert _audit_rows("consilium_registry_exported") == []
    login(client, DEV_USERNAME)
    create_account("doctor.plain", "doctor")
    login(client, "doctor.plain")
    assert client.get(EXPORTS[0], headers=client.csrf).status_code == 403
    assert _audit_rows("consilium_registry_exported") == []


def test_registry_export_is_rate_limited(client):
    _make_state(client, "recorded")
    codes = [client.get(EXPORTS[0], headers=client.csrf).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20
    assert codes[20] == 429
    assert len(_audit_rows("consilium_registry_exported")) == 20


def test_consilium_writes_are_rate_limited(client):
    _, patient = _make_state(client, "recorded")
    codes = [
        client.delete(f"/api/consilium/favorites/{patient['id']}", headers=client.csrf).status_code
        for _ in range(61)
    ]
    assert codes[:60] == [200] * 60
    assert codes[60] == 429


# --- Б6: начало рассмотрения — отдельное действие --------------------------

from .conftest import PROJECT_ROOT  # noqa: E402


def _method_body(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("\n    },", start)
    return source[start:end]


def test_opening_decision_modal_does_not_start_review():
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    index_html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
    opener = _method_body(app_js, "openConsiliumDecision(appointmentId) {")
    assert "/review" not in opener
    assert "apiPost" not in opener and "apiFetch" not in opener
    starter = _method_body(app_js, "async startConsiliumReview() {")
    assert "/review`" in starter
    assert 'id="startConsiliumReviewBtn"' in index_html
    assert "on('startConsiliumReviewBtn', 'click', () => this.startConsiliumReview());" in app_js
    sync = _method_body(app_js, "syncConsiliumReviewState(record) {")
    assert "$('signConsiliumBtn').disabled = !inReview;" in sync


# --- Б7: лечащий врач и адресат уведомления -------------------------------

def test_treating_doctor_comes_from_patient_card_not_record_author(client):
    doctor_a = create_account("doctor.a", "doctor", full_name="Лечащева Анна Карточная")
    doctor_b = create_account("doctor.b", "doctor", full_name="Новиков Борис Переданный")
    create_account(DEV_USERNAME)
    create_account("chair.doctor", "consilium_chair")
    login(client, "chair.doctor")
    assert client.post(
        "/api/consilium/schedule/generate",
        json={
            "scheduledDate": "2026-11-12", "consiliumGroup": "thoracic",
            "startTime": "10:00", "endTime": "11:00", "intervalMinutes": 30,
        },
        headers=client.csrf,
    ).status_code == 200
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент Лечащего", treatingDoctorUserId=doctor_a)
    created = client.post(
        "/api/consilium/appointments", json=appointment_payload(patient["id"]), headers=client.csrf,
    )
    assert created.status_code == 201, created.text
    assert created.json()["treatingDoctorName"] == "Лечащева Анна Карточная"
    stored = get_conn().execute(
        "SELECT treating_doctor_name FROM oncology_consilium_appointments WHERE id = ?",
        (created.json()["id"],),
    ).fetchone()["treating_doctor_name"]
    assert stored == "Лечащева Анна Карточная"

    conn = get_conn()
    conn.execute("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (doctor_b, patient["id"]))
    conn.commit()
    login(client, "chair.doctor")
    listed = client.get("/api/consilium/appointments").json()["appointments"][0]
    assert listed["treatingDoctorName"] == "Новиков Борис Переданный"
    slots = client.get("/api/consilium/schedule").json()["slots"]
    assert _slot(slots, "2026-11-12", "10:30")["appointment"]["treatingDoctorName"] == "Новиков Борис Переданный"


def test_revision_notification_respects_recipient_scope(client):
    doctor_a = create_account("doctor.a", "doctor", full_name="Врачова Анна Первая")
    doctor_b = create_account("doctor.b", "doctor")
    create_account(DEV_USERNAME)
    create_account("chair.notify", "consilium_chair")
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Уведомляемый Пациент", treatingDoctorUserId=doctor_a)
    login(client, "doctor.a")
    created = client.post(
        "/api/consilium/appointments", json=appointment_payload(patient["id"]), headers=client.csrf,
    ).json()
    login(client, "chair.notify")
    assert _post(client, created["id"], "review").status_code == 200
    assert _post(client, created["id"], "return", {"comment": "Уточните"}).status_code == 200

    login(client, "doctor.a")
    items = client.get("/api/internal-notifications?type=consilium_revision").json()["items"]
    assert [i["source_id"] for i in items] == [created["id"]]

    conn = get_conn()
    conn.execute("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (doctor_b, patient["id"]))
    conn.commit()
    response = client.get("/api/internal-notifications?type=consilium_revision")
    assert response.json()["items"] == []
    assert "Уведомляемый Пациент" not in client.get("/api/internal-notifications").text


# --- Б8: демо-скрипты -----------------------------------------------------

import hashlib  # noqa: E402
import runpy  # noqa: E402

BACKUP_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _script(name):
    return runpy.run_path(str(PROJECT_ROOT / "backend" / "scripts" / name), run_name="seed_under_test")


def _seed_world(client):
    """Owner, chair and two patients in the per-test temp DB."""
    create_account(DEV_USERNAME)
    create_account("admin", "developer")
    create_account("chair.demo", "consilium_chair")
    login(client, DEV_USERNAME)
    first = create_patient(client, name="Демо Первый", diagnosis="C34.1", nosology="lung34")
    second = create_patient(client, name="Демо Второй", diagnosis="C34.9", nosology="lung12")
    return first, second


def _db_digest():
    from backend.app import config
    conn = get_conn()
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    return hashlib.sha256(config.DB_PATH.read_bytes()).hexdigest()


def _files_under(path):
    return sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file())


@pytest.mark.parametrize("env, marker", [("local", True), ("demo", False), ("production", True)])
def test_demo_seed_refuses_outside_marked_demo_environment(client, monkeypatch, capsys, env, marker):
    from backend.app import config

    _seed_world(client)
    monkeypatch.setattr(config, "APP_ENV", env)
    if marker:
        (config.DATA_DIR / ".clinic-demo").write_text("demo\n")
    monkeypatch.setenv("CLINIC_BACKUP_KEY", BACKUP_KEY)
    before, files_before = _db_digest(), _files_under(config.DATA_DIR)
    for name, argv in (
        ("seed_consilium_demo.py", ["seed", "--confirm-reset"]),
        ("seed_test_patients.py", ["seed"]),
    ):
        monkeypatch.setattr("sys.argv", argv)
        assert _script(name)["main"]() == 2
    assert _db_digest() == before
    assert _files_under(config.DATA_DIR) == files_before


def test_demo_seed_without_backup_key_changes_nothing(client, monkeypatch):
    from backend.app import config

    _seed_world(client)
    monkeypatch.setattr(config, "APP_ENV", "demo")
    (config.DATA_DIR / ".clinic-demo").write_text("demo\n")
    for var in ("CLINIC_BACKUP_KEY", "CLINIC_BACKUP_KEY_FILE"):
        monkeypatch.delenv(var, raising=False)
    before = _db_digest()
    monkeypatch.setattr("sys.argv", ["seed", "--confirm-reset"])
    assert _script("seed_consilium_demo.py")["main"]() == 2
    assert _db_digest() == before
    assert [p.name for p in config.DATA_DIR.rglob("*.db")] == ["clinic.db"]
    assert not config.BACKUP_DIR.exists() or not any(config.BACKUP_DIR.iterdir())


def test_demo_seed_writes_only_encrypted_backup_and_keeps_routes(client, monkeypatch):
    from backend.app import config, maintenance

    first, second = _seed_world(client)
    monkeypatch.setattr(config, "APP_ENV", "demo")
    (config.DATA_DIR / ".clinic-demo").write_text("demo\n")
    monkeypatch.setenv("CLINIC_BACKUP_KEY", BACKUP_KEY)
    routes_before = {
        pid: client.get(f"/api/patients/{pid}").json()["history"] for pid in (first["id"], second["id"])
    }
    monkeypatch.setattr("sys.argv", ["seed", "--confirm-reset"])
    assert _script("seed_consilium_demo.py")["main"]() == 0

    backups = list(config.BACKUP_DIR.iterdir())
    assert len(backups) == 1 and backups[0].suffix == ".cbk"
    assert maintenance.looks_like_encrypted_backup(backups[0])
    assert not maintenance.looks_like_sqlite(backups[0])
    assert [p.name for p in config.DATA_DIR.rglob("*.db")] == ["clinic.db"]

    conn = get_conn()
    rows = conn.execute("SELECT patient_id, consilium_group FROM oncology_consilium_appointments").fetchall()
    assert {r["patient_id"] for r in rows} == {first["id"], second["id"]}
    assert {r["consilium_group"] for r in rows} == {"thoracic"}
    for pid, history in routes_before.items():
        assert client.get(f"/api/patients/{pid}").json()["history"] == history
    assert _audit_rows("consilium_demo_reset")[0]["detail"].startswith("slots=")


# --- Б9: аналитика без сборки SQL из строк --------------------------------

def test_chair_analytics_validates_dates_and_filters_by_range(client):
    aid, _ = _make_state(client, "recorded")
    assert client.get("/api/consilium/analytics?date_from=2026-13-01").status_code == 422
    assert client.get("/api/consilium/analytics?date_to=1%27%20OR%201=1").status_code == 422
    inside = client.get("/api/consilium/analytics?date_from=2026-11-12&date_to=2026-11-12").json()
    outside = client.get("/api/consilium/analytics?date_from=2026-11-13").json()
    assert inside["summary"]["total"] == 1
    assert outside["summary"]["total"] == 0
    assert client.get("/api/consilium/analytics").json()["summary"]["total"] == 1


# --- Правило 3: повторный возврат -----------------------------------------

def test_second_return_needs_new_review_and_new_comment(client):
    aid, patient = _make_state(client, "revised")
    assert _row(aid)["revision_count"] == 1
    # Повторно отправленную запись нельзя вернуть, не начав рассмотрение заново.
    assert _post(client, aid, "return", {"comment": "Ещё уточнение"}).status_code == 409
    assert _post(client, aid, "review").status_code == 200
    before = _row(aid)
    for blank in ("", "   "):
        assert _post(client, aid, "return", {"comment": blank}).status_code == 422
    same = _post(client, aid, "return", {"comment": "Уточните данные"})
    assert same.status_code == 422
    assert _row(aid) == before

    second = _post(client, aid, "return", {"comment": "Приложите заключение КТ"})
    assert second.status_code == 200, second.text
    assert second.json()["revisionCount"] == 2
    assert second.json()["status"] == "needs_revision"
    details = [r["detail"] for r in _audit_rows("consilium_returned_for_revision")]
    assert details == ["revision_round=1", "revision_round=2"]
    assert "КТ" not in str(_audit_rows("consilium_returned_for_revision"))


# --- Правило 4: лечащий врач в подписанном решении -------------------------

def test_signed_decision_keeps_treating_doctor_at_signing(client):
    doctor_a = create_account("doctor.a", "doctor", full_name="Подписанова Анна Историческая")
    doctor_b = create_account("doctor.b", "doctor", full_name="Текущев Борис Новый")
    create_account(DEV_USERNAME)
    create_account("chair.sign", "consilium_chair")
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент Подписи", treatingDoctorUserId=doctor_a)
    created = client.post(
        "/api/consilium/appointments", json=appointment_payload(patient["id"]), headers=client.csrf,
    ).json()
    login(client, "chair.sign")
    assert _post(client, created["id"], "review").status_code == 200
    signed = _post(client, created["id"], "sign", {"treatmentType": "chemotherapy", "treatmentScheme": "FOLFOX"})
    assert signed.status_code == 200, signed.text
    row = _row(created["id"])
    assert (row["signed_treating_doctor_user_id"], row["signed_treating_doctor_name"]) == (
        doctor_a, "Подписанова Анна Историческая")

    conn = get_conn()
    conn.execute("UPDATE patients SET treating_doctor_user_id = ?, doctor = ? WHERE id = ?",
                 (doctor_b, "Текущев Борис Новый", patient["id"]))
    conn.commit()
    listed = client.get("/api/consilium/appointments").json()["appointments"][0]
    assert listed["treatingDoctorName"] == "Подписанова Анна Историческая"
    assert listed["currentTreatingDoctorName"] == "Текущев Борис Новый"
    registry = client.get("/api/consilium/registries?date=2026-11-12").json()["chemotherapy"][0]
    assert registry["treatingDoctorName"] == "Подписанова Анна Историческая"
    exported = client.get("/api/consilium/registries/chemotherapy.xlsx", headers=client.csrf)
    texts = [t.text for t in _sheet(exported.content)[0].findall(".//m:t", NS)]
    assert "Подписанова Анна Историческая" in texts and "Текущев Борис Новый" not in texts
    for column, value in (("signed_treating_doctor_name", "Подмена"), ("signed_treating_doctor_user_id", doctor_b)):
        with pytest.raises(sqlite3.IntegrityError, match="consilium_signed_immutable"):
            conn.execute(f"UPDATE oncology_consilium_appointments SET {column} = ? WHERE id = ?", (value, created["id"]))
        conn.rollback()


# --- Правило 6: настраиваемые лимиты на пользователя -------------------------

@pytest.fixture()
def low_limits(monkeypatch):
    # Должно быть задано до фикстуры client: лимиты читаются при импорте приложения.
    monkeypatch.setenv("CLINIC_CONSILIUM_EXPORT_RATE_LIMIT", "3")
    monkeypatch.setenv("CLINIC_CONSILIUM_WRITE_RATE_LIMIT", "4")


def test_consilium_limits_are_configurable_and_per_user(low_limits, client):
    _, patient = _make_state(client, "recorded")
    create_account("chair.second", "consilium_chair")
    export = EXPORTS[0]
    assert [client.get(export, headers=client.csrf).status_code for _ in range(4)] == [200, 200, 200, 429]
    favorite = f"/api/consilium/favorites/{patient['id']}"
    assert [client.delete(favorite, headers=client.csrf).status_code for _ in range(5)] == [200] * 4 + [429]

    # Другой председатель с того же адреса не заблокирован чужим счётчиком.
    login(client, "chair.second")
    assert client.get(export, headers=client.csrf).status_code == 200
    assert client.delete(favorite, headers=client.csrf).status_code == 200


# --- R7: минимальные полномочия --------------------------------------------

from .conftest import create_department  # noqa: E402

ROLES = ("developer", "doctor", "department_head", "consilium_chair", "deputy_chief_oncology", "caop_head")
ACCESS = {"developer", "doctor", "department_head", "consilium_chair"}
BOOKING = {"developer", "doctor", "department_head"}
MANAGING = {"developer", "consilium_chair"}
DECIDING = {"consilium_chair"}
REFERRAL_MARKING = {"consilium_chair"}
FAVORITES = {"consilium_chair"}
ENDPOINTS = (
    ("GET", "/api/consilium/meta", None, ACCESS),
    ("GET", "/api/consilium/appointments", None, ACCESS),
    ("GET", "/api/consilium/schedule", None, ACCESS),
    ("GET", "/api/consilium/booked-patients", None, BOOKING),
    ("POST", "/api/consilium/appointments", "booking", BOOKING),
    ("PUT", "/api/consilium/appointments/{aid}", "booking", BOOKING),
    ("DELETE", "/api/consilium/appointments/{aid}", None, BOOKING),
    ("POST", "/api/consilium/appointments/{aid}/archive", {}, BOOKING),
    ("POST", "/api/consilium/appointments/{aid}/restore", {}, BOOKING),
    ("POST", "/api/consilium/schedule/generate", {
        "scheduledDate": "2026-12-01", "consiliumGroup": "thoracic",
        "startTime": "10:00", "endTime": "11:00", "intervalMinutes": 30}, MANAGING),
    ("DELETE", "/api/consilium/schedule/missing-slot", None, MANAGING),
    ("POST", "/api/consilium/appointments/{aid}/review", {}, DECIDING),
    ("POST", "/api/consilium/appointments/{aid}/sign", DECISION, DECIDING),
    ("POST", "/api/consilium/appointments/{aid}/return", {"comment": "Проверка прав"}, DECIDING),
    ("POST", "/api/consilium/appointments/{aid}/referral-completion", {"completed": True}, REFERRAL_MARKING),
    ("GET", "/api/consilium/analytics", None, MANAGING),
    ("POST", "/api/consilium/favorites/{pid}", {}, FAVORITES),
    ("DELETE", "/api/consilium/favorites/{pid}", None, FAVORITES),
    ("GET", "/api/consilium/registries", None, MANAGING),
    ("GET", "/api/consilium/registries/chemotherapy.xlsx", None, MANAGING),
    ("GET", "/api/consilium/registries/goc.xlsx", None, MANAGING),
    ("GET", "/api/consilium/registries/goc-65-plus.xlsx", None, MANAGING),
)


def _denied_count():
    return get_conn().execute(
        "SELECT COUNT(*) AS c FROM audit_events WHERE event_type = 'access_denied' AND detail LIKE 'action=consilium_%'"
    ).fetchone()["c"]


@pytest.mark.parametrize("role", ROLES)
def test_consilium_endpoint_authorization_matrix(client, role):
    department = create_department("Отделение проверки прав")
    create_account(DEV_USERNAME)
    user_id = create_account(f"role.{role}", role, department_ids=[department])
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент Матрицы Прав")
    aid = client.post(
        "/api/consilium/appointments", json=appointment_payload(patient["id"]), headers=client.csrf,
    ).json()["id"]

    login(client, f"role.{role}")
    me = client.get("/api/auth/me").json()["user"]["permissions"]
    assert me["accessConsilium"] is (role in ACCESS)
    assert me["bookConsilium"] is (role in BOOKING)
    assert me["manageConsilium"] is (role in MANAGING)
    assert me["decideConsilium"] is (role in DECIDING)
    assert me["markReferralCompletion"] is (role in REFERRAL_MARKING)
    assert me["useConsiliumFavorites"] is (role in FAVORITES)

    for method, template, body, allowed_roles in ENDPOINTS:
        path = template.format(aid=aid, pid=patient["id"])
        if body == "booking":
            body = appointment_payload(patient["id"])
        before_denied, before_row = _denied_count(), _row(aid)
        response = client.request(method, path, json=body, headers=client.csrf)
        if role in allowed_roles:
            assert response.status_code != 403, (role, method, template, response.text)
        else:
            assert response.status_code == 403, (role, method, template, response.status_code)
            assert _denied_count() == before_denied + 1, (role, method, template)
            assert _row(aid) == before_row
    assert user_id


def test_department_head_books_only_within_own_department(client):
    own = create_department("Своё отделение")
    other = create_department("Чужое отделение")
    create_account(DEV_USERNAME)
    create_account("head.own", "department_head", department_ids=[own])
    doctor_own = create_account("doctor.own", "doctor", department_ids=[own])
    doctor_other = create_account("doctor.other", "doctor", department_ids=[other])
    login(client, DEV_USERNAME)
    mine = create_patient(client, name="Пациент Своего Отделения", treatingDoctorUserId=doctor_own)
    foreign = create_patient(client, name="Пациент Чужого Отделения", diagnosis="C34.9",
                             nosology="lung12", treatingDoctorUserId=doctor_other)

    login(client, "head.own")
    booked = client.post("/api/consilium/appointments", json=appointment_payload(mine["id"]), headers=client.csrf)
    assert booked.status_code == 201, booked.text
    refused = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(foreign["id"], scheduledTime="11:00", primaryDiagnosis="C34.9"),
        headers=client.csrf,
    )
    assert refused.status_code == 404
    assert client.get("/api/consilium/booked-patients").json()["patientIds"] == [mine["id"]]
    for forbidden in ("/api/consilium/analytics", "/api/consilium/registries"):
        assert client.get(forbidden).status_code == 403


def test_consilium_menu_follows_access_permission():
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "consilium: this.can('accessConsilium')," in app_js
    assert "if (view === 'consilium' && !this.can('accessConsilium')) view = 'patients';" in app_js


# --- R6: импорт и удаление не уничтожают решения консилиума ----------------

import json  # noqa: E402


def _data_digest():
    """Content of every table except the audit trail and session activity,
    which a refused request legitimately writes."""
    conn = get_conn()
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name NOT IN ('audit_events', 'sessions', 'sqlite_sequence') ORDER BY name"
    )]
    digest = hashlib.sha256()
    for table in tables:
        for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY 1'):
            digest.update(repr((table, tuple(row))).encode())
    return digest.hexdigest()


def _export_document(client):
    response = client.get("/api/export", headers=client.csrf)
    assert response.status_code == 200
    return response.json()


def _import(client, document, mode):
    return client.post(
        "/api/import/commit", json={"raw": json.dumps(document, ensure_ascii=False), "mode": mode},
        headers=client.csrf,
    )


@pytest.mark.parametrize("state", ["signed", "recorded"])
@pytest.mark.parametrize("mode", ["replace", "update"])
def test_dangerous_import_stops_before_any_change(client, state, mode):
    aid, patient = _make_state(client, state)
    login(client, DEV_USERNAME)
    document = _export_document(client)
    before_digest, before_row = _data_digest(), _row(aid)

    response = _import(client, document, mode)
    assert response.status_code == 409, response.text
    assert "Данные не изменены" in response.json()["detail"]
    assert _data_digest() == before_digest
    assert _row(aid) == before_row
    denied = [r for r in _audit_rows("import_committed") if r["result"] == "denied"]
    expected_signed = 1 if state == "signed" else 0
    assert denied[-1]["detail"] == f"mode={mode} consilium_records=1 signed={expected_signed}"


def test_safe_imports_still_work_next_to_consilium_records(client):
    aid, patient = _make_state(client, "signed")
    login(client, DEV_USERNAME)
    other = create_patient(client, name="Пациент Без Консилиума", diagnosis="C50.1", nosology="breast")
    document = _export_document(client)
    only_other = {**document, "patients": [p for p in document["patients"] if p["id"] == other["id"]]}

    updated = _import(client, only_other, "update")
    assert updated.status_code == 200, updated.text
    assert updated.json()["updated"] == 1
    added = _import(client, document, "add")
    assert added.status_code == 200, added.text
    assert _row(aid)["status"] == "signed"

    preview = client.post(
        "/api/import/preview", json={"raw": json.dumps(document, ensure_ascii=False)}, headers=client.csrf,
    ).json()
    impact = preview["consiliumImpact"]
    assert {k: impact["replace"][k] for k in ("records", "signed", "unsigned", "blocked")} == {
        "records": 1, "signed": 1, "unsigned": 0, "blocked": True}
    assert impact["update"]["blocked"] is True
    assert impact["add"] == {"records": 0, "signed": 0, "unsigned": 0, "blocked": False, "reason": "", "advice": ""}


def test_patient_with_consilium_records_cannot_be_deleted(client):
    aid, patient = _make_state(client, "signed")
    login(client, DEV_USERNAME)
    before = _data_digest()
    response = client.delete(f"/api/patients/{patient['id']}", headers=client.csrf)
    assert response.status_code == 409
    assert _data_digest() == before
    assert _row(aid)["status"] == "signed"
    assert _audit_rows("patient_deleted")[-1]["result"] == "denied"


@pytest.mark.parametrize("role", sorted(ACCESS))
def test_frontend_load_sequence_is_allowed_for_every_role_with_access(client, role):
    """Mirrors loadConsilium/renderConsilium: every request the page makes for
    a role must be permitted, otherwise the section silently stays empty."""
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    loader = _method_body(app_js, "async loadConsilium() {")
    assert "this.can('bookConsilium')\n                ? await apiGetJson('/api/consilium/booked-patients')" in loader
    assert "if (this.can('consiliumChair')) {\n                const query" in loader

    create_account(f"load.{role}", role)
    login(client, f"load.{role}")
    permissions = client.get("/api/auth/me").json()["user"]["permissions"]
    calls = ["/api/consilium/meta", "/api/consilium/appointments", "/api/consilium/schedule"]
    if permissions["bookConsilium"]:
        calls.append("/api/consilium/booked-patients")
    if permissions["consiliumChair"]:
        calls += ["/api/consilium/registries", "/api/consilium/analytics"]
    for path in calls:
        assert client.get(path).status_code == 200, (role, path)



# --- Задача 2: разработчик не наследует полномочия председателя -------------

def test_developer_without_chair_role_cannot_decide(client):
    create_account(DEV_USERNAME)
    create_account("chair.real", "consilium_chair")
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент Решения")
    aid = client.post(
        "/api/consilium/appointments", json=appointment_payload(patient["id"]), headers=client.csrf,
    ).json()["id"]

    def attempts():
        return {
            "review": _post(client, aid, "review").status_code,
            "sign": _post(client, aid, "sign", DECISION).status_code,
            "return": _post(client, aid, "return", {"comment": "Попытка разработчика"}).status_code,
        }

    before_row, before_denied = _row(aid), _denied_count()
    assert attempts() == {"review": 403, "sign": 403, "return": 403}
    assert _row(aid) == before_row
    assert _denied_count() == before_denied + 3

    # Даже когда председатель уже начал рассмотрение, подписать разработчик не может.
    login(client, "chair.real")
    assert _post(client, aid, "review").status_code == 200
    login(client, DEV_USERNAME)
    in_review = _row(aid)
    assert _post(client, aid, "sign", DECISION).status_code == 403
    assert _row(aid) == in_review

    # Административные функции разработчика сохраняются.
    assert client.get("/api/consilium/registries").status_code == 200
    assert client.get("/api/consilium/analytics").status_code == 200
    assert client.get(EXPORTS[0], headers=client.csrf).status_code == 200
    assert client.post("/api/consilium/schedule/generate", json={
        "scheduledDate": "2026-12-01", "consiliumGroup": "thoracic",
        "startTime": "10:00", "endTime": "11:00", "intervalMinutes": 30,
    }, headers=client.csrf).status_code == 200


# --- Задача 1: заведующий в расписании ------------------------------------

HEAD_FIELDS = {"patientName", "treatingDoctorName", "status", "statusLabel", "isMine", "view"}


def _department_world(client):
    own = create_department("Отделение заведующего")
    other = create_department("Соседнее отделение")
    create_account(DEV_USERNAME)
    create_account("head.sched", "department_head", department_ids=[own])
    create_account("doctor.own", "doctor", full_name="Своева Врач Отделения", department_ids=[own])
    create_account("doctor.other", "doctor", full_name="Чужова Врач Соседняя", department_ids=[other])
    ids = {r["username"]: r["id"] for r in get_conn().execute("SELECT id, username FROM users")}
    login(client, DEV_USERNAME)
    mine = create_patient(client, name="Пациент Своего Отделения", policyNumber="7700000000004401",
                          treatingDoctorUserId=ids["doctor.own"])
    foreign = create_patient(client, name="Пациент Соседнего Отделения", policyNumber="7700000000004402",
                             diagnosis="C34.9", nosology="lung12", treatingDoctorUserId=ids["doctor.other"])
    today = date.today().isoformat()
    login(client, "doctor.own")
    own_booking = client.post("/api/consilium/appointments", headers=client.csrf,
                              json=appointment_payload(mine["id"], scheduledDate=today, scheduledTime="09:00"))
    assert own_booking.status_code == 201, own_booking.text
    login(client, "doctor.other")
    other_booking = client.post("/api/consilium/appointments", headers=client.csrf,
                                json=appointment_payload(foreign["id"], scheduledDate=today, scheduledTime="09:15",
                                                         primaryDiagnosis="C34.9"))
    assert other_booking.status_code == 201, other_booking.text
    login(client, "head.sched")
    return mine, foreign, today, own_booking.json()["id"], other_booking.json()["id"]


def test_head_sees_minimal_details_for_own_department(client):
    mine, _, today, own_id, _ = _department_world(client)
    response = client.get("/api/consilium/schedule")
    assert response.status_code == 200
    details = _slot(response.json()["slots"], today, "09:00")["appointment"]
    assert set(details) == HEAD_FIELDS
    assert details == {
        "patientName": "Пациент Своего Отделения", "treatingDoctorName": "Своева Врач Отделения",
        "status": "recorded", "statusLabel": "Записан", "isMine": False, "view": "department",
    }
    for secret in ("7700000000004401", "C34.1", mine["id"], own_id):
        assert secret not in json.dumps(_slot(response.json()["slots"], today, "09:00"), ensure_ascii=False)


def test_head_gets_only_occupancy_for_other_department(client):
    _, foreign, today, _, other_id = _department_world(client)
    response = client.get("/api/consilium/schedule")
    slot = _slot(response.json()["slots"], today, "09:15")
    assert slot["occupied"] is True and slot["appointment"] is None
    for secret in ("Пациент Соседнего Отделения", "Чужова Врач Соседняя", "7700000000004402",
                   "C34.9", foreign["id"], other_id):
        assert secret not in response.text


def test_doctor_of_same_department_still_sees_only_occupancy(client):
    _department_world(client)
    create_account("doctor.colleague", "doctor", department_ids=[
        get_conn().execute("SELECT id FROM departments WHERE name = 'Отделение заведующего'").fetchone()["id"]])
    login(client, "doctor.colleague")
    response = client.get("/api/consilium/schedule")
    assert _slot(response.json()["slots"], date.today().isoformat(), "09:00")["appointment"] is None
    assert "Пациент Своего Отделения" not in response.text


# --- Задача 3: история возвратов ------------------------------------------

ROUND_COMMENTS = ("Приложите заключение КТ", "Уточните стадию по TNM", "Добавьте гистологию")


def _three_rounds(client):
    doctor = create_account("doctor.rounds", "doctor", full_name="Раундова Врач Автор")
    chair = create_account("chair.rounds", "consilium_chair", full_name="Раундов Председатель")
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент Трёх Раундов", treatingDoctorUserId=doctor)
    login(client, "doctor.rounds")
    aid = client.post("/api/consilium/appointments", json=appointment_payload(patient["id"]),
                      headers=client.csrf).json()["id"]
    for index, comment in enumerate(ROUND_COMMENTS):
        login(client, "chair.rounds")
        assert _post(client, aid, "review").status_code == 200
        assert _post(client, aid, "return", {"comment": comment}).status_code == 200
        login(client, "doctor.rounds")
        resubmitted = client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf,
                                 json=appointment_payload(patient["id"], comment=f"Исправление {index + 1}"))
        assert resubmitted.status_code == 200, resubmitted.text
    return aid, patient, doctor, chair


def test_every_return_round_keeps_its_text_author_and_time(client):
    aid, _, _, chair = _three_rounds(client)
    login(client, "chair.rounds")
    history = client.get(f"/api/consilium/appointments/{aid}/revisions").json()["revisions"]
    assert [(h["round"], h["comment"], h["returnedByName"], h["migrated"]) for h in history] == [
        (1, ROUND_COMMENTS[0], "Раундов Председатель", False),
        (2, ROUND_COMMENTS[1], "Раундов Председатель", False),
        (3, ROUND_COMMENTS[2], "Раундов Председатель", False),
    ]
    assert all(h["returnedAt"] for h in history)

    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "sign", DECISION).status_code == 200
    after_sign = client.get(f"/api/consilium/appointments/{aid}/revisions").json()["revisions"]
    assert after_sign == history
    assert _row(aid)["status"] == "signed"

    audit_dump = str([dict(r) for r in get_conn().execute("SELECT * FROM audit_events")])
    for comment in ROUND_COMMENTS:
        assert comment not in audit_dump


def test_revision_history_is_limited_to_chair_and_author(client):
    aid, _, doctor, _ = _three_rounds(client)
    login(client, "doctor.rounds")
    assert len(client.get(f"/api/consilium/appointments/{aid}/revisions").json()["revisions"]) == 3

    create_account("doctor.stranger", "doctor")
    create_account("deputy.rounds", "deputy_chief_oncology")
    for username, expected in (("doctor.stranger", 404), (DEV_USERNAME, 404), ("deputy.rounds", 403)):
        login(client, username)
        response = client.get(f"/api/consilium/appointments/{aid}/revisions")
        assert response.status_code == expected, (username, response.status_code)
        assert ROUND_COMMENTS[0] not in response.text

    # Автор теряет доступ к истории, когда пациент уходит из его области.
    other = create_account("doctor.next", "doctor")
    conn = get_conn()
    conn.execute("UPDATE patients SET treating_doctor_user_id = ? WHERE treating_doctor_user_id = ?", (other, doctor))
    conn.commit()
    login(client, "doctor.rounds")
    assert client.get(f"/api/consilium/appointments/{aid}/revisions").status_code == 404


def test_revision_history_rows_cannot_be_rewritten(client):
    aid, _, _, _ = _three_rounds(client)
    conn = get_conn()
    with pytest.raises(sqlite3.IntegrityError, match="consilium_revision_history_immutable"):
        conn.execute("UPDATE consilium_revision_history SET comment = 'подмена' WHERE appointment_id = ?", (aid,))
    conn.rollback()


# --- Архив врача: явное действие, не следствие подписи ---------------------

def _signed_by_doctor(client):
    doctor = create_account("doctor.archive", "doctor", full_name="Архивова Врач Автор")
    create_account("chair.archive2", "consilium_chair")
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент Архива", treatingDoctorUserId=doctor)
    login(client, "doctor.archive")
    aid = client.post("/api/consilium/appointments", json=appointment_payload(patient["id"]),
                      headers=client.csrf).json()["id"]
    login(client, "chair.archive2")
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "return", {"comment": "Уточните перед подписью"}).status_code == 200
    login(client, "doctor.archive")
    assert client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf,
                      json=appointment_payload(patient["id"], comment="Исправлено")).status_code == 200
    login(client, "chair.archive2")
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "sign", DECISION).status_code == 200
    login(client, "doctor.archive")
    return aid, patient, doctor


DECISION_COLUMNS = ("status", "treatment_type", "treatment_scheme", "treatment_scheme_code", "goc", "goc_65_plus",
                    "signed_at", "signed_by_user_id", "signed_treating_doctor_user_id",
                    "signed_treating_doctor_name", "patient_id", "revision_count")


def _history(aid):
    return [tuple(r) for r in get_conn().execute(
        "SELECT round, comment, returned_by_user_id, returned_at FROM consilium_revision_history "
        "WHERE appointment_id = ? ORDER BY round", (aid,))]


def test_archive_and_restore_keep_the_signed_decision_and_its_history(client):
    aid, patient, doctor = _signed_by_doctor(client)
    before = {c: _row(aid)[c] for c in DECISION_COLUMNS}
    history = _history(aid)
    assert history and client.get("/api/consilium/appointments?archived=true").json()["appointments"] == []

    archived = _post(client, aid, "archive")
    assert archived.status_code == 200 and archived.json()["archived"] is True
    row = _row(aid)
    assert row["archived_by_user_id"] == doctor and row["archived_at"]
    assert {c: row[c] for c in DECISION_COLUMNS} == before
    assert _history(aid) == history
    assert [a["id"] for a in client.get("/api/consilium/appointments?archived=true").json()["appointments"]] == [aid]
    assert client.get("/api/consilium/appointments").json()["appointments"] == []
    assert _post(client, aid, "archive").status_code == 409

    restored = _post(client, aid, "restore")
    assert restored.status_code == 200 and restored.json()["archived"] is False
    assert {c: _row(aid)[c] for c in DECISION_COLUMNS} == before
    assert _history(aid) == history
    assert _post(client, aid, "restore").status_code == 409
    events = [r["event_type"] for r in get_conn().execute(
        "SELECT event_type FROM audit_events WHERE event_type IN ('consilium_archived', 'consilium_restored') ORDER BY id")]
    assert events == ["consilium_archived", "consilium_restored"]


@pytest.mark.parametrize("username, role, expected", [
    ("chair.archive2", None, 403),            # председатель не ведёт личный архив врача
    ("doctor.other.archive", "doctor", 404),  # чужая запись неотличима от несуществующей
    ("deputy.archive", "deputy_chief_oncology", 403),
    (DEV_USERNAME, None, 404),                # разработчик — не автор записи
])
def test_only_the_author_can_archive_or_restore(client, username, role, expected):
    aid, _, _ = _signed_by_doctor(client)
    if role:
        create_account(username, role)
    login(client, username)
    before = _row(aid)
    for action in ("archive", "restore"):
        assert _post(client, aid, action).status_code == expected, (username, action)
    assert _row(aid) == before


# --- Превью импорта: влияние на консилиум считает сервер --------------------

def test_import_preview_reports_signed_and_working_records_and_why(client):
    create_account(DEV_USERNAME)
    create_account("chair.preview", "consilium_chair")
    login(client, DEV_USERNAME)
    ids = []
    for index, (name, dx, nos) in enumerate((("Превью Подписанный", "C34.1", "lung34"),
                                             ("Превью Рассматриваемый", "C34.9", "lung12"),
                                             ("Превью Записанный", "C34.1", "lung34"))):
        patient = create_patient(client, name=name, diagnosis=dx, nosology=nos)
        aid = client.post("/api/consilium/appointments", headers=client.csrf,
                          json=appointment_payload(patient["id"], scheduledTime=f"1{index}:00", primaryDiagnosis=dx)).json()["id"]
        ids.append(aid)
    login(client, "chair.preview")
    assert _post(client, ids[0], "review").status_code == 200
    assert _post(client, ids[0], "sign", DECISION).status_code == 200
    assert _post(client, ids[1], "review").status_code == 200
    login(client, DEV_USERNAME)
    document = _export_document(client)
    preview = client.post("/api/import/preview", headers=client.csrf,
                          json={"raw": json.dumps(document, ensure_ascii=False)}).json()
    replace = preview["consiliumImpact"]["replace"]
    assert (replace["records"], replace["signed"], replace["unsigned"], replace["blocked"]) == (3, 1, 2, True)
    assert "подписанных решений: 1" in replace["reason"] and "Данные не изменены" in replace["reason"]
    assert "«Добавить как новые записи»" in replace["advice"]
    assert not any(name in json.dumps(preview, ensure_ascii=False) for name in ("C34.1", "C34.9"))

    before = _data_digest()
    response = _import(client, document, "replace")
    assert response.status_code == 409
    assert response.json()["detail"] == replace["reason"]
    assert _data_digest() == before


def test_add_import_keeps_consilium_records_and_routes(client):
    aid, patient = _make_state(client, "signed")
    login(client, DEV_USERNAME)
    history_before = client.get(f"/api/patients/{patient['id']}").json()["history"]
    row_before = _row(aid)
    document = _export_document(client)
    added = _import(client, document, "add")
    assert added.status_code == 200 and added.json()["added"] == len(document["patients"])
    assert _row(aid) == row_before
    assert client.get(f"/api/patients/{patient['id']}").json()["history"] == history_before


def test_preview_hides_modes_the_user_cannot_use(client):
    department = create_department("Отделение импорта")
    create_account(DEV_USERNAME)
    create_account("head.import", "department_head", department_ids=[department])
    login(client, "head.import")
    preview = client.post("/api/import/preview", headers=client.csrf,
                          json={"raw": json.dumps({"schemaVersion": 18, "patients": []})})
    assert preview.status_code == 200, preview.text
    assert preview.json()["valid"] is True
    assert set(preview.json()["consiliumImpact"]) == {"add"}


def test_import_dialog_uses_server_impact_and_blocks_confirm():
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    body = _method_body(app_js, "renderImportConsiliumImpact(mode) {")
    assert "this.importReport.consiliumImpact" in body
    assert "$('confirmImportCommitBtn').disabled = blocked;" in body
    assert 'id="importConsiliumImpact"' in (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")


# --- Отметка исполнения направления и избранное — отдельные права ------------

def test_referral_mark_and_favorites_are_separate_permissions(client, monkeypatch):
    """Each endpoint checks its own permission: switching one off must not
    affect the other, and neither follows from the decision permission."""
    from backend.app import policy

    aid, patient = _make_state(client, "signed")  # surgery decision, chair session
    referral = f"/api/consilium/appointments/{aid}/referral-completion"
    favorite = f"/api/consilium/favorites/{patient['id']}"
    assert client.post(referral, json={"completed": True}, headers=client.csrf).status_code == 200
    assert _row(aid)["referral_completed_at"]

    monkeypatch.setattr(policy, "can_mark_referral_completion", lambda user: False)
    assert client.post(referral, json={"completed": False}, headers=client.csrf).status_code == 403
    assert _row(aid)["referral_completed_at"]
    assert client.post(favorite, json={}, headers=client.csrf).status_code == 200

    monkeypatch.undo()
    monkeypatch.setattr(policy, "can_use_chair_favorites", lambda user: False)
    assert client.post(favorite, json={}, headers=client.csrf).status_code == 403
    assert client.post(referral, json={"completed": False}, headers=client.csrf).status_code == 200
    assert _post(client, aid, "review").status_code == 409  # decision rights untouched, record is signed


def test_referral_mark_does_not_touch_the_decision(client):
    aid, _ = _make_state(client, "signed")
    before = {c: _row(aid)[c] for c in DECISION_COLUMNS}
    assert client.post(f"/api/consilium/appointments/{aid}/referral-completion",
                       json={"completed": True}, headers=client.csrf).status_code == 200
    assert {c: _row(aid)[c] for c in DECISION_COLUMNS} == before


# --- История возвратов переживает удаление записи --------------------------

def _returned_record(client):
    """Doctor's record returned once by the chair (needs_revision); doctor session."""
    doctor = create_account("doctor.softdel", "doctor", full_name="Удалова Врач Автор")
    create_account("chair.softdel", "consilium_chair")
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент Удаления", treatingDoctorUserId=doctor)
    login(client, "doctor.softdel")
    aid = client.post("/api/consilium/appointments", json=appointment_payload(patient["id"]),
                      headers=client.csrf).json()["id"]
    login(client, "chair.softdel")
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "return", {"comment": "Текст первого возврата"}).status_code == 200
    login(client, "doctor.softdel")
    return aid, patient, doctor


def test_deleting_a_returned_record_keeps_its_history(client):
    aid, patient, doctor = _returned_record(client)
    history = _history(aid)
    assert [h[1] for h in history] == ["Текст первого возврата"]

    response = client.delete(f"/api/consilium/appointments/{aid}", headers=client.csrf)
    assert response.status_code == 200 and response.json()["historyPreserved"] is True
    row = _row(aid)
    assert row is not None and row["deleted_at"] and row["deleted_by_user_id"] == doctor
    assert _history(aid) == history
    audit = [r for r in _audit_rows("consilium_appointment_deleted")]
    assert audit[-1]["detail"] == "mode=soft;history_preserved=true"
    assert "Текст первого возврата" not in str(audit)

    assert client.get("/api/consilium/appointments").json()["appointments"] == []
    assert patient["id"] not in client.get("/api/consilium/booked-patients").json()["patientIds"]
    assert client.get(f"/api/consilium/appointments/{aid}/revisions").status_code == 404
    login(client, "chair.softdel")
    assert all(a["id"] != aid for a in client.get("/api/consilium/appointments").json()["appointments"])
    for action, body in (("review", None), ("sign", DECISION), ("return", {"comment": "Ещё"})):
        assert _post(client, aid, action, body).status_code == 404
    assert _row(aid)["deleted_at"]

    # Слот свободен, пациента можно записать снова; новая запись — отдельная история.
    login(client, "doctor.softdel")
    again = client.post("/api/consilium/appointments", json=appointment_payload(patient["id"]), headers=client.csrf)
    assert again.status_code == 201, again.text
    new_id = again.json()["id"]
    login(client, "chair.softdel")
    assert _post(client, new_id, "review").status_code == 200
    assert _post(client, new_id, "return", {"comment": "Текст для новой записи"}).status_code == 200
    assert [h[1] for h in _history(new_id)] == ["Текст для новой записи"]
    assert _history(aid) == history


def test_record_without_history_is_still_removed(client):
    aid, _ = _make_state(client, "recorded")
    login(client, DEV_USERNAME)
    response = client.delete(f"/api/consilium/appointments/{aid}", headers=client.csrf)
    assert response.status_code == 200 and response.json()["historyPreserved"] is False
    assert _row(aid) is None
    assert _audit_rows("consilium_appointment_deleted")[-1]["detail"] == "mode=hard"


def test_soft_deleted_history_still_blocks_destructive_import_and_card_deletion(client):
    aid, patient, _ = _returned_record(client)
    assert client.delete(f"/api/consilium/appointments/{aid}", headers=client.csrf).status_code == 200
    login(client, DEV_USERNAME)
    before = _data_digest()
    assert _import(client, _export_document(client), "replace").status_code == 409
    assert client.delete(f"/api/patients/{patient['id']}", headers=client.csrf).status_code == 409
    assert _data_digest() == before
    assert _history(aid)


# --- Связь подписанного решения с маршрутом пациента ------------------------

def _route(client, patient_id):
    card = client.get(f"/api/patients/{patient_id}").json()
    return card["stage"], [(h["stage"], h["enteredAt"]) for h in card["history"]]


def test_signing_records_route_context_without_moving_the_patient(client):
    doctor = create_account("doctor.route", "doctor")
    create_account("chair.route", "consilium_chair")
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент Маршрута", treatingDoctorUserId=doctor)
    route = _route(client, patient["id"])
    assert route[0] == "exam"

    def unchanged():
        login(client, DEV_USERNAME)
        assert _route(client, patient["id"]) == route

    login(client, "doctor.route")
    aid = client.post("/api/consilium/appointments", json=appointment_payload(patient["id"]),
                      headers=client.csrf).json()["id"]
    unchanged()
    login(client, "chair.route")
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "return", {"comment": "Уточните"}).status_code == 200
    unchanged()
    login(client, "doctor.route")
    assert client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf,
                      json=appointment_payload(patient["id"], comment="Исправлено")).status_code == 200
    login(client, "chair.route")
    assert _post(client, aid, "review").status_code == 200
    signed = _post(client, aid, "sign", DECISION)
    assert signed.status_code == 200
    unchanged()

    context = signed.json()["routeContext"]
    last = get_conn().execute(
        "SELECT id, stage, entered_at FROM history_entries WHERE patient_id = ? ORDER BY seq DESC LIMIT 1",
        (patient["id"],)).fetchone()
    assert context == {"stage": "exam", "historyEntryId": last["id"], "enteredAt": last["entered_at"],
                       "source": "recorded"}
    assert route[1][-1] == ("exam", last["entered_at"])

    login(client, "doctor.route")
    assert _post(client, aid, "archive").status_code == 200
    assert _post(client, aid, "restore").status_code == 200
    unchanged()


def test_route_context_stays_historical_after_the_route_moves_on(client):
    aid, patient = _make_state(client, "signed")
    signed_context = client.get("/api/consilium/appointments").json()["appointments"][0]["routeContext"]
    login(client, DEV_USERNAME)
    moved = client.post(f"/api/patients/{patient['id']}/stage", json={"newStage": "treatment-prep"},
                        headers=client.csrf)
    assert moved.status_code == 200, moved.text
    assert client.get(f"/api/patients/{patient['id']}").json()["stage"] == "treatment-prep"
    login(client, "chair.states")
    after = client.get("/api/consilium/appointments").json()["appointments"][0]["routeContext"]
    assert after == signed_context and after["stage"] == "exam"
    conn = get_conn()
    for column, value in (("signed_route_stage", "treatment-prep"), ("signed_route_history_entry_id", "x"),
                          ("signed_route_link_source", "derived")):
        with pytest.raises(sqlite3.IntegrityError, match="consilium_signed_immutable"):
            conn.execute(f"UPDATE oncology_consilium_appointments SET {column} = ? WHERE id = ?", (value, aid))
        conn.rollback()


def test_unsigned_records_have_no_route_context(client):
    _make_state(client, "in_review")
    assert client.get("/api/consilium/appointments").json()["appointments"][0]["routeContext"] is None
