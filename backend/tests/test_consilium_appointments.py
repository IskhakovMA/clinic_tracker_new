from io import BytesIO
from zipfile import ZipFile

import pytest

from .conftest import (card_version,
    DEV_USERNAME, consilium_version, create_account, get_conn, legacy_consilium_schedule, login,
    make_patient_payload,
)


@pytest.fixture(autouse=True)
def _chair_schedule(request):
    # WP2.3-B2: запись возможна только в реальные слоты председателя.
    legacy_consilium_schedule(request)


def appointment_payload(patient_id, **overrides):
    payload = {
        "patientId": patient_id,
        "scheduledDate": "2026-11-12",
        "scheduledTime": "10:30",
        "consiliumGroup": "thoracic",
        "hasBenefit": True,
        "diagnosisLocalization": "Органы дыхания и грудной клетки",
        "primaryDiagnosis": "C34.1",
        "additionalDiagnosis": "C78.0",
        "comment": "Обсудить тактику лечения",
        "additionalParticipant": "Патоморфолог",
        "needsSurgeon": True,
        "needsRadiotherapist": False,
    }
    payload.update(overrides)
    return payload


def create_patient(client, **overrides):
    response = client.post("/api/patients", json=make_patient_payload(**overrides), headers=client.csrf)
    assert response.status_code == 201, response.text
    return response.json()


def test_consilium_create_list_and_update_are_personal(auth_client):
    patient = create_patient(auth_client)
    created = auth_client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"]),
        headers=auth_client.csrf,
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["patientName"] == patient["name"]
    # Лечащий врач — из карточки пациента, а не автор записи.
    assert item["treatingDoctorName"] == patient["doctor"]
    assert item["consiliumGroupLabel"] == "Торакальная"
    assert item["hasBenefit"] is True

    listed = auth_client.get("/api/consilium/appointments")
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["appointments"]] == [item["id"]]

    updated = auth_client.put(
        f"/api/consilium/appointments/{item['id']}",
        json=appointment_payload(
            patient["id"], version=consilium_version(item['id']),
            scheduledTime="12:00",
            comment="Обновлённый комментарий",
            needsRadiotherapist=True,
        ),
        headers=auth_client.csrf,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["scheduledTime"] == "12:00"
    assert updated.json()["consiliumGroupLabel"] == "Торакальная"
    assert updated.json()["needsRadiotherapist"] is True


def test_consilium_booking_does_not_change_patient_route(auth_client):
    # Правила перехода по результатам консилиума ещё не согласованы:
    # запись и её правка не должны трогать этап и историю маршрута.
    patient = create_patient(auth_client)
    before = auth_client.get(f"/api/patients/{patient['id']}").json()

    created = auth_client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"]),
        headers=auth_client.csrf,
    )
    assert created.status_code == 201, created.text
    updated = auth_client.put(
        f"/api/consilium/appointments/{created.json()['id']}",
        json=appointment_payload(patient["id"], version=consilium_version(created.json()['id']), scheduledTime="12:00"),
        headers=auth_client.csrf,
    )
    assert updated.status_code == 200, updated.text

    after = auth_client.get(f"/api/patients/{patient['id']}").json()
    assert after["stage"] == before["stage"] == "exam"
    assert [entry["stage"] for entry in after["history"]] == ["exam"]
    conn = get_conn()
    stage_audit = conn.execute(
        "SELECT COUNT(*) AS c FROM audit_events WHERE event_type = 'patient_updated' "
        "AND patient_id = ? AND detail LIKE '%reason=consilium_appointment%'",
        (patient["id"],),
    ).fetchone()["c"]
    assert stage_audit == 0


