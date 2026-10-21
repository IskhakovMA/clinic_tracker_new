"""Import/export business logic: strict validated pipeline (preview -> commit).

Import never trusts client-sent ids to assign new identity; every commit
runs inside a single transaction so a mid-way failure leaves the database
exactly as it was before the import started.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from contextlib import contextmanager
from typing import Callable, Optional

from pydantic import ValidationError

from . import config, export_signing, policy, store, users
from .db import now_iso, transaction
from .localization_catalog import validate_new_combination
from .models import TERMINAL_STAGES, ImportDocument, ImportHistoryEntry, ImportPatientIn


# Источники карточек, создаваемых импортом (WP2.4-A): задаются точкой входа.
IMPORT_CREATION_SOURCES = ("import", "migration")


# WP2.4-B (U9): режимы, создающие новые карточки, — в них одинаковый непустой
# нормализованный полис в нескольких записях одного файла отклоняет файл.
CARD_CREATING_MODES = ("add", "replace")
FILE_POLICY_DUPLICATES_CODE = "FILE_POLICY_DUPLICATES"


class ImportFilePolicyDuplicateError(ValueError):
    """Файл содержит одинаковые непустые номера полисов ОМС в нескольких
    записях (U9). Сообщаются только номера записей, без значений полисов."""

    def __init__(self, groups: list[list[int]]):
        self.groups = groups
        super().__init__(file_policy_duplicates_reason(groups) + " Данные не изменены.")


def file_policy_duplicate_groups(document: "ImportDocument") -> list[list[int]]:
    """Группы записей файла (индексы с 0) с одинаковым непустым номером полиса.

    Номер уже нормализован моделью импорта (удалены пробелы и дефисы);
    других правил и обращений к базе нет — только содержимое самого файла."""
    by_policy: dict[str, list[int]] = {}
    for index, patient in enumerate(document.patients):
        if patient.policyNumber:
            by_policy.setdefault(patient.policyNumber, []).append(index)
    return sorted(rows for rows in by_policy.values() if len(rows) > 1)


def file_policy_duplicates_reason(groups: list[list[int]]) -> str:
    rows = "; ".join(", ".join(str(i + 1) for i in group) for group in groups)
    return (f"В файле повторяется номер полиса ОМС в записях: {rows} (нумерация записей с 1). "
            "Такой файл не импортируется: исправьте файл, чтобы каждый номер полиса встречался один раз.")


def file_policy_duplicates_impact(document: "ImportDocument", mode: str) -> dict:
    """Для preview: отклонит ли режим файл из-за повторяющихся полисов (номера записей с 1).
    update новых карточек не создает и этой проверкой не ограничивается."""
    groups = file_policy_duplicate_groups(document)
    blocked = bool(groups) and mode in CARD_CREATING_MODES
    return {"blocked": blocked, "rows": [[i + 1 for i in g] for g in groups] if blocked else [],
            "reason": file_policy_duplicates_reason(groups) if blocked else ""}


class ImportStructureError(ValueError):
    """Raised for problems with the overall document shape (not yet a
    per-record validation error)."""


@dataclass
class FileIds:
    """Stable ids carried by the file: verified (signed) or only declared.

    Declared ids of an unsigned file are never trusted to match or restore
    anything — they are used only to *block* (an id of a deleted event, an
    already imported record). Verified ids additionally identify patients."""
    verified: bool = False
    patient_ids: set[str] = field(default_factory=set)
    history_ids: set[str] = field(default_factory=set)
    event_ids: set[str] = field(default_factory=set)
    # Для подписанного файла: по каждому пациенту — [(id записи маршрута, [id событий])]
    # в порядке файла; эти проверенные id сохраняются при импорте.
    structure: list[list[tuple[str, list[str]]]] = field(default_factory=list)
    # Файл содержит внутренние идентификаторы системы (пациента, записи
    # маршрута, события, истории диагноза) — подписан он или нет.
    internal_ids: bool = False


@dataclass
class ImportInspection:
    document: Optional[ImportDocument]
    errors: list[dict]
    provenance: dict
    ids: FileIds


_RECORD_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

UNSIGNED_WARNING = (
    "Файл не имеет подтверждённой подписи системы. Он может содержать ранее импортированных "
    "пациентов или устаревшую медицинскую историю. Подтвердите, что вы проверили происхождение "
    "файла и понимаете риск создания дубликатов."
)


def _parse_raw(raw: str):
    try:
        raw_bytes = raw.encode("utf-8")
    except UnicodeEncodeError:
        raise ImportStructureError("File is not valid UTF-8 text")
    if len(raw_bytes) > config.MAX_IMPORT_FILE_BYTES:
        raise ImportStructureError(f"File exceeds max size of {config.MAX_IMPORT_FILE_BYTES} bytes")
    try:
        # Без повторяющихся ключей и NaN: проверяется и импортируется один и тот же объект.
        return export_signing.loads_strict(raw)
    except ValueError:
        raise ImportStructureError("File is corrupted or not valid JSON (duplicate keys are not allowed)")


def _document_shape(parsed) -> dict:
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


def parse_import_structure(raw: str) -> dict:
    return _document_shape(_parse_raw(raw))


def _extract_ids(doc: dict, *, required: bool) -> tuple[dict, FileIds, list[dict]]:
    """Take record ids out of history entries and events (the import models
    stay as before) and check their structure. A signed file must carry an
    id on every patient, history entry and event."""
    ids = FileIds(verified=required)
    errors: list[dict] = []
    patients = []
    for i, patient in enumerate(doc["patients"]):
        if not isinstance(patient, dict):
            patients.append(patient)
            continue
        pid = patient.get("id")
        if pid is not None or any(
            isinstance(item, dict) and (item.get("id") is not None or item.get("patientId") is not None)
            for item in (patient.get("diagnosisHistory") or [])
        ):
            ids.internal_ids = True
        if required and not (isinstance(pid, str) and _RECORD_ID_RE.fullmatch(pid)):
            errors.append({"index": i, "field": "id", "message": "Signed record without a valid id"})
        elif isinstance(pid, str):
            ids.patient_ids.add(pid)
        history = patient.get("history")
        if not isinstance(history, list):
            if required:
                errors.append({"index": i, "field": "history", "message": "Signed record without history"})
            patients.append(patient)
            continue
        entries = []
        layout: list[tuple[str, list[str]]] = []
        for h_index, entry in enumerate(history):
            if not isinstance(entry, dict):
                entries.append(entry)
                continue
            entry = dict(entry)
            hid = entry.pop("id", None)
            if hid is not None:
                ids.internal_ids = True
            if hid is not None or required:
                if not (isinstance(hid, str) and _RECORD_ID_RE.fullmatch(hid)) or hid in ids.history_ids:
                    errors.append({"index": i, "field": f"history.{h_index}.id", "message": "Invalid or duplicate history id"})
                else:
                    ids.history_ids.add(hid)
            event_layout: list[str] = []
            layout.append((hid, event_layout))
            events = entry.get("events")
            if isinstance(events, list):
                cleaned = []
                for e_index, event in enumerate(events):
                    if isinstance(event, dict):
                        event = dict(event)
                        eid = event.pop("id", None)
                        if eid is not None:
                            ids.internal_ids = True
                        if eid is not None or required:
                            if not (isinstance(eid, str) and _RECORD_ID_RE.fullmatch(eid)) or eid in ids.event_ids:
                                errors.append({"index": i, "field": f"history.{h_index}.events.{e_index}.id",
                                               "message": "Invalid or duplicate event id"})
                            else:
                                ids.event_ids.add(eid)
                        event_layout.append(eid)
                    cleaned.append(event)
                entry["events"] = cleaned
            entries.append(entry)
        if required:
            ids.structure.append(layout)
        patients.append({**patient, "history": entries})
    return {**doc, "patients": patients}, ids, errors[:200]


def _validate_document(doc_dict: dict) -> tuple[Optional[ImportDocument], list[dict]]:
    try:
        document = ImportDocument.model_validate(doc_dict)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = err["loc"]
            index = loc[1] if len(loc) > 1 and isinstance(loc[1], int) else None
            field_name = ".".join(str(x) for x in loc[2:]) if len(loc) > 2 else (str(loc[-1]) if loc else "")
            errors.append({"index": index, "field": field_name, "message": err["msg"]})
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


def inspect_import(raw: str) -> ImportInspection:
    """Parse, establish provenance and validate a file. Structural problems
    raise ImportStructureError (400). A file with signature markers that do
    not verify is never downgraded to the unsigned path."""
    parsed = _parse_raw(raw)
    provenance = {"fileSha256": export_signing.file_sha256(raw), "keyId": None, "generatedAt": None}
    if export_signing.is_signed_envelope(parsed):
        status, key_id, message = export_signing.verify(parsed)
        provenance.update(status=status, keyId=key_id, message=message, confirmationRequired=False)
        if status != export_signing.VERIFIED:
            return ImportInspection(None, [{"index": None, "field": "signature", "message": message}],
                                    provenance, FileIds())
        provenance["generatedAt"] = parsed.get("generatedAt")
        doc = {k: v for k, v in parsed.items() if k not in export_signing.SIGNATURE_MARKERS}
        required = True
    else:
        legacy = isinstance(parsed, dict) and "schemaVersion" in parsed and "generatedAt" in parsed
        provenance.update(
            status=export_signing.UNSIGNED_LEGACY if legacy else export_signing.UNSIGNED_EXTERNAL,
            message="Происхождение файла не подтверждено. " + UNSIGNED_WARNING,
            confirmationRequired=True,
        )
        doc = parsed
        required = False
    doc = _document_shape(doc)
    doc, ids, id_errors = _extract_ids(doc, required=required)
    if id_errors:
        return ImportInspection(None, id_errors, provenance, ids)
    document, errors = _validate_document(doc)
    return ImportInspection(document, errors, provenance, ids)


def validate_import(raw: str) -> tuple[Optional[ImportDocument], list[dict]]:
    """Returns (document, errors). document is None when validation failed —
    callers should treat any non-empty errors list as a hard rejection of
    the whole file (no partial import of the valid subset)."""
    inspection = inspect_import(raw)  # raises ImportStructureError; caller maps to 400
    return inspection.document, inspection.errors


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


class ImportConflictError(ValueError):
    """replace/update would delete consilium records through the patient cascade."""

    def __init__(self, records: int, signed: int):
        self.records = records
        self.signed = signed
        super().__init__(
            f"Импорт остановлен: режим затрагивает карточки с записями онкоконсилиума ({records}, из них "
            f"подписанных решений: {signed}); для таких карточек обновление и замена через импорт не "
            "выполняются. Данные не изменены. Для пациентов с записями консилиума используйте режим "
            "«добавить» или правку карточки."
        )


def consilium_conflicts(conn: sqlite3.Connection, document: ImportDocument, mode: str) -> tuple[int, int]:
    """Consilium records that `mode` would delete: (all records, signed ones).

    replace deletes every patient; update deletes and re-inserts each matched
    patient. Both go through ON DELETE CASCADE, which would silently remove
    the patient's consilium bookings and signed decisions.
    """
    if mode not in ("replace", "update"):
        return 0, 0
    rows = conn.execute("SELECT patient_id, status FROM oncology_consilium_appointments").fetchall()
    if mode == "update":
        matched = {p.id for p in document.patients if p.id}
        rows = [r for r in rows if r["patient_id"] in matched]
    return len(rows), sum(1 for r in rows if r["status"] == "signed")


class ImportArchiveConflictError(ValueError):
    """The import would destroy or re-activate soft-deleted patient events."""

    def __init__(self, mode: str, patients: int, events: int):
        self.mode = mode
        self.patients = patients
        self.events = events
        super().__init__(archive_block_reason(mode, patients, events) + " Данные не изменены.")


def archived_event_conflicts(
    conn: sqlite3.Connection, document: ImportDocument, mode: str,
    actor: Optional["policy.CurrentUser"] = None,
) -> tuple[int, int]:
    """Patients with soft-deleted events that `mode` would hit: (patients, events).

    Файл экспорта не содержит удалённых событий и идентификаторов событий,
    поэтому события файла нельзя надёжно сопоставить с архивом (сравнение по
    датам и решениям было бы угадыванием). Отсюда консервативное правило:
    - replace удаляет всех пациентов — каскад стёр бы архив любого из них;
    - update удаляет и заново создаёт совпавшую по ID карточку — архив был бы
      стёрт, а события из старого файла снова стали бы активными;
    - add не трогает существующие карточки, но запись с ID карточки, у которой
      есть удалённые события, создала бы её копию, где эти события активны.
      Для add учитываются только карточки в области видимости актора (как в
      preview_report): чужая карточка неотличима от несуществующей, а её
      архив add не затрагивает. Запись без ID с карточкой не сопоставляется.
    Пациенты без удалённых событий импортируются по прежним правилам."""
    if mode not in ("add", "update", "replace"):
        return 0, 0
    sql = """SELECT h.patient_id AS patient_id, COUNT(*) AS c FROM events
             JOIN history_entries h ON h.id = events.history_entry_id
             WHERE events.deleted_at IS NOT NULL"""
    params: list = []
    if mode == "add" and actor is not None:
        cond, scope_params = policy.patient_scope_filter(actor)
        sql += f" AND h.patient_id IN (SELECT id FROM patients WHERE {cond})"  # nosec B608 - policy fragment
        params.extend(scope_params)
    rows = conn.execute(sql + " GROUP BY h.patient_id", params).fetchall()
    archived = {r["patient_id"]: r["c"] for r in rows}
    if mode != "replace":
        matched = {p.id for p in document.patients if p.id}
        archived = {pid: c for pid, c in archived.items() if pid in matched}
    return len(archived), sum(archived.values())


def archive_block_reason(mode: str, patients: int, events: int) -> str:
    if mode == "replace":
        return (
            f"Полная замена удалила бы удалённые события медицинской истории у пациентов: {patients} "
            f"(событий: {events}). Они хранятся в базе, но не входят в файл экспорта, поэтому режим "
            "«полная замена» заблокирован."
        )
    if mode == "update":
        return (
            f"Файл обновляет карточки пациентов с удалёнными событиями: {patients} "
            f"(событий: {events}). События файла нельзя однозначно сопоставить с удалёнными, а импорт "
            "не должен снова делать их активными, поэтому режим «обновить» для такого файла заблокирован."
        )
    return (
        f"Записи файла совпадают по ID с карточками, у которых есть удалённые события: {patients} "
        f"(событий: {events}). Добавление создало бы копии этих карточек, в которых удалённые события "
        "снова были бы активными, поэтому импорт такого файла заблокирован."
    )


IMPORT_ARCHIVE_ADVICE = {
    "replace": (
        "Полная замена недоступна, пока в базе есть удалённые события. Новые записи добавляйте "
        "режимом «добавить», существующие карточки правьте вручную."
    ),
    "update": (
        "Уберите эти карточки из файла или внесите изменения в них вручную. Удалённое событие "
        "возвращается только явным восстановлением в разделе «Удалённые события» карточки."
    ),
    "add": (
        "Уберите эти карточки из файла. Удалённое событие возвращается только явным "
        "восстановлением в разделе «Удалённые события» карточки."
    ),
}


class ImportArchivedIdsError(ImportArchiveConflictError):
    """The file names, by id, events that are soft-deleted in this database."""

    def __init__(self, mode: str, patients: int, events: int):
        self.mode = mode
        self.patients = patients
        self.events = events
        ValueError.__init__(self, archived_ids_reason(patients, events) + " Данные не изменены.")


class ImportDuplicateError(ValueError):
    """add would create a second copy of records that already exist (same ids)."""

    def __init__(self, records: int):
        self.records = records
        super().__init__(duplicate_reason(records) + " Данные не изменены.")


class ImportRestrictedError(ValueError):
    """The file matches records outside the actor's scope (state not disclosed)."""

    def __init__(self, records: int):
        self.records = records
        super().__init__(RESTRICTED_REASON + " Данные не изменены.")


