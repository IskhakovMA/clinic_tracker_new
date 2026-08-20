"""Import/export business logic: strict validated pipeline (preview -> commit).

Import never trusts client-sent ids to assign new identity; every commit
runs inside a single transaction so a mid-way failure leaves the database
exactly as it was before the import started.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from pydantic import ValidationError

from . import config, policy, store, users
from .db import now_iso, transaction
from .models import ImportDocument, ImportHistoryEntry, ImportPatientIn


class ImportStructureError(ValueError):
    """Raised for problems with the overall document shape (not yet a
    per-record validation error)."""


def parse_import_structure(raw: str) -> dict:
    raw_bytes = raw.encode("utf-8")
    if len(raw_bytes) > config.MAX_IMPORT_FILE_BYTES:
        raise ImportStructureError(f"File exceeds max size of {config.MAX_IMPORT_FILE_BYTES} bytes")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ImportStructureError("File is corrupted or not valid JSON")

    if isinstance(parsed, list):
        doc = {"schemaVersion": 0, "patients": parsed}
    elif isinstance(parsed, dict):
        if not isinstance(parsed.get("patients"), list):
            raise ImportStructureError("Expected a 'patients' array at the document root")
        doc = parsed
    else:
        raise ImportStructureError("Unrecognized root structure: expected an array or an object with 'patients'")

    schema_version = doc.get("schemaVersion", 0)
    if schema_version not in config.IMPORT_ACCEPTED_SCHEMA_VERSIONS:
        raise ImportStructureError(f"Unsupported schema version: {schema_version}")

    if len(doc["patients"]) > config.MAX_IMPORT_PATIENTS:
        raise ImportStructureError(f"Too many patients in import (max {config.MAX_IMPORT_PATIENTS})")

    return doc


def validate_import(raw: str) -> tuple[Optional[ImportDocument], list[dict]]:
    """Returns (document, errors). document is None when validation failed —
    callers should treat any non-empty errors list as a hard rejection of
    the whole file (no partial import of the valid subset)."""
    doc_dict = parse_import_structure(raw)  # raises ImportStructureError; caller maps to 400
    try:
        document = ImportDocument.model_validate(doc_dict)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = err["loc"]
            index = loc[1] if len(loc) > 1 and isinstance(loc[1], int) else None
            field = ".".join(str(x) for x in loc[2:]) if len(loc) > 2 else (str(loc[-1]) if loc else "")
            errors.append({"index": index, "field": field, "message": err["msg"]})
        return None, errors[:200]

    errors = []
    seen_ids: dict[str, int] = {}
    for i, p in enumerate(document.patients):
        if p.id:
            if p.id in seen_ids:
                errors.append(
                    {"index": i, "field": "id", "message": f"Duplicate id (also used at index {seen_ids[p.id]})"}
                )
            else:
                seen_ids[p.id] = i
    if errors:
        return None, errors
    return document, []


def resolve_import_doctor(
    conn: sqlite3.Connection, p: ImportPatientIn, actor: "policy.CurrentUser"
) -> tuple[Optional[str], Optional[str]]:
    """(doctor_user_id, error) для импортируемой записи с учётом scope актора.

    ID врача из файла никогда не принимается на веру: он проверяется по БД и
    по области видимости актора. Текстовые ФИО не сопоставляются автоматически —
    запись без валидного ID у Разработчика остаётся unresolved, у остальных
    отклоняется."""
    requested = p.treatingDoctorUserId
    if actor.scope_strategy == policy.SCOPE_BY_TREATING_DOCTOR:
        # Врач импортирует только с закреплением за собой.
        if requested and requested != actor.id:
            return None, "Врач может импортировать пациентов только с закреплением за собой."
        return actor.id, None
    if actor.scope_strategy == policy.SCOPE_BY_DEPARTMENT:
        if not requested:
            return None, "Каждой записи нужен лечащий врач из вашего отделения (treatingDoctorUserId)."
        if not users.doctor_in_departments(conn, requested, list(actor.department_ids)):
            return None, "Лечащий врач записи не относится к отделениям, которыми вы управляете."
        return requested, None
    if actor.scope_strategy == policy.SCOPE_GLOBAL:
        if not requested:
            return None, None  # unresolved — очередь ручного сопоставления
        doctor = users.get_user(conn, requested)
        if doctor is None or doctor["role_id"] != "doctor":
            # Неизвестный или чужой ID не переносится молча — остаётся unresolved.
            return None, None
        return requested, None
    return None, "Ваша роль не позволяет импортировать данные."


def scope_errors(
    conn: sqlite3.Connection, document: ImportDocument, actor: "policy.CurrentUser"
) -> list[dict]:
    errors = []
    for i, p in enumerate(document.patients):
        _, err = resolve_import_doctor(conn, p, actor)
        if err is not None:
            errors.append({"index": i, "field": "treatingDoctorUserId", "message": err})
    return errors[:200]


def preview_report(conn: sqlite3.Connection, document: ImportDocument) -> dict:
    existing_ids = {r["id"] for r in conn.execute("SELECT id FROM patients").fetchall()}
    matches = sum(1 for p in document.patients if p.id and p.id in existing_ids)
    return {
        "totalRecords": len(document.patients),
        "existingMatches": matches,
        "newRecords": len(document.patients) - matches,
    }


def _insert_full_patient(
    conn: sqlite3.Connection, p: ImportPatientIn, patient_id: str,
    treating_doctor_user_id: Optional[str] = None,
) -> None:
    now = now_iso()
    arrival = p.arrival or now
    doctor_row = None
    if treating_doctor_user_id:
        doctor_row = conn.execute(
            "SELECT full_name FROM users WHERE id = ?", (treating_doctor_user_id,)
        ).fetchone()
    conn.execute(
        """INSERT INTO patients (id, name, age, priority, stage, phone, policy_number, complaint, diagnosis,
                                  consilium_code, pgi_code, pgi_malignancy_confirmed, nosology, disease_stage,
                                  arrival, doctor, head, created_at, updated_at, treating_doctor_user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            patient_id,
            p.name,
            p.age,
            p.priority,
            p.stage,
            p.phone,
            p.policyNumber,
            p.complaint,
            p.diagnosis,
            p.consiliumCode,
            p.pgiCode,
            store.pgi_result_to_db(p.pgiResult),
            p.nosology,
            p.diseaseStage,
            arrival,
            doctor_row["full_name"] if doctor_row else p.doctor,
            p.head,
            now,
            p.updatedAt or now,
            treating_doctor_user_id,
        ),
    )
    history = p.history or [ImportHistoryEntry(stage=p.stage, enteredAt=arrival, events=p.events, checklist={}, note="")]
    for seq, h in enumerate(history):
        entry_id = store.new_id()
        conn.execute(
            "INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, checklist_json, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entry_id, patient_id, seq, h.stage, h.enteredAt, json.dumps(h.checklist) if h.checklist else None, h.note),
        )
        for ev_seq, ev in enumerate(h.events):
            store.insert_event(conn, entry_id, ev_seq, ev)
    for idx_str, fp in p.futurePlans.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        conn.execute(
            "INSERT INTO future_plans (patient_id, template_index, date, time, treatment_type) VALUES (?, ?, ?, ?, ?)",
            (patient_id, idx, fp.date, fp.time, fp.treatmentType),
        )
    for item in p.diagnosisHistory:
        conn.execute(
            """INSERT INTO patient_diagnosis_history
               (id, patient_id, changed_at, actor_user_id, old_diagnosis, new_diagnosis)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                store.new_id(),
                patient_id,
                item.changedAt,
                item.actorUserId,
                item.oldDiagnosis,
                item.newDiagnosis,
            ),
        )


def commit_import(
    conn: sqlite3.Connection, document: ImportDocument, mode: str,
    actor: "policy.CurrentUser",
) -> dict:
    """Импорт от имени актора. Права на режим проверяет policy.can_import
    (update/replace — только Разработчик); закрепление каждого пациента за
    врачом проходит resolve_import_doctor и не может выйти за scope актора."""
    if mode not in ("add", "update", "replace"):
        raise ValueError("unknown import mode")
    if not policy.can_import(actor, mode):
        raise PermissionError("import mode not allowed for this role")

    resolved: list[Optional[str]] = []
    for p in document.patients:
        doctor_id, err = resolve_import_doctor(conn, p, actor)
        if err is not None:
            # Проверено ещё в preview; здесь — жёсткая страховка.
            raise PermissionError("import assignment outside actor scope")
        resolved.append(doctor_id)

    existing_ids = {r["id"] for r in conn.execute("SELECT id FROM patients").fetchall()}
    added = updated = skipped = 0

    with transaction(conn):
        if mode == "replace":
            conn.execute("DELETE FROM patients")  # cascades to history/events/future_plans
            for p, doctor_id in zip(document.patients, resolved):
                _insert_full_patient(conn, p, store.new_id(), doctor_id)
                added += 1
        elif mode == "update":
            for p, doctor_id in zip(document.patients, resolved):
                if p.id and p.id in existing_ids:
                    conn.execute("DELETE FROM patients WHERE id = ?", (p.id,))
                    _insert_full_patient(conn, p, p.id, doctor_id)
                    updated += 1
                else:
                    skipped += 1
        else:  # add
            for p, doctor_id in zip(document.patients, resolved):
                _insert_full_patient(conn, p, store.new_id(), doctor_id)
                added += 1

    return {"added": added, "updated": updated, "skipped": skipped}