def test_doctor_can_delete_unsigned_appointment_and_reuse_slot(auth_client):
    first_patient = create_patient(auth_client, name="Пациент для удаления")
    created = auth_client.post(
        "/api/consilium/appointments",
        json=appointment_payload(first_patient["id"]),
        headers=auth_client.csrf,
    )
    assert created.status_code == 201, created.text

    deleted = auth_client.delete(
        f"/api/consilium/appointments/{created.json()['id']}?version={consilium_version(created.json()['id'])}",
        headers=auth_client.csrf,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True
    assert deleted.json()["releasedSlot"]["scheduledTime"] == "10:30"
    assert auth_client.get("/api/consilium/appointments").json()["appointments"] == []

    second_patient = create_patient(auth_client, name="Пациент на освободившийся слот")
    reused = auth_client.post(
        "/api/consilium/appointments",
        json=appointment_payload(second_patient["id"]),
        headers=auth_client.csrf,
    )
    assert reused.status_code == 201, reused.text


def test_consilium_update_cannot_replace_patient(auth_client):
    first_patient = create_patient(auth_client, name="Первый пациент")
    second_patient = create_patient(auth_client, name="Второй пациент", diagnosis="C50.1", nosology="breast")
    appointment = auth_client.post(
        "/api/consilium/appointments",
        json=appointment_payload(first_patient["id"]),
        headers=auth_client.csrf,
    ).json()

    response = auth_client.put(
        f"/api/consilium/appointments/{appointment['id']}",
        json=appointment_payload(
            second_patient["id"], version=consilium_version(appointment['id']),
            primaryDiagnosis="C50.1",
            diagnosisLocalization="Молочная железа",
        ),
        headers=auth_client.csrf,
    )
    assert response.status_code == 409, response.text
    assert "нельзя изменить пациента" in response.json()["detail"]
    routed_patient = auth_client.get(f"/api/patients/{second_patient['id']}").json()
    assert routed_patient["stage"] == "exam"


def test_consilium_rejects_duplicate_patient_and_slot_across_users(client):
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    first_patient = create_patient(client, name="Пациент с одной записью")
    second_patient = create_patient(client, name="Пациент для проверки слота")
    first = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(first_patient["id"]), headers=client.csrf,
    )
    assert first.status_code == 201, first.text

    duplicate_patient = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(first_patient["id"], scheduledTime="11:00"), headers=client.csrf,
    )
    assert duplicate_patient.status_code == 409
    assert "уже записан" in duplicate_patient.json()["detail"]

    create_account("other.global.doctor")
    login(client, "other.global.doctor")
    duplicate_slot = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(second_patient["id"]), headers=client.csrf,
    )
    assert duplicate_slot.status_code == 409
    assert "слот уже занят" in duplicate_slot.json()["detail"]


def test_consilium_reschedule_stays_in_original_icd_group(auth_client):
    patient = create_patient(auth_client, name="Пациент для переноса")
    created = auth_client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"]), headers=auth_client.csrf,
    ).json()
    moved = auth_client.put(
        f"/api/consilium/appointments/{created['id']}",
        json=appointment_payload(patient["id"], version=consilium_version(created['id']), scheduledDate="2026-11-13", scheduledTime="11:15"),
        headers=auth_client.csrf,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["scheduledDate"] == "2026-11-13"
    assert moved.json()["scheduledTime"] == "11:15"
    wrong_group = auth_client.put(
        f"/api/consilium/appointments/{created['id']}",
        json=appointment_payload(patient["id"], version=consilium_version(created['id']), consiliumGroup="breast"),
        headers=auth_client.csrf,
    )
    assert wrong_group.status_code == 409
    assert "прежней консилиумной группы" in wrong_group.json()["detail"]


def test_consilium_list_does_not_show_another_users_records(client):
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client)
    first = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"]),
        headers=client.csrf,
    )
    assert first.status_code == 201

    create_account("other.developer")
    login(client, "other.developer")
    assert client.get("/api/consilium/appointments").json()["appointments"] == []
    assert client.put(
        f"/api/consilium/appointments/{first.json()['id']}",
        json=appointment_payload(patient["id"], version=consilium_version(first.json()['id'])),
        headers=client.csrf,
    ).status_code == 404


def test_consilium_rejects_patient_outside_doctor_scope(client):
    doctor_a = create_account("doctor.a", "doctor")
    create_account("doctor.b", "doctor")
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client, treatingDoctorUserId=doctor_a)

    login(client, "doctor.b")
    response = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"]),
        headers=client.csrf,
    )
    assert response.status_code == 404


def test_consilium_validates_malignant_diagnosis_range(auth_client):
    patient = create_patient(auth_client)
    response = auth_client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"], primaryDiagnosis="C98"),
        headers=auth_client.csrf,
    )
    assert response.status_code == 422


