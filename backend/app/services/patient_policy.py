"""Изменение номера полиса ОМС существующей карточки (WP2.4-C).

Одна транзакция BEGIN IMMEDIATE: права сотрудника перечитываются из БД после
получения блокировки (видимость и право записи конкретной карточки), затем —
только если канонический номер действительно меняется — поиск совпадений в
области сотрудника без самой карточки; совпадение требует подтверждения с
причиной, точно совпадающего с актуальным списком. Запись номера, подтверждение
и его обязательный аудит фиксируются вместе. Другие поля карточки и история
консилиумов, диагнозов и событий не затрагиваются.
"""
from __future__ import annotations

import sqlite3

from .. import identity, policy, store
from ..db import now_iso, transaction
from ..models import normalize_policy_number
from . import patient_duplicates


class PolicyUpdateNotFound(Exception):
    """Карточка не видна сотруднику (404, без сведений о ней)."""


class PolicyUpdateForbidden(Exception):
    """Карточка видна, но изменять ее сотрудник не может (403)."""


def update_policy_number(conn: sqlite3.Connection, actor: policy.CurrentUser, patient_id: str, value,
                         confirmation) -> dict:
    new_value = normalize_policy_number(value)   # ValueError — 422, как прежде
    with transaction(conn):
        fresh = policy.reload_current_user(conn, actor)
        if fresh is None:
            raise PolicyUpdateForbidden()
        if not policy.can_read_patient(conn, fresh, patient_id):
            raise PolicyUpdateNotFound()
        if not policy.can_write_patient(conn, fresh, patient_id):
            raise PolicyUpdateForbidden()
        current = conn.execute("SELECT policy_number FROM patients WHERE id = ?", (patient_id,)).fetchone()
        confirmed: list[dict] = []
        if identity.canonical_policy(current["policy_number"]) != identity.canonical_policy(new_value):
            confirmed = patient_duplicates.check_policy(
                conn, fresh, new_value, confirmation, exclude_patient_id=patient_id,
                message=patient_duplicates.POLICY_MESSAGE)
        conn.execute("UPDATE patients SET policy_number = ?, updated_at = ? WHERE id = ?",
                     (new_value, now_iso(), patient_id))
        if confirmed:
            patient_duplicates.record_confirmation(conn, fresh, operation="policy_update", patient_id=patient_id,
                                                   matches=confirmed, reason=confirmation.reason)
        result = store.patient_to_dict(conn, patient_id)
    return result
