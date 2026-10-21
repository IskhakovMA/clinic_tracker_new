"""Создание карточки пациента через форму и API (WP2.4-A).

Одна транзакция BEGIN IMMEDIATE — единственный владелец транзакции:
полномочия перечитываются из БД после получения блокировки, лечащий врач
выбирается по ним же, затем вставляются карточка, первая запись маршрута и
события формы, пишется обязательная запись аудита patient_created и строится
ответ. Аудит — неотъемлемая часть операции (fail-closed, A-01): карточку нельзя
создать этим сервисом без ровно одной записи patient_created. Сбой аудита
(AuditWriteFailed) или базы откатывает все целиком; после коммита ничего не
читается.

Идемпотентность (Idempotency-Key) и обработка возможных дублей сюда не входят
(этапы WP2.4-B и WP2.4-C).
"""
from __future__ import annotations

import sqlite3

from .. import audit, policy, store
from ..db import transaction
from ..models import PatientCreateIn

FORM_SOURCE = "form"


class PatientCreateForbidden(Exception):
    """Роль или область не позволяют создать карточку (403).

    detail — метка для журнала отказов (без данных пациента),
    message — текст ответа клиенту."""

    def __init__(self, detail: str, message: str = "Forbidden"):
        self.detail = detail
        self.message = message
        super().__init__(message)


def create_patient_card(conn: sqlite3.Connection, actor: policy.CurrentUser, body: PatientCreateIn) -> dict:
    """Создать карточку от имени actor. Возвращает карточку (patient_to_dict),
    построенную внутри той же транзакции.

    Обязательная запись аудита пишется здесь же, в транзакции создания:
    актуальный актор (роль и отделение после перечитывания), ID пациента и ID
    лечащего врача — без ФИО, полиса и медицинских полей. Ошибка ее записи —
    audit.AuditWriteFailed после полного отката."""
    with transaction(conn):
        fresh = policy.reload_current_user(conn, actor)
        if fresh is None or not policy.can_create_patient_card(fresh):
            raise PatientCreateForbidden("action=patient_create")
        doctor_user_id, error = policy.resolve_create_doctor(conn, fresh, body.treatingDoctorUserId)
        if error is not None:
            raise PatientCreateForbidden("action=patient_create_doctor", error)
        patient_id = store.insert_patient_rows(
            conn, body, doctor_user_id, created_by_user_id=fresh.id, creation_source=FORM_SOURCE,
        )
        audit.record_required_or_fail(conn, audit.PATIENT_CREATED, patient_id=patient_id,
                                      target_user_id=doctor_user_id, **audit.actor_ctx(fresh))
        result = store.patient_to_dict(conn, patient_id)
        if result is None:
            raise RuntimeError("created patient card is missing inside its own transaction")
    return result
