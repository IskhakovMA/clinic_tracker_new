"""Реестр возможных дублей пациентов и пометка ошибочных карточек (WP2.4-D).

Цикл: глобальное сканирование → реестр кандидатов → технический разбор
(разработчик) → клиническое решение (заместитель главного врача по онкологии)
→ возможная пометка одной карточки ошибочной → возможное снятие пометки.

Совпадение признаков лишь создает кандидата и не устанавливает, что карточки
принадлежат одному человеку. Ничего не объединяется, не удаляется и не
переносится: пометка — append-only запись, медицинские записи обеих карточек
не меняются. Помеченная карточка исключается из активной работы единым
условием области (policy.ACTIVE_PATIENT_SQL), а изменения ее записей отклоняет
база (триггеры v26).

Права (U1, U3, U7):
- developer — запуск сканирования, реестр, технический разбор;
- deputy_chief_oncology — реестр, клинические решения, пометка карточки без
  клинических зависимостей, снятие пометки, просмотр причин подтверждений C;
- остальные роли — ничего.
Каждая операция перечитывает сотрудника под блокировкой записи. Просмотр
реестра, кандидата и причины подтверждения выполняется только после успешного
обязательного аудита (fail-closed). В аудите — только технические ID, коды и
количества: без ФИО, полисов, диагнозов и текста причин.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from .. import audit, identity, policy
from ..db import now_iso, transaction
from ..phone import normalize_phone

STATES = ("open", "under_review", "needs_clinical_review", "not_duplicate", "resolved")
REASON_ORDER = ("policy", "name_age", "name_diagnosis", "name_nosology", "phone")
REASON_STRENGTH = {"policy": "strong", "name_age": "medium", "name_diagnosis": "medium",
                   "name_nosology": "weak", "phone": "weak"}
STRENGTH_ORDER = ("strong", "medium", "weak")

# Ограничения объема сканирования. Группа одинаковых признаков больше лимита не
# разворачивается в пары (иначе квадратичный рост), а учитывается в отчете.
SCAN_GROUP_LIMIT = max(2, int(os.environ.get("CLINIC_DUPLICATE_SCAN_GROUP_LIMIT", "50")))
SCAN_MAX_PATIENTS = max(1, int(os.environ.get("CLINIC_DUPLICATE_SCAN_MAX_PATIENTS", "200000")))
SCAN_MAX_PAIRS = max(1, int(os.environ.get("CLINIC_DUPLICATE_SCAN_MAX_PAIRS", "50000")))
# Запуск в состоянии running дольше этого срока считается прерванным (сбой процесса).
SCAN_STALE_SECONDS = max(60, int(os.environ.get("CLINIC_DUPLICATE_SCAN_STALE_SECONDS", "900")))

REASON_MAX = 2000
PAGE_LIMIT_MAX = 200

# Клинические зависимости (U10). Код → что означает (для интерфейса, без медицинских деталей).
DEPENDENCY_LABELS = {
    "consilium_records": "Записи консилиума (включая архивные, удаленные и оформленные задним числом)",
    "signed_consilium": "Подписанные решения консилиума",
    "consilium_decision_marks": "Отметки решений ОК в событиях",
    "performed_events": "Фактически выполненные медицинские события",
    "archived_events": "Архивированные медицинские события",
    "stage_transitions": "Переходы между этапами маршрута после первичного",
    "diagnosis_history": "История изменения диагноза",
    "attention_issues": "Вопросы внимания (включая закрытые)",
    "notification_consent": "Согласие на уведомления",
    "sent_notifications": "Отправленные пациенту уведомления",
    "pending_notifications": "Запланированные уведомления пациенту",
}
DEPENDENCY_ORDER = tuple(DEPENDENCY_LABELS)

# Зависимости, которые не являются клинической историей, но тоже не дают пометить
# карточку (решение владельца, интеграция Patient Portal): действующий мобильный
# доступ (linked/active). Отозванный доступ не мешает; при пометке доступ не
# отключается автоматически — сначала его отключает сотрудник, а после пометки
# запись доступа не меняется (триггеры ERRONEOUS_GUARDED_TABLES).
ACCESS_DEPENDENCY_LABELS = {
    "mobile_access": "Действующий мобильный доступ пациента (адрес привязан или доступ активен)",
}
MARK_DEPENDENCY_LABELS = {**DEPENDENCY_LABELS, **ACCESS_DEPENDENCY_LABELS}
MARK_DEPENDENCY_ORDER = tuple(MARK_DEPENDENCY_LABELS)

DISCLAIMER = "Совпадение данных не доказывает, что это один пациент."


# --- ошибки -------------------------------------------------------------

class RegistryForbidden(Exception):
    """Роль не допускается к реестру или к этому действию (403)."""


class CandidateNotFound(Exception):
    """Кандидата нет (404; одинаково для несуществующего и любого чужого ID)."""


class RegistryConflict(Exception):
    """Действие несовместимо с текущим состоянием (409). code — машинный код."""

    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.extra = extra
        super().__init__(message)


class RegistryInvalid(Exception):
    """Некорректный запрос (422)."""


class ClinicalDependenciesFound(RegistryConflict):
    """У выбранной карточки есть значимые клинические зависимости: пометка запрещена,
    кандидат переведен в needs_clinical_review."""

    def __init__(self, codes: list[str]):
        super().__init__(
            "CLINICAL_DEPENDENCIES",
            "У выбранной карточки есть значимые клинические зависимости: пометить ее ошибочной нельзя. "
            "Случай передан на отдельное клиническое рассмотрение.",
            dependencies=codes,
        )


class MobileAccessInForce(RegistryConflict):
    """У выбранной карточки действует мобильный доступ: пометка запрещена, пока
    сотрудник его не отключит. Это не клиническая зависимость — кандидат остается
    в прежнем состоянии, клинического рассмотрения не требуется."""

    def __init__(self, codes: list[str]):
        super().__init__(
            "MOBILE_ACCESS_IN_FORCE",
            "У выбранной карточки действует мобильный доступ пациента: пометить ее ошибочной нельзя. "
            "Сначала отключите мобильный доступ в карточке пациента — при пометке он не отключается автоматически.",
            dependencies=codes,
        )


# --- права --------------------------------------------------------------

def _reload(conn: sqlite3.Connection, actor: policy.CurrentUser, allowed) -> policy.CurrentUser:
    fresh = policy.reload_current_user(conn, actor)
    if fresh is None or not allowed(fresh):
        raise RegistryForbidden()
    return fresh


def _require(actor: policy.CurrentUser, allowed) -> None:
    if not allowed(actor):
        raise RegistryForbidden()


# --- признаки совпадения -------------------------------------------------

def _signals(row) -> dict[str, str]:
    """Ключи признаков карточки (канонические формы WP2.4-C), только для сравнения."""
    keys: dict[str, str] = {}
    canon_policy = identity.canonical_policy(row["policy_number"])
    if canon_policy:
        keys["policy"] = canon_policy
    name = identity.canonical_name(row["name"])
    diagnosis = " ".join((row["diagnosis"] or "").strip().lower().split())
    nosology = (row["nosology"] or "").strip().lower()
    if name and row["age"]:
        keys["name_age"] = f"{name}|{row['age']}"
    if name and diagnosis:
        keys["name_diagnosis"] = f"{name}|{diagnosis}"
    if name and nosology:
        keys["name_nosology"] = f"{name}|{nosology}"
    phone = normalize_phone(row["phone"])
    if phone and len(phone) >= 10:
        keys["phone"] = phone
    return keys


def _ordered_codes(codes) -> list[str]:
    present = set(codes)          # один раз: codes может быть генератором
    return [c for c in REASON_ORDER if c in present]


def _strength(codes) -> str:
    present = {REASON_STRENGTH[c] for c in codes}
    return next(s for s in STRENGTH_ORDER if s in present)


def pair_signals(a, b) -> list[str]:
    sa, sb = _signals(a), _signals(b)
    return _ordered_codes(k for k in sa if sb.get(k) == sa[k])


# --- сканирование --------------------------------------------------------

class ScanLimitExceeded(Exception):
    def __init__(self, code: str, report: dict):
        self.code = code
        self.report = report
        super().__init__(code)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _finish_scan(conn: sqlite3.Connection, scan_id: str, status: str, event: str, actor: policy.CurrentUser,
                 *, error_code: str | None = None, report: dict | None = None) -> None:
    """Завершить запуск неуспешно (failed/interrupted) отдельной транзакцией.
    Аудит fail-open: запуск уже зафиксирован, его исход — служебная запись."""
    with transaction(conn):
        cur = conn.execute(
            "UPDATE patient_duplicate_scans SET status = ?, finished_at = ?, error_code = ?, report = ? "
            "WHERE id = ? AND status = 'running'",
            (status, now_iso(), error_code, json.dumps(report) if report is not None else None, scan_id))
        if cur.rowcount:
            audit.record(conn, event, result=audit.RESULT_ERROR,
                         detail=f"scan_id={scan_id} code={error_code or '-'}", **audit.actor_ctx(actor))


def _interrupt_stale(conn: sqlite3.Connection, actor: policy.CurrentUser, now: datetime) -> None:
    """Запуск, застрявший в running (сбой процесса во время сканирования), —
    interrupted. Его результат никогда не публиковался: публикация и перевод в
    completed — одна транзакция."""
    for row in conn.execute("SELECT id, started_at FROM patient_duplicate_scans WHERE status = 'running'"):
        if now - _parse_time(row["started_at"]) >= timedelta(seconds=SCAN_STALE_SECONDS):
            conn.execute("UPDATE patient_duplicate_scans SET status = 'interrupted', finished_at = ?, "
                         "error_code = 'stale_running_scan' WHERE id = ?", (now_iso(), row["id"]))
            audit.record_required_or_fail(conn, audit.DUPLICATE_SCAN_INTERRUPTED,
                                          detail=f"scan_id={row['id']}", **audit.actor_ctx(actor))


def _compute_pairs(rows) -> tuple[dict[tuple[str, str], set[str]], dict]:
    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in rows:
        for kind, key in _signals(row).items():
            buckets[(kind, key)].append(row["id"])
    pairs: dict[tuple[str, str], set[str]] = defaultdict(set)
    oversized: Counter = Counter()
    groups: Counter = Counter()
    for (kind, _key), ids in buckets.items():
        unique = sorted(set(ids))
        if len(unique) < 2:
            continue
        if len(unique) > SCAN_GROUP_LIMIT:
            oversized[kind] += 1
            continue
        groups[kind] += 1
        for i, first in enumerate(unique):
            for second in unique[i + 1:]:
                pairs[(first, second)].add(kind)
            if len(pairs) > SCAN_MAX_PAIRS:
                raise ScanLimitExceeded("too_many_pairs", {"pairs_limit": SCAN_MAX_PAIRS})
    report = {
        "groups_by_reason": {k: groups[k] for k in REASON_ORDER if groups[k]},
        "oversized_groups_by_reason": {k: oversized[k] for k in REASON_ORDER if oversized[k]},
        "group_limit": SCAN_GROUP_LIMIT,
    }
    return pairs, report


def run_scan(conn: sqlite3.Connection, actor: policy.CurrentUser) -> dict:
    """Глобальное сканирование (только developer).

    1. Отдельной транзакцией регистрируется запуск running (не более одного
       одновременно — частичный уникальный индекс) и обязательный аудит.
    2. Активные карточки читаются в одной читающей транзакции — согласованный
       снимок WAL. Пары строятся группировкой по ключам признаков, без попарного
       сравнения всех карточек.
    3. Результат публикуется одной транзакцией вместе с переводом запуска в
       completed: частичный результат не виден никогда. Повторно найденная пара
       не создает новой записи, а ее клиническое решение не сбрасывается.
    Медицинские таблицы только читаются."""
    _require(actor, policy.can_scan_duplicates)
    scan_id = uuid.uuid4().hex
    with transaction(conn):
        fresh = _reload(conn, actor, policy.can_scan_duplicates)
        _interrupt_stale(conn, fresh, datetime.now(timezone.utc))
        if conn.execute("SELECT 1 FROM patient_duplicate_scans WHERE status = 'running'").fetchone():
            raise RegistryConflict("SCAN_ALREADY_RUNNING", "Сканирование уже выполняется. Повторите позже.")
        try:
            conn.execute("INSERT INTO patient_duplicate_scans (id, status, started_by_user_id, started_at) "
                         "VALUES (?, 'running', ?, ?)", (scan_id, fresh.id, now_iso()))
        except sqlite3.IntegrityError:
            raise RegistryConflict("SCAN_ALREADY_RUNNING", "Сканирование уже выполняется. Повторите позже.")
        audit.record_required_or_fail(conn, audit.DUPLICATE_SCAN_STARTED, detail=f"scan_id={scan_id}",
                                      **audit.actor_ctx(fresh))

    try:
        conn.execute("BEGIN")          # читающая транзакция: один снимок для всех выборок
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM patients WHERE {policy.ACTIVE_PATIENT_SQL}").fetchone()[0]
            if count > SCAN_MAX_PATIENTS:
                raise ScanLimitExceeded("too_many_patients", {"patients": count, "patients_limit": SCAN_MAX_PATIENTS})
            rows = conn.execute(
                "SELECT id, name, age, phone, policy_number, diagnosis, nosology FROM patients "
                f"WHERE {policy.ACTIVE_PATIENT_SQL}").fetchall()
        finally:
            conn.commit()
        pairs, report = _compute_pairs(rows)
    except ScanLimitExceeded as e:
        _finish_scan(conn, scan_id, "failed", audit.DUPLICATE_SCAN_FAILED, actor, error_code=e.code, report=e.report)
        raise RegistryConflict("SCAN_LIMIT_EXCEEDED", "Сканирование остановлено: превышен допустимый объем. "
                               "Результат не опубликован.", reason=e.code)
    except Exception:
        _finish_scan(conn, scan_id, "failed", audit.DUPLICATE_SCAN_FAILED, actor, error_code="internal_error")
        raise

    try:
        return _publish(conn, actor, scan_id, len(rows), pairs, report)
    except (RegistryForbidden, RegistryConflict):
        _finish_scan(conn, scan_id, "failed", audit.DUPLICATE_SCAN_FAILED, actor, error_code="not_published")
        raise
    except Exception:
        _finish_scan(conn, scan_id, "failed", audit.DUPLICATE_SCAN_FAILED, actor, error_code="internal_error")
        raise


def _publish(conn, actor, scan_id, scanned, pairs, report) -> dict:
    now = now_iso()
    with transaction(conn):
        fresh = _reload(conn, actor, policy.can_scan_duplicates)
        status = conn.execute("SELECT status FROM patient_duplicate_scans WHERE id = ?", (scan_id,)).fetchone()
        if status is None or status["status"] != "running":
            raise RegistryConflict("SCAN_INTERRUPTED", "Запуск сканирования был прерван. Результат не опубликован.")
        existing = {(r["patient_a_id"], r["patient_b_id"]): r for r in conn.execute(
            "SELECT id, patient_a_id, patient_b_id, reason_codes, state FROM patient_duplicate_candidates")}
        new = updated = 0
        for (a, b), kinds in sorted(pairs.items()):
            codes = _ordered_codes(kinds)
            codes_json = json.dumps(codes)
            row = existing.get((a, b))
            if row is None:
                conn.execute(
                    """INSERT INTO patient_duplicate_candidates
                           (id, patient_a_id, patient_b_id, reason_codes, strength, first_scan_id, last_scan_id,
                            detected_at, last_seen_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (uuid.uuid4().hex, a, b, codes_json, _strength(codes), scan_id, scan_id, now, now))
                new += 1
            elif row["reason_codes"] != codes_json:
                # Признаки изменились: состояние (в том числе клиническое решение) не
                # сбрасывается; если решение уже было, отмечается необходимость
                # повторной оценки. Версия растет — решение по устаревшей версии отклоняется.
                conn.execute(
                    """UPDATE patient_duplicate_candidates
                       SET reason_codes = ?, strength = ?, last_scan_id = ?, last_seen_at = ?,
                           signals_changed_at = CASE WHEN state = 'open' THEN signals_changed_at ELSE ? END,
                           version = version + 1
                       WHERE id = ?""",
                    (codes_json, _strength(codes), scan_id, now, now, row["id"]))
                updated += 1
            else:
                conn.execute("UPDATE patient_duplicate_candidates SET last_scan_id = ?, last_seen_at = ? WHERE id = ?",
                             (scan_id, now, row["id"]))
        summary = {"patients_scanned": scanned, "pairs_found": len(pairs), "pairs_new": new, "pairs_updated": updated}
        conn.execute(
            """UPDATE patient_duplicate_scans
               SET status = 'completed', finished_at = ?, patients_scanned = ?, pairs_found = ?, pairs_new = ?,
                   pairs_updated = ?, report = ?
               WHERE id = ? AND status = 'running'""",
            (now_iso(), scanned, len(pairs), new, updated, json.dumps(report), scan_id))
        audit.record_required_or_fail(
            conn, audit.DUPLICATE_SCAN_COMPLETED,
            detail=f"scan_id={scan_id} patients={scanned} pairs={len(pairs)} new={new} updated={updated}",
            **audit.actor_ctx(fresh))
        return {"scanId": scan_id, "status": "completed", "patientsScanned": scanned, "pairsFound": len(pairs),
                "pairsNew": new, "pairsUpdated": updated, "report": report}


