"""WP-F operational admission v1. No medical schema or backup-format change.

Pending is published and synced BEFORE maintenance, and any old permit is
revoked before a database mutation. Only the explicit offline verification
command publishes a permit. Missing state is never interpreted as success.
Files/SQLite access by a malicious service-account/root actor is outside this
boundary, like the existing advisory maintenance locks.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import stat
import struct
from pathlib import Path

from . import config, db_guard

PENDING = ".clinic-pending.json"
PERMIT = ".clinic-permit.json"
GENERATION = ".clinic-generation.json"
VERSION = 1


class AdmissionRequired(RuntimeError):
    """Safe for public diagnostics: never includes paths or database values."""

    def __init__(self, code="operator_verification_required"):
        self.code = code
        super().__init__(code + ": offline verify-db required")


def _directory() -> Path:
    root = config.DATA_DIR
    st = root.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise AdmissionRequired("unsafe_permissions")
    return root


def _private(path: Path):
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o177 or not st.st_mode & 0o400 or st.st_nlink != 1:
        raise AdmissionRequired("unsafe_permissions")
    return st


def _sync_directory(root: Path) -> None:
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read(name: str) -> dict:
    path = _directory() / name
    _private(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if st.st_size > 4096:
            raise AdmissionRequired("invalid_admission")
        raw = os.read(fd, 4097)
    finally:
        os.close(fd)

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate admission field")
            result[key] = value
        return result

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict) or type(value.get("version")) is not int or value.get("version") != VERSION:
        raise AdmissionRequired("invalid_admission")
    generation = value.get("generation")
    if not isinstance(generation, str) or len(generation) != 32 or any(c not in "0123456789abcdef" for c in generation):
        raise AdmissionRequired("invalid_admission")
    return value


def _publish(name: str, value: dict) -> None:
    root = _directory()
    target = root / name
    if target.exists() or target.is_symlink():
        _private(target)
    tmp = root / (name + "." + secrets.token_hex(8) + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
        _sync_directory(root)  # rename visibility alone never proves durability
    finally:
        tmp.unlink(missing_ok=True)


def begin(operation: str) -> str:
    """Caller holds BOTH exclusive locks. Nothing can mutate the DB before return."""
    if operation not in {"restore", "migrate-db", "bootstrap", "verify-db"}:
        raise ValueError("unknown maintenance operation")
    root = _directory()
    generation = secrets.token_hex(16)
    _publish(PENDING, {"version": VERSION, "generation": generation, "operation": operation})
    _publish(GENERATION, {"version": VERSION, "generation": generation})
    permit = root / PERMIT
    if permit.exists() or permit.is_symlink():
        _private(permit)
        permit.unlink()
        _sync_directory(root)
    return generation


def require_permit(path: Path | None = None) -> dict:
    """Cheap admission check for EVERY normal DB access, including singleton reuse."""
    try:
        root = _directory()
        if (root / PENDING).exists() or (root / PENDING).is_symlink():
            raise AdmissionRequired("maintenance_pending")
        if not (root / PERMIT).exists() and config.DB_PATH.is_file() and not config.DB_PATH.is_symlink():
            # Preserve the explicit, read-only old-schema refusal. Never open a
            # legacy WAL here or mutate/enrol a database as a side effect.
            wal = Path(str(config.DB_PATH) + "-wal")
            if not wal.exists() or wal.stat().st_size == 0:
                from . import db
                conn = sqlite3.connect(config.DB_PATH.as_uri() + "?mode=ro&immutable=1", uri=True)
                conn.row_factory = sqlite3.Row
                try:
                    db._require_no_upgrade(conn)
                finally:
                    conn.close()
        permit = _read(PERMIT)
        anchor = _read(GENERATION)
        if set(anchor) != {"version", "generation"} or anchor["generation"] != permit["generation"]:
            raise AdmissionRequired("generation_mismatch")
        if set(permit) != {"version", "generation", "database", "schema"} or permit["schema"] != config.SCHEMA_VERSION:
            raise AdmissionRequired("invalid_admission")
        path = config.DB_PATH if path is None else Path(path)
        if path.absolute() != config.DB_PATH.absolute():
            raise AdmissionRequired("database_identity_changed")
        st = _private(path)
        if not st.st_mode & 0o200:
            raise AdmissionRequired("unsafe_permissions")
        if permit["database"] != {"device": st.st_dev, "inode": st.st_ino}:
            raise AdmissionRequired("database_identity_changed")
        return permit
    except AdmissionRequired:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise AdmissionRequired() from exc


def _checksum(raw: bytes, endian: str, pair=(0, 0)):
    words = struct.unpack(endian + str(len(raw) // 4) + "I", raw)
    a, b = pair
    for i in range(0, len(words), 2):
        a = (a + words[i] + b) & 0xffffffff
        b = (b + words[i + 1] + a) & 0xffffffff
    return a, b


def _validate_wal(path: Path) -> None:
    """Conservative check: refuse torn/corrupt tails instead of silently ignoring them.

    Format/checksum: sqlite.org/fileformat2.html sections 4.1/4.2. A stale or
    torn WAL may need operator recovery; verification never edits it.
    """
    with path.open("rb") as stream:
        header = stream.read(32)
        if not header:
            return
        if len(header) != 32:
            raise AdmissionRequired("database_check_required")
        magic, version, page = struct.unpack(">III", header[:12])
        if magic not in (0x377f0682, 0x377f0683) or version != 3007000 or page < 512 or page > 65536 or page & (page - 1):
            raise AdmissionRequired("database_check_required")
        endian = "<" if magic == 0x377f0682 else ">"
        pair = _checksum(header[:24], endian)
        if pair != struct.unpack(">II", header[24:]):
            raise AdmissionRequired("database_check_required")
        while True:
            frame = stream.read(page + 24)
            if not frame:
                break
            if len(frame) != page + 24 or frame[8:16] != header[16:24] or struct.unpack(">I", frame[:4])[0] == 0:
                raise AdmissionRequired("database_check_required")
            pair = _checksum(frame[:8] + frame[24:], endian, pair)
            if pair != struct.unpack(">II", frame[16:24]):
                raise AdmissionRequired("database_check_required")


def _files_snapshot() -> dict:
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(str(config.DB_PATH) + suffix)
        if path.exists() or path.is_symlink():
            st = _private(path)
            if not st.st_mode & 0o200:
                raise AdmissionRequired("unsafe_permissions")
            if suffix == "-journal" and st.st_size:
                raise AdmissionRequired("database_check_required")
            if suffix == "-wal":
                _validate_wal(path)
            if suffix == "-shm" and st.st_size and (st.st_size < 32768 or st.st_size % 32768):
                raise AdmissionRequired("database_check_required")
            result[suffix] = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    if "" not in result:
        raise AdmissionRequired("database_unavailable")
    return result


def _verify_locked() -> dict:
    from . import maintenance
    root = _directory()
    pending_path = root / PENDING
    if not pending_path.exists() and not pending_path.is_symlink():
        # Adoption/reverification must also stay blocked on a failed check or
        # failed permit publication. Never reuse an earlier positive result.
        begin("verify-db")
    if pending_path.exists() or pending_path.is_symlink():
        pending = _read(PENDING)
        if set(pending) != {"version", "generation", "operation"} or pending["operation"] not in {"restore", "migrate-db", "bootstrap", "verify-db"}:
            raise AdmissionRequired("invalid_admission")
        generation = pending["generation"]
        anchor = _read(GENERATION)
        if set(anchor) != {"version", "generation"} or anchor["generation"] != generation:
            raise AdmissionRequired("generation_mismatch")
    before = _files_snapshot()
    report = maintenance._diagnose_copy(config.DB_PATH)
    if not report["ok"]:
        code = "migration_required" if report.get("schema_status") == "older" else "database_check_required"
        raise AdmissionRequired(code)
    if _files_snapshot() != before:
        raise AdmissionRequired("database_changed_during_check")
    for suffix in before:
        fd = os.open(str(config.DB_PATH) + suffix, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    _sync_directory(root)
    if _files_snapshot() != before:
        raise AdmissionRequired("database_changed_during_check")
    st = _private(config.DB_PATH)
    permit = {"version": VERSION, "generation": generation, "database": {"device": st.st_dev, "inode": st.st_ino}, "schema": config.SCHEMA_VERSION}
    # All database checks and database durability barriers precede publication.
    _publish(PERMIT, permit)
    if pending_path.exists():
        if _read(PENDING)["generation"] != generation:
            raise AdmissionRequired("invalid_admission")
        try:
            pending_path.unlink()
            _sync_directory(root)
        except OSError:
            # DB verification AND permit durability already succeeded. Try to
            # retain the conservative block; never claim deletion was durable.
            try:
                _publish(PENDING, pending)
            except OSError:
                pass
            raise
    return {"status": "verified", "schema_version": config.SCHEMA_VERSION, "generation": generation}


def verify_database(*, confirm_offline: bool = False) -> dict:
    if not confirm_offline:
        raise AdmissionRequired("offline_confirmation_required")
    with db_guard.exclusive_maintenance("verify-db"):
        return _verify_locked()


def bootstrap_database(*, confirm_new: bool = False) -> dict:
    """Explicit NEW-installation setup; leaves pending for separate verify-db."""
    from . import db
    if not confirm_new:
        raise AdmissionRequired("new_installation_confirmation_required")
    config.DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with db_guard.exclusive_maintenance("bootstrap-db"):
        if config.DB_PATH.exists() or config.DB_PATH.is_symlink():
            raise AdmissionRequired("database_already_exists")
        begin("bootstrap")
        fd = os.open(config.DB_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        conn = db._connect(config.DB_PATH, enable_wal=False)
        try:
            db.init_db(conn, allow_upgrade=False)
        finally:
            conn.close()
        return {"status": "created_pending_verification"}