INTERNAL_IDS_MESSAGE = (
    "Файл содержит внутренние идентификаторы медицинской системы (карточек, записей маршрута, "
    "событий или истории диагноза). Такие файлы импортирует только разработчик. Для добавления "
    "новых пациентов используйте файл без внутренних идентификаторов."
)


def internal_ids_allowed(actor: "policy.CurrentUser") -> bool:
    """Файлы с внутренними id — только у роли с правом update/replace
    (Разработчик); новых прав и ролей не вводится."""
    return policy.can_import(actor, "update")


class ImportInternalIdsError(PermissionError):
    """The actor may not import files that carry internal record ids."""


class ImportOverwriteError(ValueError):
    """update/replace would delete or overwrite existing medical data."""

    def __init__(self, mode: str, patients: int, message: str):
        self.mode = mode
        self.patients = patients
        super().__init__(message)


class ImportPreserveIdsRequired(ValueError):
    """Creating cards with the signed file's internal ids needs the developer's confirmation."""

    def __init__(self, patients: int):
        self.patients = patients
        super().__init__("preserve ids confirmation required")


HISTORICAL_DECISIONS_MESSAGE = (
    "Файл содержит решения ОК. Обычный импорт решений ОК не создает: перенос исторических решений "
    "выполняется только после отдельного подтверждения именно этого файла, режима и числа решений. "
    "Автор и время решений при переносе остаются неизвестными; сотрудник, выполняющий импорт, "
    "автором решения не становится. Данные не изменены."
)


