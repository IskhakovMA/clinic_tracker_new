"""Oncology consilium appointments owned by the current account."""
from __future__ import annotations

import sqlite3
from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from .. import audit, config, policy
from ..deps import abuse_rate_limit, get_db, require_active_password, require_csrf
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

# Лимиты на пользователя и адрес (config.CONSILIUM_*_RATE_LIMIT, по умолчанию
# 60 изменений и 20 выгрузок/генераций сетки в минуту). Запись врачом и решения
# председателя — штучные действия: 60/мин покрывают заседание и останавливают
# скрипт; выгрузки реестров с ПДн и генерация сетки тяжелее и реже.
_WINDOW = config.CONSILIUM_RATE_LIMIT_WINDOW_SECONDS
_WRITE_LIMIT = Depends(abuse_rate_limit("consilium_write", config.CONSILIUM_WRITE_RATE_LIMIT, _WINDOW))
_DECISION_LIMIT = Depends(abuse_rate_limit("consilium_decision", config.CONSILIUM_WRITE_RATE_LIMIT, _WINDOW))
_SCHEDULE_LIMIT = Depends(abuse_rate_limit("consilium_schedule_generate", config.CONSILIUM_SCHEDULE_RATE_LIMIT, _WINDOW))
_EXPORT_LIMIT = Depends(abuse_rate_limit("consilium_registry_export", config.CONSILIUM_EXPORT_RATE_LIMIT, _WINDOW))


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")


def _require(conn: sqlite3.Connection, user: policy.CurrentUser, allowed: bool, action: str) -> None:
    if not allowed:
        audit.record(
            conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED,
            detail=f"action={action}", **audit.actor_ctx(user),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _require_manage(conn: sqlite3.Connection, user: policy.CurrentUser) -> None:
    _require(conn, user, policy.can_manage_consilium(user), "consilium_manage")


def _require_decision(conn: sqlite3.Connection, user: policy.CurrentUser) -> None:
    _require(conn, user, policy.can_decide_consilium(user), "consilium_decision")


def _require_favorites(conn: sqlite3.Connection, user: policy.CurrentUser) -> None:
    _require(conn, user, policy.can_use_chair_favorites(user), "consilium_favorites")


def _require_referral_mark(conn: sqlite3.Connection, user: policy.CurrentUser) -> None:
    _require(conn, user, policy.can_mark_referral_completion(user), "consilium_referral_completion")


def _require_access(conn: sqlite3.Connection, user: policy.CurrentUser) -> None:
    _require(conn, user, policy.can_access_consilium(user), "consilium_access")


def _require_booking(conn: sqlite3.Connection, user: policy.CurrentUser) -> None:
    _require(conn, user, policy.can_book_consilium(user), "consilium_booking")


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


def _required_audit(conn: sqlite3.Connection, user: policy.CurrentUser, event_type: str, *, with_round: bool = False):
    """Обязательный аудит решения председателя в транзакции самого перехода.

    Пациент и номер раунда читаются той же транзакцией, что и изменение.
    Ошибка записи аудита откатывает переход (AuditWriteFailed → 503)."""
    def hook(appointment_id: str) -> None:
        row = conn.execute(
            "SELECT patient_id, revision_count FROM oncology_consilium_appointments WHERE id = ?", (appointment_id,)
        ).fetchone()
        audit.record_required_or_fail(
            conn, event_type, patient_id=row["patient_id"],
            detail=f"revision_round={row['revision_count']}" if with_round else None,
            **audit.actor_ctx(user))
    return hook


def _audit_unavailable():
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Не удалось сохранить изменение вместе с записью журнала аудита. Запись консилиума не изменена, повторите попытку позже",
    )