def test_consilium_metadata_and_schema(auth_client):
    meta = auth_client.get("/api/consilium/meta")
    assert meta.status_code == 200
    assert len(meta.json()["treatmentSchemes"]) == 815
    assert meta.json()["treatmentSchemes"][0] == {"code": "sh0001", "name": "Абиратерон"}
    assert "09:00" in meta.json()["timeSlots"]
    assert any(group["value"] == "thoracic" for group in meta.json()["groups"])
    conn = get_conn()
    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'oncology_consilium_appointments'"
    ).fetchone()


def test_consilium_frontend_has_navigation_form_and_editing_controls():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "static" / "app.js").read_text(encoding="utf-8")
    assert 'data-view="consilium"' in html
    assert 'id="consiliumModal"' in html
    assert 'id="consiliumPrimaryDiagnosis"' in html
    assert 'id="consiliumAdditionalDiagnosis"' in html
    assert "openConsiliumModal(item.id)" in js
    assert 'id="consiliumDecisionModal"' in html
    assert 'id="consiliumDateFilter"' in html
    assert "openConsiliumDecision(item.id)" in js


def test_consilium_chair_sees_all_returns_signs_and_gets_analytics(client):
    create_account(DEV_USERNAME)
    # WP2.3-B2: уведомление о доработке получает лечащий врач пациента (и
    # заведующий), а не автор записи; врач управляет записью, созданной другим.
    treating = create_account("doctor.revision", "doctor", full_name="Лечащева Врач Доработкина")
    login(client, DEV_USERNAME)
    patient = create_patient(client, treatingDoctorUserId=treating)
    created = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"]),
        headers=client.csrf,
    ).json()

    chair_id = create_account(
        "chair.tester", "consilium_chair",
        full_name="Председателев Тест Консилиумович",
    )
    assert login(client, "chair.tester").status_code == 200
    me = client.get("/api/auth/me").json()["user"]
    assert me["roleId"] == "consilium_chair"
    assert me["permissions"]["consiliumChair"] is True
    assert me["permissions"]["importData"] is False
    # WP2.4-A (R6): председатель новые карточки не создает; его карточка, созданная
    # до ограничения (legacy), остается за ним.
    assert me["permissions"]["createPatients"] is False
    denied = client.post("/api/patients", json=make_patient_payload(name="Пациент председателя для наблюдения"),
                         headers=client.csrf)
    assert denied.status_code == 403
    from backend.app import store
    from backend.app.models import PatientCreateIn
    tracked_id = store.create_patient(
        get_conn(), PatientCreateIn(**make_patient_payload(name="Пациент председателя для наблюдения")), chair_id)
    tracked = client.get(f"/api/patients/{tracked_id}").json()
    assert tracked["treatingDoctorUserId"] == chair_id

    listed = client.get("/api/consilium/appointments").json()["appointments"]
    assert [item["id"] for item in listed] == [created["id"]]
    reviewed = client.post(
        f"/api/consilium/appointments/{created['id']}/review",
        json={}, headers=client.csrf,
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "in_review"

    returned = client.post(
        f"/api/consilium/appointments/{created['id']}/return",
        json={"comment": "Уточните результаты обследования"}, headers=client.csrf,
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "needs_revision"

    login(client, DEV_USERNAME)
    assert client.get("/api/internal-notifications?type=consilium_revision").json()["items"] == []
    login(client, "doctor.revision")
    doctor_item = client.get("/api/consilium/appointments").json()["appointments"][0]
    assert doctor_item["revisionComment"] == "Уточните результаты обследования"
    assert doctor_item["statusLabel"] == "На доработке"
    revision_notifications = client.get(
        "/api/internal-notifications?type=consilium_revision"
    ).json()["items"]
    assert len(revision_notifications) == 1
    assert revision_notifications[0]["source_id"] == created["id"]

    revised = client.put(
        f"/api/consilium/appointments/{created['id']}",
        json=appointment_payload(patient["id"], version=consilium_version(created['id']), comment="Добавлены уточнённые сведения"),
        headers=client.csrf,
    )
    assert revised.status_code == 200, revised.text
    # WP2.3-B1: правка не означает повторную подачу — статус и уведомление прежние,
    # повторного уведомления нет; подача — отдельным действием /resubmit.
    assert revised.json()["status"] == "needs_revision"
    assert len(client.get("/api/internal-notifications?type=consilium_revision").json()["items"]) == 1
    revised = client.post(
        f"/api/consilium/appointments/{created['id']}/resubmit",
        json={"version": revised.json()["version"]}, headers=client.csrf,
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["status"] == "revised"
    assert revised.json()["statusLabel"] == "Возвращен с доработки"
    assert client.get("/api/internal-notifications?type=consilium_revision").json()["items"] == []

    login(client, "chair.tester")
    assert client.get("/api/consilium/appointments").json()["appointments"][0]["status"] == "revised"
    client.post(
        f"/api/consilium/appointments/{created['id']}/review",
        json={}, headers=client.csrf,
    )
    signed = client.post(
        f"/api/consilium/appointments/{created['id']}/sign",
        json={"treatmentType": "chemotherapy", "treatmentScheme": "Абиратерон", "treatmentSchemeCode": "sh0001"},
        headers=client.csrf,
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["status"] == "signed"
    assert signed.json()["treatmentTypeLabel"] == "Химиотерапевтическое лечение"
    assert signed.json()["treatmentSchemeCode"] == "sh0001"

    analytics = client.get("/api/consilium/analytics").json()
    assert analytics["summary"]["signed"] == 1
    assert next(row for row in analytics["treatments"] if row["code"] == "chemotherapy")["count"] == 1
    assert analytics["schemes"] == [{"code": "sh0001", "label": "Абиратерон", "count": 1}]


def test_chair_schedule_grid_is_used_for_doctor_booking(client):
    # Тест генератора: начинаем с пустого расписания (без слотов фикстуры).
    get_conn().execute("DELETE FROM consilium_schedule_slots")
    get_conn().commit()
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент расписания")
    chair_id = create_account("chair.schedule", "consilium_chair")
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
    assert generated.json()["created"] == 2
    slots = [
        slot for slot in generated.json()["slots"]
        if slot["scheduledDate"] == "2026-11-12" and slot["consiliumGroup"] == "thoracic"
    ]
    assert [slot["scheduledTime"] for slot in slots] == ["10:00", "10:30"]
    assert all(slot["occupied"] is False for slot in slots)
    generated_urology = client.post(
        "/api/consilium/schedule/generate",
        json={
            "scheduledDate": "2026-11-12", "consiliumGroup": "urology",
            "startTime": "10:00", "endTime": "11:00", "intervalMinutes": 30,
        },
        headers=client.csrf,
    )
    assert generated_urology.status_code == 200, generated_urology.text

    login(client, DEV_USERNAME)
    missing = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"], scheduledTime="11:30"),
        headers=client.csrf,
    )
    assert missing.status_code == 409
    booked = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"], scheduledTime="10:30", uvRuv=True),
        headers=client.csrf,
    )
    assert booked.status_code == 201, booked.text
    schedule = client.get("/api/consilium/schedule").json()["slots"]
    occupied = next(slot for slot in schedule if slot["scheduledDate"] == "2026-11-12" and slot["scheduledTime"] == "10:30" and slot["consiliumGroup"] == "thoracic")
    assert occupied["occupied"] is True
    assert occupied["appointment"]["patientName"] == "Пациент расписания"
    assert occupied["appointment"]["primaryDiagnosis"] == "C34.1"
    assert occupied["appointment"]["treatingDoctorName"] == booked.json()["treatingDoctorName"]
    assert occupied["appointment"]["uvRuv"] is True
    assert occupied["appointment"]["isMine"] is True
    same_time_other_group = next(
        slot for slot in schedule
        if slot["scheduledDate"] == "2026-11-12" and slot["scheduledTime"] == "10:30" and slot["consiliumGroup"] == "urology"
    )
    assert same_time_other_group["occupied"] is False
    assert same_time_other_group["appointment"] is None

    login(client, "chair.schedule")
    assert client.delete(
        f"/api/consilium/schedule/{occupied['id']}", headers=client.csrf
    ).status_code == 409
    chair_schedule = client.get("/api/consilium/schedule").json()["slots"]
    chair_occupied = next(
        slot for slot in chair_schedule
        if slot["scheduledDate"] == "2026-11-12" and slot["scheduledTime"] == "10:30" and slot["consiliumGroup"] == "thoracic"
    )
    assert chair_occupied["appointment"]["isMine"] is False
    free = next(slot for slot in schedule if slot["scheduledDate"] == "2026-11-12" and slot["scheduledTime"] == "10:00" and slot["consiliumGroup"] == "thoracic")
    assert client.delete(f"/api/consilium/schedule/{free['id']}", headers=client.csrf).status_code == 200


def test_schedule_has_no_generated_current_week(client):
    # WP2.3-B2: вычисляемой текущей недели больше нет — только слоты председателя.
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    slots = client.get("/api/consilium/schedule").json()["slots"]
    stored = get_conn().execute("SELECT COUNT(*) FROM consilium_schedule_slots").fetchone()[0]
    assert len(slots) == stored
    assert all(slot["isDefaultWeek"] is False for slot in slots)
    assert not [slot for slot in slots if slot["id"].startswith("current-week-")]


def test_consilium_chemo_scheme_is_required_for_signature(client):
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client)
    created = client.post(
        "/api/consilium/appointments", json=appointment_payload(patient["id"]), headers=client.csrf
    ).json()
    create_account("chair.tester", "consilium_chair")
    login(client, "chair.tester")
    response = client.post(
        f"/api/consilium/appointments/{created['id']}/sign",
        json={"treatmentType": "chemotherapy", "treatmentScheme": ""},
        headers=client.csrf,
    )
    assert response.status_code == 422


def test_signed_record_is_locked_policy_is_saved_and_surgical_registry_is_checkable(client):
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client, policyNumber="77 00-1234 567890")
    payload = appointment_payload(patient["id"], policyNumber="77 00-1234 567890")
    created = client.post(
        "/api/consilium/appointments", json=payload, headers=client.csrf
    ).json()
    assert created["policyNumber"] == "77001234567890"

    create_account("chair.registry", "consilium_chair")
    login(client, "chair.registry")
    assert client.post(
        f"/api/consilium/appointments/{created['id']}/review", json={}, headers=client.csrf,
    ).status_code == 200
    signed = client.post(
        f"/api/consilium/appointments/{created['id']}/sign",
        json={"treatmentType": "surgery", "treatmentScheme": ""},
        headers=client.csrf,
    )
    assert signed.status_code == 200
    registry = client.get("/api/consilium/registries?date=2026-11-12").json()
    assert [row["id"] for row in registry["surgical"]] == [created["id"]]
    assert registry["surgical"][0]["referralCompleted"] is False

    completed = client.post(
        f"/api/consilium/appointments/{created['id']}/referral-completion",
        json={"completed": True}, headers=client.csrf,
    )
    assert completed.status_code == 200
    assert completed.json()["referralCompleted"] is True

    login(client, DEV_USERNAME)
    locked = client.put(
        f"/api/consilium/appointments/{created['id']}",
        json={**payload, "version": consilium_version(created["id"])}, headers=client.csrf,
    )
    assert locked.status_code == 409


