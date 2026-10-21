"""Repository layer: SQLite rows <-> plain dicts matching the frontend's
patient shape. All mutation here is server-authoritative — ids and
timestamps are generated here, never accepted from callers.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Callable, Optional

from .db import ARCHIVED_EVENT_DELETE_ERROR, now_iso, transaction
from .models import (
    DANGEROUS_KEYS,
    PRIORITIES,
    TERMINAL_STAGES,
    TREATMENT_TYPES,
    EventIn,
    PatientCreateIn,
    normalize_policy_number,
    validate_event_value,
)
from .localization_catalog import validate_new_combination

FIELD_COLUMN = {
    "name": "name",
    "age": "age",
    "phone": "phone",
    "policyNumber": "policy_number",
    "diagnosis": "diagnosis",
    "pgiResult": "pgi_malignancy_confirmed",
    "pgiResultDate": "pgi_result_date",
    "nosology": "nosology",
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
        # Стабильный идентификатор и версия: правка, удаление и восстановление
        # адресуются по id и сверяют версию, а не позицию в списке.
        "id": row["id"],
        "version": row["version"],
        "date": row["date"],
        "time": row["time"],
        "treatmentType": row["treatment_type"],
        "actualDate": row["actual_date"],
        "actualTime": row["actual_time"],
        "matchedPlan": None if row["matched_plan"] is None else bool(row["matched_plan"]),
        "noPlan": bool(row["no_plan"]),
        "consiliumDecision": row["consilium_decision"],
        # Ручная отметка решения ОК: источник (manual / legacy / import), время и автор
        # назначены сервером; у legacy и import автора и времени нет — они не придумываются.
        "consiliumDecisionSource": row["consilium_decision_source"],
        "consiliumDecisionAt": row["consilium_decision_at"],
        "consiliumDecisionByUserId": row["consilium_decision_by_user_id"],
        "consiliumDecisionByName": (row["consilium_decision_by_name"]
                                    if "consilium_decision_by_name" in row.keys() else None),
        # Перенос исторического решения из файла: кто и когда выполнил импорт (не автор решения).
        "consiliumDecisionImportedAt": row["consilium_decision_imported_at"],
        "consiliumDecisionImportedByUserId": row["consilium_decision_imported_by_user_id"],
        "consiliumDecisionImportedByName": (row["consilium_decision_imported_by_name"]
                                            if "consilium_decision_imported_by_name" in row.keys() else None),
        "consiliumDecisionImportId": row["consilium_decision_import_id"],
        "setAt": row["set_at"],
    }


# Поля решения ОК в выгрузке не передаются: формат файла прежний, автор — учетная
# запись этой установки, а импортированное решение получает источник import.
EXPORT_EXCLUDED_EVENT_KEYS = frozenset({
    "version", "consiliumDecisionSource", "consiliumDecisionAt",
    "consiliumDecisionByUserId", "consiliumDecisionByName",
    "consiliumDecisionImportedAt", "consiliumDecisionImportedByUserId",
    "consiliumDecisionImportedByName", "consiliumDecisionImportId",
})


def pgi_result_to_db(value) -> int | None:
    if value in (None, ""):
        return None
    if value == "confirmed":
        return 1
    if value == "not_confirmed":
        return 0
    raise ValueError("pgiResult must be one of ('', 'confirmed', 'not_confirmed')")


def pgi_result_from_db(value) -> str:
    if value is None:
        return ""
    return "confirmed" if int(value) == 1 else "not_confirmed"


def normalize_pgi_result_date(result: str | None, result_date: str | None) -> str | None:
    result = result or ""
    value = (result_date or "").strip()
    if not result:
        return None
    if not value:
        raise ValueError("Дата результата ПГИ обязательна, если выбран результат ПГИ")
    from datetime import date

    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError("pgiResultDate must be YYYY-MM-DD")
    return value


def event_values(ev: EventIn) -> tuple:
    """Медицинские значения события в том виде, в каком их хранит база
    (date, time, treatment_type, actual_date, actual_time, matched_plan,
    no_plan, consilium_decision) — для вставки и для сравнения при импорте."""
    return (
        ev.date or None,
        ev.time or None,
        ev.treatmentType or None,
        ev.actualDate or None,
        ev.actualTime or None,
        None if ev.matchedPlan is None else int(ev.matchedPlan),
        int(bool(ev.noPlan)),
        ev.consiliumDecision or None,
    )


EVENT_VALUE_COLUMNS = ("date", "time", "treatment_type", "actual_date", "actual_time",
                       "matched_plan", "no_plan", "consilium_decision")


def insert_event(conn: sqlite3.Connection, history_entry_id: str, seq: int, ev: EventIn,
                 event_id: Optional[str] = None, *, decision_import: Optional[dict] = None) -> str:
    """Вставка события. Решение ОК во вставляемом событии допустимо только как
    подтвержденный перенос исторического решения из файла (decision_import:
    imported_by — исполнитель импорта, imported_at, import_id); автор и время самого
    решения при этом не известны и не придумываются. Ручная отметка решения —
    отдельная операция mark_consilium_decision."""
    event_id = event_id or new_id()
    if ev.consiliumDecision and not decision_import:
        raise ValueError(DECISION_SEPARATE_OPERATION_MESSAGE)
    imported = decision_import if ev.consiliumDecision else None
    conn.execute(
        """INSERT INTO events (id, history_entry_id, seq, date, time, treatment_type,
                                actual_date, actual_time, matched_plan, no_plan, consilium_decision, set_at,
                                consilium_decision_source, consilium_decision_imported_by_user_id,
                                consilium_decision_imported_at, consilium_decision_import_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            history_entry_id,
            seq,
            *event_values(ev),
            now_iso() if (ev.actualDate or ev.noPlan) else None,
            "import" if imported else None,
            imported["imported_by"] if imported else None,
            imported["imported_at"] if imported else None,
            imported["import_id"] if imported else None,
        ),
    )
    return event_id


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
        # Карточка показывает активные события; удалённые — отдельным запросом.
        events = conn.execute(
            """SELECT events.*, u.full_name AS consilium_decision_by_name,
                      iu.full_name AS consilium_decision_imported_by_name FROM events
               LEFT JOIN users u ON u.id = events.consilium_decision_by_user_id
               LEFT JOIN users iu ON iu.id = events.consilium_decision_imported_by_user_id
               WHERE events.history_entry_id = ? AND events.deleted_at IS NULL
               ORDER BY events.seq ASC""", (h["id"],)
        ).fetchall()
        history.append(
            {
                # Стабильный id записи маршрута: клиент называет его при смене
                # этапа и добавлении события (повторный этап с тем же названием
                # — другая запись).
                "id": h["id"],
                "stage": h["stage"],
                "enteredAt": h["entered_at"],
                "events": [_event_row_to_dict(e) for e in events],
                "checklist": json.loads(h["checklist_json"]) if h["checklist_json"] else {},
                "note": h["note"] or "",
            }
        )
    diagnosis_history_rows = conn.execute(
        """SELECT * FROM patient_diagnosis_history
           WHERE patient_id = ? ORDER BY changed_at ASC, id ASC""",
        (patient_id,),
    ).fetchall()
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
        "consiliumCode": p["consilium_code"] or "",
        "pgiCode": p["pgi_code"] or "",
        "pgiResult": pgi_result_from_db(p["pgi_malignancy_confirmed"]),
        "pgiResultDate": p["pgi_result_date"],
        "nosology": p["nosology"] or "",
        "arrival": p["arrival"],
        "doctor": p["doctor"] or "",
        "head": p["head"] or "",
        "updatedAt": p["updated_at"],
        "history": history,
        "diagnosisHistory": [
            {
                "id": row["id"],
                "patientId": row["patient_id"],
                "changedAt": row["changed_at"],
                "actorUserId": row["actor_user_id"],
                "oldDiagnosis": row["old_diagnosis"] or "",
                "newDiagnosis": row["new_diagnosis"] or "",
            }
            for row in diagnosis_history_rows
        ],
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


