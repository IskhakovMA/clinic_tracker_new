"""Parameterized local operations. No schedule, destination or retention policy is enabled.

Only encrypted artifacts are copied. A supplied replica DIRECTORY is not proof
of off-host/WORM storage: the owner must arrange the separate trust boundary.
"""
import fcntl
import math
import os
import secrets
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import audit, config, db, db_guard, idempotency, maintenance, operations_state, security


@contextmanager
def serialized():
    # One lock order; restore/migrate's exclusive DB lock excludes every cycle.
    with db_guard.shared():
        fd = os.open(config.DATA_DIR / ".clinic-operations.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            operations_state._private(config.DATA_DIR / ".clinic-operations.lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            operations_state.require_permit()
            yield
        finally:
            os.close(fd)


def _directory(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("unsafe backup directory")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError("backup directory must be owner-only")
    return path


def _replicate(source: Path, directory: Path) -> Path:
    directory = _directory(directory)
    target = directory / source.name
    if target == source:
        raise ValueError("replica directory must differ")
    temporary = directory / ("." + source.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        # Create-if-absent, never overwrite a replica or follow a symlink.
        os.link(temporary, target)
        operations_state._sync_directory(directory)
    finally:
        temporary.unlink(missing_ok=True)
    operations_state._private(target)
    maintenance.verify_backup(target)
    return target


def _verified_files(directory: Path) -> list:
    files = sorted(directory.glob("scheduled_*.cbk"))
    if len(files) > 10000:
        raise ValueError("backup directory needs operator review")
    verified = []
    for path in files:
        operations_state._private(path)
        report = maintenance.verify_backup(path)
        # Use the AUTHENTICATED creation time, not a filename or writable mtime.
        created = datetime.fromisoformat(report["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None or created > datetime.now(timezone.utc):
            raise ValueError("invalid authenticated backup time")
        verified.append((created, path))
    return sorted(verified)


def backup_cycle(directory: Path, *, retention_days: int | None = None, replica_directory: Path | None = None) -> dict:
    if retention_days is not None and (type(retention_days) is not int or retention_days < 1):
        raise ValueError("an explicit positive retention is required")
    with serialized():
        directory = _directory(directory)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = directory / ("scheduled_" + stamp + "_" + secrets.token_hex(8) + ".cbk")
        maintenance.backup_database(config.DB_PATH, dest)
        operations_state._private(dest)
        operations_state._sync_directory(directory)
        maintenance.verify_backup(dest)  # AEAD, SQLite, FK, schema, trial migration
        if replica_directory is not None:
            _replicate(dest, Path(replica_directory))
        files = _verified_files(directory)  # any unverifiable managed file aborts pruning
        removed = 0
        if retention_days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            # Always retain the newly verified artifact AND the last verified one.
            protected = {dest, files[-1][1]}
            for created, path in files:
                if created < cutoff and path not in protected:
                    path.unlink()
                    operations_state._sync_directory(directory)
                    removed += 1
        audit.record(db.get_connection(), audit.BACKUP_CREATED, detail="via=backup-cycle verified=1")
        return {"status": "verified", "removed": removed, "replica_verified": replica_directory is not None}


def backup_status(directory: Path, *, max_age_hours: float) -> dict:
    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise ValueError("an explicit positive age is required")
    with serialized():
        files = _verified_files(_directory(directory))
        fresh = bool(files) and datetime.now(timezone.utc) - files[-1][0] <= timedelta(hours=max_age_hours)
        return {"status": "ok" if fresh else "stale", "verified_count": len(files)}


def cleanup(*, audit_days: int | None = None) -> dict:
    with serialized():
        conn = db.get_connection()
        security.purge_expired_sessions(conn)
        idempotency.purge_expired(conn)
        removed = audit.prune(conn, audit_days) if audit_days is not None else 0
        return {"status": "ok", "audit_removed": removed}
