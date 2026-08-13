"""Repository layer: SQLite rows <-> plain dicts matching the frontend's
patient shape. All mutation here is server-authoritative — ids and
timestamps are generated here, never accepted from callers.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Optional

from .db import now_iso
from .models import (
    DISEASE_STAGES,
    PRIORITIES,
    EventIn,
    PatientCreateIn,
    normalize_policy_number,
)

FIELD_COLUMN = {
    "name": "name",
    "age": "age",
    "phone": "phone",
    "policyNumber": "policy_number",
    "diagnosis": "diagnosis",
    "nosology": "nosology",
    "diseaseStage": "disease_stage",
    "priority": "priority",
    "doctor": "doctor",
    "head": "head",
    "complaint": "complaint",
}
FIELD_MAX_LEN = {
    "name": 200,
    "phone": 50,
    "policyNumber": 64,
    "diagnosis": 500,
    "nosology": 100,
    "doctor": 200,
    "head": 200,
    "complaint": 5000,
}


def new_id() -> str:
    return uuid.uuid4().hex


def _event_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "date": row["date"],
        "time": row["time"],
        "treatmentType": row["treatment_type"],
        "actualDate": row["actual_date"],
        "actualTime": row["actual_time"],
        "matchedPlan": None if row["matched_plan"] is None else bool(row["matched_plan"]),
        "noPlan": bool(row["no_plan"]),
        "setAt": row["set_at"],
    }


def insert_event(conn: sqlite3.Connection, history_entry_id: str, seq: int, ev: EventIn) -> None:
    conn.execute(
        """INSERT INTO events (id, history_entry_id, seq, date, time, treatment_type,
                                actual_date, actual_time, matched_plan, no_plan, set_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id(),
            history_entry_id,
            seq,
            ev.date or None,
            ev.time or None,
            ev.treatmentType or None,
            ev.actualDate or None,
            ev.actualTime or None,
            None if ev.matchedPlan is None else int(ev.matchedPlan),
            int(bool(ev.noPlan)),
            now_iso() if (ev.actualDate or ev.noPlan) else None,
        ),
    )


def patient_to_dict(conn: sqlite3.Connection, patient_id: str) -> Optional[dict]:
    from .services import patient_attention

    p = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if p is None:
        return None
    history_rows = conn.execute(
        "SELECT * FROM history_entries WHERE patient_id = ? ORDER BY seq ASC", (patient_id,)
    ).fetchall()
    history = []
    for h in history_rows:
        events = conn.execute(
            "SELECT * FROM events WHERE history_entry_id = ? ORDER BY seq ASC", (h["id"],)
        ).fetchall()
        history.append(
            {
                "stage": h["stage"],
                "enteredAt": h["entered_at"],
                "events": [_event_row_to_dict(e) for e in events],
                "checklist": json.loads(h["checklist_json"]) if h["checklist_json"] else {},
                "note": h["note"] or "",
            }
        )
    fp_rows = conn.execute(
        "SELECT * FROM future_plans WHERE patient_id = ? ORDER BY template_index ASC", (patient_id,)
    ).fetchall()
    future_plans = {
        str(r["template_index"]): {
            "date": r["date"] or "",
            "time": r["time"] or "",
            "treatmentType": r["treatment_type"] or "",
        }
        for r in fp_rows
    }
    return {
        "id": p["id"],
        "name": p["name"],
        "age": p["age"] or 0,
        "priority": p["priority"],
        "stage": p["stage"],
        "phone": p["phone"] or "",
        "policyNumber": p["policy_number"] or "",
        "complaint": p["complaint"] or "",
        "diagnosis": p["diagnosis"] or "",
        "nosology": p["nosology"] or "",
        "diseaseStage": p["disease_stage"] or "",
        "arrival": p["arrival"],
        "doctor": p["doctor"] or "",
        "head": p["head"] or "",
        "updatedAt": p["updated_at"],
        "history": history,
        "futurePlans": future_plans,
        "treatingDoctorUserId": p["treating_doctor_user_id"],
        "treatingDoctorName": _treating_doctor_name(conn, p["treating_doctor_user_id"]),
        "attention": patient_attention.summary_for_patient(conn, patient_id),
    }