@router.get("/meta")
def get_consilium_meta(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_access(conn, user)
    return consilium.metadata()


@router.get("/appointments")
def list_consilium_appointments(
    archived: bool = Query(False),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_access(conn, user)
    return {"appointments": consilium.list_for_user(conn, user, archived=archived)}


@router.get("/booked-patients")
def list_booked_consilium_patients(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_booking(conn, user)
    return {"patientIds": consilium.booked_patient_ids(conn, user)}


@router.get("/schedule")
def list_consilium_schedule(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_access(conn, user)
    return {"slots": consilium.schedule_slots(conn, user)}


@router.post("/schedule/generate", dependencies=[Depends(require_csrf), _SCHEDULE_LIMIT])
def generate_consilium_schedule(
    body: ConsiliumScheduleGenerateIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_manage(conn, user)
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
    return {"created": created, "slots": consilium.schedule_slots(conn, user)}


@router.delete("/schedule/{slot_id}", dependencies=[Depends(require_csrf), _WRITE_LIMIT])
def delete_consilium_schedule_slot(
    slot_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_manage(conn, user)
    try:
        deleted = consilium.delete_schedule_slot(conn, slot_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        _not_found()
    audit.record(conn, audit.CONSILIUM_SCHEDULE_SLOT_DELETED, **audit.actor_ctx(user))
    return {"deleted": True}


@router.post("/appointments/{appointment_id}/archive", dependencies=[Depends(require_csrf), _WRITE_LIMIT])
def archive_consilium_appointment(
    appointment_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_booking(conn, user)
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


@router.post("/appointments/{appointment_id}/restore", dependencies=[Depends(require_csrf), _WRITE_LIMIT])
def restore_consilium_appointment(
    appointment_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_booking(conn, user)
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
    dependencies=[Depends(require_csrf), _WRITE_LIMIT],
    status_code=status.HTTP_201_CREATED,
)
def create_consilium_appointment(
    body: ConsiliumAppointmentIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_booking(conn, user)
    _require_writable_patient(conn, user, body.patientId)
    try:
        appointment = consilium.create(conn, user, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    audit.record(
        conn, audit.CONSILIUM_APPOINTMENT_CREATED, patient_id=body.patientId,
        **audit.actor_ctx(user),
    )
    return appointment


@router.put("/appointments/{appointment_id}", dependencies=[Depends(require_csrf), _WRITE_LIMIT])
def update_consilium_appointment(
    appointment_id: str,
    body: ConsiliumAppointmentIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_booking(conn, user)
    existing = consilium.get_owned(conn, user, appointment_id)
    if existing is None:
        _not_found()
    _require_writable_patient(conn, user, body.patientId)
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
    return appointment


@router.delete("/appointments/{appointment_id}", dependencies=[Depends(require_csrf), _WRITE_LIMIT])
def delete_consilium_appointment(
    appointment_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_booking(conn, user)
    try:
        appointment = consilium.delete_owned(conn, user, appointment_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if appointment is None:
        _not_found()
    audit.record(
        conn, audit.CONSILIUM_APPOINTMENT_DELETED, patient_id=appointment["patientId"],
        detail="mode=soft;history_preserved=true" if appointment["historyPreserved"] else "mode=hard",
        **audit.actor_ctx(user),
    )
    return {"deleted": True, "historyPreserved": appointment["historyPreserved"], "releasedSlot": {
        "scheduledDate": appointment["scheduledDate"],
        "scheduledTime": appointment["scheduledTime"],
        "consiliumGroup": appointment["consiliumGroup"],
    }}


@router.post("/appointments/{appointment_id}/review", dependencies=[Depends(require_csrf), _DECISION_LIMIT])
def start_consilium_review(
    appointment_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_decision(conn, user)
    try:
        appointment = consilium.start_review(
            conn, appointment_id, audit_hook=_required_audit(conn, user, audit.CONSILIUM_REVIEW_STARTED))
    except consilium.ConsiliumStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except audit.AuditWriteFailed:
        _audit_unavailable()
    if appointment is None:
        _not_found()
    return appointment


@router.post("/appointments/{appointment_id}/sign", dependencies=[Depends(require_csrf), _DECISION_LIMIT])
def sign_consilium(
    appointment_id: str,
    body: ConsiliumDecisionIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_decision(conn, user)
    try:
        appointment = consilium.sign(
            conn, user, appointment_id, body.treatmentType, body.treatmentScheme,
            body.treatmentSchemeCode, body.goc, body.goc65Plus,
            audit_hook=_required_audit(conn, user, audit.CONSILIUM_SIGNED),
        )
    except consilium.ConsiliumStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except audit.AuditWriteFailed:
        _audit_unavailable()
    if appointment is None:
        _not_found()
    return appointment


@router.post("/appointments/{appointment_id}/return", dependencies=[Depends(require_csrf), _DECISION_LIMIT])
def return_consilium_for_revision(
    appointment_id: str,
    body: ConsiliumRevisionIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_decision(conn, user)
    try:
        # Текст комментария в аудит не пишется: он может содержать сведения о пациенте.
        appointment = consilium.return_for_revision(
            conn, user, appointment_id, body.comment,
            audit_hook=_required_audit(conn, user, audit.CONSILIUM_RETURNED, with_round=True))
    except consilium.ConsiliumCommentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except consilium.ConsiliumStateError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except audit.AuditWriteFailed:
        _audit_unavailable()
    if appointment is None:
        _not_found()
    return appointment


@router.get("/appointments/{appointment_id}/revisions")
def consilium_revision_history(
    appointment_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    """Texts of every return round: only for the chair and for the record's
    author while the patient is in their scope; anyone else gets 404, as for
    a record they cannot see. Comments are never written to the audit log."""
    _require_access(conn, user)
    if policy.can_decide_consilium(user):
        if consilium.get_for_management(conn, appointment_id) is None:
            _not_found()
    elif consilium.get_owned(conn, user, appointment_id) is None:
        _not_found()
    return {"revisions": consilium.revision_history(conn, appointment_id)}


@router.get("/analytics")
def consilium_analytics(
    date_from: str | None = Query(None, max_length=10),
    date_to: str | None = Query(None, max_length=10),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_manage(conn, user)
    return consilium.analytics_summary(conn, _validated_date(date_from), _validated_date(date_to))


@router.post("/favorites/{patient_id}", dependencies=[Depends(require_csrf), _WRITE_LIMIT])
def add_consilium_favorite(
    patient_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_favorites(conn, user)
    linked = conn.execute(
        "SELECT 1 FROM oncology_consilium_appointments WHERE patient_id = ? AND deleted_at IS NULL",
        (patient_id,),
    ).fetchone()
    if linked is None or not consilium.add_favorite(conn, user, patient_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    audit.record(
        conn, audit.CONSILIUM_FAVORITE_ADDED, patient_id=patient_id,
        **audit.actor_ctx(user),
    )
    return {"patientId": patient_id, "favorite": True}


@router.delete("/favorites/{patient_id}", dependencies=[Depends(require_csrf), _WRITE_LIMIT])
def remove_consilium_favorite(
    patient_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_favorites(conn, user)
    consilium.remove_favorite(conn, user, patient_id)
    audit.record(
        conn, audit.CONSILIUM_FAVORITE_REMOVED, patient_id=patient_id,
        **audit.actor_ctx(user),
    )
    return {"patientId": patient_id, "favorite": False}


@router.post("/appointments/{appointment_id}/referral-completion", dependencies=[Depends(require_csrf), _DECISION_LIMIT])
def set_surgical_referral_completion(
    appointment_id: str,
    body: ConsiliumRegistryCompletionIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_referral_mark(conn, user)
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
    _require_manage(conn, user)
    selected_date = _validated_date(date)
    return {
        "surgical": consilium.registry_rows(conn, "surgery", selected_date),
        "chemotherapy": consilium.registry_rows(conn, "chemotherapy", selected_date),
        "goc": consilium.goc_registry_rows(conn, "goc", selected_date),
        "goc65Plus": consilium.goc_registry_rows(conn, "goc_65_plus", selected_date),
    }


_TREATMENT_HEADER = [
    "Дата", "Время", "Пациент", "Полис ОМС", "Диагноз",
    "Назначенное лечение", "Схема лечения", "Лечащий врач", "Консилиумная группа",
]


def _registry_xlsx(
    conn: sqlite3.Connection,
    user: policy.CurrentUser,
    *,
    registry: str,
    date: str | None,
    sheet_name: str,
    filename: str,
    header: list[str],
    items: list[dict],
    columns: tuple[str, ...],
) -> Response:
    """Build the workbook and log the export.

    The audit row holds only the registry, the date filter and the row
    count: never names, policy numbers or diagnoses from the file.
    """
    rows: list[list[object]] = [header, *[[item[c] for c in columns] for item in items]]
    content = workbook_bytes(sheet_name, rows)
    suffix = date or "all"
    audit.record(
        conn, audit.CONSILIUM_REGISTRY_EXPORTED,
        detail=f"registry={registry};date={suffix};row_count={len(items)};format=xlsx",
        **audit.actor_ctx(user),
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}_{suffix}.xlsx"'},
    )


# GET с обязательным X-CSRF-Token, как /api/export: файл содержит ПДн, а
# межсайтовая навигация верхнего уровня не может задать заголовок.
@router.get("/registries/chemotherapy.xlsx", dependencies=[Depends(require_csrf), _EXPORT_LIMIT])
def export_chemotherapy_registry(
    date: str | None = Query(None, max_length=10),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_manage(conn, user)
    selected_date = _validated_date(date)
    return _registry_xlsx(
        conn, user, registry="chemotherapy", date=selected_date,
        sheet_name="Химиотерапия", filename="chemotherapy_registry",
        header=[
            "Дата", "Время", "Пациент", "Полис ОМС", "Диагноз",
            "Код схемы", "Схема лечения", "Лечащий врач", "Консилиумная группа",
        ],
        items=consilium.registry_rows(conn, "chemotherapy", selected_date),
        columns=(
            "scheduledDate", "scheduledTime", "patientName", "policyNumber", "primaryDiagnosis",
            "treatmentSchemeCode", "treatmentScheme", "treatingDoctorName", "consiliumGroupLabel",
        ),
    )


_GOC_COLUMNS = (
    "scheduledDate", "scheduledTime", "patientName", "policyNumber", "primaryDiagnosis",
    "treatmentTypeLabel", "treatmentScheme", "treatingDoctorName", "consiliumGroupLabel",
)


@router.get("/registries/goc.xlsx", dependencies=[Depends(require_csrf), _EXPORT_LIMIT])
def export_goc_registry(
    date: str | None = Query(None, max_length=10),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_manage(conn, user)
    selected_date = _validated_date(date)
    return _registry_xlsx(
        conn, user, registry="goc", date=selected_date,
        sheet_name="ГОК", filename="goc_registry", header=_TREATMENT_HEADER,
        items=consilium.goc_registry_rows(conn, "goc", selected_date), columns=_GOC_COLUMNS,
    )


@router.get("/registries/goc-65-plus.xlsx", dependencies=[Depends(require_csrf), _EXPORT_LIMIT])
def export_goc_65_plus_registry(
    date: str | None = Query(None, max_length=10),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_manage(conn, user)
    selected_date = _validated_date(date)
    return _registry_xlsx(
        conn, user, registry="goc_65_plus", date=selected_date,
        sheet_name="ГОК 65+", filename="goc_65_plus_registry", header=_TREATMENT_HEADER,
        items=consilium.goc_registry_rows(conn, "goc_65_plus", selected_date), columns=_GOC_COLUMNS,
    )