def _scan_dict(row) -> dict:
    return {"id": row["id"], "status": row["status"], "startedAt": row["started_at"], "finishedAt": row["finished_at"],
            "startedBy": row["started_by_name"], "patientsScanned": row["patients_scanned"],
            "pairsFound": row["pairs_found"], "pairsNew": row["pairs_new"], "pairsUpdated": row["pairs_updated"],
            "errorCode": row["error_code"], "report": json.loads(row["report"]) if row["report"] else None}


_SCAN_SELECT = ("SELECT s.*, u.full_name AS started_by_name FROM patient_duplicate_scans s "
                "LEFT JOIN users u ON u.id = s.started_by_user_id")


def _latest_completed_scan(conn) -> str | None:
    row = conn.execute("SELECT id FROM patient_duplicate_scans WHERE status = 'completed' "
                       "ORDER BY finished_at DESC, id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


# --- представление карточек ---------------------------------------------

def _mask_policy(value) -> str:
    canon = identity.canonical_policy(value)
    return f"…{canon[-4:]}" if len(canon) >= 4 else ("…" if canon else "")


def _card(conn: sqlite3.Connection, patient_id: str) -> dict:
    """Карточка в клиническом представлении реестра. Медицинская история не
    раскрывается: только сводка и коды зависимостей с количествами."""
    row = conn.execute(
        """SELECT p.*, u.full_name AS treating_doctor_name,
                  (SELECT group_concat(d.name, ', ') FROM user_departments ud JOIN departments d ON d.id = ud.department_id
                   WHERE ud.user_id = p.treating_doctor_user_id) AS departments
           FROM patients p LEFT JOIN users u ON u.id = p.treating_doctor_user_id WHERE p.id = ?""",
        (patient_id,)).fetchone()
    if row is None:
        return {"id": patient_id, "exists": False}
    state = conn.execute("SELECT mark_id, marked_at FROM patient_erroneous_state WHERE patient_id = ?",
                         (patient_id,)).fetchone()
    deps = {**clinical_dependencies(conn, patient_id), **access_dependencies(conn, patient_id)}
    return {
        "id": row["id"], "exists": True, "name": row["name"], "age": row["age"] or 0,
        "maskedPolicy": _mask_policy(row["policy_number"]), "diagnosis": row["diagnosis"] or "",
        "nosology": row["nosology"] or "", "stage": row["stage"], "treatingDoctorName": row["treating_doctor_name"] or "",
        "departments": row["departments"] or "", "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        "creationSource": row["creation_source"],
        "erroneous": state is not None, "markId": state["mark_id"] if state else None,
        "markedAt": state["marked_at"] if state else None,
        "dependencies": [{"code": c, "label": MARK_DEPENDENCY_LABELS[c], "count": deps[c],
                          "kind": "access" if c in ACCESS_DEPENDENCY_LABELS else "clinical"}
                         for c in MARK_DEPENDENCY_ORDER if c in deps],
    }


def clinical_dependencies(conn: sqlite3.Connection, patient_id: str) -> dict[str, int]:
    """Значимые клинические зависимости карточки (U10) — код → количество.

    Пустой результат допускает пометку: только первичная запись маршрута и
    плановые события без фактов. Запланированные уведомления — техническая
    защита: пометка не должна оставлять очередь отправки."""
    def one(sql: str) -> int:
        return conn.execute(sql, (patient_id,)).fetchone()[0]

    events = ("FROM events e JOIN history_entries h ON h.id = e.history_entry_id WHERE h.patient_id = ?")
    counts = {
        "consilium_records": one("SELECT COUNT(*) FROM oncology_consilium_appointments WHERE patient_id = ?"),
        "signed_consilium": one("SELECT COUNT(*) FROM oncology_consilium_appointments "
                                "WHERE patient_id = ? AND (status = 'signed' OR signed_at IS NOT NULL)"),
        "consilium_decision_marks": one(f"SELECT COUNT(*) {events} AND e.consilium_decision IS NOT NULL"),
        "performed_events": one(f"SELECT COUNT(*) {events} AND (e.actual_date IS NOT NULL AND e.actual_date != '')"),
        "archived_events": one(f"SELECT COUNT(*) {events} AND e.deleted_at IS NOT NULL"),
        "stage_transitions": max(0, one("SELECT COUNT(*) FROM history_entries WHERE patient_id = ?") - 1),
        "diagnosis_history": one("SELECT COUNT(*) FROM patient_diagnosis_history WHERE patient_id = ?"),
        "attention_issues": one("SELECT COUNT(*) FROM patient_attention_issues WHERE patient_id = ?"),
        "notification_consent": one(
            "SELECT COUNT(*) FROM patient_notification_preferences WHERE patient_id = ? AND "
            "(consent_status != 'not_recorded' OR sms_enabled = 1 OR consent_recorded_at IS NOT NULL)"),
        "sent_notifications": one("SELECT COUNT(*) FROM notifications WHERE patient_id = ? "
                                  "AND (status = 'sent' OR sent_at IS NOT NULL)"),
        "pending_notifications": one("SELECT COUNT(*) FROM notifications WHERE patient_id = ? "
                                     "AND status IN ('pending', 'processing')"),
    }
    return {k: v for k, v in counts.items() if v}


def access_dependencies(conn: sqlite3.Connection, patient_id: str) -> dict[str, int]:
    """Неклинические зависимости, которые тоже запрещают пометку: действующий
    мобильный доступ (linked/active). Код → количество; пустой — не мешает."""
    count = conn.execute(
        "SELECT COUNT(*) FROM patient_portal_access WHERE patient_id = ? AND status IN ('linked', 'active')",
        (patient_id,)).fetchone()[0]
    return {"mobile_access": count} if count else {}


def _patient_rows(conn, ids):
    marks = ",".join("?" * len(ids))
    return {r["id"]: r for r in conn.execute(
        f"SELECT id, name, age, phone, policy_number, diagnosis, nosology, updated_at FROM patients WHERE id IN ({marks})",  # nosec B608
        list(ids))}


def _candidate_row(conn, candidate_id: str):
    if not isinstance(candidate_id, str) or not 1 <= len(candidate_id) <= 64:
        raise CandidateNotFound()
    row = conn.execute("SELECT * FROM patient_duplicate_candidates WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise CandidateNotFound()
    return row


def _candidate_summary(row, latest_scan: str | None, names: dict) -> dict:
    return {
        "id": row["id"], "state": row["state"], "strength": row["strength"],
        "reasonCodes": json.loads(row["reason_codes"]), "detectedAt": row["detected_at"],
        "lastSeenAt": row["last_seen_at"], "version": row["version"],
        "notFoundInLastScan": bool(latest_scan and row["last_scan_id"] != latest_scan),
        "signalsChangedAt": row["signals_changed_at"],
        "patients": [names.get(row["patient_a_id"], {"id": row["patient_a_id"], "exists": False}),
                     names.get(row["patient_b_id"], {"id": row["patient_b_id"], "exists": False})],
    }


# --- реестр: просмотр ----------------------------------------------------

def list_candidates(conn: sqlite3.Connection, actor: policy.CurrentUser, *, state: str | None = None,
                    strength: str | None = None, reason: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    _require(actor, policy.can_view_duplicate_registry)
    limit = max(1, min(int(limit), PAGE_LIMIT_MAX))
    offset = max(0, int(offset))
    where, params = ["1 = 1"], []
    if state:
        where.append("c.state = ?")
        params.append(state)
    if strength:
        where.append("c.strength = ?")
        params.append(strength)
    if reason:
        where.append("EXISTS (SELECT 1 FROM json_each(c.reason_codes) j WHERE j.value = ?)")
        params.append(reason)
    cond = " AND ".join(where)
    with transaction(conn):
        fresh = _reload(conn, actor, policy.can_view_duplicate_registry)
        total = conn.execute(f"SELECT COUNT(*) FROM patient_duplicate_candidates c WHERE {cond}", params).fetchone()[0]  # nosec B608
        rows = conn.execute(
            f"""SELECT c.* FROM patient_duplicate_candidates c WHERE {cond}
                ORDER BY CASE c.strength WHEN 'strong' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                         c.detected_at DESC, c.id LIMIT ? OFFSET ?""",  # nosec B608 - fixed fragments
            [*params, limit, offset]).fetchall()
        ids = {pid for r in rows for pid in (r["patient_a_id"], r["patient_b_id"])}
        names = {}
        if ids:
            erroneous = {r["patient_id"] for r in conn.execute("SELECT patient_id FROM patient_erroneous_state")}
            for pid, p in _patient_rows(conn, sorted(ids)).items():
                names[pid] = {"id": pid, "exists": True, "name": p["name"], "age": p["age"] or 0,
                              "erroneous": pid in erroneous}
        latest = _latest_completed_scan(conn)
        counts = {r["state"]: r["c"] for r in conn.execute(
            "SELECT state, COUNT(*) AS c FROM patient_duplicate_candidates GROUP BY state")}
        last_scan = conn.execute(f"{_SCAN_SELECT} ORDER BY s.started_at DESC, s.id DESC LIMIT 1").fetchone()
        audit.record_required_or_fail(conn, audit.DUPLICATE_REGISTRY_VIEWED,
                                      detail=f"rows={len(rows)} total={total}", **audit.actor_ctx(fresh))
        return {
            "items": [_candidate_summary(r, latest, names) for r in rows], "total": total, "limit": limit,
            "offset": offset, "stateCounts": {s: counts.get(s, 0) for s in STATES},
            "lastScan": _scan_dict(last_scan) if last_scan else None, "disclaimer": DISCLAIMER,
        }


def list_scans(conn: sqlite3.Connection, actor: policy.CurrentUser, limit: int = 20) -> dict:
    _require(actor, policy.can_view_duplicate_registry)
    limit = max(1, min(int(limit), 100))
    with transaction(conn):
        fresh = _reload(conn, actor, policy.can_view_duplicate_registry)
        rows = conn.execute(f"{_SCAN_SELECT} ORDER BY s.started_at DESC, s.id DESC LIMIT ?", (limit,)).fetchall()
        audit.record_required_or_fail(conn, audit.DUPLICATE_REGISTRY_VIEWED, detail=f"scans={len(rows)}",
                                      **audit.actor_ctx(fresh))
        return {"items": [_scan_dict(r) for r in rows]}


def _review_dict(row) -> dict:
    return {"id": row["id"], "action": row["action"], "state": row["state"], "reason": row["reason"],
            "dependencies": json.loads(row["dependency_codes"]) if row["dependency_codes"] else [],
            "patientId": row["patient_id"], "by": row["by_name"], "byRole": row["by_role"], "at": row["reviewed_at"]}


def _mark_dict(row) -> dict:
    return {"id": row["id"], "patientId": row["patient_id"], "candidateId": row["candidate_id"],
            "action": row["action"], "revertsMarkId": row["reverts_mark_id"], "reason": row["reason"],
            "by": row["by_name"], "byRole": row["by_role"], "at": row["at"]}


def _confirmations_for_pair(conn, a: str, b: str) -> list[dict]:
    """Подтверждения возможных дублей WP2.4-C, связанные с парой: новая или
    измененная карточка пары либо карточка пары среди подтвержденных совпадений.
    Без причин: причина читается отдельной аудируемой операцией."""
    rows = conn.execute(
        """SELECT c.id, c.operation, c.patient_id, c.matched_patient_ids, c.created_at, c.import_id,
                  u.full_name AS by_name
           FROM patient_duplicate_confirmations c LEFT JOIN users u ON u.id = c.created_by_user_id
           WHERE c.patient_id IN (?, ?)
              OR EXISTS (SELECT 1 FROM json_each(c.matched_patient_ids) j WHERE j.value IN (?, ?))
           ORDER BY c.created_at, c.id""", (a, b, a, b)).fetchall()
    return [{"id": r["id"], "operation": r["operation"], "patientId": r["patient_id"],
             "matchedPatientIds": json.loads(r["matched_patient_ids"]), "createdAt": r["created_at"],
             "by": r["by_name"] or "", "importId": r["import_id"]} for r in rows]


def get_candidate(conn: sqlite3.Connection, actor: policy.CurrentUser, candidate_id: str) -> dict:
    _require(actor, policy.can_view_duplicate_registry)
    with transaction(conn):
        fresh = _reload(conn, actor, policy.can_view_duplicate_registry)
        row = _candidate_row(conn, candidate_id)
        a, b = row["patient_a_id"], row["patient_b_id"]
        cards = [_card(conn, a), _card(conn, b)]
        current = _patient_rows(conn, [a, b])
        current_codes = pair_signals(current[a], current[b]) if a in current and b in current else []
        stored_codes = json.loads(row["reason_codes"])
        reviews = conn.execute(
            """SELECT r.*, u.full_name AS by_name, u.role_id AS by_role FROM patient_duplicate_reviews r
               LEFT JOIN users u ON u.id = r.reviewed_by_user_id WHERE r.candidate_id = ? ORDER BY r.seq""",
            (candidate_id,)).fetchall()
        marks = conn.execute(
            """SELECT m.*, u.full_name AS by_name, u.role_id AS by_role FROM patient_erroneous_marks m
               LEFT JOIN users u ON u.id = m.by_user_id WHERE m.patient_id IN (?, ?) ORDER BY m.seq""",
            (a, b)).fetchall()
        changed_since_scan = any(
            c.get("exists") and c.get("updatedAt") and c["updatedAt"] > row["last_seen_at"] for c in cards)
        latest = _latest_completed_scan(conn)
        result = {
            **_candidate_summary(row, latest, {}),
            "patients": cards,
            "currentReasonCodes": current_codes,
            "signalsOutdated": current_codes != stored_codes,
            "cardsChangedSinceScan": changed_since_scan,
            "reviews": [_review_dict(r) for r in reviews],
            "marks": [_mark_dict(m) for m in marks],
            "disclaimer": DISCLAIMER,
            "allowedActions": _allowed_actions(fresh, row, cards, signals_current=current_codes == stored_codes),
        }
        if policy.can_read_duplicate_confirmation_reasons(fresh):
            result["confirmations"] = _confirmations_for_pair(conn, a, b)
        audit.record_required_or_fail(conn, audit.DUPLICATE_CANDIDATE_VIEWED, detail=f"candidate_id={candidate_id}",
                                      **audit.actor_ctx(fresh))
        return result


def _allowed_actions(user: policy.CurrentUser, row, cards: list[dict], *, signals_current: bool) -> dict:
    """Подсказка интерфейсу. Сервер проверяет каждое действие заново."""
    state = row["state"]
    both_exist = all(c.get("exists") for c in cards)
    none_marked = not any(c.get("erroneous") for c in cards)
    markable = [c["id"] for c in cards if c.get("exists") and not c.get("erroneous") and not c.get("dependencies")]
    return {
        "technicalStart": policy.can_technically_review_duplicates(user) and state == "open",
        "technicalNote": policy.can_technically_review_duplicates(user),
        "notDuplicate": policy.can_decide_duplicates(user) and state in ("open", "under_review", "needs_clinical_review"),
        "needsClinicalReview": policy.can_decide_duplicates(user) and state in ("open", "under_review"),
        "reopen": policy.can_decide_duplicates(user) and state == "not_duplicate",
        "markPatientIds": (markable if policy.can_mark_erroneous(user) and state in ("open", "under_review")
                           and both_exist and none_marked and signals_current else []),
        "unmarkPatientIds": ([c["id"] for c in cards if c.get("erroneous")] if policy.can_unmark_erroneous(user) else []),
    }


def list_erroneous(conn: sqlite3.Connection, actor: policy.CurrentUser) -> dict:
    """Специальное представление помеченных карточек (только роли реестра)."""
    _require(actor, policy.can_view_duplicate_registry)
    with transaction(conn):
        fresh = _reload(conn, actor, policy.can_view_duplicate_registry)
        rows = conn.execute(
            """SELECT s.patient_id, s.mark_id, s.marked_at, m.candidate_id, m.reason, u.full_name AS by_name
               FROM patient_erroneous_state s JOIN patient_erroneous_marks m ON m.id = s.mark_id
               LEFT JOIN users u ON u.id = m.by_user_id ORDER BY s.marked_at DESC, s.patient_id""").fetchall()
        items = [{**_card(conn, r["patient_id"]), "candidateId": r["candidate_id"], "markReason": r["reason"],
                  "markedBy": r["by_name"] or ""} for r in rows]
        audit.record_required_or_fail(conn, audit.DUPLICATE_REGISTRY_VIEWED, detail=f"erroneous={len(items)}",
                                      **audit.actor_ctx(fresh))
        return {"items": items}


# --- разбор и решения ----------------------------------------------------

def _reason(value, *, required: bool = True) -> str | None:
    text = "\n".join(line.rstrip() for line in str(value or "").replace("\r\n", "\n").split("\n")).strip()
    if not text:
        if required:
            raise RegistryInvalid("Укажите причину.")
        return None
    if len(text) > REASON_MAX:
        raise RegistryInvalid(f"Причина длиннее {REASON_MAX} символов.")
    if any((ord(ch) < 32 and ch != "\n") or ord(ch) == 127 for ch in text):
        raise RegistryInvalid("Причина содержит недопустимые символы.")
    return text


def _insert_review(conn, candidate_id: str, action: str, state: str, user: policy.CurrentUser, *,
                   reason: str | None = None, dependency_codes: list[str] | None = None,
                   patient_id: str | None = None) -> str:
    review_id = uuid.uuid4().hex
    try:
        conn.execute(
            """INSERT INTO patient_duplicate_reviews
                   (id, candidate_id, action, state, reason, dependency_codes, patient_id, reviewed_by_user_id, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (review_id, candidate_id, action, state, reason,
             json.dumps(dependency_codes) if dependency_codes is not None else None, patient_id, user.id, now_iso()))
    except sqlite3.IntegrityError as e:
        if "transition_not_allowed" in str(e):
            raise RegistryConflict("TRANSITION_NOT_ALLOWED", "Действие недоступно в текущем состоянии кандидата.")
        raise
    return review_id


def _check_version(row, expected_version) -> None:
    if expected_version is not None and int(expected_version) != row["version"]:
        raise RegistryConflict("CANDIDATE_CHANGED", "Кандидат изменился. Обновите карточку кандидата и повторите.",
                               state=row["state"], version=row["version"])


def technical_review(conn: sqlite3.Connection, actor: policy.CurrentUser, candidate_id: str, action: str,
                     comment, expected_version=None) -> dict:
    """Технический разбор (только developer): начать проверку или записать
    технический комментарий. Клинических решений не принимает."""
    _require(actor, policy.can_technically_review_duplicates)
    if action not in ("start", "note"):
        raise RegistryInvalid("Неизвестное действие.")
    text = _reason(comment, required=action == "note")
    with transaction(conn):
        fresh = _reload(conn, actor, policy.can_technically_review_duplicates)
        row = _candidate_row(conn, candidate_id)
        _check_version(row, expected_version)
        if action == "start":
            if row["state"] != "open":
                raise RegistryConflict("TRANSITION_NOT_ALLOWED", "Техническую проверку можно начать только для нового кандидата.")
            _insert_review(conn, candidate_id, "technical_review_started", "under_review", fresh, reason=text)
        else:
            _insert_review(conn, candidate_id, "technical_note", row["state"], fresh, reason=text)
        audit.record_required_or_fail(conn, audit.DUPLICATE_TECHNICAL_REVIEW,
                                      detail=f"candidate_id={candidate_id} action={action}", **audit.actor_ctx(fresh))
        return _state_after(conn, candidate_id)


_DECISIONS = {
    "not_duplicate": ("not_duplicate", "not_duplicate"),
    "needs_clinical_review": ("needs_clinical_review", "needs_clinical_review"),
    "reopen": ("reopened", "open"),
}


def clinical_decision(conn: sqlite3.Connection, actor: policy.CurrentUser, candidate_id: str, decision: str,
                      reason, expected_version) -> dict:
    """Клиническое решение по кандидату (только deputy_chief_oncology): «не дубль»,
    передача на отдельное клиническое рассмотрение или повторное открытие."""
    _require(actor, policy.can_decide_duplicates)
    if decision not in _DECISIONS:
        raise RegistryInvalid("Неизвестное решение.")
    text = _reason(reason)
    action, target = _DECISIONS[decision]
    with transaction(conn):
        fresh = _reload(conn, actor, policy.can_decide_duplicates)
        row = _candidate_row(conn, candidate_id)
        _check_version(row, expected_version)
        _insert_review(conn, candidate_id, action, target, fresh, reason=text)
        audit.record_required_or_fail(conn, audit.DUPLICATE_CLINICAL_DECISION,
                                      detail=f"candidate_id={candidate_id} decision={decision}", **audit.actor_ctx(fresh))
        return _state_after(conn, candidate_id)


def _state_after(conn, candidate_id: str) -> dict:
    row = conn.execute("SELECT state, version FROM patient_duplicate_candidates WHERE id = ?", (candidate_id,)).fetchone()
    return {"id": candidate_id, "state": row["state"], "version": row["version"]}


def mark_erroneous(conn: sqlite3.Connection, actor: policy.CurrentUser, candidate_id: str, patient_id: str,
                   reason, expected_version) -> dict:
    """Пометить выбранную карточку пары ошибочной (только deputy_chief_oncology).

    BEGIN IMMEDIATE → перечитать сотрудника → проверить кандидата, версию,
    карточку и вторую карточку пары → заново вычислить признаки совпадения пары
    по текущим данным и сверить их с зарегистрированными → заново проверить
    клинические зависимости и действующий мобильный доступ → append-only mark и запись разбора → обязательный
    аудит → COMMIT.
    Если признаки пары изменились после сканирования (совпадение исчезло или
    стало другим), пометка отклоняется (409 SIGNALS_CHANGED) без каких-либо
    записей: решение принимается только по актуальным признакам, после
    повторного сканирования.
    При клинических зависимостях пометка отклоняется, а кандидат переводится в
    needs_clinical_review (с аудитом) — это фиксируется и при отказе. Если мешает
    только действующий мобильный доступ, отказ (MOBILE_ACCESS_IN_FORCE) пишется в
    аудит, а кандидат остается в прежнем состоянии: после отключения доступа
    сотрудником пометку можно повторить. Доступ при пометке не отключается."""
    _require(actor, policy.can_mark_erroneous)
    text = _reason(reason)
    if not isinstance(patient_id, str) or not patient_id:
        raise RegistryInvalid("Выберите карточку.")
    refusal: Exception | None = None
    with transaction(conn):
        fresh = _reload(conn, actor, policy.can_mark_erroneous)
        row = _candidate_row(conn, candidate_id)
        if patient_id not in (row["patient_a_id"], row["patient_b_id"]):
            raise RegistryInvalid("Выбранная карточка не относится к этому кандидату.")
        _check_version(row, expected_version)
        if row["state"] not in ("open", "under_review"):
            raise RegistryConflict("TRANSITION_NOT_ALLOWED",
                                   "Пометка недоступна в текущем состоянии кандидата.", state=row["state"])
        other = row["patient_b_id"] if patient_id == row["patient_a_id"] else row["patient_a_id"]
        present = {r["id"] for r in conn.execute("SELECT id FROM patients WHERE id IN (?, ?)", (patient_id, other))}
        if patient_id not in present or other not in present:
            raise RegistryConflict("PAIR_INCOMPLETE", "Одной из карточек пары больше нет. Пометка невозможна.")
        erroneous = {r["patient_id"] for r in conn.execute(
            "SELECT patient_id FROM patient_erroneous_state WHERE patient_id IN (?, ?)", (patient_id, other))}
        if patient_id in erroneous:
            raise RegistryConflict("ALREADY_ERRONEOUS", "Карточка уже помечена ошибочной.")
        if other in erroneous:
            raise RegistryConflict("OTHER_ERRONEOUS", "Вторая карточка пары уже помечена ошибочной.")
        stored_codes = json.loads(row["reason_codes"])
        current = _patient_rows(conn, [patient_id, other])
        current_codes = pair_signals(current[patient_id], current[other])
        if current_codes != stored_codes:
            raise RegistryConflict(
                "SIGNALS_CHANGED",
                "Признаки совпадения этой пары изменились после сканирования. Пометка невозможна: "
                "запустите повторное сканирование и оцените пару по актуальным данным.",
                reasonCodes=stored_codes, currentReasonCodes=current_codes)
        deps = clinical_dependencies(conn, patient_id)
        access = access_dependencies(conn, patient_id)
        if deps:
            codes = [c for c in MARK_DEPENDENCY_ORDER if c in deps or c in access]
            _insert_review(conn, candidate_id, "needs_clinical_review", "needs_clinical_review", fresh,
                           dependency_codes=codes, patient_id=patient_id)
            audit.record_required_or_fail(
                conn, audit.PATIENT_MARK_REFUSED, patient_id=patient_id,
                detail=f"candidate_id={candidate_id} dependencies={','.join(codes)}", **audit.actor_ctx(fresh))
            refusal = ClinicalDependenciesFound(codes)
        elif access:
            # Только мобильный доступ: кандидат не меняется (это не клиническая
            # зависимость), отказ фиксируется обязательным аудитом.
            codes = [c for c in MARK_DEPENDENCY_ORDER if c in access]
            audit.record_required_or_fail(
                conn, audit.PATIENT_MARK_REFUSED, patient_id=patient_id,
                detail=f"candidate_id={candidate_id} dependencies={','.join(codes)}", **audit.actor_ctx(fresh))
            refusal = MobileAccessInForce(codes)
        else:
            mark_id = uuid.uuid4().hex
            try:
                conn.execute(
                    """INSERT INTO patient_erroneous_marks (id, patient_id, candidate_id, action, reason, by_user_id, at)
                       VALUES (?, ?, ?, 'mark', ?, ?, ?)""",
                    (mark_id, patient_id, candidate_id, text, fresh.id, now_iso()))
            except sqlite3.IntegrityError as e:
                if "patient_already_erroneous" in str(e):
                    raise RegistryConflict("ALREADY_ERRONEOUS", "Карточка уже помечена ошибочной.")
                if "patient_mobile_access_in_force" in str(e):
                    raise MobileAccessInForce(["mobile_access"])
                raise
            _insert_review(conn, candidate_id, "marked_erroneous", "resolved", fresh, reason=text, patient_id=patient_id)
            audit.record_required_or_fail(
                conn, audit.PATIENT_MARKED_ERRONEOUS, patient_id=patient_id,
                detail=f"candidate_id={candidate_id} mark_id={mark_id}", **audit.actor_ctx(fresh))
            result = {**_state_after(conn, candidate_id), "markId": mark_id, "patientId": patient_id}
    if refusal is not None:
        raise refusal
    return result


def unmark_erroneous(conn: sqlite3.Connection, actor: policy.CurrentUser, patient_id: str, mark_id: str, reason) -> dict:
    """Снять пометку (только deputy_chief_oncology): append-only unmark со ссылкой на
    действующую пометку; кандидат пометки возвращается в open. Повтор с той же
    (уже снятой) пометкой — 409 без новой записи."""
    _require(actor, policy.can_unmark_erroneous)
    text = _reason(reason)
    if not isinstance(patient_id, str) or not patient_id or not isinstance(mark_id, str) or not mark_id:
        raise RegistryInvalid("Не указана пометка.")
    with transaction(conn):
        fresh = _reload(conn, actor, policy.can_unmark_erroneous)
        state = conn.execute("SELECT mark_id FROM patient_erroneous_state WHERE patient_id = ?", (patient_id,)).fetchone()
        if state is None:
            raise RegistryConflict("NOT_ERRONEOUS", "Карточка не помечена ошибочной.")
        if state["mark_id"] != mark_id:
            raise RegistryConflict("MARK_CHANGED", "Пометка карточки изменилась. Обновите данные и повторите.")
        mark = conn.execute("SELECT candidate_id FROM patient_erroneous_marks WHERE id = ?", (mark_id,)).fetchone()
        unmark_id = uuid.uuid4().hex
        try:
            conn.execute(
                """INSERT INTO patient_erroneous_marks
                       (id, patient_id, candidate_id, action, reverts_mark_id, reason, by_user_id, at)
                   VALUES (?, ?, ?, 'unmark', ?, ?, ?, ?)""",
                (unmark_id, patient_id, mark["candidate_id"], mark_id, text, fresh.id, now_iso()))
        except sqlite3.IntegrityError as e:
            if "patient_not_erroneous" in str(e):
                raise RegistryConflict("NOT_ERRONEOUS", "Карточка не помечена ошибочной.")
            raise
        cand = conn.execute("SELECT state FROM patient_duplicate_candidates WHERE id = ?", (mark["candidate_id"],)).fetchone()
        if cand is not None and cand["state"] == "resolved":
            _insert_review(conn, mark["candidate_id"], "mark_reverted", "open", fresh, reason=text, patient_id=patient_id)
        audit.record_required_or_fail(
            conn, audit.PATIENT_UNMARKED_ERRONEOUS, patient_id=patient_id,
            detail=f"candidate_id={mark['candidate_id']} mark_id={mark_id} unmark_id={unmark_id}", **audit.actor_ctx(fresh))
        return {"patientId": patient_id, "unmarkId": unmark_id, "candidateId": mark["candidate_id"],
                **({"candidateState": _state_after(conn, mark["candidate_id"])["state"]} if cand is not None else {})}


# --- причины подтверждений WP2.4-C ---------------------------------------

def read_confirmation_reason(conn: sqlite3.Connection, actor: policy.CurrentUser, candidate_id: str,
                             confirmation_id: str) -> dict:
    """Причина подтверждения возможного дубля WP2.4-C — только заместителю главного
    врача, только для подтверждения, связанного с парой рассматриваемого
    кандидата, и только после успешного обязательного аудита чтения."""
    _require(actor, policy.can_read_duplicate_confirmation_reasons)
    with transaction(conn):
        fresh = _reload(conn, actor, policy.can_read_duplicate_confirmation_reasons)
        row = _candidate_row(conn, candidate_id)
        related = {c["id"] for c in _confirmations_for_pair(conn, row["patient_a_id"], row["patient_b_id"])}
        if not isinstance(confirmation_id, str) or confirmation_id not in related:
            raise CandidateNotFound()
        reason = conn.execute("SELECT reason FROM patient_duplicate_confirmations WHERE id = ?",
                              (confirmation_id,)).fetchone()["reason"]
        audit.record_required_or_fail(
            conn, audit.DUPLICATE_CONFIRMATION_REASON_VIEWED,
            detail=f"candidate_id={candidate_id} confirmation_id={confirmation_id}", **audit.actor_ctx(fresh))
        return {"confirmationId": confirmation_id, "reason": reason}
