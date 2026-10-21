"""Ключи идемпотентности (WP2.4-B).

Клиент передает заголовок Idempotency-Key (UUID v4). Ключ относится к
области (сотрудник, операция) и действует 7 дней. Повтор того же ключа с тем
же содержимым возвращает результат первого выполнения, не выполняя операцию
снова; тот же ключ с другим содержимым — отказ 422.

В БД хранятся только хеши: SHA-256 ключа и SHA-256 запроса. Хеш запроса
считается от самого ключа и нормализованного сервером содержимого, поэтому
по нему нельзя подобрать содержимое без ключа (ключ в БД не хранится).
Полных тел запросов, ФИО, полисов и медицинских полей нет.

Все функции, кроме purge_expired, работают внутри транзакции BEGIN IMMEDIATE
вызывающей операции: поиск ключа, операция и запись результата фиксируются
вместе или не фиксируются вовсе; параллельный запрос с тем же ключом ждет
блокировку и видит уже записанный результат.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import config
from .db import now_iso

KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"

OP_PATIENT_CREATE = "patient.create"
OP_IMPORT_COMMIT = "import.commit"
OP_MIGRATION_COMMIT = "migration.commit"

STATE_COMPLETED = "completed"
STATE_REFUSED = "refused"   # резерв WP2.4-C (409 POSSIBLE_DUPLICATE); в WP2.4-B не записывается

INVALID_KEY_CODE = "IDEMPOTENCY_KEY_INVALID"
REUSED_KEY_CODE = "IDEMPOTENCY_KEY_REUSED"
INVALID_KEY_MESSAGE = "Заголовок Idempotency-Key должен содержать UUID версии 4."
REUSED_KEY_MESSAGE = ("Этот ключ запроса уже использован для других данных. "
                      "Операция не выполнена; повторите ее как новый запрос.")

_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class InvalidIdempotencyKey(ValueError):
    """Заголовок передан, но это не UUID v4 (422)."""


class IdempotencyKeyReused(Exception):
    """Тот же ключ уже использован с другим содержимым (422); прежний результат не раскрывается."""


def parse_key(raw: str | None) -> str | None:
    """None — заголовка нет (совместимость прежних клиентов, правила WP2.4-A).
    Переданный, но некорректный ключ (в том числе пустой) — InvalidIdempotencyKey."""
    if raw is None:
        return None
    key = raw.strip().lower()
    if not _UUID4.fullmatch(key):
        raise InvalidIdempotencyKey(INVALID_KEY_MESSAGE)
    return key


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class Claim:
    """Ключ запроса в его области, только в виде хешей."""
    actor_user_id: str
    operation: str
    key_hash: str
    request_hash: str


def claim(actor_user_id: str, operation: str, key: str, payload) -> Claim:
    """payload — нормализованное содержимое, влияющее на результат операции."""
    key_hash = hashlib.sha256(f"idempotency-key\n{key}".encode("utf-8")).hexdigest()
    request_hash = hashlib.sha256(
        f"idempotency-request\n{key}\n{operation}\n{canonical_json(payload)}".encode("utf-8")
    ).hexdigest()
    return Claim(actor_user_id, operation, key_hash, request_hash)


def _expires_at(now: str) -> str:
    moment = datetime.fromisoformat(now)
    return (moment + timedelta(days=config.IDEMPOTENCY_TTL_DAYS)).isoformat()


def lookup(conn: sqlite3.Connection, c: Claim) -> sqlite3.Row | None:
    """Завершенный результат для повтора или None (ключ новый, истек или был отказом).

    Истекший ключ удаляется в этой же транзакции. Другое содержимое при
    действующем ключе — IdempotencyKeyReused."""
    if not conn.in_transaction:
        raise RuntimeError("idempotency lookup must run inside the operation's transaction")
    row = conn.execute(
        "SELECT * FROM idempotency_keys WHERE actor_user_id = ? AND operation = ? AND key_hash = ?",
        (c.actor_user_id, c.operation, c.key_hash),
    ).fetchone()
    if row is None:
        return None
    if row["expires_at"] <= now_iso():
        conn.execute(
            "DELETE FROM idempotency_keys WHERE actor_user_id = ? AND operation = ? AND key_hash = ?",
            (c.actor_user_id, c.operation, c.key_hash),
        )
        return None
    if row["request_hash"] != c.request_hash:
        raise IdempotencyKeyReused(REUSED_KEY_MESSAGE)
    if row["state"] == STATE_REFUSED:
        return None   # отказ не кэшируется: то же содержимое оценивается заново
    return row


def _store(conn: sqlite3.Connection, c: Claim, *, state: str, status_code: int,
           patient_id: str | None = None, import_id: str | None = None, result: dict | None = None) -> None:
    if not conn.in_transaction:
        raise RuntimeError("idempotency result must be stored inside the operation's transaction")
    now = now_iso()
    # Прежний отказ с тем же содержимым заменяется результатом (завершенный ключ
    # изменить нельзя — триггер trg_idempotency_completed_immutable).
    conn.execute(
        "DELETE FROM idempotency_keys WHERE actor_user_id = ? AND operation = ? AND key_hash = ? AND state = ?",
        (c.actor_user_id, c.operation, c.key_hash, STATE_REFUSED),
    )
    conn.execute(
        """INSERT INTO idempotency_keys (actor_user_id, operation, key_hash, request_hash, state, status_code,
                                         result_patient_id, result_import_id, result_json, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (c.actor_user_id, c.operation, c.key_hash, c.request_hash, state, status_code, patient_id, import_id,
         canonical_json(result) if result is not None else None, now, _expires_at(now)),
    )


def complete_patient_create(conn: sqlite3.Connection, c: Claim, patient_id: str) -> None:
    _store(conn, c, state=STATE_COMPLETED, status_code=201, patient_id=patient_id)


def complete_import(conn: sqlite3.Connection, c: Claim, import_id: str, response: dict) -> None:
    """response — обезличенный итог (счетчики и статус происхождения), ответ повтору."""
    _store(conn, c, state=STATE_COMPLETED, status_code=200, import_id=import_id, result=response)


def refuse(conn: sqlite3.Connection, c: Claim, status_code: int) -> None:
    """Запомнить отказ с этим содержимым (WP2.4-C: 409 POSSIBLE_DUPLICATE).
    Повтор с тем же содержимым оценивается заново, с другим — 422."""
    _store(conn, c, state=STATE_REFUSED, status_code=status_code)


def stored_import_response(row: sqlite3.Row) -> dict:
    return json.loads(row["result_json"])


def purge_expired(conn: sqlite3.Connection) -> int:
    """Удалить только истекшие ключи. История файлов и данные пациентов не затрагиваются."""
    with conn:
        cur = conn.execute("DELETE FROM idempotency_keys WHERE expires_at <= ?", (now_iso(),))
        return cur.rowcount
