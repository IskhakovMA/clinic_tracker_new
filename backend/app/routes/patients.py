import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from .. import store
from ..deps import get_db, require_csrf, require_session
from ..models import (
    ChecklistItemIn,
    EventFieldUpdateIn,
    FuturePlanIn,
    NoteIn,
    PatientCreateIn,
    PatientFieldUpdateIn,
    StageChangeIn,
)

router = APIRouter(prefix="/api/patients", tags=["patients"], dependencies=[Depends(require_session)])


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")


@router.get("")
def list_patients(conn: sqlite3.Connection = Depends(get_db)):
    return store.list_patients(conn)


@router.post("", dependencies=[Depends(require_csrf)], status_code=status.HTTP_201_CREATED)
def create_patient(body: PatientCreateIn, conn: sqlite3.Connection = Depends(get_db)):
    pid = store.create_patient(conn, body)
    return store.patient_to_dict(conn, pid)


@router.get("/{patient_id}")
def get_patient(patient_id: str, conn: sqlite3.Connection = Depends(get_db)):
    p = store.patient_to_dict(conn, patient_id)
    if p is None:
        _not_found()
    return p


@router.patch("/{patient_id}", dependencies=[Depends(require_csrf)])
def update_patient_field(patient_id: str, body: PatientFieldUpdateIn, conn: sqlite3.Connection = Depends(get_db)):
    try:
        ok = store.update_patient_field(conn, patient_id, body.field, body.value)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.delete("/{patient_id}", dependencies=[Depends(require_csrf)])
def delete_patient(patient_id: str, conn: sqlite3.Connection = Depends(get_db)):
    if not store.delete_patient(conn, patient_id):
        _not_found()
    return {"deleted": True}


@router.post("/{patient_id}/stage", dependencies=[Depends(require_csrf)])
def change_stage(patient_id: str, body: StageChangeIn, conn: sqlite3.Connection = Depends(get_db)):
    ok = store.change_stage(conn, patient_id, body.newStage, body.lastEventUpdate)
    if not ok:
        p = store.patient_to_dict(conn, patient_id)
        if p is None:
            _not_found()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Stage unchanged")
    return store.patient_to_dict(conn, patient_id)


@router.patch("/{patient_id}/history/{occ_index}/note", dependencies=[Depends(require_csrf)])
def update_note(patient_id: str, occ_index: int, body: NoteIn, conn: sqlite3.Connection = Depends(get_db)):
    if not store.update_note(conn, patient_id, occ_index, body.note):
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.patch("/{patient_id}/history/{occ_index}/checklist", dependencies=[Depends(require_csrf)])
def update_checklist(patient_id: str, occ_index: int, body: ChecklistItemIn, conn: sqlite3.Connection = Depends(get_db)):
    try:
        ok = store.update_checklist_item(conn, patient_id, occ_index, body.item, body.checked)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.post("/{patient_id}/history/{occ_index}/events", dependencies=[Depends(require_csrf)])
def add_event(patient_id: str, occ_index: int, conn: sqlite3.Connection = Depends(get_db)):
    if not store.add_event(conn, patient_id, occ_index):
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.delete("/{patient_id}/history/{occ_index}/events/{event_index}", dependencies=[Depends(require_csrf)])
def remove_event(patient_id: str, occ_index: int, event_index: int, conn: sqlite3.Connection = Depends(get_db)):
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
):
    try:
        ok = store.update_event(conn, patient_id, occ_index, event_index, body.field, body.value)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.put("/{patient_id}/future-plans/{template_index}", dependencies=[Depends(require_csrf)])
def update_future_plan(
    patient_id: str, template_index: int, body: FuturePlanIn, conn: sqlite3.Connection = Depends(get_db)
):
    try:
        ok = store.update_future_plan(conn, patient_id, template_index, body.field, body.value)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
        _not_found()
    return store.patient_to_dict(conn, patient_id)