class ImportHistoricalDecisionsRequired(ValueError):
    """Файл создал бы решения ОК, а перенос исторических решений не подтвержден (428)."""
    code = "HISTORICAL_DECISIONS_CONFIRMATION_REQUIRED"

    def __init__(self, decisions: int):
        self.decisions = decisions
        super().__init__(HISTORICAL_DECISIONS_MESSAGE)


def historical_decision_count(document: ImportDocument, mode: str) -> int:
    """Сколько решений ОК импорт в режиме mode создал бы в новых событиях.

    Новые события с решением создают только add и replace (новые карточки целиком);
    update решения не создает — дописываемое событие с решением отклоняется планом."""
    if mode not in ("add", "replace"):
        return 0
    return sum(1 for p in document.patients for h in _file_history(p, None) for ev in h.events
               if ev.consiliumDecision)


class ImportNotEmptyError(ValueError):
    """Migration of local data is allowed only into an empty database."""


def archived_ids_reason(patients: int, events: int) -> str:
    return (
        f"Файл содержит события, удалённые в текущей базе: {events} (пациентов: {patients}). "
        "Импорт не восстанавливает удалённые события и не создаёт их активные копии; вернуть событие "
        "можно только явным восстановлением в разделе «Удалённые события» карточки."
    )


def duplicate_reason(records: int) -> str:
    return (
        f"Записи файла уже есть в базе (совпадают идентификаторы: {records}) — похоже, файл уже "
        "импортирован. Добавление создало бы дубликаты; существующие карточки меняйте вручную "
        "или режимом «обновить»."
    )