def test_chair_favorite_is_a_bookmark_and_chemo_exports_xlsx(client):
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент для избранного", policyNumber="7700000000000011")
    first = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"], policyNumber=patient["policyNumber"]),
        headers=client.csrf,
    ).json()

    create_account("chair.favorite", "consilium_chair")
    login(client, "chair.favorite")
    assert client.get("/api/patients").json() == []
    favorite = client.post(
        f"/api/consilium/favorites/{patient['id']}", json={}, headers=client.csrf
    )
    assert favorite.status_code == 200
    # Избранное — закладка в консилиуме, а не расширение области видимости.
    assert client.get("/api/patients").json() == []
    denied = client.patch(
        f"/api/patients/{patient['id']}",
        json={"expectedVersion": card_version(patient['id']), "field": "complaint", "value": "Нельзя менять избранное"},
        headers=client.csrf,
    )
    assert denied.status_code == 404
    listed = client.get("/api/consilium/appointments").json()["appointments"]
    assert [(row["id"], row["isFavorite"]) for row in listed] == [(first["id"], True)]

    assert client.post(
        f"/api/consilium/appointments/{first['id']}/review", json={}, headers=client.csrf,
    ).status_code == 200
    signed = client.post(
        f"/api/consilium/appointments/{first['id']}/sign",
        json={"treatmentType": "chemotherapy", "treatmentScheme": "FOLFOX, тест"},
        headers=client.csrf,
    )
    assert signed.status_code == 200
    exported = client.get("/api/consilium/registries/chemotherapy.xlsx?date=2026-11-12", headers=client.csrf)
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    with ZipFile(BytesIO(exported.content)) as workbook:
        assert "xl/worksheets/sheet1.xml" in workbook.namelist()
        sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "FOLFOX, тест" in sheet
        assert "Пациент для избранного" in sheet


