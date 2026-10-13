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