def _scope_fragment(mode: str, actor: Optional["policy.CurrentUser"], patient_col: str) -> tuple[str, list]:
    """add видит только карточки в области актора (как preview_report):
    чужая карточка неотличима от несуществующей."""
    if mode == "add" and actor is not None:
        cond, params = policy.patient_scope_filter(actor)
        return f" AND {patient_col} IN (SELECT id FROM patients WHERE {cond})", list(params)  # nosec B608
    return "", []


def _rows_for_ids(conn: sqlite3.Connection, sql: str, ids: set[str], params: list) -> list:
    rows, ordered = [], sorted(ids)
    for start in range(0, len(ordered), 500):
        chunk = ordered[start:start + 500]
        rows.extend(conn.execute(sql.replace("{ids}", ",".join("?" * len(chunk))), (*chunk, *params)).fetchall())
    return rows


def id_conflicts(
    conn: sqlite3.Connection, ids: Optional[FileIds], mode: str,
    actor: Optional["policy.CurrentUser"] = None,
) -> dict:
    """Exact-id checks (never medical heuristics), always against the whole
    database: file events soft-deleted here and — for add — records that
    already exist.

    Details are reported only for patients in the actor's scope. Every match
    on a patient outside it — active or deleted, card or event — counts once
    in `restricted`, so the answer does not tell which state a foreign
    record is in (F9). The import is refused either way."""
    result = {"archivedPatients": 0, "archivedEvents": 0, "existing": 0, "restricted": 0}
    if ids is None or mode not in ("add", "update", "replace"):
        return result
    matches: list[tuple[str, str, bool]] = []   # (kind, patient_id, archived)
    if ids.event_ids:
        for row in _rows_for_ids(
            conn,
            "SELECT events.id, h.patient_id, events.deleted_at IS NOT NULL FROM events "
            "JOIN history_entries h ON h.id = events.history_entry_id WHERE events.id IN ({ids})",
            ids.event_ids, [],
        ):
            matches.append(("event", row[1], bool(row[2])))
    if mode == "add":
        if ids.history_ids:
            for row in _rows_for_ids(conn, "SELECT h.id, h.patient_id FROM history_entries h WHERE h.id IN ({ids})",
                                     ids.history_ids, []):
                matches.append(("history", row[1], False))
        if ids.patient_ids:
            for row in _rows_for_ids(conn, "SELECT patients.id FROM patients WHERE patients.id IN ({ids})",
                                     ids.patient_ids, []):
                matches.append(("patient", row[0], False))
    if not matches:
        return result
    in_scope = _patients_in_scope(conn, {m[1] for m in matches}, actor)
    archived_patients: set[str] = set()
    for kind, patient_id, archived in matches:
        if patient_id not in in_scope:
            # Чужая карточка: только сам факт отказа, без вида и состояния записи.
            if mode == "add":
                result["restricted"] += 1
            continue
        if kind == "event" and archived:
            result["archivedEvents"] += 1
            archived_patients.add(patient_id)
        elif mode == "add":
            # Совпадение id своей карточки — повторный импорт (F10: и для
            # неподписанного файла). Совпадение — только основание для отказа,
            # а не доказательство идентичности: ничего не сопоставляется.
            result["existing"] += 1
    result["archivedPatients"] = len(archived_patients)
    return result


def _patients_in_scope(conn: sqlite3.Connection, patient_ids: set[str],
                       actor: Optional["policy.CurrentUser"]) -> set[str]:
    if actor is None:
        return set(patient_ids)
    cond, params = policy.patient_scope_filter(actor)
    rows = _rows_for_ids(conn, f"SELECT patients.id FROM patients WHERE patients.id IN ({{ids}}) AND {cond}",  # nosec B608
                         patient_ids, list(params))
    return {r[0] for r in rows}


RESTRICTED_REASON = (
    "Часть записей файла нельзя импортировать от вашего имени: они относятся к карточкам, "
    "недоступным вам. Такие записи может проверить и импортировать разработчик."
)


def restricted_import_impact(
    conn: sqlite3.Connection, ids: Optional[FileIds], mode: str, actor: Optional["policy.CurrentUser"] = None,
) -> dict:
    """Generic block for matches outside the actor's scope: no counts, no kind, no state."""
    blocked = id_conflicts(conn, ids, mode, actor)["restricted"] > 0
    return {"blocked": blocked, "reason": RESTRICTED_REASON if blocked else ""}


