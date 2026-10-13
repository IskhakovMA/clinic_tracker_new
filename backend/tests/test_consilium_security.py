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
    "return": {"recorded", "in_review", "needs_revision", "revised"},
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
