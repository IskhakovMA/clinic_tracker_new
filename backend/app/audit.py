"""Anonymised server-side audit log.

What goes in: *that* an operation happened, its outcome, and the opaque
identifiers needed to correlate events. What never goes in: passwords,
password hashes, session tokens, CSRF tokens, cookies, patient names,
phones, diagnoses, complaints, doctors, notes, or import/export payloads.

`actor` is a short, non-reversible fingerprint of the session token hash —
enough to tell "these events came from one session", useless for recovering
the session itself. Patient ids are server-generated UUIDs and carry no
personal information, so they are recorded to make an action traceable to a
record without naming the person.

Failure policy: **fail-open**. A broken audit insert must never block or
break a clinical operation, and must never surface data in an error path —
the exception is swallowed and reported to the server log without payload.
Rationale: this is a single-operator local tool where losing an audit row
is far less harmful than refusing to save a patient's treatment date. If
this ever becomes multi-user or externally accessible, revisit and make
security-relevant events fail-closed.

Exception: changes to the medical route are audited fail-closed by
record_required() in the same transaction as the change — the change is
not kept without its audit row. This covers deleting and restoring a patient
event (WP2.1) and, since WP2.2-A, stage transitions (with the OK decision of
the legacy contract), event field edits, adding an event, stage notes and
checklists, and the consilium review start, signature and return for revision.

Replays of an Idempotency-Key (patient_create_replayed, import_replayed,
migration_replayed) and a confirmed repeat of a processed import file
(import_repeat_confirmed) are fail-closed too (WP2.4-B): no replay result is
returned without its audit row. Keys themselves are never written to audit.

The duplicate registry (WP2.4-D) writes every scan, registry and candidate view,
technical review, clinical decision, mark, unmark, refused mark and every read of
a WP2.4-C confirmation reason fail-closed: protected data is returned only after
its audit row is written. Details carry technical ids, codes and counts only.

A confirmed possible duplicate (patient_duplicate_confirmed, WP2.4-C) is
written fail-closed in the transaction of the confirmed operation; the reason
itself is kept in patient_duplicate_confirmations and never in this log.

Creating a patient card is fail-closed since WP2.4-A: services.patient_create
writes exactly one `patient_created` row (current actor, patient id,
treating doctor id — no names, policy number or medical fields) inside the
creation transaction; if the row cannot be written, the card, its route and
its events are rolled back and the request gets 503. Other operations keep
their own policy above; record() itself stays fail-open.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from . import config, request_security
from .db import AUDIT_DELETE_FLOOR_DAYS, now_iso

logger = logging.getLogger("clinic.audit")

# Event types (kept as plain strings so the table stays readable in sqlite3).
LOGIN_SUCCESS = "login_success"
LOGIN_FAILURE = "login_failure"
LOGIN_RATE_LIMITED = "login_rate_limited"
LOGOUT = "logout"
PASSWORD_CHANGED = "password_changed"
PATIENT_CREATED = "patient_created"
# WP2.4-B: повтор создания с тем же Idempotency-Key — обязательная запись (fail-closed).
PATIENT_CREATE_REPLAYED = "patient_create_replayed"
# WP2.4-C: осознанно подтвержденный возможный дубль — обязательная запись в транзакции
# операции (операция и число совпадений; причина хранится отдельно и сюда не пишется).
PATIENT_DUPLICATE_CONFIRMED = "patient_duplicate_confirmed"
# WP2.4-D: реестр возможных дублей. Все записи — обязательные (fail-closed), кроме
# исхода неуспешного сканирования; детали — только технические ID, коды и количества.
DUPLICATE_SCAN_STARTED = "duplicate_scan_started"
DUPLICATE_SCAN_COMPLETED = "duplicate_scan_completed"
DUPLICATE_SCAN_FAILED = "duplicate_scan_failed"
DUPLICATE_SCAN_INTERRUPTED = "duplicate_scan_interrupted"
DUPLICATE_REGISTRY_VIEWED = "duplicate_registry_viewed"
DUPLICATE_CANDIDATE_VIEWED = "duplicate_candidate_viewed"
DUPLICATE_TECHNICAL_REVIEW = "duplicate_technical_review"
DUPLICATE_CLINICAL_DECISION = "duplicate_clinical_decision"
DUPLICATE_CONFIRMATION_REASON_VIEWED = "duplicate_confirmation_reason_viewed"
PATIENT_MARKED_ERRONEOUS = "patient_marked_erroneous"
PATIENT_UNMARKED_ERRONEOUS = "patient_unmarked_erroneous"
PATIENT_MARK_REFUSED = "patient_mark_refused"
PATIENT_UPDATED = "patient_updated"
PATIENT_DIAGNOSIS_CHANGED = "patient_diagnosis_changed"
CONSILIUM_CODE_CHANGED = "consilium_code_changed"
PGI_CODE_CHANGED = "pgi_code_changed"
CONSILIUM_DECISION_RECORDED = "consilium_decision_recorded"
# Перенос исторического решения ОК из файла импорта: по одной записи на каждое решение.
CONSILIUM_DECISION_IMPORTED = "consilium_decision_imported"
PGI_RESULT_CHANGED = "pgi_result_changed"
PATIENT_DELETED = "patient_deleted"
PATIENT_EVENT_DELETED = "patient_event_deleted"
PATIENT_EVENT_RESTORED = "patient_event_restored"
IMPORT_PREVIEW = "import_preview"
IMPORT_COMMITTED = "import_committed"
IMPORT_IDS_PRESERVED = "import_internal_ids_preserved"
IMPORT_FUTURE_FACTS = "import_future_facts"   # FIX1-P3: факт из будущего в исторической выгрузке
IMPORT_UNVERIFIED_FACTS = "import_unverified_facts"   # FIX2: перенесённый неподтверждённый факт
EXPORT = "export"
MIGRATION_COMMITTED = "migration_committed"
# WP2.4-B: повтор импорта или переноса по ключу и осознанный повтор ранее
# обработанного файла — обязательные записи (fail-closed), без ключей и данных пациентов.
IMPORT_REPLAYED = "import_replayed"
MIGRATION_REPLAYED = "migration_replayed"
IMPORT_REPEAT_CONFIRMED = "import_repeat_confirmed"
BACKUP_CREATED = "backup_created"
RESTORE_PERFORMED = "restore_performed"
DB_INTEGRITY_FAILED = "db_integrity_failed"
# WP-B: явное обновление схемы командой migrate-db (check-db в журнал не пишет — он только читает).
SCHEMA_MIGRATED = "schema_migrated"

# RBAC / администрирование
USER_CREATED = "user_created"
USER_UPDATED = "user_updated"
ROLE_CHANGED = "role_changed"
USER_BLOCKED = "user_blocked"
USER_DEACTIVATED = "user_deactivated"
USER_RESTORED = "user_restored"
PASSWORD_RESET = "password_reset"
SESSIONS_TERMINATED = "sessions_terminated"
DEPARTMENT_CREATED = "department_created"
DEPARTMENT_UPDATED = "department_updated"
HEAD_ASSIGNED = "head_assigned"
MEMBERSHIP_CHANGED = "membership_changed"
TREATING_DOCTOR_CHANGED = "treating_doctor_changed"
ACCESS_DENIED = "access_denied"

# Notifications MVP
NOTIFICATION_CONSENT_GRANTED = "notification_consent_granted"
NOTIFICATION_CONSENT_REVOKED = "notification_consent_revoked"
NOTIFICATION_CREATED = "notification_created"
NOTIFICATION_CANCELLED = "notification_cancelled"
NOTIFICATION_RETRY_REQUESTED = "notification_retry_requested"
NOTIFICATION_PROCESSING_STARTED = "notification_processing_started"
NOTIFICATION_SENT = "notification_sent"
NOTIFICATION_FAILED = "notification_failed"
NOTIFICATION_SKIPPED = "notification_skipped"
NOTIFICATION_TEMPLATE_CREATED = "notification_template_created"
NOTIFICATION_TEMPLATE_UPDATED = "notification_template_updated"

# Analytics
ANALYTICS_REPORT_EXPORTED = "analytics_report_exported"

# Мобильный доступ пациента (Patient Portal identity).
# Детали событий содержат только статус и признак наличия адреса —
# сам адрес, телефон, ФИО и медицинские данные в журнал не попадают.
PORTAL_ACCESS_LINKED = "portal_access_linked"
PORTAL_ACCESS_EMAIL_CHANGED = "portal_access_email_changed"
PORTAL_ACCESS_REVOKED = "portal_access_revoked"
# Неудачная привязка: адрес уже занят другой карточкой. Пишется, чтобы
# перебор адресов не оставался невидимым; значение адреса не попадает.
PORTAL_ACCESS_CONFLICT = "portal_access_conflict"

# Internal employee notification center
INTERNAL_NOTIFICATION_READ = "internal_notification_read"
INTERNAL_NOTIFICATION_READ_ALL = "internal_notification_read_all"

# Patient attention workflow
ATTENTION_ISSUE_CREATED = "attention_issue_created"
ATTENTION_ISSUE_CLOSED = "attention_issue_closed"

# Oncology consilium scheduling
CONSILIUM_APPOINTMENT_CREATED = "consilium_appointment_created"
CONSILIUM_APPOINTMENT_UPDATED = "consilium_appointment_updated"
CONSILIUM_APPOINTMENT_DELETED = "consilium_appointment_deleted"
CONSILIUM_REVIEW_STARTED = "consilium_review_started"
CONSILIUM_SIGNED = "consilium_signed"
CONSILIUM_RETURNED = "consilium_returned_for_revision"
CONSILIUM_RESUBMITTED = "consilium_resubmitted"
# WP2.3-B3: оформление задним числом. detail — только id, слот и reason_id, без текста причины.
CONSILIUM_BACKDATED_SLOT_CREATED = "consilium_backdated_slot_created"
CONSILIUM_BACKDATED_APPOINTMENT_CREATED = "consilium_backdated_appointment_created"
CONSILIUM_BACKDATING_REASON_VIEWED = "consilium_backdating_reason_viewed"
CONSILIUM_FAVORITE_ADDED = "consilium_favorite_added"
CONSILIUM_FAVORITE_REMOVED = "consilium_favorite_removed"
CONSILIUM_REFERRAL_COMPLETED = "consilium_referral_completed"
CONSILIUM_ARCHIVED = "consilium_archived"
CONSILIUM_RESTORED = "consilium_restored"
CONSILIUM_SCHEDULE_GENERATED = "consilium_schedule_generated"
CONSILIUM_SCHEDULE_SLOT_DELETED = "consilium_schedule_slot_deleted"
CONSILIUM_REGISTRY_EXPORTED = "consilium_registry_exported"
CONSILIUM_DEMO_RESET = "consilium_demo_reset"

# Не удаляются audit-prune: история удалений медицинских событий (тот же перечень
# закреплён в базе триггером, db.AUDIT_RETAINED_EVENT_TYPES).
RETAINED_EVENT_TYPES = frozenset({PATIENT_EVENT_DELETED, PATIENT_EVENT_RESTORED})

RESULT_OK = "ok"
RESULT_DENIED = "denied"
RESULT_ERROR = "error"


def actor_from_token_hash(token_hash: str | None) -> str | None:
    """Short irreversible fingerprint of an (already hashed) session token."""
    if not token_hash:
        return None
    return hashlib.sha256(("audit-actor:" + token_hash).encode("utf-8")).hexdigest()[:16]


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def record(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    result: str = RESULT_OK,
    actor: str | None = None,
    patient_id: str | None = None,
    request_id: str | None = None,
    detail: str | None = None,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
    actor_department_id: str | None = None,
    target_user_id: str | None = None,
    target_department_id: str | None = None,
) -> None:
    """Append one audit row. `detail` must be non-sensitive metadata only
    (counts, modes, field names) — never values of patient fields.
    RBAC-поля содержат только идентификаторы и код роли — без ФИО."""
    state = request_security.current.get()
    if state is not None and result == RESULT_DENIED and state.resource.startswith('/api/') and not state.resource.startswith('/api/auth/'):
        # The outer coordinator owns this denial, atomically with its manifest.
        state.denial = {'event_type': event_type, 'detail': detail, 'patient_id': patient_id,
                        'actor_user_id': actor_user_id, 'actor_role': actor_role,
                        'actor_department_id': actor_department_id,
                        'target_user_id': target_user_id, 'target_department_id': target_department_id}
        return
    try:
        with conn:
            conn.execute(
                """INSERT INTO audit_events (created_at, event_type, actor, patient_id, result,
                                             request_id, detail, actor_user_id, actor_role,
                                             actor_department_id, target_user_id, target_department_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now_iso(), event_type, actor, patient_id, result, request_id, detail,
                 actor_user_id, actor_role, actor_department_id, target_user_id, target_department_id),
            )
        state = request_security.current.get()
        if state is not None and result == RESULT_DENIED:
            state.denied_recorded = True
    except Exception:
        # Fail-open, and deliberately without the payload in the message.
        logger.warning("audit write failed for event_type=%s", event_type)