# Источники карточки для новых производственных операций (WP2.4-A). 'legacy' —
# только для карточек до v23 и явно разрешенных исторических тестовых фикстур.
CREATION_SOURCES = ("form", "import", "migration")
LEGACY_CREATION_SOURCE = "legacy"


def insert_patient_rows(
    conn: sqlite3.Connection,
    data: PatientCreateIn,
    treating_doctor_user_id: Optional[str],
    *,
    created_by_user_id: Optional[str],
    creation_source: str,
) -> str:
    """SQL-вставка карточки, первой записи маршрута и событий формы.

    Транзакцией НЕ управляет: ее открывает, фиксирует или откатывает вызывающий
    (единственный владелец транзакции). Проверки данных выполняются до первой
    вставки. Автор и источник задает только сервер."""
    if creation_source not in (*CREATION_SOURCES, LEGACY_CREATION_SOURCE):
        raise RuntimeError("unknown creation source")
    if creation_source != LEGACY_CREATION_SOURCE and not created_by_user_id:
        raise RuntimeError("creation author is required for a new card")
    pid = new_id()
    now = now_iso()
    # Отображаемый текст врача следует за учётной записью; свободный текст
    # остаётся только для legacy-записей без сопоставления.
    doctor_text = _treating_doctor_name(conn, treating_doctor_user_id) or data.doctor
    if any(ev.consiliumDecision for ev in data.events):
        # Создание карточки не записывает решение ОК: это отдельная отметка в событии.
        raise ValueError(DECISION_SEPARATE_OPERATION_MESSAGE)
    nosology = validate_new_combination(data.nosology, data.diagnosis)
    pgi_result_date = normalize_pgi_result_date(data.pgiResult, data.pgiResultDate)
    conn.execute(
        """INSERT INTO patients (id, name, age, priority, stage, phone, policy_number, complaint, diagnosis,
                                  consilium_code, pgi_code, pgi_malignancy_confirmed, pgi_result_date, nosology, disease_stage,
                                  arrival, doctor, head, created_at, updated_at, treating_doctor_user_id,
                                  created_by_user_id, creation_source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            data.consiliumCode,
            data.pgiCode,
            pgi_result_to_db(data.pgiResult),
            pgi_result_date,
            nosology,
            "",
            now,
            doctor_text,
            data.head,
            now,
            now,
            treating_doctor_user_id,
            created_by_user_id,
            creation_source,
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


def create_patient(
    conn: sqlite3.Connection, data: PatientCreateIn, treating_doctor_user_id: Optional[str] = None
) -> str:
    """Историческая вставка карточки для тестовых фикстур (источник 'legacy',
    автор неизвестен). Производственный код ее не вызывает: форма создает
    карточку через services.patient_create.create_patient_card, импорт и миграция —
    через importexport с источником точки входа (проверяется тестом)."""
    with conn:
        return insert_patient_rows(conn, data, treating_doctor_user_id,
                                   created_by_user_id=None, creation_source=LEGACY_CREATION_SOURCE)


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


class PatientDeleteBlocked(Exception):
    """Карточку нельзя удалить физически: каскад уничтожил бы историю (409)."""

    def __init__(self, reason: str, count: int, message: str):
        self.reason = reason
        self.count = count
        super().__init__(message)


def archived_event_count(conn: sqlite3.Connection, patient_id: str) -> int:
    return conn.execute(
        """SELECT COUNT(*) FROM events JOIN history_entries h ON h.id = events.history_entry_id
           WHERE h.patient_id = ? AND events.deleted_at IS NOT NULL""",
        (patient_id,),
    ).fetchone()[0]


def is_archived_event_delete_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.IntegrityError) and ARCHIVED_EVENT_DELETE_ERROR in str(exc)


def delete_patient(conn: sqlite3.Connection, patient_id: str) -> bool:
    """Физическое удаление карточки, если оно не уничтожает историю.

    Проверки и удаление идут под одной блокировкой записи: запись консилиума
    или удалённое событие, появившиеся параллельно, не будут стёрты каскадом."""
    with transaction(conn):
        consilium_records = conn.execute(
            "SELECT COUNT(*) FROM oncology_consilium_appointments WHERE patient_id = ?", (patient_id,)
        ).fetchone()[0]
        if consilium_records:
            raise PatientDeleteBlocked(
                "consilium_records", consilium_records,
                "У пациента есть записи онкологического консилиума; карточку удалить нельзя.",
            )
        archived = archived_event_count(conn, patient_id)
        if archived:
            raise PatientDeleteBlocked(
                "archived_events", archived,
                "У пациента есть удалённые события медицинской истории; они хранятся в базе, "
                "поэтому карточку удалить нельзя. Данные не изменены.",
            )
        try:
            cur = conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
        except sqlite3.IntegrityError as exc:
            if is_archived_event_delete_error(exc):  # страховка базы данных
                raise PatientDeleteBlocked(
                    "archived_events", 1,
                    "У пациента есть удалённые события медицинской истории; карточку удалить нельзя.",
                ) from exc
            raise
        return cur.rowcount > 0


def update_patient_field(conn: sqlite3.Connection, patient_id: str, field: str, value) -> bool:
    return update_patient_field_with_actor(conn, patient_id, field, value, actor_user_id=None)


def update_patient_field_with_actor(
    conn: sqlite3.Connection, patient_id: str, field: str, value, *, actor_user_id: str | None = None
) -> bool:
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
    elif field == "policyNumber":
        value = normalize_policy_number(value)
    elif field == "pgiResult":
        if isinstance(value, dict):
            result = value.get("result") or ""
            pgi_date = normalize_pgi_result_date(result, value.get("date"))
        else:
            result = value or ""
            row = conn.execute("SELECT pgi_result_date FROM patients WHERE id = ?", (patient_id,)).fetchone()
            if row is None:
                return False
            pgi_date = normalize_pgi_result_date(result, row["pgi_result_date"])
        value = pgi_result_to_db(result)
    elif field == "pgiResultDate":
        row = conn.execute("SELECT pgi_malignancy_confirmed FROM patients WHERE id = ?", (patient_id,)).fetchone()
        if row is None:
            return False
        result = pgi_result_from_db(row["pgi_malignancy_confirmed"])
        value = normalize_pgi_result_date(result, value)
    elif field == "nosology":
        row = conn.execute("SELECT diagnosis FROM patients WHERE id = ?", (patient_id,)).fetchone()
        if row is None:
            return False
        value = validate_new_combination(str(value or ""), row["diagnosis"] or "")
    elif field == "diagnosis":
        value = "" if value is None else str(value)
        limit = FIELD_MAX_LEN.get(field)
        if limit and len(value) > limit:
            raise ValueError(f"{field} exceeds max length {limit}")
        value_nosology = validate_new_combination("", value)
    else:
        value = "" if value is None else str(value)
        limit = FIELD_MAX_LEN.get(field)
        if limit and len(value) > limit:
            raise ValueError(f"{field} exceeds max length {limit}")
        if field == "name" and not value.strip():
            # Create и import требуют непустое ФИО — правка не должна его снимать.
            raise ValueError("name must not be empty")

    with conn:
        previous = None
        if field == "diagnosis":
            row = conn.execute("SELECT diagnosis FROM patients WHERE id = ?", (patient_id,)).fetchone()
            if row is None:
                return False
            previous = row["diagnosis"] or ""
        if field == "diagnosis":
            cur = conn.execute(
                "UPDATE patients SET diagnosis = ?, nosology = ?, updated_at = ? WHERE id = ?",
                (value, value_nosology, now_iso(), patient_id),
            )
        elif field == "pgiResult":
            cur = conn.execute(
                "UPDATE patients SET pgi_malignancy_confirmed = ?, pgi_result_date = ?, updated_at = ? WHERE id = ?",
                (value, pgi_date, now_iso(), patient_id),
            )
        else:
            cur = conn.execute(
                f"UPDATE patients SET {column} = ?, updated_at = ? WHERE id = ?",  # nosec B608
                (value, now_iso(), patient_id),
            )
        if field == "diagnosis" and cur.rowcount > 0 and previous != value:
            conn.execute(
                """INSERT INTO patient_diagnosis_history
                   (id, patient_id, changed_at, actor_user_id, old_diagnosis, new_diagnosis)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (new_id(), patient_id, now_iso(), actor_user_id, previous, str(value or "")),
            )
        return cur.rowcount > 0


