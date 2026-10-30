"""Notification preferences, queue generation and worker logic.

The MVP is intentionally network-free: the only provider is MockProvider. SMS
texts are rendered from approved templates at processing time and are not
stored in the queue or audit log.
"""
from __future__ import annotations

import re
import hashlib
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .. import audit, clock, config, policy, store
from ..db import now_iso, transaction
from ..models import TERMINAL_STAGES
from ..notifications.mock_provider import provider_from_config
from ..notifications.provider import NotificationContext, NotificationProvider
from ..phone import masked_phone, normalize_phone, phone_sendable

CONSENT_STATUSES = ("not_recorded", "granted", "revoked")
QUEUE_STATUSES = ("pending", "processing", "sent", "failed", "cancelled", "skipped")
SKIP_REASONS = ("no_consent", "invalid_phone", "missing_phone", "patient_unavailable", "template_disabled")
ALLOWED_PLACEHOLDERS = frozenset({"date", "time"})
SAFE_TEMPLATE_BODY = "Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией."
MANUAL_SOURCE_ID = "manual"


def utcnow() -> datetime:
    return clock.now_moscow().astimezone(timezone.utc)


def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(timezone.utc)
    except OverflowError:
        return None


def event_dt(date_value: str | None, time_value: str | None) -> datetime | None:
    # Правило прежней реализации для события без времени: 09:00, теперь явно МСК.
    # Некорректное непустое время не заменяется выдуманным временем.
    try:
        moment = clock.slot_moment(date_value, time_value or "09:00")
        return moment.astimezone(timezone.utc) if moment is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def safe_detail(**parts) -> str:
    return " ".join(f"{key}={value}" for key, value in parts.items() if value not in (None, ""))


def notification_idempotency_key(
    patient_id: str, source_type: str, source_id: str, template_code: str, scheduled_at: str
) -> str:
    raw = "\x1f".join((patient_id, source_type, source_id, template_code, scheduled_at))
    return "notif_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_known_provider() -> None:
    if config.NOTIFICATION_PROVIDER != "mock":
        raise RuntimeError("unknown notification provider")


MAX_TEMPLATE_LENGTH = 500
MAX_RENDERED_LENGTH = 1000
_TEMPLATE_TOKEN = re.compile(r"\{\{|\}\}|\{(?:date|time)\}|[^{}]+")


def _template_tokens(body: str) -> list[str]:
    tokens = []
    end = 0
    for match in _TEMPLATE_TOKEN.finditer(body):
        if match.start() != end:
            raise ValueError("template contains unsupported placeholders")
        tokens.append(match.group())
        end = match.end()
    if end != len(body):
        raise ValueError("template contains unsupported placeholders")
    return tokens


def template_placeholders(body: str) -> set[str]:
    return {token[1:-1] for token in _template_tokens(validate_template_body(body)) if token in ("{date}", "{time}")}


def validate_template_body(body: str) -> str:
    if not isinstance(body, str):
        raise ValueError("template body is required")
    if len(body) > MAX_TEMPLATE_LENGTH:
        raise ValueError("template body too long")
    if not body.strip():
        raise ValueError("template body is required")
    body = " ".join(body.strip().split())
    _template_tokens(body)
    return body


def render_template(body: str, *, scheduled_at: str, event_at: datetime | None = None) -> str:
    scheduled = event_at or parse_dt(scheduled_at)
    if scheduled is None:
        raise ValueError("invalid scheduled_at")
    try:
        local = scheduled.astimezone(clock.institution_tz())
    except (ValueError, OverflowError):
        raise ValueError("invalid scheduled_at") from None
    values = {"{date}": local.date().isoformat(), "{time}": local.strftime("%H:%M"), "{{": "{", "}}": "}"}
    result = "".join(values.get(token, token) for token in _template_tokens(validate_template_body(body)))
    if len(result) > MAX_RENDERED_LENGTH:
        raise ValueError("rendered template too long")
    return result


def get_preference(conn: sqlite3.Connection, patient_id: str) -> dict:
    row = conn.execute(
        """SELECT p.*, u.full_name AS consent_recorded_by_name
           FROM patient_notification_preferences p
           LEFT JOIN users u ON u.id = p.consent_recorded_by_user_id
           WHERE p.patient_id = ?""",
        (patient_id,),
    ).fetchone()
    if row is None:
        return {
            "patient_id": patient_id,
            "sms_enabled": False,
            "consent_status": "not_recorded",
            "consent_recorded_at": None,
            "consent_recorded_by_user_id": None,
            "consent_recorded_by_name": None,
            "consent_revoked_at": None,
            "updated_at": None,
        }
    data = dict(row)
    data["sms_enabled"] = bool(data["sms_enabled"])
    return data


