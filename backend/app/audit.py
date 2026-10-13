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
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from . import config
from .db import now_iso

logger = logging.getLogger("clinic.audit")

# Event types (kept as plain strings so the table stays readable in sqlite3).
LOGIN_SUCCESS = "login_success"
LOGIN_FAILURE = "login_failure"
LOGIN_RATE_LIMITED = "login_rate_limited"
LOGOUT = "logout"
PASSWORD_CHANGED = "password_changed"
PATIENT_CREATED = "patient_created"
PATIENT_UPDATED = "patient_updated"
PATIENT_DIAGNOSIS_CHANGED = "patient_diagnosis_changed"
CONSILIUM_CODE_CHANGED = "consilium_code_changed"
PGI_CODE_CHANGED = "pgi_code_changed"
CONSILIUM_DECISION_RECORDED = "consilium_decision_recorded"
PGI_RESULT_CHANGED = "pgi_result_changed"
PATIENT_DELETED = "patient_deleted"
IMPORT_PREVIEW = "import_preview"
IMPORT_COMMITTED = "import_committed"
EXPORT = "export"
MIGRATION_COMMITTED = "migration_committed"
BACKUP_CREATED = "backup_created"
RESTORE_PERFORMED = "restore_performed"
DB_INTEGRITY_FAILED = "db_integrity_failed"

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
CONSILIUM_FAVORITE_ADDED = "consilium_favorite_added"
CONSILIUM_FAVORITE_REMOVED = "consilium_favorite_removed"
CONSILIUM_REFERRAL_COMPLETED = "consilium_referral_completed"
CONSILIUM_ARCHIVED = "consilium_archived"
CONSILIUM_RESTORED = "consilium_restored"
CONSILIUM_SCHEDULE_GENERATED = "consilium_schedule_generated"
CONSILIUM_SCHEDULE_SLOT_DELETED = "consilium_schedule_slot_deleted"
CONSILIUM_REGISTRY_EXPORTED = "consilium_registry_exported"
CONSILIUM_DEMO_RESET = "consilium_demo_reset"

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
    except Exception:
        # Fail-open, and deliberately without the payload in the message.
        logger.warning("audit write failed for event_type=%s", event_type)


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
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with conn:
        cur = conn.execute("DELETE FROM audit_events WHERE created_at < ?", (cutoff,))
        return cur.rowcount