def _history_entry_id(conn: sqlite3.Connection, patient_id: str, occ_index: int) -> Optional[str]:
    row = conn.execute(
        "SELECT id FROM history_entries WHERE patient_id = ? ORDER BY seq ASC LIMIT 1 OFFSET ?",
        (patient_id, occ_index),
    ).fetchone()
    return row["id"] if row else None


def _next_event_seq(conn: sqlite3.Connection, history_entry_id: str) -> int:
    return conn.execute(
        "SELECT COALESCE(MAX(seq), -1) + 1 AS s FROM events WHERE history_entry_id = ?", (history_entry_id,)
    ).fetchone()["s"]


class EventConflict(Exception):
    """Событие нельзя изменить в его текущем состоянии (409)."""


class FactConflict(EventConflict):
    """Перевод этапа просит записать факт, отличный от уже сохраненного (409 FACT_CONFLICT).

    Существующие фактические дата и время меняются только отдельной правкой
    события; перевод этапа их не перезаписывает и не стирает."""
    code = "FACT_CONFLICT"


FACT_DATE_CONFLICT_MESSAGE = (
    "У события уже записана другая фактическая дата. Перевод не выполнен, данные не изменены. "
    "Проверьте факт события на вкладке событий и повторите перевод"
)
FACT_TIME_CONFLICT_MESSAGE = (
    "У события уже записано другое фактическое время (или время не было записано). Перевод не выполнен, "
    "данные не изменены. Исправьте факт события отдельно и повторите перевод"
)
FACT_TIME_WITHOUT_DATE_MESSAGE = "Фактическое время передано без фактической даты"

