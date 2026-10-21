"""Возможные дубли пациентов: проверка и подтверждение (WP2.4-C, R1–R2).

Совпадение номера полиса ОМС с карточкой ОБЛАСТИ ДОСТУПА сотрудника — повод
предупредить (409 POSSIBLE_DUPLICATE), а не доказательство и не запрет: после
осознанного подтверждения с причиной операция выполняется. Совпадения вне
области не ищутся вовсе (identity.policy_matches) и ни на что не влияют.

Все функции работают внутри транзакции BEGIN IMMEDIATE вызывающей операции:
совпадения вычисляются заново под блокировкой записи, подтвержденный список
должен точно совпасть с ними, запись подтверждения и обязательный аудит
фиксируются вместе с операцией или не фиксируются вовсе.
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from .. import audit, identity, policy
from ..db import now_iso

POSSIBLE_DUPLICATE_CODE = "POSSIBLE_DUPLICATE"
CREATE_MESSAGE = (
    "В вашей области доступа уже есть карточка с таким номером полиса ОМС. Возможно, это тот же "
    "пациент. Откройте найденную карточку или подтвердите создание новой с указанием причины."
)
POLICY_MESSAGE = (
    "В вашей области доступа уже есть другая карточка с таким номером полиса ОМС. Возможно, это тот же "
    "пациент. Проверьте карточки или подтвердите изменение номера с указанием причины."
)
LIMIT_EXCEEDED_CODE = "DUPLICATE_MATCH_LIMIT_EXCEEDED"
LIMIT_EXCEEDED_MESSAGE = (
    f"В вашей области доступа больше {identity.MATCH_LIMIT} карточек с таким номером полиса ОМС. "
    "Подтвердить неполный список нельзя: нужен ручной разбор карточек с этим номером. Данные не изменены."
)
IMPORT_LIMIT_EXCEEDED_MESSAGE = (
    f"Для записей файла в вашей области доступа больше {identity.MATCH_LIMIT} карточек с совпадающими "
    "номерами полиса ОМС. Подтвердить неполный список нельзя: нужен ручной разбор этих карточек или "
    "исправление файла. Данные не изменены."
)
IMPORT_MESSAGE = (
    "Записи файла совпадают по номеру полиса ОМС с карточками вашей области доступа. Проверьте их или "
    "подтвердите импорт с указанием причины. Данные не изменены."
)


class PossibleDuplicate(Exception):
    """Совпадения в области сотрудника, подтверждения нет или оно устарело (409).

    matches — минимальные сведения о видимых сотруднику карточках."""

    def __init__(self, matches: list[dict], message: str = CREATE_MESSAGE):
        self.matches = matches
        super().__init__(message)


class DuplicateMatchLimitExceeded(PossibleDuplicate):
    """Совпадений в области сотрудника больше лимита (409 DUPLICATE_MATCH_LIMIT_EXCEEDED, C-01).

    Операция с совпадающим полисом невозможна с любым подтверждением: первые
    MATCH_LIMIT карточек — не полный список. Карточки в ответ не включаются."""

    def __init__(self):
        super().__init__([], LIMIT_EXCEEDED_MESSAGE)


def confirmed_ids(confirmation) -> set[str] | None:
    return set(confirmation.matchedPatientIds) if confirmation is not None else None


def require_confirmation(matches: list[dict], confirmation, message: str = CREATE_MESSAGE) -> bool:
    """True — совпадения есть и подтверждение точно им соответствует; False —
    совпадений нет (подтверждать нечего, присланный список не используется).
    Иначе PossibleDuplicate с актуальным списком видимых совпадений: в том числе
    если список устарел или в нем есть ID, которых сотрудник не видит."""
    if not matches:
        return False
    if confirmation is None or confirmed_ids(confirmation) != {m["id"] for m in matches}:
        raise PossibleDuplicate(matches, message)
    return True


def record_confirmation(
    conn: sqlite3.Connection, actor: policy.CurrentUser, *, operation: str, patient_id: str,
    matches: list[dict], reason: str, import_id: str | None = None,
) -> None:
    """Append-only запись подтверждения и обязательный аудит (без причины и данных пациентов)."""
    if not conn.in_transaction:
        raise RuntimeError("duplicate confirmation must be stored inside the operation's transaction")
    ids = [m["id"] for m in matches]
    conn.execute(
        """INSERT INTO patient_duplicate_confirmations
               (id, operation, patient_id, matched_patient_ids, reason, created_by_user_id, created_at, import_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (uuid.uuid4().hex, operation, patient_id, json.dumps(ids), reason, actor.id, now_iso(), import_id),
    )
    detail = f"operation={operation} matches={len(ids)}" + (f" import_id={import_id}" if import_id else "")
    audit.record_required_or_fail(conn, audit.PATIENT_DUPLICATE_CONFIRMED, patient_id=patient_id,
                                  detail=detail, **audit.actor_ctx(actor))


def check_policy(conn: sqlite3.Connection, actor: policy.CurrentUser, policy_number, confirmation, *,
                 exclude_patient_id: str | None = None, message: str = CREATE_MESSAGE) -> list[dict]:
    """Совпадения в области actor, подтвержденные confirmation ([] — подтверждать нечего).
    PossibleDuplicate — подтверждения нет или оно не совпадает с актуальным списком;
    DuplicateMatchLimitExceeded — совпадений больше лимита (подтверждение не принимается)."""
    try:
        matches = identity.policy_matches(conn, actor, policy_number, exclude_patient_id=exclude_patient_id)
    except identity.MatchLimitExceeded:
        raise DuplicateMatchLimitExceeded() from None
    return matches if require_confirmation(matches, confirmation, message) else []
