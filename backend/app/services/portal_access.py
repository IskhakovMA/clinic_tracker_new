"""Managed mobile access for an existing patient record (Stage 1).

Scope of this module: a member of staff links an email address to a patient
card, changes it, revokes access, and sees its state. Nothing here
authenticates anybody — there is no OTP, no session, no enrollment. Those
belong to Stage 2 and need a decision on an email provider first.

Two invariants are enforced here rather than left to callers:

* one address belongs to at most one patient card (UNIQUE on the column,
  surfaced as a clean 409 instead of an integrity error);
* a revoked row keeps its address, so the same address cannot quietly be
  re-pointed at a different patient after access was withdrawn — for as long
  as the patient card itself exists. Deleting the card removes the access row
  with it (ON DELETE CASCADE), which frees the address again; that is a
  destructive, audited action inside the actor's own write scope, and the
  record of what happened lives in the audit log, not in this table.

Every change (link, change, revoke) follows the WP2.3–2.4 write standard: one
BEGIN IMMEDIATE transaction; the actor's role, status and scope are re-read
from the database after the lock is taken; the required audit row is written
in the same transaction (a failed audit write rolls the change back, 503).
Repeating the same request is safe: linking the address a card already has
changes nothing (activation is kept), revoking a revoked access changes
nothing. A card marked erroneous (WP2.4-D) is outside active work: it is not
visible here, and database triggers reject any change of its access row.

The row is the single source of truth for "can this person activate the app".
`activation_allowed` is the seam Stage 2 will call; it is already reported to
staff so that a revoked state is visible before anyone tries to use it.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import audit, policy
from ..db import now_iso, transaction
from ..email_address import masked_email
from ..models import PortalAccessLinkIn

STATUS_LINKED = "linked"
STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"

STATUS_LABELS = {
    STATUS_LINKED: "Адрес привязан, вход ещё не выполнялся",
    STATUS_ACTIVE: "Мобильный доступ активен",
    STATUS_REVOKED: "Мобильный доступ отключён",
}

# Состояния, из которых пациент может завершить активацию в мобильном
# приложении. Stage 2 обязан спрашивать именно это, а не статус напрямую.
ACTIVATION_ALLOWED_STATUSES = (STATUS_LINKED, STATUS_ACTIVE)
# Действующий доступ: он же не дает пометить карточку ошибочной (WP2.4-D).
IN_FORCE_STATUSES = ACTIVATION_ALLOWED_STATUSES


class EmailAlreadyLinked(Exception):
    """Адрес уже принадлежит другой карточке.

    Поднимается и проверкой под блокировкой записи, и перехватом нарушения
    UNIQUE: источником истины является ограничение базы. Ответ одинаков, какой
    бы карточке (в области сотрудника или вне ее, помеченной или нет) адрес ни
    принадлежал.
    """


class PortalAccessNotFound(Exception):
    """Карточка не видна сотруднику (404, без сведений о ней)."""


class PortalAccessForbidden(Exception):
    """Карточка видна, но действие сотруднику не разрешено (403)."""


class PortalAccessNotConfigured(Exception):
    """Отключать нечего: доступ для карточки не настраивался (404)."""


def activation_allowed(row: sqlite3.Row | None) -> bool:
    return row is not None and row["status"] in ACTIVATION_ALLOWED_STATUSES


_ASCII_FOLD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _same_address(a: str, b: str) -> bool:
    """Тот же адрес в смысле UNIQUE COLLATE NOCASE столбца (регистр ASCII)."""
    return a.translate(_ASCII_FOLD) == b.translate(_ASCII_FOLD)


def _audit_conflict(conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str, *, required: bool) -> None:
    """След неудачной привязки.

    Без него перебор адресов не оставлял следа ровно в том случае, который
    интересен злоумышленнику. В строку идёт только исход: ни адреса, ни
    карточки-владельца в журнале нет.
    """
    write = audit.record_required_or_fail if required else audit.record
    write(
        conn,
        audit.PORTAL_ACCESS_CONFLICT,
        result=audit.RESULT_DENIED,
        patient_id=patient_id,
        detail="status=conflict",
        **audit.actor_ctx(user),
    )


def _get_row(conn: sqlite3.Connection, patient_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM patient_portal_access WHERE patient_id = ?", (patient_id,)
    ).fetchone()


def _owner_of_email(conn: sqlite3.Connection, email: str) -> str | None:
    # Сравнение по коллации столбца (NOCASE): совпадает с тем, что проверит UNIQUE.
    row = conn.execute(
        "SELECT patient_id FROM patient_portal_access WHERE email_normalized = ?", (email,)
    ).fetchone()
    return row["patient_id"] if row else None


def _linked_by_name(conn: sqlite3.Connection, user_id: str | None) -> str | None:
    if not user_id:
        return None
    row = conn.execute("SELECT full_name, username FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return row["full_name"] or row["username"]


def _row_to_dict(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, Any]:
    """Представление для интерфейса сотрудника.

    Адрес отдаётся только в маскированном виде: сотруднику достаточно узнать
    то, что он сам вводил, а полный адрес на общем экране — лишняя утечка.
    """
    if row is None:
        return {
            "linked": False,
            "status": None,
            "statusLabel": "Мобильный доступ не настроен",
            "emailMasked": "",
            "activationAllowed": False,
            "linkedAt": None,
            "linkedByName": None,
            "activatedAt": None,
            "enrollmentState": None,
            "lastLoginAt": None,
            "revokedAt": None,
            "revokedByName": None,
        }
    return {
        "linked": True,
        "status": row["status"],
        "statusLabel": STATUS_LABELS.get(row["status"], row["status"]),
        "emailMasked": masked_email(row["email_normalized"]),
        "activationAllowed": activation_allowed(row),
        "linkedAt": row["linked_at"],
        "linkedByName": _linked_by_name(conn, row["linked_by_user_id"]),
        "activatedAt": row["activated_at"],
        "enrollmentState": row["enrollment_state"],
        "lastLoginAt": row["last_login_at"],
        "revokedAt": row["revoked_at"],
        "revokedByName": _linked_by_name(conn, row["revoked_by_user_id"]),
    }


def get_state(conn: sqlite3.Connection, patient_id: str) -> dict[str, Any]:
    """Состояние доступа. Вызывается только после проверки видимости пациента."""
    return _row_to_dict(conn, _get_row(conn, patient_id))


def _fresh_actor(conn: sqlite3.Connection, actor: policy.CurrentUser, patient_id: str, allowed) -> policy.CurrentUser:
    """Права, перечитанные под блокировкой записи: роль, статус, область.

    Порядок тот же, что у остальных endpoint'ов карточки: невидимая карточка —
    404 (существование не раскрывается), видимая без права — 403."""
    fresh = policy.reload_current_user(conn, actor)
    if fresh is None:
        raise PortalAccessForbidden()
    if not policy.can_read_patient(conn, fresh, patient_id):
        raise PortalAccessNotFound()
    if not allowed(conn, fresh, patient_id):
        raise PortalAccessForbidden()
    return fresh


def link_email(
    conn: sqlite3.Connection,
    user: policy.CurrentUser,
    patient_id: str,
    body: PortalAccessLinkIn,
) -> dict[str, Any]:
    """Привязать или сменить адрес; повторная привязка снимает отзыв.

    Тот же адрес у действующего доступа (linked/active) — повтор запроса:
    ничего не меняется, активация и вход пациента сохраняются, новой записи
    журнала нет. Новый адрес или возврат отозванного доступа начинают
    активацию заново: прежний enrollment относился к прежнему владельцу адреса.
    """
    email = body.email
    conflict = False
    try:
        with transaction(conn):
            fresh = _fresh_actor(conn, user, patient_id, policy.can_manage_portal_access)
            owner = _owner_of_email(conn, email)
            if owner is not None and owner != patient_id:
                # Чужую привязку не подтверждаем деталями: вызывающий слой отдаёт 409
                # без имени пациента и без адреса. След пишется в этой же транзакции.
                _audit_conflict(conn, fresh, patient_id, required=True)
                conflict = True
            else:
                existing = _get_row(conn, patient_id)
                if (existing is not None and existing["status"] in IN_FORCE_STATUSES
                        and _same_address(existing["email_normalized"], email)):
                    return get_state(conn, patient_id)           # повтор: состояние не меняется
                now = now_iso()
                if existing is None:
                    conn.execute(
                        """INSERT INTO patient_portal_access
                           (patient_id, email_normalized, status, linked_at, linked_by_user_id,
                            activated_at, enrollment_state, last_login_at, revoked_at, revoked_by_user_id, updated_at)
                           VALUES (?, ?, 'linked', ?, ?, NULL, 'none', NULL, NULL, NULL, ?)""",
                        (patient_id, email, now, fresh.id, now),
                    )
                    event = audit.PORTAL_ACCESS_LINKED
                    detail = "status=linked previous=none"
                else:
                    email_changed = not _same_address(existing["email_normalized"], email)
                    conn.execute(
                        """UPDATE patient_portal_access
                           SET email_normalized = ?, status = 'linked', linked_at = ?, linked_by_user_id = ?,
                               activated_at = NULL, enrollment_state = 'none', last_login_at = NULL,
                               revoked_at = NULL, revoked_by_user_id = NULL, updated_at = ?
                           WHERE patient_id = ?""",
                        (email, now, fresh.id, now, patient_id),
                    )
                    event = audit.PORTAL_ACCESS_EMAIL_CHANGED if email_changed else audit.PORTAL_ACCESS_LINKED
                    detail = (f"status=linked previous={existing['status']} "
                              f"email_changed={'yes' if email_changed else 'no'}")
                    if existing["status"] == STATUS_REVOKED:
                        detail += " restored=yes"
                audit.record_required_or_fail(conn, event, patient_id=patient_id, detail=detail,
                                              **audit.actor_ctx(fresh))
                result = get_state(conn, patient_id)
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" not in str(exc) or "patient_portal_access.email_normalized" not in str(exc):
            raise
        # Адрес занят записью, которую проверка под блокировкой не увидела (иной
        # путь записи в обход сервиса). Транзакция уже откатана; след — отдельно,
        # без адреса, ответ — тот же безопасный конфликт.
        _audit_conflict(conn, user, patient_id, required=False)
        raise EmailAlreadyLinked() from None
    if conflict:
        raise EmailAlreadyLinked()
    return result


def revoke(conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str) -> dict[str, Any]:
    """Отключить мобильный доступ.

    Адрес намеренно сохраняется: это и след для разбора, и защита от того,
    чтобы отозванный адрес тихо увели на другую карточку. Повтор для уже
    отозванного доступа ничего не меняет и журнал не дополняет.
    """
    with transaction(conn):
        fresh = _fresh_actor(conn, user, patient_id, policy.can_revoke_portal_access)
        existing = _get_row(conn, patient_id)
        if existing is None:
            raise PortalAccessNotConfigured()
        if existing["status"] == STATUS_REVOKED:
            return get_state(conn, patient_id)
        now = now_iso()
        conn.execute(
            """UPDATE patient_portal_access
               SET status = 'revoked', revoked_at = ?, revoked_by_user_id = ?,
                   enrollment_state = 'none', updated_at = ?
               WHERE patient_id = ?""",
            (now, fresh.id, now, patient_id),
        )
        audit.record_required_or_fail(
            conn,
            audit.PORTAL_ACCESS_REVOKED,
            patient_id=patient_id,
            detail=f"status=revoked previous={existing['status']}",
            **audit.actor_ctx(fresh),
        )
        return get_state(conn, patient_id)
