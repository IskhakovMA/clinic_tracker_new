"""Database integrity checks, backup and restore.

Backup/restore go through the SQLite backup API (`Connection.backup`) rather
than copying the file, so a copy taken while the app is running is
transactionally consistent and correct under WAL. Nothing here prints
patient data — only counts, paths and check results.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from . import config
from .db import REQUIRED_TABLES


class MaintenanceError(RuntimeError):
    """Operator-facing failure (bad file, failed check) — safe to print."""


# --- integrity checks ---------------------------------------------------

def check_database(db_path: Path) -> dict:
    """Run integrity_check, foreign_key_check, schema version and table checks.
    Returns a report dict; never includes row contents."""
    if not db_path.exists():
        raise MaintenanceError(f"База не найдена: {db_path}")

    report: dict = {"path": str(db_path), "ok": True, "problems": []}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
        report["integrity_check"] = integrity
        if integrity != ["ok"]:
            report["ok"] = False
            report["problems"].append("integrity_check failed")

        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        report["foreign_key_violations"] = len(fk_rows)
        if fk_rows:
            report["ok"] = False
            report["problems"].append(f"{len(fk_rows)} foreign key violation(s)")

        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        report["missing_tables"] = missing
        if missing:
            report["ok"] = False
            report["problems"].append("missing tables: " + ", ".join(missing))

        if "schema_version" in tables:
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            version = row["version"] if row else None
            report["schema_version"] = version
            if version != config.SCHEMA_VERSION:
                report["ok"] = False
                report["problems"].append(
                    f"schema version {version} != expected {config.SCHEMA_VERSION}"
                )
        else:
            report["schema_version"] = None

        if "patients" in tables:
            report["patient_count"] = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    finally:
        conn.close()

    return report


def looks_like_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


# --- backup -------------------------------------------------------------

def backup_database(db_path: Path, dest: Path | None = None, *, overwrite: bool = False) -> Path:
    if not db_path.exists():
        raise MaintenanceError(f"База не найдена: {db_path}")

    if dest is None:
        config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = config.BACKUP_DIR / f"clinic_{stamp}.db"
    else:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and not overwrite:
        raise MaintenanceError(f"Файл уже существует: {dest} (используйте --overwrite)")

    source = sqlite3.connect(str(db_path))
    try:
        target = sqlite3.connect(str(dest))
        try:
            with target:
                source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    _restrict_permissions(dest)
    return dest


def _restrict_permissions(path: Path) -> None:
    """Best-effort 0600. POSIX only — on Windows this is a no-op and file
    confidentiality must come from NTFS ACLs plus full-disk encryption."""
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass


# --- restore ------------------------------------------------------------

def validate_backup(backup_path: Path) -> dict:
    """Verify a candidate backup before it is allowed anywhere near the live DB."""
    backup_path = Path(backup_path)
    if not backup_path.exists():
        raise MaintenanceError(f"Файл резервной копии не найден: {backup_path}")
    if not looks_like_sqlite(backup_path):
        raise MaintenanceError("Файл не является базой SQLite")

    report = check_database(backup_path)
    if not report["ok"]:
        raise MaintenanceError("Резервная копия отклонена: " + "; ".join(report["problems"]))
    return report


def restore_database(backup_path: Path, db_path: Path) -> dict:
    """Validate, snapshot the current DB, then replace it atomically.

    The replacement is written to a temp file next to the live database and
    moved into place with os.replace semantics, so the live path is never
    left half-written: either the old database or the restored one.
    """
    backup_path = Path(backup_path)
    report = validate_backup(backup_path)

    pre_restore_backup = None
    if db_path.exists():
        config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_restore_backup = backup_database(
            db_path, config.BACKUP_DIR / f"pre_restore_{stamp}.db"
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    staging = db_path.with_name(db_path.name + ".restore-tmp")
    if staging.exists():
        staging.unlink()

    # Copy through the SQLite backup API into a fresh file, then swap.
    source = sqlite3.connect(str(backup_path))
    try:
        target = sqlite3.connect(str(staging))
        try:
            with target:
                source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    try:
        staging.replace(db_path)
    except OSError as exc:
        staging.unlink(missing_ok=True)
        raise MaintenanceError(f"Не удалось заменить базу: {exc}") from exc

    # Stale WAL/SHM from the previous database would misrepresent the restored file.
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()

    _restrict_permissions(db_path)

    return {
        "restored_from": str(backup_path),
        "pre_restore_backup": str(pre_restore_backup) if pre_restore_backup else None,
        "patient_count": report.get("patient_count"),
        "schema_version": report.get("schema_version"),
    }