def _treating_doctor_name(conn: sqlite3.Connection, user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    row = conn.execute("SELECT full_name FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["full_name"] if row else None


def list_patients(
    conn: sqlite3.Connection,
    scope: Optional[tuple[str, list]] = None,
    *,
    attention: str | None = None,
    attention_reason: str | None = None,
) -> list[dict]:
    """Список пациентов; scope — SQL-условие из policy.patient_scope_filter.
    Одна и та же фильтрация обслуживает список, поиск, статистику, календарь
    и экспорт — обхода через другой endpoint нет."""
    cond, params = scope if scope is not None else ("1 = 1", [])
    ids = [
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM patients WHERE {cond} ORDER BY arrival ASC", params  # nosec B608 - cond is policy scope.
        ).fetchall()
    ]
    from .services import patient_attention as attention_service

    summary = attention_service.bulk_summary(conn, ids)
    if attention in {"active", "none"} or attention_reason:
        active_ids = {pid for pid, item in summary.items() if item["active"]}
        if attention_reason:
            active_ids = attention_service.active_patient_ids(conn, set(ids), attention_reason)
        if attention == "none":
            allowed_ids = set(ids) - active_ids
        else:
            allowed_ids = active_ids
        ids = [pid for pid in ids if pid in allowed_ids]
    patients = []
    for pid in ids:
        item = patient_to_dict(conn, pid)
        if item is not None:
            item["attention"] = summary.get(pid, {
                "active": False,
                "activeCount": 0,
                "primaryReasonCode": None,
                "primaryReasonLabel": "",
            })
            patients.append(item)
    return patients


def create_patient(
    conn: sqlite3.Connection, data: PatientCreateIn, treating_doctor_user_id: Optional[str] = None
) -> str:
    pid = new_id()
    now = now_iso()
    # Отображаемый текст врача следует за учётной записью; свободный текст
    # остаётся только для legacy-записей без сопоставления.
    doctor_text = _treating_doctor_name(conn, treating_doctor_user_id) or data.doctor
    with conn:
        conn.execute(
            """INSERT INTO patients (id, name, age, priority, stage, phone, policy_number, complaint, diagnosis,
                                      nosology, disease_stage, arrival, doctor, head, created_at,
                                      updated_at, treating_doctor_user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pid,
                data.name,
                data.age,
                data.priority,
                data.stage,
                data.phone,
                data.policyNumber,
                data.complaint,
                data.diagnosis,
                data.nosology,
                data.diseaseStage,
                now,
                doctor_text,
                data.head,
                now,
                now,
                treating_doctor_user_id,
            ),
        )
        entry_id = new_id()
        conn.execute(
            "INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, checklist_json, note) "
            "VALUES (?, ?, 0, ?, ?, NULL, '')",
            (entry_id, pid, data.stage, now),
        )
        for seq, ev in enumerate(data.events):
            insert_event(conn, entry_id, seq, ev)
    return pid


def set_treating_doctor(
    conn: sqlite3.Connection, patient_id: str, doctor_user_id: Optional[str]
) -> bool:
    """Смена лечащего врача: обновляет стабильную связь и отображаемый текст.
    Пациент не копируется — меняется только закрепление; доступ прежнего
    врача и его заведующего исчезает при следующем запросе."""
    p = conn.execute("SELECT id FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if p is None:
        return False
    doctor_text = _treating_doctor_name(conn, doctor_user_id)
    with conn:
        if doctor_text is not None:
            conn.execute(
                "UPDATE patients SET treating_doctor_user_id = ?, doctor = ?, updated_at = ? WHERE id = ?",
                (doctor_user_id, doctor_text, now_iso(), patient_id),
            )
        else:
            # Сопоставление снято: legacy-текст сохраняем как был.
            conn.execute(
                "UPDATE patients SET treating_doctor_user_id = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), patient_id),
            )
    return True


def unresolved_patients(conn: sqlite3.Connection) -> list[dict]:
    """Пациенты без сопоставленной учётной записи лечащего врача — очередь
    ручного сопоставления для Разработчика."""
    rows = conn.execute(
        """SELECT id, name, doctor FROM patients
           WHERE treating_doctor_user_id IS NULL ORDER BY name"""
    ).fetchall()
    return [
        {"id": r["id"], "name": r["name"], "legacyDoctor": r["doctor"] or ""}
        for r in rows
    ]


def touch_patient(conn: sqlite3.Connection, patient_id: str) -> None:
    conn.execute("UPDATE patients SET updated_at = ? WHERE id = ?", (now_iso(), patient_id))


def delete_patient(conn: sqlite3.Connection, patient_id: str) -> bool:
    with conn:
        cur = conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
        return cur.rowcount > 0


def update_patient_field(conn: sqlite3.Connection, patient_id: str, field: str, value) -> bool:
    if field not in FIELD_COLUMN:
        raise ValueError("unknown field")
    column = FIELD_COLUMN[field]

    if field == "age":
        try:
            value = max(0, min(150, int(value)))
        except (TypeError, ValueError):
            raise ValueError("age must be an integer")
    elif field == "priority":
        if value not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")
    elif field == "diseaseStage":
        if value not in DISEASE_STAGES:
            raise ValueError(f"diseaseStage must be one of {DISEASE_STAGES}")
    elif field == "policyNumber":
        value = normalize_policy_number(value)
    else:
        value = "" if value is None else str(value)
        limit = FIELD_MAX_LEN.get(field)
        if limit and len(value) > limit:
            raise ValueError(f"{field} exceeds max length {limit}")

    with conn:
        cur = conn.execute(
            f"UPDATE patients SET {column} = ?, updated_at = ? WHERE id = ?",  # nosec B608
            (value, now_iso(), patient_id),
        )
        return cur.rowcount > 0


def _history_entry_id(conn: sqlite3.Connection, patient_id: str, occ_index: int) -> Optional[str]:
    row = conn.execute(
        "SELECT id FROM history_entries WHERE patient_id = ? ORDER BY seq ASC LIMIT 1 OFFSET ?",
        (patient_id, occ_index),
    ).fetchone()
    return row["id"] if row else None


def _event_id(conn: sqlite3.Connection, history_entry_id: str, event_index: int) -> Optional[str]:
    row = conn.execute(
        "SELECT id FROM events WHERE history_entry_id = ? ORDER BY seq ASC LIMIT 1 OFFSET ?",
        (history_entry_id, event_index),
    ).fetchone()
    return row["id"] if row else None


def change_stage(conn: sqlite3.Connection, patient_id: str, new_stage: str, last_event_update: Optional[EventIn]) -> bool:
    p = conn.execute("SELECT stage FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if p is None:
        return False
    if p["stage"] == new_stage:
        return False
    with conn:
        history_count = _count_history(conn, patient_id)
        current_entry_id = _history_entry_id(conn, patient_id, history_count - 1) if history_count > 0 else None
        if current_entry_id and last_event_update is not None:
            events = conn.execute(
                "SELECT id FROM events WHERE history_entry_id = ? ORDER BY seq ASC", (current_entry_id,)
            ).fetchall()
            if events:
                last_event_id = events[-1]["id"]
                conn.execute(
                    """UPDATE events SET actual_date = ?, actual_time = ?, matched_plan = ?, set_at = ?
                       WHERE id = ?""",
                    (
                        last_event_update.actualDate or None,
                        last_event_update.actualTime or "",
                        None if last_event_update.matchedPlan is None else int(last_event_update.matchedPlan),
                        now_iso(),
                        last_event_id,
                    ),
                )
            else:
                next_seq = 0
                insert_event(conn, current_entry_id, next_seq, last_event_update)

        now = now_iso()
        next_seq = _count_history(conn, patient_id)
        conn.execute(
            "INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, checklist_json, note) "
            "VALUES (?, ?, ?, ?, ?, NULL, '')",
            (new_id(), patient_id, next_seq, new_stage, now),
        )
        conn.execute("UPDATE patients SET stage = ?, updated_at = ? WHERE id = ?", (new_stage, now, patient_id))
    return True


def _count_history(conn: sqlite3.Connection, patient_id: str) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM history_entries WHERE patient_id = ?", (patient_id,)).fetchone()
    return row["c"]


def add_event(conn: sqlite3.Connection, patient_id: str, occ_index: int) -> bool:
    entry_id = _history_entry_id(conn, patient_id, occ_index)
    if entry_id is None:
        return False
    with conn:
        next_seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 AS s FROM events WHERE history_entry_id = ?", (entry_id,)
        ).fetchone()
        insert_event(conn, entry_id, next_seq_row["s"], EventIn())
        touch_patient(conn, patient_id)
    return True


def remove_event(conn: sqlite3.Connection, patient_id: str, occ_index: int, event_index: int) -> bool:
    entry_id = _history_entry_id(conn, patient_id, occ_index)
    if entry_id is None:
        return False
    event_id = _event_id(conn, entry_id, event_index)
    if event_id is None:
        return False
    with conn:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        touch_patient(conn, patient_id)
    return True


EVENT_FIELD_COLUMN = {
    "date": "date",
    "time": "time",
    "treatmentType": "treatment_type",
    "actualDate": "actual_date",
    "actualTime": "actual_time",
}


def update_event(conn: sqlite3.Connection, patient_id: str, occ_index: int, event_index: int, field: str, value: str) -> bool:
    if field not in EVENT_FIELD_COLUMN:
        raise ValueError("unknown event field")
    entry_id = _history_entry_id(conn, patient_id, occ_index)
    if entry_id is None:
        return False
    event_id = _event_id(conn, entry_id, event_index)
    if event_id is None:
        if event_index != 0:
            return False
        # Lazily materialize the first event of an occurrence that was
        # created without one (e.g. a primary-appointment stage entered
        # before its actual date/time is known) — the frontend renders an
        # empty placeholder for event 0 in this case; the first edit to it
        # is what actually creates the row.
        with conn:
            insert_event(conn, entry_id, 0, EventIn())
        event_id = _event_id(conn, entry_id, 0)
    value = "" if value is None else str(value)
    if len(value) > 20:
        raise ValueError("value too long")
    column = EVENT_FIELD_COLUMN[field]
    with conn:
        conn.execute(
            f"UPDATE events SET {column} = ? WHERE id = ?",  # nosec B608
            (value or None, event_id),
        )
        if field in ("actualDate", "actualTime"):
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if not row["no_plan"]:
                matched = (row["actual_date"] == row["date"]) and ((row["actual_time"] or "") == (row["time"] or ""))
                conn.execute("UPDATE events SET matched_plan = ? WHERE id = ?", (int(matched), event_id))
        touch_patient(conn, patient_id)
    return True


def update_note(conn: sqlite3.Connection, patient_id: str, occ_index: int, note: str) -> bool:
    entry_id = _history_entry_id(conn, patient_id, occ_index)
    if entry_id is None:
        return False
    with conn:
        conn.execute("UPDATE history_entries SET note = ? WHERE id = ?", (note, entry_id))
        touch_patient(conn, patient_id)
    return True


def update_checklist_item(conn: sqlite3.Connection, patient_id: str, occ_index: int, item: str, checked: bool) -> bool:
    entry_id = _history_entry_id(conn, patient_id, occ_index)
    if entry_id is None:
        return False
    row = conn.execute("SELECT checklist_json FROM history_entries WHERE id = ?", (entry_id,)).fetchone()
    checklist = json.loads(row["checklist_json"]) if row["checklist_json"] else {}
    if len(checklist) > 100 and item not in checklist:
        raise ValueError("too many checklist items")
    checklist[item] = bool(checked)
    with conn:
        conn.execute("UPDATE history_entries SET checklist_json = ? WHERE id = ?", (json.dumps(checklist), entry_id))
        touch_patient(conn, patient_id)
    return True


def update_future_plan(conn: sqlite3.Connection, patient_id: str, template_index: int, field: str, value: str) -> bool:
    if field not in ("date", "time", "treatmentType"):
        raise ValueError("unknown future plan field")
    p = conn.execute("SELECT id FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if p is None:
        return False
    column = {"date": "date", "time": "time", "treatmentType": "treatment_type"}[field]
    with conn:
        existing = conn.execute(
            "SELECT 1 FROM future_plans WHERE patient_id = ? AND template_index = ?", (patient_id, template_index)
        ).fetchone()
        if existing:
            conn.execute(
                f"UPDATE future_plans SET {column} = ? WHERE patient_id = ? AND template_index = ?",  # nosec B608
                (value, patient_id, template_index),
            )
        else:
            values = {"date": "", "time": "", "treatment_type": ""}
            values[column] = value
            conn.execute(
                "INSERT INTO future_plans (patient_id, template_index, date, time, treatment_type) "
                "VALUES (?, ?, ?, ?, ?)",
                (patient_id, template_index, values["date"], values["time"], values["treatment_type"]),
            )
        touch_patient(conn, patient_id)
    return True