def duplicate_import_impact(
    conn: sqlite3.Connection, ids: Optional[FileIds], mode: str, actor: Optional["policy.CurrentUser"] = None,
) -> dict:
    existing = id_conflicts(conn, ids, mode, actor)["existing"]
    return {"records": existing, "blocked": existing > 0, "reason": duplicate_reason(existing) if existing else ""}


def archive_import_impact(
    conn: sqlite3.Connection, document: ImportDocument, mode: str,
    actor: Optional["policy.CurrentUser"] = None, ids: Optional[FileIds] = None,
) -> dict:
    """What `mode` would do to soft-deleted events — the same checks commit_import
    repeats inside its transaction. Counts only, never patient data."""
    patients, events = archived_event_conflicts(conn, document, mode, actor)
    by_id = id_conflicts(conn, ids, mode, actor)
    reasons = []
    if patients:
        reasons.append(archive_block_reason(mode, patients, events))
    if by_id["archivedEvents"]:
        reasons.append(archived_ids_reason(by_id["archivedPatients"], by_id["archivedEvents"]))
    blocked = bool(reasons)
    return {
        "patients": max(patients, by_id["archivedPatients"]),
        "events": max(events, by_id["archivedEvents"]),
        "blocked": blocked,
        "reason": " ".join(reasons),
        "advice": IMPORT_ARCHIVE_ADVICE.get(mode, "") if blocked else "",
    }


IMPORT_CONSILIUM_ADVICE = (
    "Используйте режим «Добавить как новые записи» или исправьте нужные карточки "
    "вручную. Записи консилиума и подписанные решения удаляться не будут."
)


def consilium_import_impact(conn: sqlite3.Connection, document: ImportDocument, mode: str) -> dict:
    """What `mode` would do to consilium records, from the same check that
    commit_import enforces: counts only, never patient data."""
    records, signed = consilium_conflicts(conn, document, mode)
    deleted = _deleted_with_history(conn, document, mode)
    blocked = records > 0
    return {
        "records": records,
        "signed": signed,
        "unsigned": records - signed - deleted,
        "deletedWithHistory": deleted,
        "blocked": blocked,
        "reason": (
            f"Режим затрагивает карточки с записями онкоконсилиума ({records}, из них подписанных "
            f"решений: {signed}). Для таких карточек обновление и замена через импорт не выполняются; "
            "записи консилиума, подписанные решения и история возвратов не меняются, импорт будет "
            "отклонён без изменения данных."
        ) if blocked else "",
        "advice": IMPORT_CONSILIUM_ADVICE if blocked else "",
    }


def _deleted_with_history(conn: sqlite3.Connection, document: ImportDocument, mode: str) -> int:
    """Soft-deleted records kept for their return history, among those `mode` would hit."""
    if mode not in ("replace", "update"):
        return 0
    rows = conn.execute(
        "SELECT patient_id FROM oncology_consilium_appointments WHERE deleted_at IS NOT NULL"
    ).fetchall()
    if mode == "update":
        matched = {p.id for p in document.patients if p.id}
        rows = [r for r in rows if r["patient_id"] in matched]
    return len(rows)


def preview_report(
    conn: sqlite3.Connection, document: ImportDocument, actor: "policy.CurrentUser"
) -> dict:
    # Совпадения считаются только в scope актора: пациент вне scope должен
    # быть неотличим от несуществующего (как 404 на чтении).
    cond, params = policy.patient_scope_filter(actor)
    existing_ids = {
        r["id"] for r in conn.execute(f"SELECT id FROM patients WHERE {cond}", params).fetchall()  # nosec B608
    }
    matches = sum(1 for p in document.patients if p.id and p.id in existing_ids)
    return {
        "totalRecords": len(document.patients),
        "existingMatches": matches,
        "newRecords": len(document.patients) - matches,
    }


def _diagnosis_actor(
    conn: sqlite3.Connection, requested: Optional[str], actor: Optional["policy.CurrentUser"]
) -> Optional[str]:
    """Автор записи истории диагноза. Врач и заведующий не могут приписать
    изменение другому сотруднику — автором становится импортирующий. У
    Разработчика (roundtrip/restore) сохраняется существующий ID из файла,
    неизвестный ID обнуляется вместо ошибки внешнего ключа."""
    if actor is not None and actor.scope_strategy != policy.SCOPE_GLOBAL:
        return actor.id
    if requested and users.get_user(conn, requested) is not None:
        return requested
    return None


def _patient_columns(conn: sqlite3.Connection, p: ImportPatientIn, treating_doctor_user_id: Optional[str],
                     now: Optional[str]) -> dict:
    """Значения карточки так, как их записал бы импорт: для вставки и для
    сравнения с текущей карточкой (now=None — дата прибытия только из файла)."""
    doctor_row = None
    if treating_doctor_user_id:
        doctor_row = conn.execute(
            "SELECT full_name FROM users WHERE id = ?", (treating_doctor_user_id,)
        ).fetchone()
    return {
        "name": p.name,
        "age": p.age,
        "priority": p.priority,
        "stage": p.stage,
        "phone": p.phone,
        "policy_number": p.policyNumber,
        "complaint": p.complaint,
        "diagnosis": p.diagnosis,
        "consilium_code": p.consiliumCode,
        "pgi_code": p.pgiCode,
        "pgi_malignancy_confirmed": store.pgi_result_to_db(p.pgiResult),
        "pgi_result_date": store.normalize_pgi_result_date(p.pgiResult, p.pgiResultDate) if p.pgiResultDate else None,
        "nosology": validate_new_combination(p.nosology, p.diagnosis),
        "disease_stage": "",
        "arrival": p.arrival or now,
        "doctor": doctor_row["full_name"] if doctor_row else p.doctor,
        "head": p.head,
        "treating_doctor_user_id": treating_doctor_user_id,
    }


