"""Создание карточки пациента через форму и API (WP2.4-A).

Одна транзакция BEGIN IMMEDIATE — единственный владелец транзакции:
полномочия перечитываются из БД после получения блокировки, лечащий врач
выбирается по ним же, затем вставляются карточка, первая запись маршрута и
события формы, пишется обязательная запись аудита patient_created и строится
ответ. Аудит — неотъемлемая часть операции (fail-closed, A-01): карточку нельзя
создать этим сервисом без ровно одной записи patient_created. Сбой аудита
(AuditWriteFailed) или базы откатывает все целиком; после коммита ничего не
читается.

WP2.4-B: create_patient_card_idempotent — то же создание с ключом
Idempotency-Key. Ключ, карточка, маршрут, события, аудит и ответ фиксируются
одной транзакцией; повтор с тем же ключом и содержимым карточку не создает,
а возвращает исходную после повторной проверки прав (обязательный аудит
patient_create_replayed). Обработка возможных дублей — этап WP2.4-C.
"""
from __future__ import annotations

import sqlite3

from .. import audit, idempotency, policy, store
from . import patient_duplicates
from ..db import transaction
from ..models import PatientCreateIn

FORM_SOURCE = "form"


class PatientReplayNotFound(Exception):
    """Карточка повторяемого создания больше не видна сотруднику (404, без сведений о ней)."""


class ReplayAuditUnavailable(Exception):
    """Обязательная запись о повторе не записана (503): результат не возвращается.
    Карточка первого запроса при этом существует — повторять нужно тем же ключом."""


class PatientCreateForbidden(Exception):
    """Роль или область не позволяют создать карточку (403).

    detail — метка для журнала отказов (без данных пациента),
    message — текст ответа клиенту."""

    def __init__(self, detail: str, message: str = "Forbidden"):
        self.detail = detail
        self.message = message
        super().__init__(message)


def _create_locked(conn: sqlite3.Connection, fresh: policy.CurrentUser, body: PatientCreateIn) -> str:
    """Создание внутри уже открытой транзакции владельца: права по свежим данным,
    выбор врача, вставки и единственная обязательная запись patient_created."""
    if not policy.can_create_patient_card(fresh):
        raise PatientCreateForbidden("action=patient_create")
    doctor_user_id, error = policy.resolve_create_doctor(conn, fresh, body.treatingDoctorUserId)
    if error is not None:
        raise PatientCreateForbidden("action=patient_create_doctor", error)
    # WP2.4-C: совпадение полиса в области сотрудника (по свежим правам, под
    # блокировкой) — только с подтверждением, точно совпадающим с этим списком.
    confirmed = patient_duplicates.check_policy(conn, fresh, body.policyNumber, body.duplicateConfirmation)
    patient_id = store.insert_patient_rows(
        conn, body, doctor_user_id, created_by_user_id=fresh.id, creation_source=FORM_SOURCE,
    )
    audit.record_required_or_fail(conn, audit.PATIENT_CREATED, patient_id=patient_id,
                                  target_user_id=doctor_user_id, **audit.actor_ctx(fresh))
    if confirmed:
        patient_duplicates.record_confirmation(conn, fresh, operation="create", patient_id=patient_id,
                                               matches=confirmed, reason=body.duplicateConfirmation.reason)
    return patient_id


def _card(conn: sqlite3.Connection, patient_id: str) -> dict:
    result = store.patient_to_dict(conn, patient_id)
    if result is None:
        raise RuntimeError("patient card is missing inside its own transaction")
    return result


def create_patient_card(conn: sqlite3.Connection, actor: policy.CurrentUser, body: PatientCreateIn) -> dict:
    """Создать карточку от имени actor. Возвращает карточку (patient_to_dict),
    построенную внутри той же транзакции.

    Обязательная запись аудита пишется здесь же, в транзакции создания:
    актуальный актор (роль и отделение после перечитывания), ID пациента и ID
    лечащего врача — без ФИО, полиса и медицинских полей. Ошибка ее записи —
    audit.AuditWriteFailed после полного отката."""
    with transaction(conn):
        fresh = policy.reload_current_user(conn, actor)
        if fresh is None:
            raise PatientCreateForbidden("action=patient_create")
        patient_id = _create_locked(conn, fresh, body)
        result = _card(conn, patient_id)
    return result


def create_patient_card_idempotent(
    conn: sqlite3.Connection, actor: policy.CurrentUser, body: PatientCreateIn, key: str,
) -> tuple[dict, bool]:
    """Создание с ключом Idempotency-Key. Возвращает (карточка, повтор ли это).

    Одна транзакция BEGIN IMMEDIATE: свежие права; поиск ключа в области
    (сотрудник, patient.create) — другой отпечаток дает IdempotencyKeyReused;
    завершенный ключ — повтор: карточка видна сотруднику по текущей области,
    обязательный аудит patient_create_replayed, ответ из текущего состояния;
    иначе обычное создание и запись результата ключа вместе с ним."""
    claim = idempotency.claim(actor.id, idempotency.OP_PATIENT_CREATE, key, body.model_dump(mode="json"))
    refusal = None
    with transaction(conn):
        fresh = policy.reload_current_user(conn, actor)
        if fresh is None:
            raise PatientCreateForbidden("action=patient_create")
        row = idempotency.lookup(conn, claim)
        if row is not None:
            patient_id = row["result_patient_id"]
            if not policy.can_read_patient(conn, fresh, patient_id):
                raise PatientReplayNotFound()
            try:
                audit.record_required_or_fail(conn, audit.PATIENT_CREATE_REPLAYED, patient_id=patient_id,
                                              **audit.actor_ctx(fresh))
            except audit.AuditWriteFailed as exc:
                raise ReplayAuditUnavailable() from exc
            result, replayed = _card(conn, patient_id), True
        else:
            # WP2.4-C: отказ «возможный дубль» фиксируется как refused этого ключа:
            # точка сохранения откатывает попытку, отказ сохраняется (повтор того же
            # тела оценивается заново, другое тело с этим ключом — 422).
            conn.execute("SAVEPOINT patient_create_attempt")
            try:
                patient_id = _create_locked(conn, fresh, body)
            except patient_duplicates.PossibleDuplicate as dup:
                conn.execute("ROLLBACK TO patient_create_attempt")
                conn.execute("RELEASE patient_create_attempt")
                idempotency.refuse(conn, claim, 409)
                refusal = dup
            else:
                conn.execute("RELEASE patient_create_attempt")
                idempotency.complete_patient_create(conn, claim, patient_id)
                result, replayed = _card(conn, patient_id), False
    if refusal is not None:
        raise refusal
    return result, replayed
