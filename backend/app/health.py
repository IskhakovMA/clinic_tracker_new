"""Public probes: bounded metadata only, no PHI, paths or raw exceptions."""
import sqlite3
import shutil

from . import config, db, operations_state


def readiness() -> dict:
    try:
        if (config.DATA_DIR / operations_state.PENDING).exists():
            return {"status": "not_ready", "reason": "maintenance_pending"}
        if shutil.disk_usage(config.DATA_DIR).free == 0:
            return {"status": "not_ready", "reason": "storage_unavailable"}
        if not config.DB_PATH.is_file():
            return {"status": "not_ready", "reason": "database_unavailable"}
        wal = config.DB_PATH.with_name("clinic.db-wal")
        shm = config.DB_PATH.with_name("clinic.db-shm")
        if wal.exists() and wal.stat().st_size and not shm.exists():
            return {"status": "not_ready", "reason": "database_check_required"}
        conn = sqlite3.connect(config.DB_PATH.as_uri() + "?mode=ro", uri=True, timeout=0.25)
        conn.row_factory = sqlite3.Row
        try:
            versions = conn.execute("SELECT version FROM schema_version").fetchall()
            if len(versions) != 1 or not isinstance(versions[0][0], int):
                return {"status": "not_ready", "reason": "database_check_required"}
            if versions[0][0] < config.SCHEMA_VERSION:
                return {"status": "not_ready", "reason": "migration_required"}
            if versions[0][0] != config.SCHEMA_VERSION or any(db.trigger_drift(conn).values()):
                return {"status": "not_ready", "reason": "database_check_required"}
            from . import quality_revision
            quality_revision.require_valid(conn, structure=True)
        finally:
            conn.close()
        operations_state.require_permit()
    except operations_state.AdmissionRequired as exc:
        return {"status": "not_ready", "reason": exc.code}
    except (OSError, sqlite3.Error):
        return {"status": "not_ready", "reason": "database_check_required"}
    return {"status": "ready"}


def startup_check() -> None:
    from . import maintenance
    operations_state.require_permit()
    operations_state._files_snapshot()  # refuse torn WAL before SQLite could ignore a tail
    report = maintenance.check_database(config.DB_PATH, read_only=True)
    if not report["ok"]:
        if report.get("schema_status") == "older":
            raise db.SchemaUpgradeRequiredError("Explicit migrate-db required for older schema")
        if report.get("schema_status") in {"newer", "damaged"}:
            raise db.SchemaVersionError("Database schema v" + str(report.get("schema_version")) + " is unsupported")
        drift = report.get("trigger_drift") or {}
        if drift.get("missing") or drift.get("changed"):
            raise db.SchemaDriftError("Protective triggers differ: " + ", ".join(drift.get("missing", []) + drift.get("changed", [])))
        raise operations_state.AdmissionRequired("database_check_required")