def record_required(
    conn: sqlite3.Connection,
    event_type: str,
    *,
    patient_id: str | None = None,
    detail: str | None = None,
    actor_user_id: str | None = None,
    actor_role: str | None = None,
    actor_department_id: str | None = None,
    target_user_id: str | None = None,
    result: str = RESULT_OK,
    target_department_id: str | None = None,
) -> None:
    """Fail-closed audit row inside the caller's open transaction.

    Used where the audit row is part of the operation itself (deleting and
    restoring patient events): it is written by the same connection before
    the commit, and any error propagates so the caller rolls the change back.
    Unlike record() it neither commits nor swallows errors. The same content
    rules apply: identifiers and flags only, never patient field values."""
    if not conn.in_transaction:
        raise RuntimeError("record_required must run inside the operation's transaction")
    conn.execute(
        """INSERT INTO audit_events (created_at, event_type, actor, patient_id, result,
                                     request_id, detail, actor_user_id, actor_role,
                                     actor_department_id, target_user_id, target_department_id)
           VALUES (?, ?, NULL, ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
        (now_iso(), event_type, patient_id, result, detail,
         actor_user_id, actor_role, actor_department_id, target_user_id, target_department_id),
    )


class AuditWriteFailed(Exception):
    """The required audit row was not written; the caller's transaction is rolled back."""


def record_required_or_fail(conn: sqlite3.Connection, event_type: str, **kwargs) -> None:
    """record_required() for audit hooks: a database error while writing the
    audit row becomes AuditWriteFailed, which the route turns into 503 after
    the transaction has been rolled back."""
    try:
        record_required(conn, event_type, **kwargs)
    except sqlite3.Error as exc:
        raise AuditWriteFailed() from exc


def record_committed_or_fail(conn: sqlite3.Connection, event_type: str, *, release_objects=(), **kwargs) -> None:
    """Обязательная запись аудита для операции, которую нельзя включить в транзакцию
    базы (выдача файла экспорта): запись фиксируется отдельной транзакцией ДО того,
    как данные покидают сервер. Сбой — AuditWriteFailed, и вызывающий не выдаёт
    данные (503). Для HTTP-экспорта координатор откладывает эту запись до
    сериализации и фиксирует ее атомарно с manifest перед выдачей.
    Вызов вне HTTP сохраняет прежнюю отдельную транзакцию.
    Модель «аудит, затем выдача»: журнал может показать экспорт,
    ответ на который не дошёл до клиента, но никогда — выданные данные без записи."""
    if conn.in_transaction:
        raise RuntimeError("record_committed_or_fail must not run inside another transaction")
    state = request_security.current.get()
    if state is not None and event_type in {'export', 'analytics_report_exported', 'consilium_registry_exported'}:
        # HTTP coordinator owns the atomic event+manifest transaction, after
        # serialization and a fresh authorization check, before any body release.
        if state.export_event is not None:
            raise RuntimeError("one export event per response")
        state.export_event = {'event_type': event_type, **kwargs}
        state.export_objects = tuple((obj['type'], str(obj['id'])) for obj in release_objects)
        state.export_recorded = True
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        record_required(conn, event_type, **kwargs)
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise AuditWriteFailed() from exc


def actor_ctx(user) -> dict:
    """Kwargs для record(): контекст актора из CurrentUser (id и роль,
    первое отделение как основное — без персональных данных)."""
    return {
        "actor_user_id": user.id,
        "actor_role": user.role_id,
        "actor_department_id": user.department_ids[0] if user.department_ids else None,
    }


def list_events(conn: sqlite3.Connection, limit: int = 50, offset: int = 0) -> list[dict]:
    limit = max(1, min(int(limit), config.AUDIT_PAGE_LIMIT_MAX))
    offset = max(0, int(offset))
    rows = conn.execute(
        """SELECT id, created_at, event_type, actor, patient_id, result, request_id, detail,
                  actor_user_id, actor_role, actor_department_id, target_user_id, target_department_id
           FROM audit_events ORDER BY id DESC LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def count_events(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM audit_events").fetchone()["c"]


def prune(conn: sqlite3.Connection, retention_days: int | None = None) -> int:
    """Delete events older than the retention window. Returns rows removed."""
    days = config.AUDIT_RETENTION_DAYS if retention_days is None else int(retention_days)
    # WP-C (AUD-13): триггер trg_audit_events_delete_retention не даёт удалить запись
    # моложе AUDIT_DELETE_FLOOR_DAYS; меньший срок — явный отказ, а не тихая подмена.
    if days < AUDIT_DELETE_FLOOR_DAYS:
        raise ValueError(f"audit retention must be at least {AUDIT_DELETE_FLOOR_DAYS} days")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    # Удаление и восстановление событий медицинской истории — единственный
    # след прошлых удалений (восстановление очищает deleted_at/deleted_by),
    # поэтому эти записи хранятся столько же, сколько сами события.
    kept = tuple(RETAINED_EVENT_TYPES) + ('medical_read_release', 'medical_read_denied', 'mfa_pre_auth_created', 'mfa_challenge_claimed', 'mfa_assertion_denied', 'mfa_login_success', 'mfa_factor_registered_pending', 'mfa_factor_revoked', 'mfa_browser_session_revoked', 'mfa_registration_parent_denied')
    with conn:
        cur = conn.execute(
            f"DELETE FROM audit_events WHERE created_at < ? AND NOT EXISTS (SELECT 1 FROM read_access_manifests WHERE audit_event_id = audit_events.id) AND event_type NOT IN ({','.join('?' * len(kept))})",  # nosec B608
            (cutoff, *kept),
        )
        return cur.rowcount