def test_goc_marks_create_registry_and_excel_export(client):
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент ГОК", policyNumber="7700000000000065")
    created = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"]), headers=client.csrf,
    ).json()

    create_account("chair.goc", "consilium_chair")
    login(client, "chair.goc")
    assert client.post(
        f"/api/consilium/appointments/{created['id']}/review", json={}, headers=client.csrf,
    ).status_code == 200
    signed = client.post(
        f"/api/consilium/appointments/{created['id']}/sign",
        json={
            "treatmentType": "active_observation", "treatmentScheme": "",
            "goc": True, "goc65Plus": True,
        },
        headers=client.csrf,
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["goc"] is True
    assert signed.json()["goc65Plus"] is True

    registries = client.get("/api/consilium/registries?date=2026-11-12").json()
    assert [row["id"] for row in registries["goc"]] == [created["id"]]
    assert [row["id"] for row in registries["goc65Plus"]] == [created["id"]]
    exported = client.get("/api/consilium/registries/goc.xlsx?date=2026-11-12", headers=client.csrf)
    assert exported.status_code == 200
    with ZipFile(BytesIO(exported.content)) as workbook:
        sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "Пациент ГОК" in sheet
    exported_65 = client.get("/api/consilium/registries/goc-65-plus.xlsx?date=2026-11-12", headers=client.csrf)
    assert exported_65.status_code == 200
    with ZipFile(BytesIO(exported_65.content)) as workbook:
        sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "Пациент ГОК" in sheet


def test_doctor_can_archive_and_restore_only_signed_consilium(client):
    create_account(DEV_USERNAME)
    login(client, DEV_USERNAME)
    patient = create_patient(client, name="Пациент для архива")
    created = client.post(
        "/api/consilium/appointments",
        json=appointment_payload(patient["id"]), headers=client.csrf,
    ).json()
    unsigned_archive = client.post(
        f"/api/consilium/appointments/{created['id']}/archive",
        json={"version": consilium_version(created["id"])}, headers=client.csrf,
    )
    assert unsigned_archive.status_code == 409

    create_account("chair.archive", "consilium_chair")
    login(client, "chair.archive")
    assert client.post(
        f"/api/consilium/appointments/{created['id']}/review", json={}, headers=client.csrf,
    ).status_code == 200
    signed = client.post(
        f"/api/consilium/appointments/{created['id']}/sign",
        json={"treatmentType": "active_observation", "treatmentScheme": ""},
        headers=client.csrf,
    )
    assert signed.status_code == 200

    login(client, DEV_USERNAME)
    # Подпись не переносит запись в архив: это отдельное действие автора.
    assert client.get("/api/consilium/appointments?archived=true").json()["appointments"] == []
    assert [row["id"] for row in client.get("/api/consilium/appointments").json()["appointments"]] == [created["id"]]
    archived = client.post(
        f"/api/consilium/appointments/{created['id']}/archive",
        json={"version": consilium_version(created["id"])}, headers=client.csrf,
    )
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert client.get("/api/consilium/appointments").json()["appointments"] == []
    archive_rows = client.get("/api/consilium/appointments?archived=true").json()["appointments"]
    assert [row["id"] for row in archive_rows] == [created["id"]]

    restored = client.post(
        f"/api/consilium/appointments/{created['id']}/restore",
        json={"version": consilium_version(created["id"])}, headers=client.csrf,
    )
    assert restored.status_code == 200
    assert restored.json()["archived"] is False
    assert [row["id"] for row in client.get("/api/consilium/appointments").json()["appointments"]] == [created["id"]]
    assert client.get("/api/consilium/appointments?archived=true").json()["appointments"] == []


def test_doctor_consilium_frontend_has_single_day_multi_group_grid_and_delete(auth_client):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="consiliumDoctorDate"' in html
    assert 'id="consiliumDoctorGroupOptions"' in html
    assert 'id="consiliumChairGroupOptions"' in html
    assert 'id="consiliumAllDatesBtn"' not in html
    assert 'id="consiliumDoctorScheduleTables"' in html
    assert 'id="consiliumChairScheduleTables"' in html
    assert 'id="consiliumDateFrom"' not in html
    assert 'id="consiliumDateTo"' not in html
    assert 'id="openConsiliumArchiveBtn"' in html
    assert 'id="consiliumArchiveModal"' in html
    assert 'id="consiliumArchiveDate"' in html
    assert 'id="consiliumArchiveSearch"' in html
    assert "consilium-archive-row" in js
    assert "this.openConsiliumDetails(item)" in js
    assert "renderConsiliumDoctorSchedule()" in js
    assert "renderConsiliumGroupPicker(role)" in js
    assert "consiliumSelectedGroups(role)" in js
    assert "is-sidebar-collapsed" in js
    assert "consilium-schedule-group__title" in js
    assert "text: slot.scheduledTime" in js
    assert "text: 'В архив'" not in js
    assert "text: 'Удалить'" in js
    assert "deleteConsiliumAppointment(item)" in js
    assert "/api/consilium/appointments/${encodeURIComponent(item.id)}" in js
    assert "text: 'Моя запись'" in js
    assert "this.openConsiliumModal(null, slot)" in js
    assert 'id="consiliumSearchInput"' in html
    assert 'id="consiliumScheduleForm"' in html
    assert 'id="consiliumAppointmentsPanel"' in html
    assert "$('consiliumAppointmentsPanel').classList.add('is-hidden');" in js
    assert 'id="consiliumChairOnlyFreeBtn"' in html
    assert 'id="consiliumDoctorOnlyFreeBtn"' in html
    assert 'id="consiliumMineOnly"' in html
    assert 'consilium-schedule-filter-btn consilium-mine-filter-btn" id="consiliumMineOnly"' in html
    assert 'class="btn btn--danger is-hidden" id="deleteConsiliumBtn"' in html
    assert 'id="consiliumScheduleDatePicker"' in html
    assert 'id="consiliumScheduleCalendarDays"' in html
    assert "event.target.checked ? null : []" in js
    assert "!picker.contains(event.target)" in js
    assert "async fillConsiliumPatientsForGroup(record = null, cursor = null" in js
    assert "/api/patients/workspace/patients?" in js
    thoracic = create_patient(auth_client, name='Синтетический Торакальный', diagnosis='C34.1')
    breast = create_patient(auth_client, name='Синтетический Маммологический', diagnosis='C50.1', nosology='breast')
    for group, wanted, excluded in [('thoracic',thoracic,breast),('breast',breast,thoracic)]:
        response = auth_client.get('/api/patients/workspace/patients',params={'group':group})
        assert response.status_code == 200, response.text
        ids = {p['id'] for p in response.json()['patients']}
        assert wanted['id'] in ids and excluded['id'] not in ids
    assert 'id="consiliumPatientHint"' in html
    assert 'id="consiliumUvRuv"' in html
    assert 'id="consiliumDate" required disabled' in html
    assert 'id="consiliumTime" required disabled' in html
    assert "text: 'УВ/РУВ'" in js
    assert js.index("text: 'УВ/РУВ'", js.index('renderConsiliumDoctorSchedule()')) < js.index("text: 'Моя запись'", js.index('renderConsiliumDoctorSchedule()'))
    assert "this.renderConsiliumChairActions(appointment)" in js
    assert "consilium_revision" in js
    assert "this.consilium.focusId = item.source_id" in js
    assert "consiliumGroupForDiagnosis" in js
    assert "if ((code >= 0 && code <= 14) || (code >= 30 && code <= 32)) return 'head_neck';" in js
    assert "if ((code >= 15 && code <= 16) || (code >= 22 && code <= 25)) return 'abdominal';" in js
    assert "if (code >= 17 && code <= 20) return 'colorectal';" in js
    assert "return 'general';" in js


def test_consilium_meta_includes_general_group(auth_client):
    response = auth_client.get("/api/consilium/meta")
    assert response.status_code == 200
    groups = response.json()["groups"]
    assert "general" in {group["value"] for group in groups}
    assert "Общеонкологическая" in {group["label"] for group in groups}
