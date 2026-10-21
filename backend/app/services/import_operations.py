"""Операции импорта и переноса localStorage с ключом идемпотентности и
историей обработанных файлов (WP2.4-B).

Три независимых механизма:
- B2 — ключ Idempotency-Key операции: повтор того же commit возвращает
  сохраненный итог первого выполнения (200, Idempotent-Replayed) и не
  выполняет импорт снова;
- B3 — история обработанных файлов: повтор файла, который этот же сотрудник
  уже успешно импортировал, — только с отдельным подтверждением (428 без него);
- B3 — повторяющиеся полисы внутри файла проверяет importexport (U9).

Порядок внутри одной транзакции BEGIN IMMEDIATE: сначала ключ (повтор
сетевого запроса не попадает в сценарий «файл уже импортирован»), затем все
прежние проверки импорта (их отказы сохраняют приоритет), затем — перед самой
записью — повторяющиеся полисы файла (U9) и история файла (U4), затем запись
импорта с обязательным аудитом, истории файла и результата ключа. Отказ на
любом шаге откатывает все.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from .. import audit, idempotency, importexport, policy
from ..db import now_iso, transaction

OPERATIONS = {"import": idempotency.OP_IMPORT_COMMIT, "migration": idempotency.OP_MIGRATION_COMMIT}
REPLAY_EVENTS = {"import": audit.IMPORT_REPLAYED, "migration": audit.MIGRATION_REPLAYED}
FILE_ALREADY_IMPORTED_CODE = "FILE_ALREADY_IMPORTED"
FILE_ALREADY_IMPORTED_MESSAGE = (
    "Этот файл вы уже успешно импортировали. Повторный импорт выполняется только после "
    "отдельного подтверждения именно этого файла и режима. Данные не изменены."
)


class FileAlreadyImported(Exception):
    """Сотрудник уже обрабатывал этот файл, подтверждения повтора нет (428)."""

    def __init__(self, previous: dict):
        self.previous = previous
        super().__init__(FILE_ALREADY_IMPORTED_MESSAGE)


class ImportForbidden(Exception):
    """Права на операцию, перечитанные под блокировкой записи, ее не допускают (403):
    роль сменена, сотрудник заблокирован или режим больше не разрешен. Относится и
    к новому выполнению, и к повтору по ключу; данные не меняются (B-01)."""


# Прежнее имя (повтор по ключу) — тот же отказ.
ImportReplayForbidden = ImportForbidden


class ReplayAuditUnavailable(Exception):
    """Обязательная запись о повторе не записана (503); итог не возвращается."""


@dataclass(frozen=True)
class Outcome:
    response: dict
    import_id: str
    replayed: bool


def previous_processing(conn: sqlite3.Connection, actor_user_id: str, file_sha256: str) -> dict | None:
    """Последняя успешная обработка этого файла этим же сотрудником (только
    технические сведения). Обработки других сотрудников не учитываются."""
    row = conn.execute(
        """SELECT processed_at, operation, mode, added, updated, unchanged, skipped, appended_events
           FROM import_files_processed WHERE actor_user_id = ? AND file_sha256 = ?
           ORDER BY processed_at DESC, rowid DESC LIMIT 1""",
        (actor_user_id, file_sha256),
    ).fetchone()
    if row is None:
        return None
    return {"importedAt": row["processed_at"], "operation": row["operation"], "mode": row["mode"],
            "added": row["added"], "updated": row["updated"], "unchanged": row["unchanged"],
            "skipped": row["skipped"], "appendedEvents": row["appended_events"]}


def _record_file(conn: sqlite3.Connection, *, import_id: str, actor_user_id: str, operation: str,
                 file_sha256: str, mode: str, result: dict, repeat_confirmed: bool) -> None:
    conn.execute(
        """INSERT INTO import_files_processed (import_id, actor_user_id, operation, file_sha256, mode, processed_at,
                                               added, updated, skipped, unchanged, appended_events, repeat_confirmed)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (import_id, actor_user_id, operation, file_sha256, mode, now_iso(), result["added"], result["updated"],
         result["skipped"], result["unchanged"], result["appendedEvents"], int(repeat_confirmed)),
    )


def run(
    conn: sqlite3.Connection,
    *,
    actor: policy.CurrentUser,
    operation: str,
    mode: str,
    file_sha256: str,
    import_id: str,
    key: str | None,
    fingerprint: dict,
    repeat_confirmed: bool,
    check_file_history: bool,
    execute: Callable[[Callable[[], None], policy.CurrentUser], dict],
    respond: Callable[[dict], dict],
) -> Outcome:
    """Выполнить импорт (execute(before_write, fresh) — commit_import во внешней
    транзакции от имени fresh; before_write проверяет историю файла после всех
    прежних проверок импорта, непосредственно перед записью) или вернуть
    сохраненный итог повтора по ключу.

    B-01: сразу после получения блокировки записи права перечитываются из БД
    (reload_current_user) и проверяются для режима — одинаково для нового
    выполнения и повтора, с ключом и без. Импорт, выбор врача закрепления и
    весь обязательный аудит выполняются от имени перечитанного сотрудника.

    respond(result) строит обезличенный ответ клиенту; он же сохраняется как
    итог ключа. Аудит самого импорта пишет execute (как раньше)."""
    if operation not in OPERATIONS:
        raise ValueError("unknown import operation")
    claim = idempotency.claim(actor.id, OPERATIONS[operation], key, fingerprint) if key else None
    refusal = None
    with transaction(conn):
        fresh = policy.reload_current_user(conn, actor)
        if fresh is None or not policy.can_import(fresh, mode):
            raise ImportForbidden()
        if claim is not None:
            row = idempotency.lookup(conn, claim)
            if row is not None:
                try:
                    audit.record_required_or_fail(
                        conn, REPLAY_EVENTS[operation],
                        detail=f"import_id={row['result_import_id']} mode={mode} file_sha256={file_sha256}",
                        **audit.actor_ctx(fresh))
                except audit.AuditWriteFailed as exc:
                    raise ReplayAuditUnavailable() from exc
                return Outcome(idempotency.stored_import_response(row), row["result_import_id"], True)
        repeat = {"confirmed": False}

        def before_write() -> None:
            if not check_file_history:
                return
            previous = previous_processing(conn, fresh.id, file_sha256)
            if previous is not None:
                if not repeat_confirmed:
                    raise FileAlreadyImported(previous)
                repeat["confirmed"] = True

        # WP2.4-C: отказ «возможные дубли» фиксируется как refused этого ключа —
        # точка сохранения откатывает попытку целиком, отказ сохраняется.
        conn.execute("SAVEPOINT import_attempt")
        try:
            result = execute(before_write, fresh)
        except importexport.ImportPossibleDuplicates as dup:
            conn.execute("ROLLBACK TO import_attempt")
            conn.execute("RELEASE import_attempt")
            if claim is not None:
                idempotency.refuse(conn, claim, 409)
            refusal = dup
        else:
            conn.execute("RELEASE import_attempt")
            if repeat["confirmed"]:
                audit.record_required(
                    conn, audit.IMPORT_REPEAT_CONFIRMED,
                    detail=f"import_id={import_id} mode={mode} file_sha256={file_sha256}",
                    **audit.actor_ctx(fresh))
            _record_file(conn, import_id=import_id, actor_user_id=fresh.id, operation=operation,
                         file_sha256=file_sha256, mode=mode, result=result, repeat_confirmed=repeat["confirmed"])
            response = respond(result)
            if claim is not None:
                idempotency.complete_import(conn, claim, import_id, response)
    if refusal is not None:
        raise refusal
    return Outcome(response, import_id, False)
