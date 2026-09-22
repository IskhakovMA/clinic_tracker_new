"""Пациенты. Каждый endpoint работает через централизованную политику:
список фильтруется patient_scope_filter, объектные операции проходят
can_read/can_write; недоступный пациент неотличим от несуществующего (404).
"""
import sqlite3

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, policy, store
from ..deps import get_db, require_active_password, require_csrf
from ..models import (
    ChecklistItemIn,
    EventFieldUpdateIn,
    AttentionIssueCloseIn,
    AttentionIssueCreateIn,
    NoteIn,
    PatientCreateIn,
    PatientFieldUpdateIn,
    StageChangeIn,
    TreatingDoctorIn,
)
from ..services import patient_attention

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
    attention: Literal["active", "none"] | None = None,
    attention_reason: str | None = Query(None, max_length=80),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if attention_reason and attention_reason not in patient_attention.REASON_LABELS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown attention reason")
    return store.list_patients(
        conn,
        policy.patient_scope_filter(user),
        attention=attention,
        attention_reason=attention_reason,
    )


@router.get("/attention/reasons")
def attention_reasons(
    user: policy.CurrentUser = Depends(require_active_password),
):
    return {"reasons": patient_attention.reason_options(), "commentMaxLength": patient_attention.comment_max_length()}


@router.get("/unresolved")
def list_unresolved(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    """Очередь ручного сопоставления legacy-врачей. Просмотр — Разработчик
    и глобальные read-only роли; само сопоставление — только Разработчик
    (PUT /{id}/treating-doctor)."""
    if not policy.can_view_unresolved(user):
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
    try:
        pid = store.create_patient(conn, body, doctor_user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
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


@router.get("/{patient_id}/attention")
def get_patient_attention(
    patient_id: str,
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    result = patient_attention.list_for_patient(conn, user, patient_id, limit=limit, offset=offset)
    if not result.get("found"):
        _not_found()
    return result


@router.post("/{patient_id}/attention", dependencies=[Depends(require_csrf)], status_code=status.HTTP_201_CREATED)
def create_attention_issue(
    patient_id: str,
    body: AttentionIssueCreateIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    try:
        issue = patient_attention.create_issue(conn, user, patient_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if issue is None:
        _not_found()
    return {"issue": issue, "attention": patient_attention.list_for_patient(conn, user, patient_id)}


@router.post("/{patient_id}/attention/{issue_id}/close", dependencies=[Depends(require_csrf)])
def close_attention_issue(
    patient_id: str,
    issue_id: str,
    body: AttentionIssueCloseIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    try:
        issue = patient_attention.close_issue(conn, user, patient_id, issue_id, body)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if issue is None:
        _not_found()
    return {"issue": issue, "attention": patient_attention.list_for_patient(conn, user, patient_id)}


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
        ok = store.update_patient_field_with_actor(conn, patient_id, body.field, body.value, actor_user_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
        _not_found()
    # Field *name* only — never the value, which is patient data. Diagnosis
    # and physician-requested structured values get explicit safe event types
    # without detail.
    if body.field == "diagnosis":
        audit.record(conn, audit.PATIENT_DIAGNOSIS_CHANGED, patient_id=patient_id, **audit.actor_ctx(user))
    elif body.field in {"pgiResult", "pgiResultDate"}:
        audit.record(conn, audit.PGI_RESULT_CHANGED, patient_id=patient_id, **audit.actor_ctx(user))
    else:
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
    try:
        ok = store.change_stage(conn, patient_id, body.newStage, body.lastEventUpdate)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
        p = store.patient_to_dict(conn, patient_id)
        if p is None:
            _not_found()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Stage unchanged")
    audit.record(conn, audit.PATIENT_UPDATED, patient_id=patient_id,
                 detail=f"stage={body.newStage}", **audit.actor_ctx(user))
    if body.lastEventUpdate and body.lastEventUpdate.consiliumDecision:
        audit.record(conn, audit.CONSILIUM_DECISION_RECORDED, patient_id=patient_id, **audit.actor_ctx(user))
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
    try:
        ok = store.add_event(conn, patient_id, occ_index)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
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