def _file_history(p: ImportPatientIn, arrival: Optional[str]) -> list[ImportHistoryEntry]:
    return p.history or [ImportHistoryEntry(stage=p.stage, enteredAt=arrival or "", events=p.events,
                                            checklist={}, note="")]


def _same(a, b) -> bool:
    """Сравнение хранимых значений: пустые формы ("" / None / {} / []) равны."""
    empty = (None, "", {}, [])
    if a in empty and b in empty:
        return True
    return a == b


OVERWRITE_MESSAGE = (
    "Импорт заблокирован: текущая карточка содержит изменения, которые отсутствуют в загружаемом "
    "файле, или файл изменил бы существующие записи. Во избежание потери медицинской истории "
    "обновление не выполнено."
)
REPLACE_MESSAGE = (
    "Полная замена заблокирована: в базе есть пациенты, и замена удалила бы их медицинские данные. "
    "Полная замена выполняется только в пустую базу; восстановление прежней версии базы — "
    "отдельная процедура восстановления из резервной копии."
)


def _plan_patient_update(conn: sqlite3.Connection, p: ImportPatientIn, doctor_id: Optional[str],
                         layout: Optional[list[tuple[str, list[str]]]], verified: bool):
    """Неразрушающее обновление одной карточки.

    Возвращает список добавляемых событий [(history_entry_id, EventIn, event_id|None)]
    (пустой — карточка совпадает с файлом) или None, если обновление удалило бы
    или изменило существующие данные либо это нельзя доказать. Разрешено одно:
    дописать в конец существующих этапов события, которых в карточке ещё нет.
    Всё остальное — значения карточки, история диагноза, этапы маршрута и уже
    существующие события — должно совпадать с файлом."""
    row = conn.execute("SELECT * FROM patients WHERE id = ?", (p.id,)).fetchone()
    try:
        expected = _patient_columns(conn, p, doctor_id, None)
    except ValueError:
        return None
    if any(not _same(row[column], value) for column, value in expected.items()):
        return None
    db_diagnoses = [
        (r["changed_at"], r["old_diagnosis"] or "", r["new_diagnosis"] or "")
        for r in conn.execute(
            "SELECT * FROM patient_diagnosis_history WHERE patient_id = ? ORDER BY changed_at ASC, id ASC", (p.id,))
    ]
    if db_diagnoses != [(d.changedAt, d.oldDiagnosis or "", d.newDiagnosis or "") for d in p.diagnosisHistory]:
        return None
    db_entries = conn.execute("SELECT * FROM history_entries WHERE patient_id = ? ORDER BY seq ASC", (p.id,)).fetchall()
    file_entries = _file_history(p, expected["arrival"])
    # Новые этапы из файла изменили бы маршрут — импорт этого не делает.
    if len(db_entries) != len(file_entries):
        return None
    if verified and (len(layout) != len(file_entries)
                     or any(len(layout[i][1]) != len(e.events) for i, e in enumerate(file_entries))):
        return None   # структура id не совпадает с данными — не угадываем
    appended = []
    for index, (db_entry, entry) in enumerate(zip(db_entries, file_entries)):
        if verified and layout[index][0] != db_entry["id"]:
            return None
        checklist = json.loads(db_entry["checklist_json"]) if db_entry["checklist_json"] else {}
        if not (_same(db_entry["stage"], entry.stage) and _same(db_entry["entered_at"], entry.enteredAt)
                and _same(checklist, entry.checklist) and _same(db_entry["note"], entry.note)):
            return None
        db_events = conn.execute(
            f"SELECT id, deleted_at, {', '.join(store.EVENT_VALUE_COLUMNS)} FROM events "  # nosec B608 - fixed names
            "WHERE history_entry_id = ? ORDER BY seq ASC", (db_entry["id"],)).fetchall()
        if any(e["deleted_at"] is not None for e in db_events) or len(db_events) > len(entry.events):
            return None
        for e_index, (db_event, event) in enumerate(zip(db_events, entry.events)):
            if verified and layout[index][1][e_index] != db_event["id"]:
                return None
            stored = tuple(db_event[c] for c in store.EVENT_VALUE_COLUMNS)
            if any(not _same(a, b) for a, b in zip(stored, store.event_values(event))):
                return None
        for e_index in range(len(db_events), len(entry.events)):
            event = entry.events[e_index]
            # Дописывать можно только плановое событие в текущий этап незавершённого
            # маршрута — как кнопка «Добавить событие»: факт, решение ОК, отметки
            # плана и прошлые этапы импорт не трогает (правила маршрута — в API).
            if (index != len(db_entries) - 1 or row["stage"] in TERMINAL_STAGES
                    or event.actualDate or event.actualTime or event.consiliumDecision
                    or event.matchedPlan is not None or event.noPlan):
                return None
            event_id = layout[index][1][e_index] if verified else None
            if event_id and conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone():
                return None
            appended.append((db_entry["id"], entry.events[e_index], event_id))
    return appended


def plan_overwrite(conn: sqlite3.Connection, document: ImportDocument, mode: str,
                   actor: "policy.CurrentUser", ids: Optional[FileIds]) -> dict:
    """What update/replace would do to existing cards (same rules as commit)."""
    result = {"blocked": False, "conflicts": 0, "unchanged": 0, "appendedEvents": 0, "updated": 0, "reason": ""}
    if mode == "replace":
        if conn.execute("SELECT 1 FROM patients LIMIT 1").fetchone():
            result.update(blocked=True, reason=REPLACE_MESSAGE)
        return result
    if mode != "update":
        return result
    verified = ids is not None and ids.verified
    existing = {r["id"] for r in conn.execute("SELECT id FROM patients").fetchall()}
    plans = []
    for index, p in enumerate(document.patients):
        if not (p.id and p.id in existing):
            continue
        doctor_id, _ = resolve_import_doctor(conn, p, actor)
        plan = _plan_patient_update(conn, p, doctor_id, ids.structure[index] if verified else None, verified)
        plans.append((index, plan))
        if plan is None:
            result["conflicts"] += 1
        elif plan:
            result["updated"] += 1
            result["appendedEvents"] += len(plan)
        else:
            result["unchanged"] += 1
    if result["conflicts"]:
        result.update(blocked=True, reason=OVERWRITE_MESSAGE)
    result["plans"] = plans
    return result