DECISION_SEPARATE_OPERATION_MESSAGE = (
    "Решение ОК отмечается отдельной операцией в выбранном событии этапа «Направлен на ОК» "
    "и не передается вместе с переводом этапа или созданием карточки. Данные не изменены"
)


class DecisionRequiresSeparateOperation(ValueError):
    """consiliumDecision пришел в перевод этапа (прежний контракт): 422 DECISION_SEPARATE_OPERATION."""
    code = "DECISION_SEPARATE_OPERATION"


class DecisionConflict(EventConflict):
    """Ручную отметку решения ОК нельзя записать в этом состоянии (409)."""
    def __init__(self, message: str, code: str, extra: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.extra = extra or {}


DECISION_ARCHIVED_EXISTS_MESSAGE = (
    "В этом этапе «Направлен на ОК» решение ОК уже отмечалось: событие с ним находится в архиве "
    "(удалено). Новая отметка не исправляет и не удаляет прежнее решение — оно остается в архиве и "
    "в истории пациента. Чтобы продолжить, подтвердите, что вы видели архивное решение. Данные не изменены"
)


DECISION_NOT_OK_STAGE_MESSAGE = (
    "Решение ОК отмечается только на этапе «Направлен на ОК». Обновите карточку пациента"
)
DECISION_OTHER_ENTRY_MESSAGE = (
    "Событие не относится к текущему этапу «Направлен на ОК». Обновите карточку пациента"
)
DECISION_ALREADY_RECORDED_MESSAGE = (
    "Решение ОК в этом событии уже отмечено. Повторная запись и замена не выполняются, данные не изменены"
)


def mark_consilium_decision(
    conn: sqlite3.Connection, patient_id: str, event_id: str, decision: str, version: int, *,
    expected_stage: str, expected_history_entry_id: str, user_id: str,
    acknowledged_archived_event_ids: Optional[list[str]] = None,
    audit_hook: Optional[Callable[[dict], None]] = None,
) -> Optional[dict]:
    """Ручная отметка решения ОК в явно выбранном событии текущего этапа ОК.

    Однократная запись: решение, автор (пользователь сессии), серверное время и
    источник manual пишутся одним условным UPDATE под BEGIN IMMEDIATE вместе с
    обязательным аудитом (audit_hook). Этап, история, факт события, matched_plan,
    set_at и записи консилиума не меняются. Событие другого пациента не находится
    (None → 404). Повтор или параллельная отметка — DecisionConflict (409)."""
    with transaction(conn):
        p = conn.execute("SELECT stage FROM patients WHERE id = ?", (patient_id,)).fetchone()
        if p is None:
            return None
        row = event_of_patient(conn, patient_id, event_id)
        if row is None:
            return None
        if p["stage"] != expected_stage:
            raise DecisionConflict(STALE_STAGE_MESSAGE, "STALE_STAGE")
        if p["stage"] != "consult":
            raise DecisionConflict(DECISION_NOT_OK_STAGE_MESSAGE, "NOT_OK_STAGE")
        history_count = _count_history(conn, patient_id)
        current_entry_id = _history_entry_id(conn, patient_id, history_count - 1) if history_count > 0 else None
        if current_entry_id is None or current_entry_id != expected_history_entry_id:
            raise DecisionConflict(STALE_STAGE_MESSAGE, "STALE_STAGE")
        if row["history_entry_id"] != current_entry_id:
            raise DecisionConflict(DECISION_OTHER_ENTRY_MESSAGE, "EVENT_NOT_IN_CURRENT_STAGE")
        if row["deleted_at"] is not None:
            raise DecisionConflict("Событие удалено. Обновите карточку пациента", "EVENT_DELETED")
        if row["consilium_decision"] is not None:
            raise DecisionConflict(DECISION_ALREADY_RECORDED_MESSAGE, "DECISION_ALREADY_RECORDED")
        if row["version"] != version:
            raise DecisionConflict(
                "Событие уже изменено другим пользователем. Обновите карточку пациента", "STALE_EVENT_VERSION")
        # Архивные решения этой же записи маршрута (другие посещения этапа ОК не
        # учитываются): новая отметка требует явного подтверждения именно этого набора
        # событий. Проверка — в той же транзакции, что и запись: удаление или
        # восстановление события между предупреждением и подтверждением дает новое
        # предупреждение, а не отметку.
        archived = [r["id"] for r in conn.execute(
            """SELECT id FROM events WHERE history_entry_id = ? AND deleted_at IS NOT NULL
               AND consilium_decision IS NOT NULL ORDER BY seq""", (current_entry_id,))]
        if archived and sorted(set(acknowledged_archived_event_ids or [])) != sorted(archived):
            raise DecisionConflict(DECISION_ARCHIVED_EXISTS_MESSAGE, "ARCHIVED_DECISION_EXISTS",
                                   {"archivedEventIds": archived})
        recorded_at = now_iso()
        cur = conn.execute(
            """UPDATE events SET consilium_decision = ?, consilium_decision_by_user_id = ?,
                                 consilium_decision_at = ?, consilium_decision_source = 'manual',
                                 version = version + 1
               WHERE id = ? AND version = ? AND deleted_at IS NULL AND consilium_decision IS NULL""",
            (decision, user_id, recorded_at, event_id, version),
        )
        if cur.rowcount != 1:
            raise DecisionConflict(DECISION_ALREADY_RECORDED_MESSAGE, "DECISION_ALREADY_RECORDED")
        touch_patient(conn, patient_id)
        result = {"event_id": event_id, "history_seq": row["history_seq"], "recorded_at": recorded_at,
                  "archived_acknowledged": archived}
        if audit_hook is not None:
            audit_hook(result)
    return result


def event_matched_plan(no_plan, plan_date, plan_time, actual_date, actual_time) -> Optional[int]:
    """Соответствие факта плану — единое вычисление для перевода этапа и правки события.

    1 — факт совпал с планом по дате и времени, 0 — не совпал, NULL — сравнивать
    не с чем: нет факта, нет плановой даты или событие без плана. Это те же
    значения, что присылал клиент приложения при переводе этапа и что создается
    для первичного приема без плана; правило сравнения (дата и время) — прежнее.
    Сохраненные строки не пересчитываются: функция применяется только при записи."""
    if no_plan or not actual_date or not plan_date:
        return None
    return int(actual_date == plan_date and (actual_time or "") == (plan_time or ""))


def _active_event_count(conn: sqlite3.Connection, history_entry_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM events WHERE history_entry_id = ? AND deleted_at IS NULL", (history_entry_id,)
    ).fetchone()[0]


STALE_EVENTS_MESSAGE = "События этапа уже изменены другим пользователем. Обновите карточку пациента"
EVENT_REF_REQUIRED_MESSAGE = (
    "События этапа изменились или открыта устаревшая версия приложения: для записи факта "
    "нужны идентификатор и версия события. Обновите страницу"
)
STAGE_REF_REQUIRED_MESSAGE = (
    "Для перевода этапа нужны текущий этап и запись маршрута карточки. "
    "Обновите страницу: открыта устаревшая версия приложения"
)
STALE_STAGE_MESSAGE = "Этап пациента уже изменён другим пользователем. Обновите карточку пациента"


def change_stage(
    conn: sqlite3.Connection,
    patient_id: str,
    new_stage: str,
    last_event_update: Optional[EventIn],
    *,
    expected_event_id: Optional[str] = None,
    expected_event_version: Optional[int] = None,
    expected_stage: Optional[str] = None,
    expected_history_entry_id: Optional[str] = None,
    audit_hook: Optional[Callable[[dict], None]] = None,
) -> bool:
    """Перевод пациента на новый этап с записью факта текущего события.

    Всё выполняется под одной блокировкой записи (BEGIN IMMEDIATE): этап,
    события и история читаются и меняются как одно целое. Если запрос
    меняет существующее событие, клиент обязан назвать его id и версию —
    без них сервер не угадывает, какое событие менять.

    Существующий факт (actual_date, actual_time, matched_plan) перевод не
    стирает и не подменяет: другая дата или другое непустое время — FactConflict
    (409 FACT_CONFLICT) без каких-либо изменений; совпадающий факт и пустое время
    оставляют записанное как есть. Если факта еще нет, он записывается из запроса;
    сервер дату не придумывает. matched_plan вычисляет сервер.

    audit_hook пишет обязательную запись аудита в той же транзакции: ошибка
    аудита откатывает перевод целиком (этап, история, событие).

    Решение ОК перевод не записывает и не меняет (WP2.2-B): запрос с
    consiliumDecision отклоняется явно (DecisionRequiresSeparateOperation, 422),
    а не игнорируется."""
    from . import config
    if last_event_update is not None and last_event_update.consiliumDecision:
        raise DecisionRequiresSeparateOperation(DECISION_SEPARATE_OPERATION_MESSAGE)
    with transaction(conn):
        p = conn.execute("SELECT stage FROM patients WHERE id = ?", (patient_id,)).fetchone()
        if p is None:
            return False
        # Этап, который видел клиент, обязателен: иначе перевод из устаревшей
        # карточки (или параллельный перевод) прошёл бы от чужого этапа.
        if expected_stage is None or expected_history_entry_id is None:
            raise EventConflict(STAGE_REF_REQUIRED_MESSAGE)
        if p["stage"] != expected_stage:
            raise EventConflict(STALE_STAGE_MESSAGE)
        if p["stage"] == new_stage:
            return False
        old_stage = p["stage"]
        history_count = _count_history(conn, patient_id)
        if history_count >= config.MAX_HISTORY_ENTRIES_PER_PATIENT:
            raise ValueError(f"no more than {config.MAX_HISTORY_ENTRIES_PER_PATIENT} history entries per patient")
        current_entry_id = _history_entry_id(conn, patient_id, history_count - 1) if history_count > 0 else None
        # Название этапа не доказывает актуальность: exam → appointment → exam
        # снова даёт «exam». Клиент обязан назвать текущую запись маршрута.
        if current_entry_id != expected_history_entry_id:
            raise EventConflict(STALE_STAGE_MESSAGE)
        outcome = {"event_id": None, "fact": "none", "from_stage": old_stage,
                   "history_seq": history_count - 1 if history_count > 0 else None}
        if current_entry_id and last_event_update is not None:
            # Поля, которых нет в запросе, не трогают сохраненные значения.
            sent = last_event_update.model_fields_set
            req_date = last_event_update.actualDate if "actualDate" in sent else None
            req_time = (last_event_update.actualTime or "") if "actualTime" in sent else ""
            events = conn.execute(
                "SELECT * FROM events WHERE history_entry_id = ? AND deleted_at IS NULL ORDER BY seq ASC",
                (current_entry_id,),
            ).fetchall()
            if events:
                last = events[-1]
                if expected_event_id is None or expected_event_version is None:
                    raise EventConflict(EVENT_REF_REQUIRED_MESSAGE)
                # Карточка устарела: последнее активное событие уже другое
                # (удалено, восстановлено или добавлено) либо изменено.
                if last["id"] != expected_event_id or last["version"] != expected_event_version:
                    raise EventConflict(STALE_EVENTS_MESSAGE)
                if last["actual_date"]:
                    # Факт уже записан: перевод его только подтверждает.
                    if req_date and req_date != last["actual_date"]:
                        raise FactConflict(FACT_DATE_CONFLICT_MESSAGE)
                    if req_time and req_time != (last["actual_time"] or ""):
                        raise FactConflict(FACT_TIME_CONFLICT_MESSAGE)
                    fact_date, fact_time, matched = last["actual_date"], last["actual_time"], last["matched_plan"]
                    outcome["fact"] = "kept"
                elif req_date:
                    # Даты еще нет, но время могло быть записано раньше правкой события:
                    # оно тоже факт и защищается так же — другое время дает FactConflict,
                    # пустое или отсутствующее в запросе время не стирает сохраненное.
                    stored_time = last["actual_time"] or ""
                    if req_time and stored_time and req_time != stored_time:
                        raise FactConflict(FACT_TIME_CONFLICT_MESSAGE)
                    fact_date, fact_time = req_date, stored_time or req_time
                    matched = event_matched_plan(last["no_plan"], last["date"], last["time"], req_date, fact_time)
                    outcome["fact"] = "set"
                else:
                    if req_time:
                        raise ValueError(FACT_TIME_WITHOUT_DATE_MESSAGE)
                    fact_date, fact_time, matched = None, last["actual_time"], last["matched_plan"]
                outcome["event_id"] = last["id"]
                # Строка меняется, только если записывается новый факт; иначе событие
                # (и его версия) остаются как были. Решение ОК перевод не трогает.
                if outcome["fact"] == "set":
                    cur = conn.execute(
                        """UPDATE events SET actual_date = ?, actual_time = ?, matched_plan = ?,
                                               set_at = ?, version = version + 1
                           WHERE id = ? AND deleted_at IS NULL AND version = ?""",
                        (
                            fact_date,
                            fact_time,
                            matched,
                            now_iso(),
                            expected_event_id,
                            expected_event_version,
                        ),
                    )
                    if cur.rowcount != 1:
                        raise EventConflict(STALE_EVENTS_MESSAGE)
            else:
                if expected_event_id is not None:
                    # Клиент видел событие, которого среди активных уже нет.
                    raise EventConflict(STALE_EVENTS_MESSAGE)
                if req_time and not req_date:
                    raise ValueError(FACT_TIME_WITHOUT_DATE_MESSAGE)
                # Удалённые события сохраняют свои seq — новое встаёт после них.
                ev = last_event_update.model_copy(update={
                    "actualDate": req_date,
                    "actualTime": req_time,
                    "matchedPlan": event_matched_plan(
                        last_event_update.noPlan, last_event_update.date, last_event_update.time, req_date, req_time),
                })
                next_seq = _next_event_seq(conn, current_entry_id)
                outcome["event_id"] = insert_event(conn, current_entry_id, next_seq, ev)
                outcome["fact"] = "set" if req_date else "none"

        now = now_iso()
        # Условие по прежнему этапу: два одновременных перевода не создадут
        # две записи истории от одного исходного этапа.
        cur = conn.execute(
            "UPDATE patients SET stage = ?, updated_at = ? WHERE id = ? AND stage = ?",
            (new_stage, now, patient_id, old_stage),
        )
        if cur.rowcount != 1:
            raise EventConflict(STALE_STAGE_MESSAGE)
        conn.execute(
            "INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, checklist_json, note) "
            "VALUES (?, ?, ?, ?, ?, NULL, '')",
            (new_id(), patient_id, history_count, new_stage, now),
        )
        if new_stage in TERMINAL_STAGES:
            _cancel_event_reminders(conn, "patient_id = ?", (patient_id,), "event_inactive")
        elif last_event_update is not None and current_entry_id:
            _cancel_event_reminders(
                conn, "source_id IN (SELECT id FROM events WHERE history_entry_id = ?)", (current_entry_id,), "event_inactive")
        if audit_hook is not None:
            audit_hook(outcome)
    return True


def _count_history(conn: sqlite3.Connection, patient_id: str) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM history_entries WHERE patient_id = ?", (patient_id,)).fetchone()
    return row["c"]


def _cancel_event_reminders(conn: sqlite3.Connection, where: str, params: tuple, reason: str) -> None:
    """Отменить ожидающие напоминания о событиях (в транзакции вызывающего):
    изменённое, удалённое или завершённое событие не должно порождать SMS."""
    now = now_iso()
    conn.execute(
        f"""UPDATE notifications SET status = 'cancelled', cancelled_at = ?, updated_at = ?, error_code = ?
            WHERE source_type = 'event' AND status = 'pending' AND {where}""",  # nosec B608 - fixed fragments
        (now, now, reason, *params),
    )


STALE_EVENT_SET_MESSAGE = "События этапа уже изменены другим пользователем. Обновите карточку пациента"
ADD_CONTEXT_REQUIRED_MESSAGE = (
    "Для добавления события нужен список событий этапа, который видела карточка. "
    "Обновите страницу: открыта устаревшая версия приложения"
)


def add_event(
    conn: sqlite3.Connection, patient_id: str, occ_index: int,
    expected_event_ids: Optional[list[str]] = None, *, placeholder: bool = False,
    audit_hook: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Добавить пустое событие в запись маршрута; возвращает id нового события.

    Клиент называет активные события этапа, которые видел (по порядку).
    Если набор уже другой — карточка устарела (EventConflict): так две
    открытые копии пустого этапа не создадут два события. Проверяется
    именно набор id, а не их число: удаление одного события и добавление
    другого число не меняет. placeholder — автоматическое создание при
    заполнении поля-заготовки: допустимо только для этапа без событий."""
    from . import config
    if expected_event_ids is None:
        raise EventConflict(ADD_CONTEXT_REQUIRED_MESSAGE)
    if placeholder and expected_event_ids:
        raise ValueError("placeholder event can be created only for a stage without events")
    with transaction(conn):
        entry_id = _history_entry_id(conn, patient_id, occ_index)
        if entry_id is None:
            return None
        active = [r["id"] for r in conn.execute(
            "SELECT id FROM events WHERE history_entry_id = ? AND deleted_at IS NULL ORDER BY seq ASC", (entry_id,)
        ).fetchall()]
        if active != list(expected_event_ids):
            raise EventConflict(STALE_EVENT_SET_MESSAGE)
        # Лимит и следующий seq — под той же блокировкой записи, что и вставка.
        if len(active) >= config.MAX_EVENTS_PER_HISTORY_ENTRY:
            raise ValueError(f"no more than {config.MAX_EVENTS_PER_HISTORY_ENTRY} events per history entry")
        event_id = insert_event(conn, entry_id, _next_event_seq(conn, entry_id), EventIn())
        touch_patient(conn, patient_id)
        if audit_hook is not None:
            audit_hook(event_id)   # обязательный аудит в той же транзакции
    return event_id


EVENT_FIELD_COLUMN = {
    "date": "date",
    "time": "time",
    "treatmentType": "treatment_type",
    "actualDate": "actual_date",
    "actualTime": "actual_time",
}


def event_of_patient(conn: sqlite3.Connection, patient_id: str, event_id: str) -> Optional[sqlite3.Row]:
    """Событие пациента по стабильному id (активное или удалённое).

    Событие другого пациента не находится — так id из чужой карточки
    не даёт доступа к событию."""
    return conn.execute(
        """SELECT events.*, h.seq AS history_seq FROM events
           JOIN history_entries h ON h.id = events.history_entry_id
           WHERE events.id = ? AND h.patient_id = ?""",
        (event_id, patient_id),
    ).fetchone()


def _event_conflict(row: sqlite3.Row, version: int, *, want_deleted: bool) -> None:
    if want_deleted and row["deleted_at"] is None:
        raise EventConflict("Событие не удалено")
    if not want_deleted and row["deleted_at"] is not None:
        raise EventConflict("Событие удалено. Обновите карточку пациента")
    if row["version"] != version:
        raise EventConflict("Событие уже изменено другим пользователем. Обновите карточку пациента")


def event_facts(row: sqlite3.Row) -> dict:
    """Только признаки события для аудита — без дат и решений."""
    return {
        "planned": int(bool(row["date"] or row["time"])),
        "actual": int(bool(row["actual_date"])),
        "decision": int(bool(row["consilium_decision"])),
    }


def delete_event(
    conn: sqlite3.Connection, patient_id: str, event_id: str, version: int, user_id: str,
    *, audit_hook: Optional[Callable[[sqlite3.Row], None]] = None,
) -> Optional[sqlite3.Row]:
    """Мягкое удаление: событие скрывается из маршрута, данные сохраняются.

    Возвращает строку события до удаления или None, если события у пациента
    нет. Уже удалённое или изменённое событие — EventConflict. audit_hook
    пишет обязательную запись аудита в той же транзакции: ошибка аудита
    откатывает удаление."""
    with transaction(conn):
        row = event_of_patient(conn, patient_id, event_id)
        if row is None:
            return None
        cur = conn.execute(
            """UPDATE events SET deleted_at = ?, deleted_by_user_id = ?, version = version + 1
               WHERE id = ? AND version = ? AND deleted_at IS NULL""",
            (now_iso(), user_id, event_id, version),
        )
        if cur.rowcount != 1:
            if row["deleted_at"] is not None:
                raise EventConflict("Событие уже удалено")
            _event_conflict(row, version, want_deleted=False)
            raise EventConflict("Событие уже изменено другим пользователем. Обновите карточку пациента")
        _cancel_event_reminders(conn, "source_id = ?", (event_id,), "event_removed")
        touch_patient(conn, patient_id)
        if audit_hook is not None:
            audit_hook(row)
    return row


def restore_event(
    conn: sqlite3.Connection, patient_id: str, event_id: str, version: int,
    *, audit_hook: Optional[Callable[[sqlite3.Row], None]] = None,
) -> Optional[sqlite3.Row]:
    """Вернуть удалённое событие на его место в истории (тот же этап и seq).

    Этап пациента не меняется. Напоминание не создаётся здесь: его, если
    событие ещё актуально, поставит обычная генерация по существующим правилам.
    Сведения о прошлом удалении (кто и когда) остаются в журнале аудита:
    audit_hook пишет восстановление в той же транзакции."""
    from . import config
    with transaction(conn):
        row = event_of_patient(conn, patient_id, event_id)
        if row is None:
            return None
        if row["deleted_at"] is not None and _active_event_count(conn, row["history_entry_id"]) >= config.MAX_EVENTS_PER_HISTORY_ENTRY:
            raise ValueError(f"no more than {config.MAX_EVENTS_PER_HISTORY_ENTRY} events per history entry")
        cur = conn.execute(
            """UPDATE events SET deleted_at = NULL, deleted_by_user_id = NULL, version = version + 1
               WHERE id = ? AND version = ? AND deleted_at IS NOT NULL""",
            (event_id, version),
        )
        if cur.rowcount != 1:
            _event_conflict(row, version, want_deleted=True)
            raise EventConflict("Событие уже изменено другим пользователем. Обновите карточку пациента")
        touch_patient(conn, patient_id)
        if audit_hook is not None:
            audit_hook(row)
    return row


def update_event(conn: sqlite3.Connection, patient_id: str, event_id: str, field: str, value: str, version: int,
                 *, audit_hook: Optional[Callable[[sqlite3.Row], None]] = None) -> Optional[sqlite3.Row]:
    if field not in EVENT_FIELD_COLUMN:
        raise ValueError("unknown event field")
    value = "" if value is None else str(value)
    if len(value) > 20:
        raise ValueError("value too long")
    validate_event_value(field, value)
    if field == "treatmentType" and value not in TREATMENT_TYPES:
        raise ValueError(f"treatmentType must be one of {TREATMENT_TYPES}")
    column = EVENT_FIELD_COLUMN[field]
    with conn:
        row = event_of_patient(conn, patient_id, event_id)
        if row is None:
            return None
        previous = row[column]
        cur = conn.execute(
            f"""UPDATE events SET {column} = ?, version = version + 1
                WHERE id = ? AND version = ? AND deleted_at IS NULL""",  # nosec B608 - column from allowlist
            (value or None, event_id, version),
        )
        if cur.rowcount != 1:
            _event_conflict(row, version, want_deleted=False)
            raise EventConflict("Событие уже изменено другим пользователем. Обновите карточку пациента")
        # Перенос или факт визита: старое напоминание недействительно;
        # следующий generate-due создаст новое, если событие ещё актуально.
        # Правка, не меняющая время напоминания или активность, его не трогает.
        if field in ("date", "time") and (previous or "") != value:
            _cancel_event_reminders(conn, "source_id = ?", (event_id,), "event_rescheduled")
        elif field == "actualDate" and value:
            _cancel_event_reminders(conn, "source_id = ?", (event_id,), "event_inactive")
        if field in ("actualDate", "actualTime"):
            current = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            # Событие без плана не трогаем, как и раньше. Нет факта или нет плановой
            # даты — NULL («не определено»), а не «по плану» / «с отклонением».
            if not current["no_plan"]:
                matched = event_matched_plan(current["no_plan"], current["date"], current["time"],
                                             current["actual_date"], current["actual_time"])
                conn.execute("UPDATE events SET matched_plan = ? WHERE id = ?", (matched, event_id))
        touch_patient(conn, patient_id)
        if audit_hook is not None:
            audit_hook(row)   # обязательный аудит в той же транзакции: ошибка откатывает правку
    return row


def deleted_events(conn: sqlite3.Connection, patient_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT events.*, h.seq AS history_seq, h.stage AS history_stage,
                  u.full_name AS deleted_by_name, du.full_name AS consilium_decision_by_name,
                  iu.full_name AS consilium_decision_imported_by_name
           FROM events
           JOIN history_entries h ON h.id = events.history_entry_id
           LEFT JOIN users u ON u.id = events.deleted_by_user_id
           LEFT JOIN users du ON du.id = events.consilium_decision_by_user_id
           LEFT JOIN users iu ON iu.id = events.consilium_decision_imported_by_user_id
           WHERE h.patient_id = ? AND events.deleted_at IS NOT NULL
           ORDER BY events.deleted_at DESC, events.id ASC""",
        (patient_id,),
    ).fetchall()
    return [
        {
            **_event_row_to_dict(r),
            "historyIndex": r["history_seq"],
            "historyStage": r["history_stage"],
            "deletedAt": r["deleted_at"],
            "deletedByName": r["deleted_by_name"] or "Сотрудник",
        }
        for r in rows
    ]


def update_note(conn: sqlite3.Connection, patient_id: str, occ_index: int, note: str,
                *, audit_hook: Optional[Callable[[], None]] = None) -> bool:
    entry_id = _history_entry_id(conn, patient_id, occ_index)
    if entry_id is None:
        return False
    with conn:
        conn.execute("UPDATE history_entries SET note = ? WHERE id = ?", (note, entry_id))
        touch_patient(conn, patient_id)
        if audit_hook is not None:
            audit_hook()
    return True


def update_checklist_item(conn: sqlite3.Connection, patient_id: str, occ_index: int, item: str, checked: bool,
                          *, audit_hook: Optional[Callable[[], None]] = None) -> bool:
    entry_id = _history_entry_id(conn, patient_id, occ_index)
    if entry_id is None:
        return False
    row = conn.execute("SELECT checklist_json FROM history_entries WHERE id = ?", (entry_id,)).fetchone()
    checklist = json.loads(row["checklist_json"]) if row["checklist_json"] else {}
    if item in DANGEROUS_KEYS:
        raise ValueError("checklist contains disallowed keys")
    if len(checklist) >= 100 and item not in checklist:
        raise ValueError("too many checklist items")
    checklist[item] = bool(checked)
    with conn:
        conn.execute("UPDATE history_entries SET checklist_json = ? WHERE id = ?", (json.dumps(checklist), entry_id))
        touch_patient(conn, patient_id)
        if audit_hook is not None:
            audit_hook()
    return True
