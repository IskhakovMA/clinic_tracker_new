"""Пациенты. Каждый endpoint работает через централизованную политику:
список фильтруется patient_scope_filter, объектные операции проходят
can_read/can_write; недоступный пациент неотличим от несуществующего (404).
"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, policy, store
from ..deps import get_db, require_active_password, require_csrf
from ..models import (
    ChecklistItemIn,
    EventFieldUpdateIn,
    FuturePlanIn,
    NoteIn,
    PatientCreateIn,
    PatientFieldUpdateIn,
    StageChangeIn,
    TreatingDoctorIn,
)

router = APIRouter(prefix="/api/patients", tags=["patients"])


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")


def _forbidden(conn: sqlite3.Connection, user: policy.CurrentUser, detail: str):
    """Отказ по правам для видимого объекта/действия. Фиксируется в аудите
    без каких-либо медицинских данных."""
    audit.record(conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED,
                 detail=detail, **audit.actor_ctx(user))
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _require_readable(conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str) -> None:
    if not policy.can_read_patient(conn, user, patient_id):
        _not_found()


def _require_writable(conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str) -> None:
    # Сначала видимость (иначе 404 — существование не раскрывается),
    # затем право записи (read-only роли получают 403).
    _require_readable(conn, user, patient_id)
    if not policy.can_write_patient(conn, user, patient_id):
        _forbidden(conn, user, "action=patient_write")


@router.get("")
def list_patients(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    return store.list_patients(conn, policy.patient_scope_filter(user))


@router.get("/unresolved")
def list_unresolved(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    """Очередь ручного сопоставления legacy-врачей — только Разработчику."""
    if not policy.can_change_treating_doctor(user):
        _forbidden(conn, user, "area=unresolved")
    return {"patients": store.unresolved_patients(conn)}


@router.post("", dependencies=[Depends(require_csrf)], status_code=status.HTTP_201_CREATED)
def create_patient(
    body: PatientCreateIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if not policy.can_create_patient(user):
        _forbidden(conn, user, "action=patient_create")
    doctor_user_id, error = policy.resolve_create_doctor(conn, user, body.treatingDoctorUserId)
    if error is not None:
        audit.record(conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED,
                     detail="action=patient_create_doctor", **audit.actor_ctx(user))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=error)
    pid = store.create_patient(conn, body, doctor_user_id)
    audit.record(conn, audit.PATIENT_CREATED, patient_id=pid,
                 target_user_id=doctor_user_id, **audit.actor_ctx(user))
    return store.patient_to_dict(conn, pid)


@router.get("/{patient_id}")
def get_patient(
    patient_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_readable(conn, user, patient_id)
    p = store.patient_to_dict(conn, patient_id)
    if p is None:
        _not_found()
    return p


@router.patch("/{patient_id}", dependencies=[Depends(require_csrf)])
def update_patient_field(
    patient_id: str,
    body: PatientFieldUpdateIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    if body.field == "doctor" and not policy.can_change_treating_doctor(user):
        # Legacy-текст врача участвует в отображении ответственных; менять его
        # (как и само закрепление) может только Разработчик.
        _forbidden(conn, user, "action=patient_doctor_text")
    try:
        ok = store.update_patient_field(conn, patient_id, body.field, body.value)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
        _not_found()
    # Field *name* only — never the value, which is patient data.
    audit.record(conn, audit.PATIENT_UPDATED, patient_id=patient_id,
                 detail=f"field={body.field}", **audit.actor_ctx(user))
    return store.patient_to_dict(conn, patient_id)


@router.put("/{patient_id}/treating-doctor", dependencies=[Depends(require_csrf)])
def change_treating_doctor(
    patient_id: str,
    body: TreatingDoctorIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    """Смена лечащего врача — только Разработчик. Прежний врач и его
    заведующий теряют доступ немедленно (scope вычисляется на каждый запрос)."""
    if not policy.can_change_treating_doctor(user):
        _require_readable(conn, user, patient_id)  # вне scope — 404, не 403
        _forbidden(conn, user, "action=change_treating_doctor")
    if body.userId is not None:
        doctor_user_id, error = policy.resolve_create_doctor(conn, user, body.userId)
        if error is not None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=error)
    else:
        doctor_user_id = None
    if not store.set_treating_doctor(conn, patient_id, doctor_user_id):
        _not_found()
    audit.record(conn, audit.TREATING_DOCTOR_CHANGED, patient_id=patient_id,
                 target_user_id=doctor_user_id, **audit.actor_ctx(user))
    return store.patient_to_dict(conn, patient_id)


@router.delete("/{patient_id}", dependencies=[Depends(require_csrf)])
def delete_patient(
    patient_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    if not store.delete_patient(conn, patient_id):
        _not_found()
    audit.record(conn, audit.PATIENT_DELETED, patient_id=patient_id, **audit.actor_ctx(user))
    return {"deleted": True}


@router.post("/{patient_id}/stage", dependencies=[Depends(require_csrf)])
def change_stage(
    patient_id: str,
    body: StageChangeIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    ok = store.change_stage(conn, patient_id, body.newStage, body.lastEventUpdate)
    if not ok:
        p = store.patient_to_dict(conn, patient_id)
        if p is None:
            _not_found()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Stage unchanged")
    audit.record(conn, audit.PATIENT_UPDATED, patient_id=patient_id,
                 detail=f"stage={body.newStage}", **audit.actor_ctx(user))
    return store.patient_to_dict(conn, patient_id)


@router.patch("/{patient_id}/history/{occ_index}/note", dependencies=[Depends(require_csrf)])
def update_note(
    patient_id: str,
    occ_index: int,
    body: NoteIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    if not store.update_note(conn, patient_id, occ_index, body.note):
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.patch("/{patient_id}/history/{occ_index}/checklist", dependencies=[Depends(require_csrf)])
def update_checklist(
    patient_id: str,
    occ_index: int,
    body: ChecklistItemIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    try:
        ok = store.update_checklist_item(conn, patient_id, occ_index, body.item, body.checked)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.post("/{patient_id}/history/{occ_index}/events", dependencies=[Depends(require_csrf)])
def add_event(
    patient_id: str,
    occ_index: int,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    if not store.add_event(conn, patient_id, occ_index):
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.delete("/{patient_id}/history/{occ_index}/events/{event_index}", dependencies=[Depends(require_csrf)])
def remove_event(
    patient_id: str,
    occ_index: int,
    event_index: int,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    if not store.remove_event(conn, patient_id, occ_index, event_index):
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.patch("/{patient_id}/history/{occ_index}/events/{event_index}", dependencies=[Depends(require_csrf)])
def update_event(
    patient_id: str,
    occ_index: int,
    event_index: int,
    body: EventFieldUpdateIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    try:
        ok = store.update_event(conn, patient_id, occ_index, event_index, body.field, body.value)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.put("/{patient_id}/future-plans/{template_index}", dependencies=[Depends(require_csrf)])
def update_future_plan(
    patient_id: str,
    template_index: int,
    body: FuturePlanIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    try:
        ok = store.update_future_plan(conn, patient_id, template_index, body.field, body.value)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
        _not_found()
    return store.patient_to_dict(conn, patient_id)