def overwrite_impact(conn, document, mode, actor, ids) -> dict:
    plan = plan_overwrite(conn, document, mode, actor, ids)
    plan.pop("plans", None)
    return plan


def _insert_full_patient(
    conn: sqlite3.Connection, p: ImportPatientIn, patient_id: str,
    treating_doctor_user_id: Optional[str] = None,
    actor: Optional["policy.CurrentUser"] = None,
    layout: Optional[list[tuple[str, list[str]]]] = None,
    decision_import: Optional[dict] = None,
    imported_decisions: Optional[list] = None,
    *,
    creation_source: str,
) -> None:
    """Новая карточка из файла. Автор создания — исполнитель импорта (actor),
    источник — точка входа операции (import или migration); из файла эти
    значения не берутся."""
    if creation_source not in IMPORT_CREATION_SOURCES:
        raise RuntimeError("unknown import creation source")
    if actor is None:
        raise RuntimeError("import actor is required to create a card")
    now = now_iso()
    columns = _patient_columns(conn, p, treating_doctor_user_id, now)
    names = ["id", *columns, "created_at", "updated_at", "created_by_user_id", "creation_source"]
    conn.execute(
        f"INSERT INTO patients ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",  # nosec B608 - fixed names
        (patient_id, *columns.values(), now, p.updatedAt or now, actor.id, creation_source),
    )
    history = _file_history(p, columns["arrival"])
    for seq, h in enumerate(history):
        # Подписанный файл: проверенные id записи маршрута и событий сохраняются,
        # чтобы повторный импорт и удалённые позже события узнавались точно.
        entry_id = layout[seq][0] if layout else store.new_id()
        conn.execute(
            "INSERT INTO history_entries (id, patient_id, seq, stage, entered_at, checklist_json, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entry_id, patient_id, seq, h.stage, h.enteredAt, json.dumps(h.checklist) if h.checklist else None, h.note),
        )
        for ev_seq, ev in enumerate(h.events):
            # Решение ОК из файла — только подтвержденный перенос исторического решения
            # (источник import): автор и время решения неизвестны и не придумываются,
            # исполнитель импорта хранится отдельно и автором не становится.
            event_id = store.insert_event(conn, entry_id, ev_seq, ev, layout[seq][1][ev_seq] if layout else None,
                                          decision_import=decision_import)
            if ev.consiliumDecision and imported_decisions is not None:
                imported_decisions.append({"patientId": patient_id, "eventId": event_id, "historySeq": seq})
    for item in p.diagnosisHistory:
        conn.execute(
            """INSERT INTO patient_diagnosis_history
               (id, patient_id, changed_at, actor_user_id, old_diagnosis, new_diagnosis)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                store.new_id(),
                patient_id,
                item.changedAt,
                _diagnosis_actor(conn, item.actorUserId, actor),
                item.oldDiagnosis,
                item.newDiagnosis,
            ),
        )


def commit_import(
    conn: sqlite3.Connection, document: ImportDocument, mode: str,
    actor: "policy.CurrentUser", *, ids: Optional[FileIds] = None,
    audit_hook: Optional[Callable[[dict], None]] = None, require_empty: bool = False,
    preserve_ids_confirmed: bool = False, historical_decisions: Optional[dict] = None,
    creation_source: str = "import", in_open_transaction: bool = False,
    before_write: Optional[Callable[[], None]] = None,
) -> dict:
    """Импорт от имени актора. Права на режим проверяет policy.can_import
    (update/replace — только Разработчик); закрепление каждого пациента за
    врачом проходит resolve_import_doctor и не может выйти за scope актора.

    creation_source — источник новых карточек по точке входа операции:
    импорт файла ("import") или перенос локальных данных ("migration").
    Существующие карточки (update) автора и источника создания не меняют.

    in_open_transaction=True — импорт выполняется внутри уже открытой
    транзакции BEGIN IMMEDIATE владельца операции (WP2.4-B: ключ
    идемпотентности, история файлов и импорт фиксируются вместе).
    before_write() вызывается под той же блокировкой после всех проверок,
    непосредственно перед записью; его исключение отменяет импорт."""
    if mode not in ("add", "update", "replace"):
        raise ValueError("unknown import mode")
    if creation_source not in IMPORT_CREATION_SOURCES:
        raise ValueError("unknown import creation source")
    if not policy.can_import(actor, mode):
        raise PermissionError("import mode not allowed for this role")
    if ids is not None and ids.internal_ids and not internal_ids_allowed(actor):
        # До любых обращений к данным: ответ не зависит от состояния базы.
        raise ImportInternalIdsError(INTERNAL_IDS_MESSAGE)

    resolved: list[Optional[str]] = []
    for p in document.patients:
        doctor_id, err = resolve_import_doctor(conn, p, actor)
        if err is not None:
            # Проверено ещё в preview; здесь — жёсткая страховка.
            raise PermissionError("import assignment outside actor scope")
        resolved.append(doctor_id)

    added = updated = skipped = unchanged = appended_events = 0
    created_ids: list[str] = []

    # Проверки и запись — одна транзакция под блокировкой записи (BEGIN
    # IMMEDIATE): между проверкой и удалением карточек никто не добавит запись
    # консилиума или удалённое событие. Любой отказ откатывает всё целиком.
    with (_joined_transaction(conn) if in_open_transaction else transaction(conn)):
        if require_empty and conn.execute("SELECT 1 FROM patients LIMIT 1").fetchone():
            raise ImportNotEmptyError("Server already has patient data; use regular import instead of migration.")
        records, signed = consilium_conflicts(conn, document, mode)
        if records:
            raise ImportConflictError(records, signed)
        patients, events = archived_event_conflicts(conn, document, mode, actor)
        if patients:
            raise ImportArchiveConflictError(mode, patients, events)
        by_id = id_conflicts(conn, ids, mode, actor)
        if by_id["archivedEvents"]:
            raise ImportArchivedIdsError(mode, by_id["archivedPatients"], by_id["archivedEvents"])
        if by_id["existing"]:
            raise ImportDuplicateError(by_id["existing"])
        if by_id["restricted"]:
            raise ImportRestrictedError(by_id["restricted"])
        # Решение A: update/replace не удаляют и не перезаписывают существующие
        # медицинские данные. План строится здесь, под блокировкой записи.
        overwrite = plan_overwrite(conn, document, mode, actor, ids)
        if overwrite["blocked"]:
            raise ImportOverwriteError(mode, overwrite["conflicts"], overwrite["reason"])
        plans = dict(overwrite.get("plans", []))
        verified = ids is not None and ids.verified
        if verified and mode in ("add", "replace") and document.patients and not (
                preserve_ids_confirmed and internal_ids_allowed(actor)):
            # Решение C: карточки с прежними id подписанного файла создаются только
            # после отдельного подтверждения разработчика — проверяется последним,
            # чтобы не просить подтверждения операции, которая всё равно отклонена.
            raise ImportPreserveIdsRequired(len(document.patients))
        # Перенос исторических решений ОК — только с отдельным подтверждением этого
        # файла, режима и числа решений (проверяется под той же блокировкой, до записи).
        # Без него файл с решениями не импортируется вовсе — частичного импорта нет.
        decision_count = historical_decision_count(document, mode)
        decision_import = None
        if decision_count:
            if not (historical_decisions and historical_decisions.get("confirmed")
                    and historical_decisions.get("decisions") == decision_count):
                raise ImportHistoricalDecisionsRequired(decision_count)
            decision_import = {"imported_by": actor.id, "imported_at": now_iso(),
                               "import_id": historical_decisions["import_id"]}
        # WP2.4-B: проверки, которые действуют только для импорта, дошедшего до записи
        # (все прежние отказы выше сохраняют приоритет и свои ответы).
        # U9: режим, создающий карточки, не принимает файл с одинаковыми непустыми
        # полисами в нескольких записях — проверка по самому файлу, без поиска в базе.
        if mode in CARD_CREATING_MODES:
            groups = file_policy_duplicate_groups(document)
            if groups:
                raise ImportFilePolicyDuplicateError(groups)
        if before_write is not None:
            before_write()   # U4: повтор ранее обработанного файла (services.import_operations)
        imported_decisions: list[dict] = []

        def layout_of(index: int):
            return ids.structure[index] if verified else None

        def new_patient_id(p: ImportPatientIn) -> str:
            # Подписанный файл сохраняет проверенный id пациента (его отсутствие
            # в базе проверено выше), иначе — новый id, как раньше.
            return p.id if verified else store.new_id()

        try:
            if mode == "replace":
                # Только в пустую базу (проверено планом выше): ничего не удаляется.
                for index, (p, doctor_id) in enumerate(zip(document.patients, resolved)):
                    created_id = new_patient_id(p)
                    _insert_full_patient(conn, p, created_id, doctor_id, actor, layout_of(index),
                                         decision_import, imported_decisions, creation_source=creation_source)
                    created_ids.append(created_id)
                    added += 1
            elif mode == "update":
                for index, p in enumerate(document.patients):
                    plan = plans.get(index)
                    if plan is None:
                        skipped += 1          # карточки с этим id нет — обновлять нечего
                    elif not plan:
                        unchanged += 1        # карточка уже совпадает с файлом
                    else:
                        # Только дописываем новые события в конец существующих этапов.
                        for entry_id, event, event_id in plan:
                            store.insert_event(conn, entry_id, store._next_event_seq(conn, entry_id), event, event_id)
                        store.touch_patient(conn, p.id)
                        updated += 1
                        appended_events += len(plan)
            else:  # add
                for index, (p, doctor_id) in enumerate(zip(document.patients, resolved)):
                    created_id = new_patient_id(p)
                    _insert_full_patient(conn, p, created_id, doctor_id, actor, layout_of(index),
                                         decision_import, imported_decisions, creation_source=creation_source)
                    created_ids.append(created_id)
                    added += 1
        except sqlite3.IntegrityError as exc:
            # Страховка базы: триггер не даёт стереть удалённое событие.
            if store.is_archived_event_delete_error(exc):
                raise ImportArchiveConflictError(mode, 1, 1) from exc
            if verified and "UNIQUE constraint failed" in str(exc):
                # Страховка: совпадение, не найденное проверками выше, — без подробностей.
                raise ImportRestrictedError(1) from exc
            raise
        result = {"added": added, "updated": updated, "skipped": skipped,
                  "unchanged": unchanged, "appendedEvents": appended_events}
        if audit_hook is not None:
            # Обязательная запись аудита — в той же транзакции: её сбой
            # откатывает импорт целиком. Созданные id и события с перенесенными
            # решениями нужны только аудиту (ответ клиенту прежний).
            audit_hook({**result, "createdIds": list(created_ids), "importedDecisions": len(imported_decisions),
                        "decisionEvents": list(imported_decisions)})

    return result


@contextmanager
def _joined_transaction(conn: sqlite3.Connection):
    """Работа внутри транзакции, которую открыл и закроет владелец операции."""
    if not conn.in_transaction:
        raise RuntimeError("commit_import(in_open_transaction=True) needs the owner's open transaction")
    yield conn


def document_event_count(document: ImportDocument) -> int:
    total = 0
    for p in document.patients:
        if p.history:
            total += sum(len(h.events) for h in p.history)
        else:
            total += len(p.events)
    return total
