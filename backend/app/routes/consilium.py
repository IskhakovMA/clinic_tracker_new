"""Oncology consilium appointments owned by the current account."""
from __future__ import annotations

import sqlite3
from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from .. import audit, policy
from ..deps import get_db, require_active_password, require_csrf
from ..models import (
    ConsiliumAppointmentIn,
    ConsiliumDecisionIn,
    ConsiliumRegistryCompletionIn,
    ConsiliumRevisionIn,
    ConsiliumScheduleGenerateIn,
)
from ..services import consilium
from ..services.xlsx_export import workbook_bytes


router = APIRouter(prefix="/api/consilium", tags=["consilium"])


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")


def _require_chair(conn: sqlite3.Connection, user: policy.CurrentUser) -> None:
    if not policy.can_manage_consilium(user):
        audit.record(
            conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED,
            detail="action=consilium_manage", **audit.actor_ctx(user),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _validated_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        date_type.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="date must be YYYY-MM-DD") from exc
    return value


def _require_writable_patient(
    conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str
) -> None:
    if not policy.can_read_patient(conn, user, patient_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    if not policy.can_write_patient(conn, user, patient_id):
        audit.record(
            conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED,
            detail="action=consilium_appointment_write", **audit.actor_ctx(user),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


@router.get("/meta")
def get_consilium_meta(
    user: policy.CurrentUser = Depends(require_active_password),
):
    return consilium.metadata()


@router.get("/appointments")
def list_consilium_appointments(
    archived: bool = Query(False),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    return {"appointments": consilium.list_for_user(conn, user, archived=archived)}


@router.get("/booked-patients")
def list_booked_consilium_patients(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    return {"patientIds": consilium.booked_patient_ids(conn, user)}


@router.get("/schedule")
def list_consilium_schedule(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    return {"slots": consilium.schedule_slots(conn, user.id)}


@router.post("/schedule/generate", dependencies=[Depends(require_csrf)])
def generate_consilium_schedule(
    body: ConsiliumScheduleGenerateIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    try:
        created = consilium.generate_schedule(
            conn, user, body.scheduledDate, body.consiliumGroup,
            body.startTime, body.endTime, body.intervalMinutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    audit.record(
        conn, audit.CONSILIUM_SCHEDULE_GENERATED,
        detail=f"created={created};interval={body.intervalMinutes}", **audit.actor_ctx(user),
    )
    return {"created": created, "slots": consilium.schedule_slots(conn, user.id)}


@router.delete("/schedule/{slot_id}", dependencies=[Depends(require_csrf)])
def delete_consilium_schedule_slot(
    slot_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    try:
        deleted = consilium.delete_schedule_slot(conn, slot_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        _not_found()
    audit.record(conn, audit.CONSILIUM_SCHEDULE_SLOT_DELETED, **audit.actor_ctx(user))
    return {"deleted": True}


@router.post("/appointments/{appointment_id}/archive", dependencies=[Depends(require_csrf)])
def archive_consilium_appointment(
    appointment_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    try:
        appointment = consilium.set_archived(conn, user, appointment_id, True)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if appointment is None:
        _not_found()
    audit.record(
        conn, audit.CONSILIUM_ARCHIVED, patient_id=appointment["patientId"],
        **audit.actor_ctx(user),
    )
    return appointment


@router.post("/appointments/{appointment_id}/restore", dependencies=[Depends(require_csrf)])
def restore_consilium_appointment(
    appointment_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    try:
        appointment = consilium.set_archived(conn, user, appointment_id, False)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if appointment is None:
        _not_found()
    audit.record(
        conn, audit.CONSILIUM_RESTORED, patient_id=appointment["patientId"],
        **audit.actor_ctx(user),
    )
    return appointment


@router.post(
    "/appointments",
    dependencies=[Depends(require_csrf)],
    status_code=status.HTTP_201_CREATED,
)
def create_consilium_appointment(
    body: ConsiliumAppointmentIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable_patient(conn, user, body.patientId)
    previous_stage = conn.execute(
        "SELECT stage FROM patients WHERE id = ?", (body.patientId,)
    ).fetchone()["stage"]
    try:
        appointment = consilium.create(conn, user, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit.record(
        conn, audit.CONSILIUM_APPOINTMENT_CREATED, patient_id=body.patientId,
        **audit.actor_ctx(user),
    )
    if previous_stage != "consult":
        audit.record(
            conn, audit.PATIENT_UPDATED, patient_id=body.patientId,
            detail="field=stage;reason=consilium_appointment",
            **audit.actor_ctx(user),
        )
    return appointment


@router.put("/appointments/{appointment_id}", dependencies=[Depends(require_csrf)])
def update_consilium_appointment(
    appointment_id: str,
    body: ConsiliumAppointmentIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    existing = consilium.get_owned(conn, user, appointment_id)
    if existing is None:
        _not_found()
    if existing["status"] == "signed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Подписанная запись недоступна для редактирования.",
        )
    _require_writable_patient(conn, user, body.patientId)
    previous_stage = conn.execute(
        "SELECT stage FROM patients WHERE id = ?", (body.patientId,)
    ).fetchone()["stage"]
    try:
        appointment = consilium.update(conn, user, appointment_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if appointment is None:
        _not_found()
    audit.record(
        conn, audit.CONSILIUM_APPOINTMENT_UPDATED, patient_id=body.patientId,
        **audit.actor_ctx(user),
    )
    if previous_stage != "consult":
        audit.record(
            conn, audit.PATIENT_UPDATED, patient_id=body.patientId,
            detail="field=stage;reason=consilium_appointment",
            **audit.actor_ctx(user),
        )
    return appointment


@router.delete("/appointments/{appointment_id}", dependencies=[Depends(require_csrf)])
def delete_consilium_appointment(
    appointment_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    try:
        appointment = consilium.delete_owned(conn, user, appointment_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if appointment is None:
        _not_found()
    audit.record(
        conn, audit.CONSILIUM_APPOINTMENT_DELETED, patient_id=appointment["patientId"],
        **audit.actor_ctx(user),
    )
    return {"deleted": True, "releasedSlot": {
        "scheduledDate": appointment["scheduledDate"],
        "scheduledTime": appointment["scheduledTime"],
        "consiliumGroup": appointment["consiliumGroup"],
    }}


@router.post("/appointments/{appointment_id}/review", dependencies=[Depends(require_csrf)])
def start_consilium_review(
    appointment_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    appointment = consilium.start_review(conn, appointment_id)
    if appointment is None:
        _not_found()
    audit.record(
        conn, audit.CONSILIUM_REVIEW_STARTED, patient_id=appointment["patientId"],
        **audit.actor_ctx(user),
    )
    return appointment


@router.post("/appointments/{appointment_id}/sign", dependencies=[Depends(require_csrf)])
def sign_consilium(
    appointment_id: str,
    body: ConsiliumDecisionIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    appointment = consilium.sign(
        conn, user, appointment_id, body.treatmentType, body.treatmentScheme,
        body.treatmentSchemeCode, body.goc, body.goc65Plus,
    )
    if appointment is None:
        _not_found()
    audit.record(
        conn, audit.CONSILIUM_SIGNED, patient_id=appointment["patientId"],
        **audit.actor_ctx(user),
    )
    return appointment


@router.post("/appointments/{appointment_id}/return", dependencies=[Depends(require_csrf)])
def return_consilium_for_revision(
    appointment_id: str,
    body: ConsiliumRevisionIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    appointment = consilium.return_for_revision(conn, appointment_id, body.comment)
    if appointment is None:
        _not_found()
    audit.record(
        conn, audit.CONSILIUM_RETURNED, patient_id=appointment["patientId"],
        **audit.actor_ctx(user),
    )
    return appointment


@router.get("/analytics")
def consilium_analytics(
    date_from: str | None = Query(None, max_length=10),
    date_to: str | None = Query(None, max_length=10),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    return consilium.analytics_summary(conn, date_from, date_to)


@router.post("/favorites/{patient_id}", dependencies=[Depends(require_csrf)])
def add_consilium_favorite(
    patient_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    linked = conn.execute(
        "SELECT 1 FROM oncology_consilium_appointments WHERE patient_id = ?",
        (patient_id,),
    ).fetchone()
    if linked is None or not consilium.add_favorite(conn, user, patient_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    audit.record(
        conn, audit.CONSILIUM_FAVORITE_ADDED, patient_id=patient_id,
        **audit.actor_ctx(user),
    )
    return {"patientId": patient_id, "favorite": True}


@router.delete("/favorites/{patient_id}", dependencies=[Depends(require_csrf)])
def remove_consilium_favorite(
    patient_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    consilium.remove_favorite(conn, user, patient_id)
    audit.record(
        conn, audit.CONSILIUM_FAVORITE_REMOVED, patient_id=patient_id,
        **audit.actor_ctx(user),
    )
    return {"patientId": patient_id, "favorite": False}


@router.post("/appointments/{appointment_id}/referral-completion", dependencies=[Depends(require_csrf)])
def set_surgical_referral_completion(
    appointment_id: str,
    body: ConsiliumRegistryCompletionIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    try:
        appointment = consilium.set_referral_completed(
            conn, user, appointment_id, body.completed
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if appointment is None:
        _not_found()
    audit.record(
        conn, audit.CONSILIUM_REFERRAL_COMPLETED,
        patient_id=appointment["patientId"],
        detail=f"completed={str(body.completed).lower()}",
        **audit.actor_ctx(user),
    )
    return appointment


@router.get("/registries")
def treatment_registries(
    date: str | None = Query(None, max_length=10),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    selected_date = _validated_date(date)
    return {
        "surgical": consilium.registry_rows(conn, "surgery", selected_date),
        "chemotherapy": consilium.registry_rows(conn, "chemotherapy", selected_date),
        "goc": consilium.goc_registry_rows(conn, "goc", selected_date),
        "goc65Plus": consilium.goc_registry_rows(conn, "goc_65_plus", selected_date),
    }


@router.get("/registries/chemotherapy.xlsx")
def export_chemotherapy_registry(
    date: str | None = Query(None, max_length=10),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    selected_date = _validated_date(date)
    items = consilium.registry_rows(conn, "chemotherapy", selected_date)
    rows: list[list[object]] = [[
        "Дата", "Время", "Пациент", "Полис ОМС", "Диагноз",
        "Код схемы", "Схема лечения", "Лечащий врач", "Консилиумная группа",
    ]]
    rows.extend([
        [
            item["scheduledDate"], item["scheduledTime"], item["patientName"],
            item["policyNumber"], item["primaryDiagnosis"], item["treatmentSchemeCode"], item["treatmentScheme"],
            item["treatingDoctorName"], item["consiliumGroupLabel"],
        ]
        for item in items
    ])
    suffix = selected_date or "all"
    content = workbook_bytes("Химиотерапия", rows)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="chemotherapy_registry_{suffix}.xlsx"'},
    )


@router.get("/registries/goc.xlsx")
def export_goc_registry(
    date: str | None = Query(None, max_length=10),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    selected_date = _validated_date(date)
    items = consilium.goc_registry_rows(conn, "goc", selected_date)
    rows: list[list[object]] = [[
        "Дата", "Время", "Пациент", "Полис ОМС", "Диагноз",
        "Назначенное лечение", "Схема лечения", "Лечащий врач", "Консилиумная группа",
    ]]
    rows.extend([
        [
            item["scheduledDate"], item["scheduledTime"], item["patientName"],
            item["policyNumber"], item["primaryDiagnosis"], item["treatmentTypeLabel"],
            item["treatmentScheme"], item["treatingDoctorName"], item["consiliumGroupLabel"],
        ]
        for item in items
    ])
    suffix = selected_date or "all"
    content = workbook_bytes("ГОК", rows)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="goc_registry_{suffix}.xlsx"'},
    )


@router.get("/registries/goc-65-plus.xlsx")
def export_goc_65_plus_registry(
    date: str | None = Query(None, max_length=10),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_chair(conn, user)
    selected_date = _validated_date(date)
    items = consilium.goc_registry_rows(conn, "goc_65_plus", selected_date)
    rows: list[list[object]] = [[
        "Дата", "Время", "Пациент", "Полис ОМС", "Диагноз",
        "Назначенное лечение", "Схема лечения", "Лечащий врач", "Консилиумная группа",
    ]]
    rows.extend([
        [
            item["scheduledDate"], item["scheduledTime"], item["patientName"],
            item["policyNumber"], item["primaryDiagnosis"], item["treatmentTypeLabel"],
            item["treatmentScheme"], item["treatingDoctorName"], item["consiliumGroupLabel"],
        ]
        for item in items
    ])
    suffix = selected_date or "all"
    content = workbook_bytes("ГОК 65+", rows)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="goc_65_plus_registry_{suffix}.xlsx"'},
    )
