"""Inter-process locks around the database file (WP-B, AUD-04).

Two advisory locks (fcntl.flock) live in DATA_DIR:

- `.clinic-app.lock` (process_guard.LOCK_NAME): the running backend holds it
  exclusively for its lifetime. Maintenance takes it exclusively too, so it
  only succeeds while no backend is running, and no backend can start until
  the maintenance has finished.
- `.clinic-db.lock`: every process that works with the database (the backend
  and the database-using CLI commands, including the notification worker)
  holds it *shared*. Maintenance (restore, migrate-db) needs it *exclusive*,
  so it fails while anybody uses the database, and a CLI command or a backend
  start fails while maintenance runs.

Both are held until the maintenance has completely finished: the file is
replaced, WAL/SHM are handled, the directory is synced and every connection
is closed. The kernel releases flock locks when the holder exits for any
reason (including SIGKILL), so a crashed operation never leaves a stale lock.

A `BEGIN EXCLUSIVE` transaction in SQLite only shows that no other connection
holds a lock at that moment; an idle connection of a running backend holds
none, which is why it is not used as proof that the application is stopped.
Processes that open the file directly (sqlite3 shell, third-party tools) are
not covered by these locks; the operating procedure forbids them during
maintenance.
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config, process_guard

DB_LOCK_NAME = ".clinic-db.lock"


class DatabaseBusyError(RuntimeError):
    """Operator-facing: the lock is held by another process; nothing was changed."""


def _lock_path(name: str) -> Path:
    return config.DATA_DIR / name


class LockUnavailableError(DatabaseBusyError):
    """The lock file cannot be opened (permissions, read-only volume)."""


def _open_lock(name: str) -> int:
    try:
        config.DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        return os.open(str(_lock_path(name)), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise LockUnavailableError(
            f"Файл блокировки {name} в {config.DATA_DIR} недоступен ({type(exc).__name__}): "
            "каталог данных недоступен для записи. База не изменена."
        ) from exc


def _try_lock(name: str, mode: int) -> int | None:
    fd = _open_lock(name)
    try:
        fcntl.flock(fd, mode | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def release(fd: int | None) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def acquire_shared() -> int:
    """Shared database lock for the backend and DB-using CLI commands."""
    fd = _try_lock(DB_LOCK_NAME, fcntl.LOCK_SH)
    if fd is None:
        raise DatabaseBusyError(
            f"Идёт обслуживание базы (restore или migrate-db) в {config.DATA_DIR}; "
            "дождитесь его завершения. База не изменена."
        )
    return fd


@contextmanager
def shared() -> Iterator[None]:
    fd = acquire_shared()
    try:
        yield
    finally:
        release(fd)


@contextmanager
def exclusive_maintenance(operation: str) -> Iterator[None]:
    """Exclusive access for restore/migrate-db; refuses before any change.

    The application lock is taken first: while the backend runs it fails
    immediately. Then the database lock: it fails while a CLI command,
    the notification worker or another maintenance operation uses the file.
    """
    app_fd = _try_lock(process_guard.LOCK_NAME, fcntl.LOCK_EX)
    if app_fd is None:
        raise DatabaseBusyError(
            f"{operation} отклонён: приложение запущено на {config.DATA_DIR}. "
            "Остановите сервис (systemctl stop / Ctrl+C) и повторите. База не изменена."
        )
    try:
        db_fd = _try_lock(DB_LOCK_NAME, fcntl.LOCK_EX)
        if db_fd is None:
            raise DatabaseBusyError(
                f"{operation} отклонён: базу использует другой процесс (CLI-команда, "
                "notification worker или другой restore/migrate-db). База не изменена."
            )
        try:
            yield
        finally:
            release(db_fd)
    finally:
        release(app_fd)


def try_exclusive_read() -> tuple[int, int] | None:
    """Both locks exclusively without waiting, or None if anyone uses the DB.

    Used by read-only diagnostics to decide whether a file copy is
    consistent (nobody can write while the locks are held)."""
    app_fd = _try_lock(process_guard.LOCK_NAME, fcntl.LOCK_EX)
    if app_fd is None:
        return None
    db_fd = _try_lock(DB_LOCK_NAME, fcntl.LOCK_EX)
    if db_fd is None:
        release(app_fd)
        return None
    return app_fd, db_fd
