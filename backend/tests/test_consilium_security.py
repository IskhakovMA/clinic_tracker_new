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
