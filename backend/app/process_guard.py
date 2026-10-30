"""Single-process guard for the in-memory request limiters.

security.check_request_rate_limit and the login limiter keep their counters
in this process's memory. With several worker processes every one of them
would count separately and the effective limit would silently grow by the
number of workers. Until a shared limiter exists the backend refuses to run
that way:

- an explicit multi-worker setting (WEB_CONCURRENCY or UVICORN_WORKERS > 1)
  stops the start;
- each process takes an exclusive lock on DATA_DIR/.clinic-app.lock for its
  lifetime, so a second process on the same data directory (another uvicorn
  worker, a second instance) fails to start instead of serving with its own
  counters.
"""
from __future__ import annotations

import fcntl
import os
import stat

from . import config

LOCK_NAME = ".clinic-app.lock"
WORKER_SETTINGS = ("WEB_CONCURRENCY", "UVICORN_WORKERS")


class MultipleProcessesError(RuntimeError):
    pass


def _check_worker_settings() -> None:
    for name in WORKER_SETTINGS:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            workers = int(raw)
        except ValueError as exc:
            raise MultipleProcessesError(f"{name} is not a number of workers") from exc
        if workers > 1:
            raise MultipleProcessesError(
                f"{name}={workers}: the request limiters are per process, so the backend runs as a "
                "single process only. Start one uvicorn process without --workers."
            )


def acquire() -> int:
    """Take the single-process lock; returns the descriptor to release."""
    _check_worker_settings()
    config.DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(config.DATA_DIR / LOCK_NAME), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
        os.close(fd)
        raise MultipleProcessesError("Unsafe application lock")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise MultipleProcessesError(
            f"another backend process already serves {config.DATA_DIR}, or a restore/migrate-db "
            "maintenance holds it: the request limiters are per process, so only one process may run "
            "(no --workers, no second instance); wait for the maintenance to finish."
        ) from exc
    return fd


def release(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