def set_preference(
    conn: sqlite3.Connection,
    *,
    patient_id: str,
    sms_enabled: bool,
    consent_status: str,
    user: policy.CurrentUser,
) -> dict:
    if consent_status not in CONSENT_STATUSES:
        raise ValueError("unknown consent status")
    now = now_iso()
    old = get_preference(conn, patient_id)
    recorded_at = now if consent_status == "granted" else old["consent_recorded_at"]
    recorded_by = user.id if consent_status == "granted" else old["consent_recorded_by_user_id"]
    revoked_at = now if consent_status == "revoked" else None
    event = audit.NOTIFICATION_CONSENT_REVOKED if consent_status == "revoked" else audit.NOTIFICATION_CONSENT_GRANTED
    # WP-C (AUD-06): согласие и его запись в журнал — одна транзакция; без записи
    # аудита согласие не меняется (AuditWriteFailed → 503).
    with transaction(conn):
        conn.execute(
            """INSERT INTO patient_notification_preferences
               (patient_id, sms_enabled, consent_status, consent_recorded_at,
                consent_recorded_by_user_id, consent_revoked_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(patient_id) DO UPDATE SET
                   sms_enabled = excluded.sms_enabled,
                   consent_status = excluded.consent_status,
                   consent_recorded_at = excluded.consent_recorded_at,
                   consent_recorded_by_user_id = excluded.consent_recorded_by_user_id,
                   consent_revoked_at = excluded.consent_revoked_at,
                   updated_at = excluded.updated_at""",
            (patient_id, int(bool(sms_enabled)), consent_status, recorded_at, recorded_by, revoked_at, now),
        )
        if consent_status == "revoked":
            conn.execute(
                """UPDATE notifications
                   SET status = 'cancelled', cancelled_at = ?, cancelled_by_user_id = ?,
                       error_code = 'no_consent', updated_at = ?
                   WHERE patient_id = ? AND status = 'pending'""",
                (now, user.id, now, patient_id),
            )
        audit.record_required_or_fail(conn, event, patient_id=patient_id, detail="channel=sms",
                                      **audit.actor_ctx(user))
    return get_preference(conn, patient_id)


def can_queue_for_patient(conn: sqlite3.Connection, patient_id: str) -> tuple[bool, str]:
    pref = get_preference(conn, patient_id)
    if pref["consent_status"] != "granted" or not pref["sms_enabled"]:
        return False, "no_consent"
    patient = conn.execute("SELECT phone FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if patient is None:
        return False, "patient_unavailable"
    return phone_sendable(patient["phone"])


def get_template(conn: sqlite3.Connection, code: str) -> dict | None:
    row = conn.execute("SELECT * FROM notification_templates WHERE code = ?", (code,)).fetchone()
    return dict(row) if row else None


def list_templates(conn: sqlite3.Connection, *, include_disabled: bool = False) -> list[dict]:
    where = "" if include_disabled else "WHERE enabled = 1"
    rows = conn.execute(
        f"""SELECT id, code, name, channel, body, enabled, version, created_at, updated_at
            FROM notification_templates {where} ORDER BY code"""
    ).fetchall()
    return [
        {**dict(row), "enabled": bool(row["enabled"]), "placeholders": sorted(ALLOWED_PLACEHOLDERS)}
        for row in rows
    ]


def update_template(
    conn: sqlite3.Connection,
    *,
    code: str,
    body: str | None = None,
    enabled: bool | None = None,
    user: policy.CurrentUser,
) -> dict:
    current = get_template(conn, code)
    if current is None:
        raise KeyError("template not found")
    next_body = validate_template_body(body if body is not None else current["body"])
    next_enabled = int(bool(enabled)) if enabled is not None else int(current["enabled"])
    next_version = int(current["version"]) + (1 if next_body != current["body"] else 0)
    now = now_iso()
    with conn:
        conn.execute(
            """UPDATE notification_templates
               SET body = ?, enabled = ?, version = ?, updated_at = ? WHERE code = ?""",
            (next_body, next_enabled, next_version, now, code),
        )
    audit.record(
        conn,
        audit.NOTIFICATION_TEMPLATE_UPDATED,
        detail=safe_detail(template_code=code, status="enabled" if next_enabled else "disabled"),
        **audit.actor_ctx(user),
    )
    return get_template(conn, code) or {}


def notification_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "patient_id": row["patient_id"],
        "patient_name": row["patient_name"],
        "treating_doctor_name": row["treating_doctor_name"] or row["doctor"] or "Не назначен",
        "department_names": [x for x in (row["department_names"] or "").split(",") if x],
        "phone_masked": masked_phone(row["phone"]),
        "channel": row["channel"],
        "template_code": row["template_code"],
        "template_name": row["template_name"] or row["template_code"],
        "template_version": row["template_version"],
        "source_type": row["source_type"],
        "source_id": row["source_id"],
        "scheduled_at": row["scheduled_at"],
        "status": row["status"],
        "attempt_count": row["attempt_count"],
        "max_attempts": row["max_attempts"],
        "next_attempt_at": row["next_attempt_at"],
        "provider_status": row["provider_status"],
        "error_code": row["error_code"],
        "error_message_safe": row["error_message_safe"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "sent_at": row["sent_at"],
        "cancelled_at": row["cancelled_at"],
    }


def scoped_notifications_base(user: policy.CurrentUser) -> tuple[str, list[Any]]:
    cond, params = policy.patient_scope_filter(user)
    return cond, params


def list_notifications(
    conn: sqlite3.Connection,
    user: policy.CurrentUser,
    *,
    status: str | None = None,
    channel: str | None = None,
    patient_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    template_code: str | None = None,
    source_type: str | None = None,
    search: str | None = None,
    treating_doctor: str | None = None,
    department: str | None = None,
    sort: str = "scheduled_desc",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    cond, params = scoped_notifications_base(user)
    where = [cond]
    values: list[Any] = list(params)
    if status:
        where.append("notifications.status = ?")
        values.append(status)
    if channel:
        where.append("notifications.channel = ?")
        values.append(channel)
    if patient_id:
        where.append("notifications.patient_id = ?")
        values.append(patient_id)
    if date_from:
        where.append("notifications.scheduled_at >= ?")
        values.append(date_from)
    if date_to:
        where.append("notifications.scheduled_at <= ?")
        values.append(date_to)
    if template_code:
        where.append("notifications.template_code = ?")
        values.append(template_code)
    if source_type:
        where.append("notifications.source_type = ?")
        values.append(source_type)
    if search:
        where.append("patients.name LIKE ?")
        values.append(f"%{search}%")
    if treating_doctor:
        where.append("COALESCE(u.full_name, patients.doctor, '') LIKE ?")
        values.append(f"%{treating_doctor}%")
    if department:
        where.append("EXISTS (SELECT 1 FROM user_departments ud2 JOIN departments d2 ON d2.id = ud2.department_id AND d2.status = 'active' WHERE ud2.user_id = patients.treating_doctor_user_id AND d2.name = ?)")
        values.append(department)
    where_sql = " AND ".join(f"({x})" for x in where)
    order = {
        "scheduled_asc": "notifications.scheduled_at ASC, notifications.id ASC",
        "status": "notifications.status ASC, notifications.scheduled_at ASC, notifications.id ASC",
    }.get(sort, "notifications.scheduled_at DESC, notifications.id DESC")
    join_sql = """FROM notifications
        JOIN patients ON patients.id = notifications.patient_id
        LEFT JOIN users u ON u.id = patients.treating_doctor_user_id
        LEFT JOIN user_departments ud ON ud.user_id = patients.treating_doctor_user_id
        LEFT JOIN departments d ON d.id = ud.department_id AND d.status = 'active'
        LEFT JOIN notification_templates t ON t.code = notifications.template_code"""
    total = conn.execute(f"SELECT COUNT(DISTINCT notifications.id) AS c {join_sql} WHERE {where_sql}", values).fetchone()["c"]
    rows = conn.execute(
        f"""SELECT notifications.*, patients.name AS patient_name, patients.phone, patients.doctor,
                   u.full_name AS treating_doctor_name, t.name AS template_name,
                   GROUP_CONCAT(DISTINCT d.name) AS department_names
            {join_sql}
            WHERE {where_sql}
            GROUP BY notifications.id
            ORDER BY {order}
            LIMIT ? OFFSET ?""",
        [*values, limit, offset],
    ).fetchall()
    return {
        "notifications": [notification_row_to_dict(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def summary(conn: sqlite3.Connection, user: policy.CurrentUser) -> dict:
    cond, params = policy.patient_scope_filter(user)
    counts = {status: 0 for status in QUEUE_STATUSES}
    rows = conn.execute(
        f"""SELECT notifications.status, COUNT(*) AS c
            FROM notifications JOIN patients ON patients.id = notifications.patient_id
            WHERE {cond} GROUP BY notifications.status""",
        params,
    ).fetchall()
    for row in rows:
        counts[row["status"]] = row["c"]
    now_local = utcnow().astimezone(clock.institution_tz())
    start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    bounds = (start.astimezone(timezone.utc).isoformat(), (start + timedelta(days=1)).astimezone(timezone.utc).isoformat())
    due_today = conn.execute(
        f"""SELECT COUNT(*) AS c FROM notifications JOIN patients ON patients.id = notifications.patient_id
            WHERE {cond} AND notifications.status = 'pending'
              AND notifications.scheduled_at >= ? AND notifications.scheduled_at < ?""",
        [*params, *bounds],
    ).fetchone()["c"]
    no_consent = conn.execute(
        f"""SELECT COUNT(*) AS c
            FROM patients LEFT JOIN patient_notification_preferences pref ON pref.patient_id = patients.id
            WHERE {cond} AND COALESCE(pref.consent_status, 'not_recorded') != 'granted'""",
        params,
    ).fetchone()["c"]
    invalid = 0
    phone_rows = conn.execute(f"SELECT id, phone FROM patients WHERE {cond}", params).fetchall()
    from ..request_security import aggregate_patients
    aggregate_patients((row['id'] for row in phone_rows), resources=('/api/notifications/summary',))
    for row in phone_rows:
        ok, reason = phone_sendable(row["phone"])
        if not ok and reason == "invalid_phone":
            invalid += 1
    return {
        "pending_count": counts["pending"],
        "sent_count": counts["sent"],
        "failed_count": counts["failed"],
        "cancelled_count": counts["cancelled"],
        "skipped_count": counts["skipped"],
        "due_today": due_today,
        "patients_without_consent": no_consent,
        "patients_with_invalid_phone": invalid,
        "provider": config.NOTIFICATION_PROVIDER,
        "mock_mode": config.NOTIFICATION_MOCK_MODE,
    }


def create_notification(
    conn: sqlite3.Connection,
    *,
    patient_id: str,
    template_code: str,
    scheduled_at: str,
    source_type: str,
    source_id: str,
    created_by_user_id: str | None,
    audit_user: policy.CurrentUser | None = None,
    event_at: datetime | None = None,
) -> tuple[dict | None, str | None]:
    ensure_known_provider()
    scheduled = parse_dt(scheduled_at)
    if scheduled is None:
        raise ValueError("scheduled_at must be ISO datetime")
    template = get_template(conn, template_code)
    if template is None or not template["enabled"]:
        return None, "template_disabled"
    try:
        validate_template_body(template["body"])
    except ValueError:
        return None, "template_invalid"
    ok, reason = can_queue_for_patient(conn, patient_id)
    if not ok:
        return None, reason
    # Only a verified canonical schedule receives the new durable provenance.
    # Direct callers with historical schedules remain explicitly unverified.
    if source_type == "event" and event_at is None:
        ev = conn.execute("SELECT date, time FROM events WHERE id = ?", (source_id,)).fetchone()
        candidate = event_dt(ev["date"], ev["time"]) if ev else None
        if candidate and candidate - timedelta(hours=config.NOTIFICATION_DEFAULT_REMINDER_HOURS) == scheduled:
            event_at = candidate
    event_iso = event_at.astimezone(timezone.utc).isoformat() if event_at else None
    now = now_iso()
    nid = store.new_id()
    scheduled_iso = scheduled.isoformat()
    idempotency_key = notification_idempotency_key(
        patient_id, source_type, source_id, template_code, scheduled_iso
    )
    if event_iso:
        idempotency_key = "event_msk_v1:" + idempotency_key
    try:
        with conn:
            conn.execute(
                """INSERT INTO notifications
                   (id, patient_id, channel, template_id, template_code, template_version,
                    source_type, source_id, scheduled_at, idempotency_key, event_at, status, attempt_count, max_attempts,
                    next_attempt_at, created_by_user_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?)""",
                (
                    nid, patient_id, template["channel"], template["id"], template["code"], template["version"],
                    source_type, source_id, scheduled_iso, idempotency_key, event_iso, config.NOTIFICATION_MAX_ATTEMPTS,
                    scheduled_iso, created_by_user_id, now, now,
                ),
            )
    except sqlite3.IntegrityError:
        row = conn.execute(
            """SELECT * FROM notifications WHERE idempotency_key = ?
               AND (status IN ('pending', 'processing', 'sent', 'failed')
                    OR (status = 'cancelled' AND error_code IS NULL))""",
            (idempotency_key,),
        ).fetchone()
        return (dict(row) if row else None), "duplicate"
    if audit_user is not None:
        audit.record(
            conn,
            audit.NOTIFICATION_CREATED,
            patient_id=patient_id,
            detail=safe_detail(notification_id=nid, template_code=template_code, status="pending", source_type=source_type),
            **audit.actor_ctx(audit_user),
        )
    return get_notification_raw(conn, nid), None


def get_notification_raw(conn: sqlite3.Connection, notification_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM notifications WHERE id = ?", (notification_id,)).fetchone()
    return dict(row) if row else None


def get_notification_scoped(conn: sqlite3.Connection, user: policy.CurrentUser, notification_id: str) -> dict | None:
    # Avoid using the public list result for lookup across all rows.
    cond, params = policy.patient_scope_filter(user)
    row = conn.execute(
        f"""SELECT notifications.*, patients.name AS patient_name, patients.phone, patients.doctor,
                   u.full_name AS treating_doctor_name, t.name AS template_name,
                   GROUP_CONCAT(DISTINCT d.name) AS department_names
            FROM notifications
            JOIN patients ON patients.id = notifications.patient_id
            LEFT JOIN users u ON u.id = patients.treating_doctor_user_id
            LEFT JOIN user_departments ud ON ud.user_id = patients.treating_doctor_user_id
            LEFT JOIN departments d ON d.id = ud.department_id AND d.status = 'active'
            LEFT JOIN notification_templates t ON t.code = notifications.template_code
            WHERE notifications.id = ? AND {cond}
            GROUP BY notifications.id""",
        [notification_id, *params],
    ).fetchone()
    return notification_row_to_dict(row) if row else None


def cancel_notification(conn: sqlite3.Connection, *, notification_id: str, user: policy.CurrentUser) -> bool:
    row = get_notification_raw(conn, notification_id)
    if row is None:
        return False
    now = now_iso()
    with conn:
        cur = conn.execute(
            """UPDATE notifications
               SET status = 'cancelled', processing_started_at = NULL,
                   cancelled_at = ?, cancelled_by_user_id = ?, updated_at = ?
               WHERE id = ? AND status = 'pending'""",
            (now, user.id, now, notification_id),
        )
    if cur.rowcount:
        audit.record(
            conn,
            audit.NOTIFICATION_CANCELLED,
            patient_id=row["patient_id"],
            detail=safe_detail(notification_id=notification_id, template_code=row["template_code"], status="cancelled", source_type=row["source_type"]),
            **audit.actor_ctx(user),
        )
        return True
    return False


def retry_notification(conn: sqlite3.Connection, *, notification_id: str, user: policy.CurrentUser) -> bool:
    row = get_notification_raw(conn, notification_id)
    if row is None or row["status"] != "failed":
        return False
    now = now_iso()
    with conn:
        conn.execute(
            """UPDATE notifications
               SET status = 'pending', next_attempt_at = ?, error_code = NULL,
                   error_message_safe = NULL, provider_status = NULL,
                   processing_started_at = NULL, updated_at = ?
               WHERE id = ?""",
            (now, now, notification_id),
        )
    audit.record(
        conn,
        audit.NOTIFICATION_RETRY_REQUESTED,
        patient_id=row["patient_id"],
        detail=safe_detail(notification_id=notification_id, template_code=row["template_code"], status="pending", source_type=row["source_type"], attempt_count=row["attempt_count"]),
        **audit.actor_ctx(user),
    )
    return True


def planned_events(conn: sqlite3.Connection, user: policy.CurrentUser | None = None) -> list[sqlite3.Row]:
    if user is None:
        cond, params = "1 = 1", []
    else:
        cond, params = policy.patient_scope_filter(user)
    return conn.execute(
        f"""SELECT patients.id AS patient_id, patients.stage AS patient_stage,
                   patients.treating_doctor_user_id AS treating_doctor_user_id,
                   events.id AS event_id, events.date, events.time, events.actual_date, events.no_plan
            FROM events
            JOIN history_entries h ON h.id = events.history_entry_id
            JOIN patients ON patients.id = h.patient_id
            WHERE {cond} AND events.deleted_at IS NULL
            ORDER BY patients.id, events.id""",
        params,
    ).fetchall()


def _pending_event_rows_for_doctor(conn: sqlite3.Connection, doctor_id: str | None) -> int:
    """Сколько ожидающих напоминаний уже стоит в очереди за пациентами этого
    лечащего врача (пациенты без врача считаются одной группой)."""
    if doctor_id is None:
        return conn.execute(
            """SELECT COUNT(*) FROM notifications n JOIN patients p ON p.id = n.patient_id
               WHERE n.status = 'pending' AND n.source_type = 'event'
                 AND p.treating_doctor_user_id IS NULL"""
        ).fetchone()[0]
    return conn.execute(
        """SELECT COUNT(*) FROM notifications n JOIN patients p ON p.id = n.patient_id
           WHERE n.status = 'pending' AND n.source_type = 'event'
             AND p.treating_doctor_user_id = ?""",
        (doctor_id,),
    ).fetchone()[0]


def _event_business_keys(patient_id: str, event_id: str, template_code: str, event_at: datetime) -> tuple[str, str]:
    # Unversioned v28 rows have no reliable formula provenance. Aliases apply
    # ONLY to those rows, conservatively; never to a new verified reminder.
    lead = timedelta(hours=config.NOTIFICATION_DEFAULT_REMINDER_HOURS)
    legacy = event_at.astimezone(clock.institution_tz()).replace(tzinfo=timezone.utc)
    return tuple(notification_idempotency_key(patient_id, "event", event_id, template_code, (at - lead).isoformat())
                 for at in (event_at, legacy))


def generate_due_reminders(conn: sqlite3.Connection, user: policy.CurrentUser | None = None) -> dict:
    ensure_known_provider()
    created = skipped = cancelled = duplicates = 0
    per_doctor: dict[str | None, int] = {}
    now_dt = utcnow()
    for row in planned_events(conn, user):
        if row["actual_date"] or row["no_plan"] or row["patient_stage"] in TERMINAL_STAGES:
            with conn:
                cur = conn.execute(
                    """UPDATE notifications
                       SET status = 'cancelled', cancelled_at = ?, updated_at = ?, error_code = 'event_inactive'
                       WHERE patient_id = ? AND source_type = 'event' AND source_id = ?
                         AND status = 'pending'""",
                    (now_iso(), now_iso(), row["patient_id"], row["event_id"]),
                )
                cancelled += cur.rowcount
            continue
        event_at = event_dt(row["date"], row["time"])
        if event_at is None:
            skipped += 1
            continue
        if event_at <= now_dt or event_at > now_dt + timedelta(days=config.NOTIFICATION_MAX_SCHEDULE_DAYS):
            # Прошедшие и слишком далёкие события не ставятся в общую очередь.
            skipped += 1
            continue
        try:
            scheduled = event_at - timedelta(hours=config.NOTIFICATION_DEFAULT_REMINDER_HOURS)
        except OverflowError:
            # Дата у нижней границы календаря: одна такая запись не должна
            # останавливать генерацию для всех остальных пациентов.
            skipped += 1
            continue
        template_code = "appointment_reminder"
        with conn:
            cur = conn.execute(
                """UPDATE notifications
                   SET status = 'cancelled', cancelled_at = ?, updated_at = ?, error_code = 'event_rescheduled'
                   WHERE patient_id = ? AND source_type = 'event' AND source_id = ?
                     AND template_code = ? AND (scheduled_at != ? OR event_at IS NULL) AND status = 'pending'""",
                (now_iso(), now_iso(), row["patient_id"], row["event_id"], template_code, scheduled.isoformat()),
            )
            cancelled += cur.rowcount
        pending = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE patient_id = ? AND source_type = 'event' AND status = 'pending'",
            (row["patient_id"],),
        ).fetchone()[0]
        if pending >= config.NOTIFICATION_MAX_PENDING_EVENT_PER_PATIENT:
            skipped += 1
            continue
        doctor_id = row["treating_doctor_user_id"]
        if doctor_id not in per_doctor:
            per_doctor[doctor_id] = _pending_event_rows_for_doctor(conn, doctor_id)
        if per_doctor[doctor_id] >= config.NOTIFICATION_MAX_PENDING_EVENT_PER_DOCTOR:
            # Очередь общая: один врач не занимает её целиком.
            skipped += 1
            continue
        # Смена формулы времени не должна повторять отправленное сообщение,
        # обходить исчерпанные попытки или явную отмену сотрудника.
        blocked = conn.execute(
            """SELECT 1 FROM notifications WHERE patient_id = ? AND source_type = 'event'
               AND source_id = ? AND template_code = ?
               AND (event_at = ? OR (event_at IS NULL AND idempotency_key IN (?, ?)))
               AND (status IN ('sent', 'processing', 'failed')
                    OR (status = 'cancelled' AND error_code IS NULL)) LIMIT 1""",
            (row["patient_id"], row["event_id"], template_code, event_at.isoformat(),
             *_event_business_keys(row["patient_id"], row["event_id"], template_code, event_at)),
        ).fetchone()
        if blocked:
            duplicates += 1
            continue
        notification, reason = create_notification(
            conn,
            patient_id=row["patient_id"],
            template_code=template_code,
            scheduled_at=scheduled.isoformat(),
            source_type="event",
            source_id=row["event_id"],
            created_by_user_id=user.id if user else None,
            audit_user=user,
            event_at=event_at,
        )
        if reason == "duplicate":
            duplicates += 1
        elif reason:
            skipped += 1
        elif notification:
            created += 1
            per_doctor[doctor_id] = per_doctor.get(doctor_id, 0) + 1
    return {"created": created, "skipped": skipped, "cancelled": cancelled, "duplicates": duplicates}


def claim_next(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict | None:
    now_dt = now or utcnow()
    due = now_dt.isoformat()
    claim_started_at = now_dt.isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # Справедливый выбор между врачами: первым берётся врач, чьё
        # напоминание забирали раньше всех (или ещё ни разу), внутри врача —
        # самое раннее. Накопленный backlog одного врача не задерживает
        # напоминания пациентов остальных врачей.
        row = conn.execute(
            """WITH pending_rows AS (
                   SELECT notifications.*,
                          COALESCE(patients.treating_doctor_user_id, '') AS doctor_key
                   FROM notifications
                   JOIN patients ON patients.id = notifications.patient_id
                   WHERE notifications.status = 'pending' AND notifications.next_attempt_at <= ?
               ),
               last_claim AS (
                   -- Когда воркер в последний раз занимался пациентом этого
                   -- врача. processing_started_at обнуляется по завершении,
                   -- поэтому берётся и время отправки/последнего изменения.
                   SELECT COALESCE(patients.treating_doctor_user_id, '') AS doctor_key,
                          MAX(COALESCE(notifications.processing_started_at,
                                       notifications.sent_at,
                                       notifications.updated_at)) AS claimed_at
                   FROM notifications
                   JOIN patients ON patients.id = notifications.patient_id
                   WHERE notifications.status IN ('processing', 'sent', 'failed', 'skipped')
                   GROUP BY doctor_key
               )
               SELECT pending_rows.* FROM pending_rows
               LEFT JOIN last_claim ON last_claim.doctor_key = pending_rows.doctor_key
               ORDER BY COALESCE(last_claim.claimed_at, '') ASC,
                        pending_rows.scheduled_at ASC, pending_rows.id ASC
               LIMIT 1""",
            (due,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        cur = conn.execute(
            """UPDATE notifications
               SET status = 'processing', processing_started_at = ?, updated_at = ?
               WHERE id = ? AND status = 'pending'""",
            (claim_started_at, claim_started_at, row["id"]),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None
        return get_notification_raw(conn, row["id"])
    except Exception:
        conn.rollback()
        raise


def restore_stale_processing(conn: sqlite3.Connection, *, now: datetime | None = None) -> dict:
    now_dt = now or utcnow()
    threshold = (now_dt - timedelta(seconds=config.NOTIFICATION_PROCESSING_LEASE_SECONDS)).isoformat()
    now_text = now_dt.isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        stale = conn.execute(
            """SELECT id, attempt_count, max_attempts
               FROM notifications
               WHERE status = 'processing'
                 AND (processing_started_at IS NULL OR processing_started_at <= ?)
               ORDER BY processing_started_at ASC, id ASC""",
            (threshold,),
        ).fetchall()
        restored = failed = 0
        for row in stale:
            if int(row["attempt_count"]) >= int(row["max_attempts"]):
                cur = conn.execute(
                    """UPDATE notifications
                       SET status = 'failed', processing_started_at = NULL, next_attempt_at = NULL,
                           error_code = 'processing_lease_expired', error_message_safe = 'processing_lease_expired',
                           updated_at = ?
                       WHERE id = ? AND status = 'processing'
                         AND (processing_started_at IS NULL OR processing_started_at <= ?)""",
                    (now_text, row["id"], threshold),
                )
                failed += cur.rowcount
            else:
                cur = conn.execute(
                    """UPDATE notifications
                       SET status = 'pending', processing_started_at = NULL, next_attempt_at = ?,
                           error_code = 'processing_lease_expired', error_message_safe = 'processing_lease_expired',
                           updated_at = ?
                       WHERE id = ? AND status = 'processing'
                         AND (processing_started_at IS NULL OR processing_started_at <= ?)""",
                    (now_text, now_text, row["id"], threshold),
                )
                restored += cur.rowcount
        conn.commit()
        return {"restored": restored, "failed": failed}
    except Exception:
        conn.rollback()
        raise


def mark_skipped(conn: sqlite3.Connection, row: dict, reason: str) -> None:
    now = now_iso()
    with conn:
        conn.execute(
            """UPDATE notifications
               SET status = 'skipped', error_code = ?, error_message_safe = ?,
                   provider_status = NULL, processing_started_at = NULL, updated_at = ?
               WHERE id = ?""",
            (reason, reason, now, row["id"]),
        )
    audit.record(
        conn,
        audit.NOTIFICATION_SKIPPED,
        patient_id=row["patient_id"],
        detail=safe_detail(notification_id=row["id"], template_code=row["template_code"], status="skipped", error_code=reason, source_type=row["source_type"], attempt_count=row["attempt_count"]),
    )


def _current_event_time(conn: sqlite3.Connection, row) -> tuple[datetime | None, str | None]:
    ev = conn.execute(
        """SELECT events.date, events.time, events.actual_date, events.no_plan, events.deleted_at, patients.stage
           FROM events
           JOIN history_entries h ON h.id = events.history_entry_id
           JOIN patients ON patients.id = h.patient_id
           WHERE events.id = ? AND patients.id = ?""",
        (row["source_id"], row["patient_id"]),
    ).fetchone()
    if ev is None or ev["deleted_at"] is not None:
        # Удалённое (в том числе мягко) событие не порождает SMS.
        return None, "event_removed"
    if ev["actual_date"] or ev["no_plan"] or ev["stage"] in TERMINAL_STAGES:
        return None, "event_inactive"
    event_at = event_dt(ev["date"], ev["time"])
    if event_at is None:
        return None, "event_invalid"
    if event_at <= utcnow():
        return None, "event_passed"
    if row["event_at"] is None:
        return None, "event_schedule_unverified"
    if parse_dt(row["event_at"]) != event_at:
        return None, "event_rescheduled"
    already_sent = conn.execute(
        """SELECT 1 FROM notifications WHERE patient_id = ? AND source_type = 'event'
           AND source_id = ? AND template_code = ? AND status = 'sent' AND id != ?
           AND (event_at = ? OR (event_at IS NULL AND idempotency_key IN (?, ?))) LIMIT 1""",
        (row["patient_id"], row["source_id"], row["template_code"], row["id"], event_at.isoformat(),
         *_event_business_keys(row["patient_id"], row["source_id"], row["template_code"], event_at)),
    ).fetchone()
    if already_sent:
        return None, "event_already_sent"
    if event_at <= utcnow():
        # Напоминание из накопившейся очереди не отправляется после приёма.
        return None, "event_passed"
    return event_at, None


def process_one(conn: sqlite3.Connection, provider: NotificationProvider | None = None) -> dict | None:
    ensure_known_provider()
    row = claim_next(conn)
    if row is None:
        return None
    audit.record(
        conn,
        audit.NOTIFICATION_PROCESSING_STARTED,
        patient_id=row["patient_id"],
        detail=safe_detail(notification_id=row["id"], template_code=row["template_code"], status="processing", source_type=row["source_type"], attempt_count=row["attempt_count"]),
    )
    ok, reason = can_queue_for_patient(conn, row["patient_id"])
    if not ok:
        mark_skipped(conn, row, reason)
        return {"id": row["id"], "status": "skipped", "error_code": reason}
    patient = conn.execute("SELECT phone FROM patients WHERE id = ?", (row["patient_id"],)).fetchone()
    template = get_template(conn, row["template_code"])
    if patient is None:
        mark_skipped(conn, row, "patient_unavailable")
        return {"id": row["id"], "status": "skipped", "error_code": "patient_unavailable"}
    if template is None or not template["enabled"]:
        mark_skipped(conn, row, "template_disabled")
        return {"id": row["id"], "status": "skipped", "error_code": "template_disabled"}
    event_at = None
    if row["source_type"] == "event":
        # Последняя проверка перед отправкой: событие существует, не выполнено,
        # не отменено, пациент не закрыт (перенос отменяет напоминание в store).
        event_at, reason = _current_event_time(conn, row)
        if reason:
            mark_skipped(conn, row, reason)
            return {"id": row["id"], "status": "skipped", "error_code": reason}
    try:
        body = render_template(template["body"], scheduled_at=row["scheduled_at"], event_at=event_at)
    except ValueError:
        mark_skipped(conn, row, "template_invalid")
        return {"id": row["id"], "status": "skipped", "error_code": "template_invalid"}
    provider = provider or provider_from_config()
    ok, reason = can_queue_for_patient(conn, row["patient_id"])
    if not ok:
        mark_skipped(conn, row, reason)
        return {"id": row["id"], "status": "skipped", "error_code": reason}
    if row["source_type"] == "event":
        current, reason = _current_event_time(conn, row)
        if not reason and current != event_at:
            reason = "event_rescheduled"
        if reason:
            mark_skipped(conn, row, reason)
            return {"id": row["id"], "status": "skipped", "error_code": reason}
    # A single read snapshot supplies both the validated consent and the actual
    # recipient. No write transaction spans provider I/O. Mutations after this
    # boundary still need provider-side coordination, which mock cannot promise.
    target = conn.execute(
        """SELECT p.phone, p.stage, pref.sms_enabled, pref.consent_status,
                  e.date, e.time, e.actual_date, e.no_plan, e.deleted_at, h.patient_id AS event_patient_id
           FROM patients p LEFT JOIN patient_notification_preferences pref ON pref.patient_id = p.id
           LEFT JOIN events e ON e.id = ?
           LEFT JOIN history_entries h ON h.id = e.history_entry_id
           WHERE p.id = ?""", (row["source_id"] if row["source_type"] == "event" else None, row["patient_id"]),
    ).fetchone()
    if target is None:
        reason = "patient_unavailable"
    elif not target["sms_enabled"] or target["consent_status"] != "granted":
        reason = "no_consent"
    else:
        ok, reason = phone_sendable(target["phone"])
        if ok:
            reason = None
    if not reason and row["source_type"] == "event":
        current = event_dt(target["date"], target["time"])
        if target["event_patient_id"] != row["patient_id"] or target["deleted_at"] is not None:
            reason = "event_removed"
        elif target["actual_date"] or target["no_plan"] or target["stage"] in TERMINAL_STAGES:
            reason = "event_inactive"
        elif current is None:
            reason = "event_invalid"
        elif current <= utcnow():
            reason = "event_passed"
        elif current != event_at:
            reason = "event_rescheduled"
    if reason:
        mark_skipped(conn, row, reason)
        return {"id": row["id"], "status": "skipped", "error_code": reason}
    result = provider.send(NotificationContext(
        notification_id=row["id"],
        channel=row["channel"],
        recipient=normalize_phone(target["phone"]),
        body=body,
    ))
    now = now_iso()
    attempts = int(row["attempt_count"]) + 1
    if result.success:
        with conn:
            conn.execute(
                """UPDATE notifications
                   SET status = 'sent', attempt_count = ?, provider_message_id = ?,
                       provider_status = ?, processing_started_at = NULL, sent_at = ?, updated_at = ?
                   WHERE id = ?""",
                (attempts, result.provider_message_id, result.provider_status, now, now, row["id"]),
            )
        audit.record(
            conn,
            audit.NOTIFICATION_SENT,
            patient_id=row["patient_id"],
            detail=safe_detail(notification_id=row["id"], template_code=row["template_code"], status="sent", source_type=row["source_type"], attempt_count=attempts),
        )
        return {"id": row["id"], "status": "sent"}

    if result.temporary and attempts < int(row["max_attempts"]):
        backoff = 2 ** attempts
        next_attempt = (utcnow() + timedelta(minutes=backoff)).isoformat()
        with conn:
            conn.execute(
                """UPDATE notifications
                   SET status = 'pending', attempt_count = ?, next_attempt_at = ?,
                       provider_status = ?, error_code = ?, error_message_safe = ?,
                       processing_started_at = NULL, updated_at = ?
                   WHERE id = ?""",
                (attempts, next_attempt, result.provider_status, result.error_code, result.error_code, now, row["id"]),
            )
        return {"id": row["id"], "status": "pending", "error_code": result.error_code}

    with conn:
        conn.execute(
            """UPDATE notifications
               SET status = 'failed', attempt_count = ?, provider_status = ?,
                   error_code = ?, error_message_safe = ?, processing_started_at = NULL, updated_at = ?
               WHERE id = ?""",
            (attempts, result.provider_status, result.error_code, result.error_code, now, row["id"]),
        )
    audit.record(
        conn,
        audit.NOTIFICATION_FAILED,
        patient_id=row["patient_id"],
        detail=safe_detail(notification_id=row["id"], template_code=row["template_code"], status="failed", error_code=result.error_code, source_type=row["source_type"], attempt_count=attempts),
    )
    return {"id": row["id"], "status": "failed", "error_code": result.error_code}


def process_due(conn: sqlite3.Connection, *, limit: int = 100, provider: NotificationProvider | None = None) -> dict:
    limit = max(1, min(int(limit), config.NOTIFICATION_BATCH_LIMIT_MAX))
    recovery = restore_stale_processing(conn)
    processed = sent = failed = skipped = retry_scheduled = 0
    for _ in range(limit):
        result = process_one(conn, provider=provider)
        if result is None:
            break
        processed += 1
        if result["status"] == "sent":
            sent += 1
        elif result["status"] == "failed":
            failed += 1
        elif result["status"] == "skipped":
            skipped += 1
        elif result["status"] == "pending":
            retry_scheduled += 1
    return {
        "processed": processed,
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "retry_scheduled": retry_scheduled,
        "restored": recovery["restored"],
        "recovery_failed": recovery["failed"],
    }
