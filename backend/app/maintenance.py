"""Database integrity checks, encrypted backup and restore.

Backup/restore go through the SQLite backup API (`Connection.backup`) rather
than copying the file, so a copy taken while the app is running is
transactionally consistent and correct under WAL. Backup files are encrypted
with an AEAD container; nothing here prints patient data or key material.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config, db_guard, operations_state
from . import db as _db
from .db import REQUIRED_TABLES, connect_readonly_check, init_db


class MaintenanceError(RuntimeError):
    """Operator-facing failure (bad file, failed check) — safe to print."""


class CommittedButUnverifiedError(MaintenanceError):
    """The operation passed its commit point, a later step failed.

    restore: os.replace already installed the restored file, but the directory
    sync (durability) or the file permissions could not be confirmed.
    migrate-db: init_db committed the new schema, but the final check did not
    complete or found problems. The database is NOT unchanged; nothing is
    retried automatically. `state` is a short machine-readable label for the
    CLI and the audit event; `details` holds non-PHI paths for the operator."""

    def __init__(self, message: str, *, operation: str, state: str, details: dict):
        super().__init__(message)
        self.operation = operation
        self.state = state
        self.details = details


MAGIC = b"CTBK"
FORMAT_VERSION = 1
ALGORITHM = "AES-256-GCM"
HEADER_LEN_BYTES = 4
NONCE_BYTES = 12
KEY_BYTES = 32
DEFAULT_BACKUP_SUFFIX = ".cbk"
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_STATIC_DIR = _REPO_ROOT / "static"
MAX_HEADER_BYTES = int(os.environ.get("CLINIC_BACKUP_MAX_HEADER_BYTES", str(16 * 1024)))
MIN_HEADER_BYTES = 2
MAX_BACKUP_BYTES = int(os.environ.get("CLINIC_BACKUP_MAX_BYTES", str(1024 * 1024 * 1024)))
SQLITE_EXCLUSIVE_TIMEOUT_MS = int(os.environ.get("CLINIC_RESTORE_EXCLUSIVE_TIMEOUT_MS", "1000"))
HEADER_FIELDS = frozenset(
    {
        "format",
        "version",
        "algorithm",
        "key_id",
        "nonce",
        "created_at",
        "schema_version",
        "ciphertext_length",
    }
)


@dataclass(frozen=True)
class BackupKey:
    key_id: str
    key: bytes


# --- integrity checks ---------------------------------------------------

DEMO_ENV = "demo"
DEMO_MARKER_NAME = ".clinic-demo"


def require_demo_environment() -> Path:
    """Refuse to run a demo-data script outside an explicitly marked demo setup.

    Two independent signals are required: CLINIC_ENV=demo for the process and
    a `.clinic-demo` marker file in the configured data directory. An
    environment variable alone is too easy to leave set in a shell that later
    points at a real database; the marker ties the permission to one data
    directory. Returns the configured database path.
    """
    if config.APP_ENV != DEMO_ENV:
        raise MaintenanceError("Демо-скрипт разрешён только при CLINIC_ENV=demo")
    marker = config.DATA_DIR / DEMO_MARKER_NAME
    if marker.is_symlink() or not marker.is_file():
        raise MaintenanceError(
            f"Каталог данных не помечен как демо: нет файла {DEMO_MARKER_NAME} в {config.DATA_DIR}"
        )
    return config.DB_PATH


def _readonly_uri(path: Path) -> str:
    return Path(path).resolve().as_uri() + "?mode=ro"


def check_database(db_path: Path, *, read_only: bool = False) -> dict:
    """Run integrity_check, foreign_key_check, schema version and table checks.
    Returns a report dict; never includes row contents.

    read_only=True opens the file with SQLite `mode=ro`: the main file and the
    WAL are not written (used on the live file only while another process has
    it open, see diagnose_database). Otherwise the caller passes a private
    copy. A file SQLite cannot read is reported as a problem, not raised."""
    if not db_path.exists():
        raise MaintenanceError(f"База не найдена: {db_path}")

    report: dict = {"path": str(db_path), "ok": True, "problems": [], "integrity_check": [],
                    "foreign_key_violations": 0, "missing_tables": [], "schema_version": None,
                    "schema_status": None}
    if not looks_like_sqlite(db_path):
        report["ok"] = False
        report["schema_status"] = "unreadable"
        report["problems"].append("file is not a SQLite database (header)")
        return report
    try:
        if read_only:
            conn = sqlite3.connect(_readonly_uri(db_path), uri=True)
        else:
            conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        report["ok"] = False
        report["schema_status"] = "unreadable"
        report["problems"].append(f"SQLite cannot open the file: {type(exc).__name__}")
        return report
    conn.row_factory = sqlite3.Row
    try:
        _fill_check_report(conn, report)
    except sqlite3.DatabaseError as exc:
        report["ok"] = False
        report["schema_status"] = report["schema_status"] or "unreadable"
        report["problems"].append(f"SQLite error while checking: {type(exc).__name__}: {exc}"[:300])
    finally:
        conn.close()
    return report


def _fill_check_report(conn: sqlite3.Connection, report: dict) -> None:
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
        if _db.schema_version_problem(conn) == "damaged":
            report["ok"] = False
            report["schema_status"] = "damaged"
            report["problems"].append("schema version damaged (several rows or not an integer)")
        elif version != config.SCHEMA_VERSION:
            report["ok"] = False
            newer = isinstance(version, int) and version > config.SCHEMA_VERSION
            report["schema_status"] = "newer" if newer else "older"
            report["problems"].append(
                f"schema version {version} != expected {config.SCHEMA_VERSION}"
                + ("" if newer else " (older schema: explicit migrate-db required, see OPERATIONS.md)")
            )
        else:
            report["schema_status"] = "current"
    else:
        report["schema_version"] = None
        report["schema_status"] = "missing"

    if report.get("schema_version") == config.SCHEMA_VERSION:
        # Текущая схема: таблицы со строгой сверкой должны совпадать с эталоном
        # этой сборки целиком — с правилами CHECK, индексами и триггерами.
        expected = _current_structure()[1]
        actual = _strict_structure(conn, STRICT_TABLES)
        for table in STRICT_TABLES:
            if actual[table] is None or actual[table] == expected[table]:
                continue   # отсутствующую таблицу уже сообщает missing tables
            differs = sorted(k for k in expected[table] if actual[table].get(k) != expected[table][k])
            report["ok"] = False
            report["problems"].append(
                f"structure of {table} differs from this build: {', '.join(differs)}")
        # WP-C (AUD-14): определения триггеров сверяются с эталоном этой сборки по тексту,
        # а не только по именам. Подмененная или отсутствующая защита — проблема;
        # посторонний триггер — предупреждение (о нем сообщается, работа не блокируется).
        drift = _db.trigger_drift(conn)
        report["trigger_drift"] = {"missing": drift["missing"], "changed": drift["changed"]}
        report["unknown_triggers"] = drift["unknown"]
        if drift["missing"] or drift["changed"]:
            report["ok"] = False
            report["problems"].append(
                "protective triggers differ from this build ("
                + ", ".join(drift["missing"] + drift["changed"]) + "): run migrate-db --confirm-offline to restore")

    if "patients" in tables:
        report["patient_count"] = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]


def _sidecars(db_path: Path) -> dict[str, Path]:
    return {suffix: db_path.with_name(db_path.name + suffix) for suffix in ("-wal", "-shm")}


def _copy_for_diagnosis(db_path: Path, temp_dir: Path) -> Path:
    """Raw copy of the main file and its WAL into a private directory.

    Only called while both maintenance locks are held, so no process of the
    application writes during the copy. The -shm file is not copied: SQLite
    rebuilds the WAL index from the WAL in the copy, so committed transactions
    that were not yet checkpointed are part of the check."""
    copy = temp_dir / "diagnose.sqlite"
    shutil.copyfile(db_path, copy)
    wal = _sidecars(db_path)["-wal"]
    if wal.exists():
        shutil.copyfile(wal, temp_dir / "diagnose.sqlite-wal")
    for path in temp_dir.iterdir():
        _restrict_permissions(path)
    return copy


def _diagnose_copy(db_path: Path) -> dict:
    """Check a private raw copy; the caller holds the exclusive locks."""
    with tempfile.TemporaryDirectory(prefix="clinic-check-", dir=str(db_path.parent)) as tmp_name:
        temp_dir = Path(tmp_name)
        _ensure_private_dir(temp_dir)
        copy = _copy_for_diagnosis(db_path, temp_dir)
        report = check_database(copy)
    report["method"] = "copy"
    report["path"] = str(db_path)
    return report


def _file_state(db_path: Path):
    st = db_path.stat()
    return (st.st_ino, st.st_size, st.st_mtime_ns, _sidecars(db_path)["-wal"].exists())


def _diagnose_idle_copy(db_path: Path, attempts: int = 3) -> dict:
    """Copy of a file that another process may open meanwhile.

    The copy counts only if the file (inode, size, mtime) did not change and
    no WAL appeared while it was taken; otherwise it is retried, then refused."""
    for _ in range(attempts):
        before = _file_state(db_path)
        with tempfile.TemporaryDirectory(prefix="clinic-check-", dir=str(db_path.parent)) as tmp_name:
            temp_dir = Path(tmp_name)
            _ensure_private_dir(temp_dir)
            copy = _copy_for_diagnosis(db_path, temp_dir)
            if _file_state(db_path) != before or before[3]:
                continue
            report = check_database(copy)
        report["method"] = "copy-while-idle"
        report["path"] = str(db_path)
        return report
    raise MaintenanceError(
        "База менялась во время снятия копии для проверки; повторите check-db позже или после "
        "остановки приложения. База не изменена."
    )


def diagnose_database(db_path: Path) -> dict:
    """check-db: diagnosis that never changes the database (AUD-02).

    - No schema creation, no migration, no audit record, no WAL switch.
    - A missing file or directory is reported; nothing is created.
    - While nobody uses the database (both maintenance locks are free) the
      main file and WAL are copied into a private temporary directory and the
      copy is checked; SQLite never opens the live file, so not even -shm is
      created. The locks are held during the copy so the application cannot
      start meanwhile.
    - While the application or another process has the file open, the live
      file is opened with SQLite mode=ro under the shared lock: the main file
      and the WAL are not written; the -shm (shared memory of the running
      process) already exists then. If it does not exist, the check refuses
      instead of creating it.
    """
    db_path = Path(db_path)
    if not db_path.parent.is_dir() or not db_path.exists():
        raise MaintenanceError(f"База не найдена: {db_path}. Ничего не создано.")
    if db_path.is_symlink() or not db_path.is_file():
        raise MaintenanceError("Путь базы должен быть обычным файлом (не symlink)")
    sidecars = _sidecars(db_path)
    present = {suffix: path.exists() for suffix, path in sidecars.items()}
    if db_path.stat().st_size == 0:
        return {"path": str(db_path), "ok": False, "method": "none", "schema_status": "empty",
                "problems": ["database file is empty (0 bytes)"], "integrity_check": [],
                "foreign_key_violations": 0, "missing_tables": [], "schema_version": None,
                "wal_present": present["-wal"], "shm_present": present["-shm"]}
    try:
        held = db_guard.try_exclusive_read()
    except db_guard.DatabaseBusyError as exc:
        raise MaintenanceError(str(exc)) from exc
    if held is not None:
        try:
            report = _diagnose_copy(db_path)
        finally:
            db_guard.release(held[1])
            db_guard.release(held[0])
    else:
        try:
            with db_guard.shared():
                if present["-wal"] and present["-shm"]:
                    report = check_database(db_path, read_only=True)
                    report["method"] = "live-read-only"
                else:
                    # Нет открытых соединений (нет WAL/SHM): mode=ro создал бы их,
                    # поэтому проверяется копия, снятая, пока файл не менялся.
                    report = _diagnose_idle_copy(db_path)
        except db_guard.DatabaseBusyError as exc:
            raise MaintenanceError(str(exc)) from exc
    report["path"] = str(db_path)
    report["wal_present"] = present["-wal"]
    report["shm_present"] = present["-shm"]
    return report


CONSILIUM_SCHEDULE_PROBLEM_KEYS = (
    "nonCanonicalAppointmentDates", "nonCanonicalAppointmentTimes", "unknownAppointmentGroups",
    "nonCanonicalSlotDates", "nonCanonicalSlotTimes", "appointmentsOutsideGrid",
    "realSlotsWithSeveralAppointments", "patientsWithSeveralOpenRecords",
)


def consilium_schedule_report(db_path: Path) -> dict:
    """Aggregate check of stored consilium schedule data (WP2.3-A).

    Opens the file read-only (mode=ro): nothing is migrated or corrected.
    The report holds counts only — no record ids, names or diagnoses."""
    from .services import consilium

    if not db_path.exists():
        raise MaintenanceError(f"База не найдена: {db_path}")
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        report = consilium.schedule_diagnostics(conn)
    except sqlite3.Error as exc:
        raise MaintenanceError(f"Не удалось прочитать расписание консилиума: {exc.__class__.__name__}") from exc
    finally:
        conn.close()
    report["problems"] = [key for key in CONSILIUM_SCHEDULE_PROBLEM_KEYS if report.get(key)]
    report["ok"] = not report["problems"]
    return report


# Восстанавливаются только копии проверенных сборок. Номер схемы сборку не
# определяет (ветка Patient Portal и Max обе называли свою схему v10), поэтому
# снимок должен точно совпасть по структуре (таблицы и их столбцы) с одной из
# сборок data/restore_builds.json для своего номера или с текущей сборкой.
RESTORE_BUILDS_PATH = Path(__file__).resolve().parent / "data" / "restore_builds.json"
MIN_RESTORE_SCHEMA_VERSION = 9


def _structure(conn: sqlite3.Connection) -> dict[str, frozenset[str]]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()]
    return {
        table: frozenset(row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall())
        for table in tables
    }


# Таблицы, для которых совпадения набора столбцов мало. Сверяются: столбцы
# (тип, NOT NULL, значение по умолчанию, первичный ключ, скрытые/вычисляемые),
# уникальные индексы с коллацией, внешние ключи, каждое правило CHECK (текст
# выражения после разбора на токены, с владельцем — столбцом или таблицей),
# каждое определение столбца целиком (COLLATE, DEFAULT, GENERATED и прочее),
# весь CREATE TABLE после разбора на токены, результат пробных записей,
# параметры таблицы (WITHOUT ROWID, STRICT), явные индексы и все триггеры,
# которые срабатывают на таблице или ссылаются на нее. Удаленное, измененное или
# постороннее правило дает другую структуру. Для мобильного доступа это отличает
# проверенную сборку portal-a3fbe00 от ранней или измененной таблицы.
from .mfa_lifecycle_schema import TABLES as MFA_LIFECYCLE_TABLES

STRICT_TABLES = MFA_LIFECYCLE_TABLES + ("patient_portal_access", "read_access_manifests", "mfa_policy", "mfa_user_state", "mfa_credentials", "mfa_flows", "mfa_session_assurance")

_SQL_TOKEN = re.compile(
    r"""(?P<skip>\s+|--[^\n]*(?:\n|$)|/\*.*?(?:\*/|$))
      |(?P<string>'(?:[^']|'')*')
      |(?P<quoted>"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\])
      |(?P<word>[A-Za-z_][A-Za-z0-9_$]*)
      |(?P<number>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)
      |(?P<op><>|!=|<=|>=|==|\|\||<<|>>|.)""",
    re.S | re.X,
)
_TABLE_CONSTRAINT_START = {"constraint", "check", "primary", "unique", "foreign"}


def _sql_tokens(sql: str | None) -> list[str]:
    """SQL как последовательность токенов: без пробелов и комментариев, слова и
    идентификаторы в нижнем регистре (кавычки идентификаторов снимаются), строки
    — как есть. Комментарий с текстом CHECK правилом не считается."""
    tokens = []
    for match in _SQL_TOKEN.finditer(sql or ""):
        kind, value = match.lastgroup, match.group()
        if kind == "skip":
            continue
        if kind == "quoted":
            value = value[1:-1].replace(value[0] * 2, value[0]) if value[0] != "[" else value[1:-1]
            value = value.lower()
        elif kind == "word":
            value = value.lower()
        tokens.append(value)
    return tokens


def _balanced(tokens: list[str], start: int) -> int:
    """Индекс закрывающей скобки для открывающей в tokens[start]."""
    depth = 0
    for i in range(start, len(tokens)):
        if tokens[i] == "(":
            depth += 1
        elif tokens[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced parentheses")


def _table_rules(sql: str | None) -> dict:
    """Правила CHECK, определения столбцов и параметры таблицы из ее CREATE TABLE.

    `column_definitions` — каждое определение (столбец или ограничение таблицы)
    целиком: тип, COLLATE, DEFAULT, NOT NULL, ключи, CHECK, GENERATED. Атрибут,
    который не виден ни в PRAGMA, ни в индексах (например COLLATE столбца,
    меняющий смысл CHECK), здесь виден."""
    tokens = _sql_tokens(sql)
    open_at = tokens.index("(")
    close_at = _balanced(tokens, open_at)
    definitions, current, depth = [], [], 0
    for token in tokens[open_at + 1:close_at]:
        if token == "," and depth == 0:
            definitions.append(current)
            current = []
            continue
        depth += token == "("
        depth -= token == ")"
        current.append(token)
    definitions.append(current)
    checks = []
    for definition in definitions:
        owner = "" if definition and definition[0] in _TABLE_CONSTRAINT_START else (definition[0] if definition else "")
        i = 0
        while i < len(definition):
            if definition[i] == "check" and i + 1 < len(definition) and definition[i + 1] == "(":
                end = _balanced(definition, i + 1)
                name = definition[i - 1] if i >= 2 and definition[i - 2] == "constraint" else ""
                checks.append([owner, name, " ".join(definition[i + 2:end])])
                i = end + 1
            else:
                i += 1
    return {"checks": sorted(checks), "options": " ".join(tokens[close_at + 1:]),
            "column_definitions": sorted(" ".join(d) for d in definitions)}


# Пробные записи (по таблицам строгой сверки): правила проверяются не только по
# тексту, но и фактической записью — в отдельной таблице в памяти, созданной из
# того же CREATE TABLE. Сюда входят значения, отличающиеся только регистром.
_PORTAL_BASE = {"patient_id": "p1", "email_normalized": "probe@example.test", "status": "linked",
                "linked_at": "t", "enrollment_state": "none", "updated_at": "t"}
_WRITE_PROBES = {
    "patient_portal_access": (
        ("valid", {}),
        ("valid_revoked", {"status": "revoked", "revoked_at": "t"}),
        ("valid_active_enrolled", {"status": "active", "activated_at": "t", "enrollment_state": "totp_enrolled"}),
        ("status_upper", {"status": "LINKED"}),
        ("status_mixed", {"status": "Active"}),
        ("status_revoked_upper", {"status": "REVOKED", "revoked_at": "t"}),
        ("status_unknown", {"status": "INVALID"}),
        ("status_null", {"status": None}),
        ("revoked_without_time", {"status": "revoked"}),
        ("enrollment_upper", {"enrollment_state": "TOTP_ENROLLED"}),
        ("enrollment_mixed", {"enrollment_state": "Totp_Enrolled"}),
        ("enrollment_none_upper", {"enrollment_state": "NONE"}),
        ("enrollment_unknown", {"enrollment_state": "sms"}),
        ("email_null", {"email_normalized": None}),
        ("email_case_duplicate", {"__second__": {"patient_id": "p2", "email_normalized": "PROBE@EXAMPLE.TEST"}}),
        ("email_trailing_space_duplicate", {"__second__": {"patient_id": "p2", "email_normalized": "probe@example.test "}}),
        ("patient_case_duplicate", {"__second__": {"patient_id": "P1", "email_normalized": "other@example.test"}}),
    ),
}
_PROBE_BASES = {"patient_portal_access": _PORTAL_BASE}


def _write_probes(table: str, create_sql: str | None) -> list | str | None:
    """[[probe, accepted]] для пробных записей, или "error", если CREATE TABLE не выполнить."""
    probes = _WRITE_PROBES.get(table)
    tokens = _sql_tokens(create_sql)
    if probes is None:
        return None
    if tokens[:3] != ["create", "table", table]:
        return "error"
    scratch = sqlite3.connect(":memory:")
    try:
        try:
            scratch.execute(create_sql)      # один оператор; внешние ключи в пустой базе выключены
        except sqlite3.Error:
            return "error"
        results = []
        for name, change in probes:
            second = change.get("__second__")
            first = {**_PROBE_BASES[table], **{k: v for k, v in change.items() if k != "__second__"}}
            rows = [first] + ([{**_PROBE_BASES[table], **second}] if second else [])
            scratch.execute("SAVEPOINT probe")
            accepted = True
            try:
                for row in rows:
                    scratch.execute(f'INSERT INTO "{table}" ({", ".join(row)}) VALUES ({", ".join("?" * len(row))})',
                                    tuple(row.values()))
            except sqlite3.Error:
                accepted = False
            finally:
                scratch.execute("ROLLBACK TO probe")
                scratch.execute("RELEASE probe")
            results.append([name, accepted])
        return results
    finally:
        scratch.close()


def _strict_table(conn: sqlite3.Connection, table: str) -> dict | None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    if row is None:
        return None
    columns = sorted([r[1], (r[2] or "").upper(), r[3], r[4], r[5], r[6]]
                     for r in conn.execute(f'PRAGMA table_xinfo("{table}")').fetchall())
    indexes = []
    for index in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
        keys = [[r[2], r[4], r[3]] for r in conn.execute(f'PRAGMA index_xinfo("{index[1]}")').fetchall() if r[5]]
        indexes.append([index[2], index[3], index[4], keys])   # unique, origin, partial, (column, collation, desc)
    foreign_keys = sorted([r[2], r[3], r[4], r[5], r[6]]
                          for r in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall())
    index_sql = sorted(" ".join(_sql_tokens(r[0])) for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL", (table,)))
    triggers = sorted(
        " ".join(tokens) for tokens in (_sql_tokens(r[1]) for r in conn.execute(
            "SELECT tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"))
        if table in tokens)
    return {"columns": columns, "indexes": sorted(indexes), "foreign_keys": foreign_keys,
            **_table_rules(row[0]), "ddl": " ".join(_sql_tokens(row[0])),
            "write_probes": _write_probes(table, row[0]), "index_sql": index_sql, "triggers": triggers}


def _strict_structure(conn: sqlite3.Connection, tables) -> dict[str, dict | None]:
    return {table: _strict_table(conn, table) for table in tables}


_current_structure_cache: tuple[dict[str, frozenset[str]], dict] | None = None


def _current_structure() -> tuple[dict[str, frozenset[str]], dict]:
    global _current_structure_cache
    if _current_structure_cache is None:
        conn = connect_readonly_check(Path(":memory:"))
        try:
            _db.init_db(conn)  # эталон структуры текущей сборки, не миграция копии
            _current_structure_cache = (_structure(conn), _strict_structure(conn, STRICT_TABLES))
        finally:
            conn.close()
    return _current_structure_cache


def restore_builds() -> list[dict]:
    """Verified builds: [{id, schema_version, tables, strict_tables}] including the running one.

    `strict_tables` (optional) holds the detailed structure of selected tables;
    a backup must match it exactly as well as the column sets."""
    data = json.loads(RESTORE_BUILDS_PATH.read_text(encoding="utf-8"))
    builds = [
        {"id": b["id"], "schema_version": b["schema_version"],
         "tables": {t: frozenset(cols) for t, cols in b["tables"].items()},
         "strict_tables": b.get("strict_tables", {})}
        for b in data["builds"]
    ]
    tables, strict = _current_structure()
    builds.append({"id": "current", "schema_version": config.SCHEMA_VERSION, "tables": tables,
                   "strict_tables": strict})
    return builds


def identify_backup_build(path: Path, schema_version: int) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        snapshot = _structure(conn)
        builds = [b for b in restore_builds()
                  if b["schema_version"] == schema_version and b["tables"] == snapshot]
        builds = [b for b in builds
                  if _strict_structure(conn, b["strict_tables"]) == b["strict_tables"]]
    finally:
        conn.close()
    for build in builds:
        return build["id"]
    supported = ", ".join(f"v{b['schema_version']} ({b['id']})" for b in restore_builds())
    raise MaintenanceError(
        f"Резервная копия отклонена: структура схемы v{schema_version} не совпадает ни с одной "
        f"проверенной сборкой (поддерживаются: {supported})"
    )


def _stored_schema_version(path: Path) -> int | None:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        # Та же проверка, что при запуске: копия с повреждённой версией схемы
        # (несколько строк, не целое число) не должна заменить рабочую базу.
        if _db.schema_version_problem(conn) == "damaged":
            raise MaintenanceError("Резервная копия отклонена: версия схемы повреждена или не распознана")
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
        ).fetchone()
        row = conn.execute("SELECT version FROM schema_version").fetchone() if has_table else None
    finally:
        conn.close()
    return row[0] if row else None


def _integrity_problems(path: Path) -> list[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        problems = []
        if [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()] != ["ok"]:
            problems.append("integrity_check failed")
        violations = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        if violations:
            problems.append(f"{violations} foreign key violation(s)")
    finally:
        conn.close()
    return problems


def _check_or_migrate_plaintext(plaintext: Path, metadata: dict) -> dict:
    """Validate a decrypted snapshot, migrating a supported older one in place.

    `plaintext` is always the throw-away decrypted copy inside a private temp
    directory, never the live database. The header's schema_version is
    authenticated (AAD), so it must match the snapshot it describes. A
    supported older snapshot must pass integrity and FK checks *before* the
    migration (a migration must not paper over a damaged copy) and the full
    check_database afterwards.
    """
    stored = _stored_schema_version(plaintext)
    if stored != metadata["schema_version"]:
        raise MaintenanceError("Резервная копия отклонена: версия схемы в заголовке не совпадает с содержимым")
    if stored is None or stored < MIN_RESTORE_SCHEMA_VERSION or stored > config.SCHEMA_VERSION:
        raise MaintenanceError(f"Резервная копия отклонена: схема v{stored} не поддерживается для восстановления")
    build = identify_backup_build(plaintext, stored)
    migrated_from = None
    if stored < config.SCHEMA_VERSION:
        problems = _integrity_problems(plaintext)
        if problems:
            raise MaintenanceError("Резервная копия отклонена: " + "; ".join(problems))
        conn = connect_readonly_check(plaintext)
        try:
            init_db(conn)
        except Exception as exc:  # любая ошибка миграции — отказ; живая база не затронута
            raise MaintenanceError(
                f"Резервную копию v{stored} не удалось обновить до схемы v{config.SCHEMA_VERSION}: "
                f"{type(exc).__name__}"
            ) from exc
        finally:
            conn.close()
        migrated_from = stored
    elif _trigger_drift_only(plaintext):
        # WP-C (AUD-14): копия текущей схемы с подмененными или отсутствующими
        # защитными триггерами (например, копия перед восстановлением защиты) —
        # эталонные определения восстанавливаются на временной копии.
        problems = _integrity_problems(plaintext)
        if problems:
            raise MaintenanceError("Резервная копия отклонена: " + "; ".join(problems))
        conn = connect_readonly_check(plaintext)
        try:
            init_db(conn)
        finally:
            conn.close()
    report = check_database(plaintext)
    if not report["ok"]:
        raise MaintenanceError("Резервная копия отклонена: " + "; ".join(report["problems"]))
    report["migrated_from_schema_version"] = migrated_from
    report["source_build"] = build
    return report


def _trigger_drift_only(path: Path) -> bool:
    """Текущая схема, и единственная проблема копии — расхождение защитных триггеров."""
    report = check_database(path)
    drift = report.get("trigger_drift") or {}
    other = [p for p in report["problems"] if not p.startswith("protective triggers differ")]
    return report.get("schema_status") == "current" and bool(drift.get("missing") or drift.get("changed")) and not other


def looks_like_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def looks_like_encrypted_backup(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _decode_key(value: str, *, source: str) -> bytes:
    raw = value.strip()
    if not raw:
        raise MaintenanceError(f"{source}: ключ резервного копирования пуст")
    try:
        if len(raw) == KEY_BYTES * 2 and all(ch in "0123456789abcdefABCDEF" for ch in raw):
            key = bytes.fromhex(raw)
        else:
            key = _b64decode(raw)
    except (ValueError, binascii.Error) as exc:
        raise MaintenanceError(f"{source}: ключ должен быть base64url/base64 или hex") from exc
    if len(key) != KEY_BYTES:
        raise MaintenanceError(f"{source}: ключ должен содержать 32 байта")
    return key


def _safe_key_id(key_id: str | None, key: bytes) -> str:
    if key_id is not None and not key_id.strip():
        raise MaintenanceError("key_id не должен быть пустым")
    value = key_id.strip() if key_id is not None else ""
    if not value:
        value = hashlib.sha256(b"clinic-backup-key-id:" + key).hexdigest()[:16]
    if not _KEY_ID_RE.fullmatch(value):
        raise MaintenanceError("key_id содержит недопустимые символы")
    return value


KEY_FILE_ID_PREFIX = "key_id="


def _derived_key_id(key: bytes) -> str:
    return _safe_key_id(None, key)


def _parse_key_file(text: str) -> tuple[str, str | None]:
    """Key file: first line the key; optional second line `key_id=<id>` (AUD-20).

    Files written before WP-B hold only the key line; their key_id stays as
    before (CLINIC_BACKUP_KEY_ID or the id derived from the key)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise MaintenanceError("CLINIC_BACKUP_KEY_FILE: ключ резервного копирования пуст")
    stored_id = None
    for line in lines[1:]:
        if not line.startswith(KEY_FILE_ID_PREFIX) or stored_id is not None:
            raise MaintenanceError("Файл backup-ключа: допускается только строка ключа и одна строка key_id=")
        stored_id = line[len(KEY_FILE_ID_PREFIX):].strip()
        if not stored_id:
            raise MaintenanceError("Файл backup-ключа: key_id не должен быть пустым")
    return lines[0], stored_id


def _load_active_key() -> BackupKey:
    env_key = os.environ.get("CLINIC_BACKUP_KEY")
    key_file = os.environ.get("CLINIC_BACKUP_KEY_FILE")
    env_id = os.environ.get("CLINIC_BACKUP_KEY_ID")
    stored_id = None
    if env_key and key_file:
        raise MaintenanceError("Задан только один источник backup-ключа: env или key file")
    if key_file:
        path = Path(key_file).expanduser()
        _reject_symlink(path, "key file")
        try:
            mode = path.stat().st_mode & 0o777
            if mode & 0o077:
                raise MaintenanceError("Файл backup-ключа должен быть доступен только владельцу")
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MaintenanceError("Файл backup-ключа не прочитан") from exc
        raw_key, stored_id = _parse_key_file(raw)
        key = _decode_key(raw_key, source="CLINIC_BACKUP_KEY_FILE")
    elif env_key:
        key = _decode_key(env_key, source="CLINIC_BACKUP_KEY")
    else:
        raise MaintenanceError("Backup-ключ не настроен: задайте CLINIC_BACKUP_KEY или CLINIC_BACKUP_KEY_FILE")
    if stored_id is not None:
        stored_id = _safe_key_id(stored_id, key)
        if env_id is not None and env_id.strip() != stored_id:
            raise MaintenanceError(
                "CLINIC_BACKUP_KEY_ID не совпадает с key_id, записанным в файле ключа; "
                "уберите переменную или исправьте её (backup не создан)")
        return BackupKey(stored_id, key)
    return BackupKey(_safe_key_id(env_id, key), key)


def _load_keyring() -> dict[str, bytes]:
    active = _load_active_key()
    keys = {active.key_id: active.key}
    raw = os.environ.get("CLINIC_BACKUP_DECRYPT_KEYS", "").strip()
    if raw:
        try:
            parsed = _loads_json_no_duplicates(raw.encode("utf-8"))
        except json.JSONDecodeError as exc:
            raise MaintenanceError("CLINIC_BACKUP_DECRYPT_KEYS должен быть JSON object") from exc
        if not isinstance(parsed, dict):
            raise MaintenanceError("CLINIC_BACKUP_DECRYPT_KEYS должен быть JSON object")
        for key_id, encoded in parsed.items():
            key_id = _safe_key_id(str(key_id), b"")
            if not isinstance(encoded, str):
                raise MaintenanceError("Decrypt-only key должен быть строкой")
            key = _decode_key(encoded, source=f"CLINIC_BACKUP_DECRYPT_KEYS[{key_id}]")
            existing = keys.get(key_id)
            if existing is not None and not secrets.compare_digest(existing, key):
                raise MaintenanceError("Backup keyring содержит конфликтующий key_id")
            keys[key_id] = key
    # Совместимость (AUD-20): до WP-B keygen --key-id не сохранял id, и копии
    # получали id, вычисленный из ключа. Тот же ключ остаётся доступен и под
    # этим id, даже если позже id задан явно; чужой id не перекрывается.
    for key in list(keys.values()):
        keys.setdefault(_derived_key_id(key), key)
    return keys


def _configured_key_ids() -> dict[str, bytes]:
    """key_id already used by this installation (active key and decrypt-only keys).

    Configuration that cannot be read counts as no keys: keygen must still
    work on a fresh installation without a key."""
    try:
        return _load_keyring()
    except MaintenanceError:
        ids: dict[str, bytes] = {}
        raw = os.environ.get("CLINIC_BACKUP_DECRYPT_KEYS", "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return ids
            if isinstance(parsed, dict):
                ids.update({str(k): b"" for k in parsed})
        env_id = (os.environ.get("CLINIC_BACKUP_KEY_ID") or "").strip()
        if env_id:
            ids[env_id] = b""
        return ids


def generate_backup_key() -> str:
    """Generate a printable 256-bit key for operator documentation/tests."""
    return _b64encode(secrets.token_bytes(KEY_BYTES))


def _key_fingerprint(key: bytes) -> str:
    return hashlib.sha256(b"clinic-backup-key-fingerprint:" + key).hexdigest()[:16]


def create_backup_key_file(path: Path, *, key_id: str | None = None) -> dict:
    """Create a new backup key file without returning or printing key material."""
    raw_key = secrets.token_bytes(KEY_BYTES)
    safe_key_id = _safe_key_id(key_id, raw_key)
    if safe_key_id in _configured_key_ids():
        raise MaintenanceError(
            "key_id уже используется ключом этой установки (активным или для расшифровки); "
            "выберите другой key_id — иначе старые копии стали бы неоднозначными")
    key_path = _prepare_key_file_path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(key_path, flags, 0o600)
    except FileExistsError as exc:
        raise MaintenanceError("Файл backup-ключа уже существует") from exc
    except OSError as exc:
        raise MaintenanceError("Файл backup-ключа не создан") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # key_id хранится в самом файле (AUD-20): им подписываются все
            # следующие копии, и он не зависит от отдельной переменной окружения.
            f.write(_b64encode(raw_key) + "\n" + KEY_FILE_ID_PREFIX + safe_key_id + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        try:
            key_path.unlink(missing_ok=True)
        finally:
            raise
    _restrict_permissions(key_path)
    _fsync_dir(key_path.parent)
    return {
        "path": str(key_path),
        "filename": key_path.name,
        "key_id": safe_key_id,
        "fingerprint": _key_fingerprint(raw_key),
    }


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _reject_path_traversal(path: Path) -> None:
    if ".." in path.parts:
        raise MaintenanceError("Небезопасный путь: '..' не допускается")


def _reject_symlink(path: Path, label: str) -> None:
    try:
        if path.is_symlink():
            raise MaintenanceError(f"{label}: symlink не допускается")
    except OSError as exc:
        raise MaintenanceError(f"{label}: путь недоступен") from exc


def _prepare_key_file_path(path: Path) -> Path:
    key_path = Path(path).expanduser()
    _reject_path_traversal(key_path)
    _reject_symlink(key_path, "key file")
    resolved = key_path.resolve(strict=False)
    if _is_relative_to(resolved, _REPO_ROOT):
        raise MaintenanceError("Файл backup-ключа нельзя создавать внутри Git repository")
    if _is_relative_to(resolved, config.DATA_DIR.resolve(strict=False)):
        raise MaintenanceError("Файл backup-ключа нельзя хранить рядом с базой или backup")
    _ensure_private_dir(resolved.parent)
    return resolved


def _prepare_output_path(dest: Path | None, *, overwrite: bool) -> Path:
    if overwrite:
        raise MaintenanceError("Перезапись backup отключена; выберите новое имя файла")
    if dest is None:
        _ensure_private_dir(config.BACKUP_DIR)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = config.BACKUP_DIR / f"clinic_{stamp}{DEFAULT_BACKUP_SUFFIX}"
    else:
        dest = Path(dest).expanduser()
        _reject_path_traversal(dest)
    _reject_symlink(dest, "output")
    dest = dest.resolve(strict=False)
    if dest.exists():
        raise MaintenanceError("Файл уже существует; выберите новое имя")
    if _is_relative_to(dest, _STATIC_DIR.resolve(strict=False)):
        raise MaintenanceError("Backup нельзя создавать внутри static directory")
    backup_dir = config.BACKUP_DIR.resolve(strict=False)
    if _is_relative_to(dest, _REPO_ROOT) and not _is_relative_to(dest, backup_dir):
        raise MaintenanceError("Backup внутри Git repository разрешён только в защищённом backend/data/backups")
    _ensure_private_dir(dest.parent)
    return dest


def _validate_input_path(path: Path) -> Path:
    path = Path(path).expanduser()
    _reject_path_traversal(path)
    _reject_symlink(path, "input")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MaintenanceError("Файл резервной копии не найден") from exc
    if not resolved.is_file():
        raise MaintenanceError("Файл резервной копии должен быть обычным файлом")
    return resolved


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise MaintenanceError("Private directory permissions are not confirmed")


def _fsync_file(path: Path) -> None:
    with open(path, "rb") as f:
        os.fsync(f.fileno())


def _fsync_dir(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sqlite_backup_to_file(db_path: Path, dest: Path) -> None:
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


def _build_header(*, key: BackupKey, nonce: bytes, ciphertext_len: int,
                  schema_version: int | None = None) -> dict:
    return {
        "format": "Clinic Backup Format",
        "version": FORMAT_VERSION,
        "algorithm": ALGORITHM,
        "key_id": key.key_id,
        "nonce": _b64encode(nonce),
        "created_at": _utc_now(),
        "schema_version": config.SCHEMA_VERSION if schema_version is None else schema_version,
        "ciphertext_length": ciphertext_len,
    }


def _header_bytes(header: dict) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _aad(header_bytes: bytes) -> bytes:
    return MAGIC + len(header_bytes).to_bytes(HEADER_LEN_BYTES, "big") + header_bytes


def _write_container(dest: Path, header_bytes: bytes, ciphertext: bytes) -> None:
    if len(header_bytes) > MAX_HEADER_BYTES:
        raise MaintenanceError("Backup header слишком большой")
    if len(ciphertext) > MAX_BACKUP_BYTES:
        raise MaintenanceError("Backup слишком большой")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(dest, flags, 0o600)
    except FileExistsError as exc:
        raise MaintenanceError("Файл уже существует") from exc
    with os.fdopen(fd, "wb") as f:
        f.write(MAGIC)
        f.write(len(header_bytes).to_bytes(HEADER_LEN_BYTES, "big"))
        f.write(header_bytes)
        f.write(ciphertext)
        f.flush()
        os.fsync(f.fileno())
    _restrict_permissions(dest)


def _read_container(path: Path) -> tuple[dict, bytes, bytes]:
    backup_path = _validate_input_path(path)
    try:
        file_size = backup_path.stat().st_size
    except OSError as exc:
        raise MaintenanceError("Файл резервной копии недоступен") from exc
    if file_size > MAX_BACKUP_BYTES + MAX_HEADER_BYTES + len(MAGIC) + HEADER_LEN_BYTES:
        raise MaintenanceError("Backup превышает допустимый размер")
    if file_size < len(MAGIC) + HEADER_LEN_BYTES:
        raise MaintenanceError("Файл резервной копии повреждён")
    try:
        with open(backup_path, "rb") as f:
            magic = f.read(len(MAGIC))
            if magic != MAGIC:
                raise MaintenanceError("Поддерживаются только encrypted backup v1; plaintext SQLite backup отклонён")
            raw_len = f.read(HEADER_LEN_BYTES)
            if len(raw_len) != HEADER_LEN_BYTES:
                raise MaintenanceError("Файл резервной копии повреждён")
            header_len = int.from_bytes(raw_len, "big")
            if header_len < MIN_HEADER_BYTES or header_len > MAX_HEADER_BYTES:
                raise MaintenanceError("Неподдерживаемый размер header")
            raw_header = f.read(header_len)
            if len(raw_header) != header_len:
                raise MaintenanceError("Файл резервной копии повреждён")
            ciphertext = f.read()
            actual_size = len(MAGIC) + HEADER_LEN_BYTES + header_len + len(ciphertext)
            if actual_size != file_size:
                raise MaintenanceError("Файл резервной копии повреждён")
            if len(ciphertext) > MAX_BACKUP_BYTES:
                raise MaintenanceError("Backup превышает допустимый размер")
    except OSError as exc:
        raise MaintenanceError("Файл резервной копии не прочитан") from exc
    try:
        header = _loads_json_no_duplicates(raw_header)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaintenanceError("Header резервной копии повреждён") from exc
    return header, raw_header, ciphertext


def _loads_json_no_duplicates(raw: bytes) -> dict:
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise json.JSONDecodeError(f"duplicate key: {key}", "", 0)
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)


def inspect_backup(backup_path: Path) -> dict:
    """Return non-PHI metadata from the encrypted container header.

    This does not decrypt, so callers must treat it as unauthenticated until
    `verify_backup` or `validate_backup` succeeds.
    """
    header, _raw_header, ciphertext = _read_container(backup_path)
    return _validate_header(header, ciphertext, require_supported=False) | {"authenticated": False}


def _validate_header(header: dict, ciphertext: bytes, *, require_supported: bool = True) -> dict:
    if not isinstance(header, dict) or set(header) != HEADER_FIELDS:
        raise MaintenanceError("Header резервной копии неполный")
    version = header["version"]
    algorithm = header["algorithm"]
    key_id = str(header["key_id"])
    if require_supported and version != FORMAT_VERSION:
        raise MaintenanceError("Неподдерживаемая версия backup format")
    if require_supported and algorithm != ALGORITHM:
        raise MaintenanceError("Неподдерживаемый алгоритм backup")
    if not _KEY_ID_RE.fullmatch(key_id):
        raise MaintenanceError("Header содержит недопустимый key_id")
    try:
        nonce = _b64decode(str(header["nonce"]))
    except (ValueError, binascii.Error) as exc:
        raise MaintenanceError("Header содержит некорректный nonce") from exc
    if require_supported and len(nonce) != NONCE_BYTES:
        raise MaintenanceError("Header содержит некорректный nonce")
    if not isinstance(header["ciphertext_length"], int) or header["ciphertext_length"] < 16:
        raise MaintenanceError("Header содержит некорректную длину ciphertext")
    if header["ciphertext_length"] != len(ciphertext):
        raise MaintenanceError("Файл резервной копии усечён или повреждён")
    schema_version = header["schema_version"]
    if not isinstance(schema_version, int) or schema_version < 1 or schema_version > 10_000:
        raise MaintenanceError("Header содержит некорректную версию схемы")
    try:
        created_at = datetime.fromisoformat(str(header["created_at"]))
    except ValueError as exc:
        raise MaintenanceError("Header содержит некорректный timestamp") from exc
    if created_at.tzinfo is None:
        raise MaintenanceError("Header содержит timestamp без timezone")
    return {
        "format": header["format"],
        "version": version,
        "algorithm": algorithm,
        "key_id": key_id,
        "created_at": header["created_at"],
        "schema_version": schema_version,
        "ciphertext_length": len(ciphertext),
    }


def _decrypt_to_temp(backup_path: Path, temp_dir: Path) -> tuple[Path, dict]:
    header, raw_header, ciphertext = _read_container(backup_path)
    metadata = _validate_header(header, ciphertext)
    keys = _load_keyring()
    key = keys.get(metadata["key_id"])
    if key is None:
        raise MaintenanceError("Backup-ключ для этого key_id не найден")
    nonce = _b64decode(str(header["nonce"]))
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(raw_header))
    except InvalidTag as exc:
        raise MaintenanceError("Резервная копия не прошла проверку подлинности") from exc
    except ValueError as exc:
        raise MaintenanceError("Резервная копия не расшифрована") from exc
    plaintext_path = temp_dir / "decrypted.sqlite"
    with open(plaintext_path, "wb") as f:
        f.write(plaintext)
        f.flush()
        os.fsync(f.fileno())
    _restrict_permissions(plaintext_path)
    return plaintext_path, metadata


# --- backup -------------------------------------------------------------

def _older_snapshot_problems(snapshot: Path) -> tuple[int | None, list[str]]:
    """For a snapshot of an older schema: (stored version, problems).

    The snapshot is acceptable as a recovery copy when SQLite integrity and
    foreign keys are fine and the version is an older one this build can
    migrate from (header and content then agree on that version)."""
    try:
        stored = _stored_schema_version(snapshot)
        problems = _integrity_problems(snapshot)
    except MaintenanceError as exc:
        return None, [str(exc)]
    except sqlite3.DatabaseError as exc:
        return None, [f"SQLite error: {type(exc).__name__}"]
    if stored is None or stored < 1 or stored >= config.SCHEMA_VERSION:
        problems.append(f"schema version {stored} is not an older supported version")
    return stored, problems


def backup_database(db_path: Path, dest: Path | None = None, *, overwrite: bool = False,
                    allow_older_schema: bool = False) -> Path:
    """Encrypted backup of a consistent snapshot.

    allow_older_schema=True (recovery copies before restore and migrate-db):
    a snapshot of an older schema is accepted when integrity and foreign keys
    pass; the header then records the snapshot's real schema version, so the
    copy can be verified and restored (with migration on a temporary copy)
    later. The normal backup command still requires the current schema."""
    if not db_path.exists():
        raise MaintenanceError("База не найдена")

    key = _load_active_key()
    dest = _prepare_output_path(dest, overwrite=overwrite)
    tmp_output = dest.with_name(f".{dest.name}.{secrets.token_hex(8)}.tmp")
    _reject_symlink(tmp_output, "temporary output")

    # Открытый снимок готовится рядом с живой базой (тот же том и те же права),
    # а не в каталоге назначения: --dest может быть NAS, USB или
    # синхронизируемой папкой, куда должен попадать только шифротекст.
    with tempfile.TemporaryDirectory(prefix="clinic-backup-", dir=str(Path(db_path).resolve().parent)) as tmp_name:
        temp_dir = Path(tmp_name)
        _ensure_private_dir(temp_dir)
        plaintext = temp_dir / "snapshot.sqlite"
        try:
            _sqlite_backup_to_file(db_path, plaintext)
            report = check_database(plaintext)
            snapshot_version = None
            if not report["ok"]:
                if allow_older_schema and _trigger_drift_only(plaintext):
                    # Копия перед восстановлением защиты: схема текущая, триггеры подменены.
                    problems = _integrity_problems(plaintext)
                    if problems:
                        raise MaintenanceError("Созданная SQLite-копия не прошла проверку: " + "; ".join(problems))
                elif not (allow_older_schema and report.get("schema_status") == "older"):
                    raise MaintenanceError(
                        "Созданная SQLite-копия не прошла проверку: " + "; ".join(report["problems"]))
                else:
                    snapshot_version, problems = _older_snapshot_problems(plaintext)
                    if problems:
                        raise MaintenanceError("Созданная SQLite-копия не прошла проверку: " + "; ".join(problems))
            nonce = secrets.token_bytes(NONCE_BYTES)
            plaintext_bytes = plaintext.read_bytes()
            # AES-GCM appends a 16-byte authentication tag to the ciphertext.
            header = _build_header(key=key, nonce=nonce, ciphertext_len=len(plaintext_bytes) + 16,
                                   schema_version=snapshot_version)
            raw_header = _header_bytes(header)
            ciphertext = AESGCM(key.key).encrypt(nonce, plaintext_bytes, _aad(raw_header))
            _write_container(tmp_output, raw_header, ciphertext)
            _fsync_file(tmp_output)
            try:
                os.link(tmp_output, dest)
            except FileExistsError as exc:
                raise MaintenanceError("Файл уже существует") from exc
            _restrict_permissions(dest)
            _fsync_dir_strict(dest.parent)
        finally:
            plaintext.unlink(missing_ok=True)
            tmp_output.unlink(missing_ok=True)
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
    # Расшифровка — рядом с рабочей базой (тот же том и права), как в
    # backup_database/restore_database, а не в системном /tmp.
    verify_parent = config.DB_PATH.resolve().parent
    _ensure_private_dir(verify_parent)
    with tempfile.TemporaryDirectory(prefix="clinic-verify-", dir=str(verify_parent)) as tmp_name:
        temp_dir = Path(tmp_name)
        _ensure_private_dir(temp_dir)
        plaintext, metadata = _decrypt_to_temp(backup_path, temp_dir)
        if not looks_like_sqlite(plaintext):
            raise MaintenanceError("Расшифрованный backup не является базой SQLite")
        # Старая поддерживаемая копия проверяется так же, как при restore:
        # миграцией одноразовой копии во временном каталоге.
        report = _check_or_migrate_plaintext(plaintext, metadata)
        report.update(metadata)
        report["authenticated"] = True
        return report


def verify_backup(backup_path: Path) -> dict:
    return validate_backup(backup_path)


RESTORE_STAGING_SUFFIX = ".restore-tmp"
QUARANTINE_DIR_NAME = "quarantine"


def _check_target_path(db_path: Path) -> Path:
    db_path = Path(db_path).resolve(strict=False)
    if db_path.name != "clinic.db":
        raise MaintenanceError("Restore разрешён только в ожидаемый файл clinic.db")
    if db_path != config.DB_PATH.resolve(strict=False):
        raise MaintenanceError("Restore разрешён только в настроенный путь базы")
    return db_path


def restore_database(backup_path: Path, db_path: Path, *, confirm_offline: bool = False,
                     replace_damaged: bool = False) -> dict:
    """Validate the backup, keep a recovery copy of the current DB, replace it.

    Safety (AUD-04/AUD-16):
    - Runs only under db_guard.exclusive_maintenance: the application lock and
      the database lock are held exclusively from before the first change
      until the file is replaced, the sidecars are handled and every
      connection is closed. A running backend, a DB-using CLI command or a
      second restore makes it fail before anything is changed.
    - The current DB is classified on a private copy: missing, empty,
      healthy (integrity and foreign keys pass, supported schema) or damaged.
      A healthy DB gets an encrypted recovery copy that is verified before
      the replacement. A damaged or unsupported one is replaced only with
      replace_damaged=True and is moved, never deleted, into
      DATA_DIR/quarantine/<stamp>/ together with its WAL/SHM.
    - The restored file is prepared as a staging file next to the DB and
      installed with os.replace, so the path holds either the old or the new
      database. Any failure before that leaves the live files and the backup
      untouched; staging files are removed.
    """
    backup_path = _validate_input_path(backup_path)
    if not confirm_offline:
        raise MaintenanceError("Restore требует подтверждения offline maintenance mode")
    db_path = _check_target_path(db_path)
    try:
        with db_guard.exclusive_maintenance("Restore"):
            from . import operations_state
            _begin_admission("restore")
            return _restore_locked(backup_path, db_path, replace_damaged=replace_damaged)
    except db_guard.DatabaseBusyError as exc:
        raise MaintenanceError(str(exc)) from exc


def _cleanup_stale_files(db_path: Path) -> int:
    """Staging and temporary directories of an interrupted restore/migrate.

    They hold decrypted copies; only called under the exclusive locks, so no
    running operation owns them."""
    removed = 0
    for path in db_path.parent.glob(f".{db_path.name}.*{RESTORE_STAGING_SUFFIX}"):
        if path.is_file() and not path.is_symlink():
            path.unlink()
            removed += 1
    for prefix in ("clinic-restore-", "clinic-migrate-", "clinic-check-", "clinic-backup-"):
        for path in db_path.parent.glob(prefix + "*"):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
                removed += 1
    return removed


def _live_problems(copy: Path) -> list[str]:
    """Problems that make a current DB unusable as a recovery source."""
    if not looks_like_sqlite(copy):
        return ["file is not a SQLite database"]
    try:
        conn = sqlite3.connect(_readonly_uri(copy), uri=True)
        try:
            problem = _db.schema_version_problem(conn)
        finally:
            conn.close()
        if problem == "damaged":
            return ["schema version damaged"]
        if problem is not None:
            return [f"schema {problem} is newer than this build (v{config.SCHEMA_VERSION})"]
        return _integrity_problems(copy)
    except sqlite3.DatabaseError as exc:
        return [f"SQLite error: {type(exc).__name__}"]


def classify_live_database(db_path: Path, temp_dir: Path) -> tuple[str, list[str]]:
    """("missing" | "empty" | "healthy" | "damaged", problems) from a raw copy.

    Caller holds the exclusive locks. The live file is not opened by SQLite
    here, so a damaged file and its WAL stay byte-for-byte as found."""
    if not db_path.exists():
        return "missing", []
    if db_path.is_symlink() or not db_path.is_file():
        raise MaintenanceError("Путь базы должен быть обычным файлом (не symlink)")
    if db_path.stat().st_size == 0:
        return "empty", ["database file is empty (0 bytes)"]
    try:
        copy = _copy_for_diagnosis(db_path, temp_dir)
    except OSError as exc:
        raise MaintenanceError(f"Текущая база не прочитана: {type(exc).__name__}") from exc
    try:
        problems = _live_problems(copy)
    finally:
        for path in list(temp_dir.glob("diagnose.sqlite*")):
            path.unlink(missing_ok=True)
    return ("damaged" if problems else "healthy"), problems


def _open_live_exclusive(db_path: Path) -> sqlite3.Connection:
    """Additional SQLite-level check for processes outside the locks.

    Checkpoints the WAL into the main file and holds BEGIN IMMEDIATE: no
    other connection can write until the file is replaced, while this
    process can still read it for the recovery copy (BEGIN EXCLUSIVE would
    block that reader on a file in rollback-journal mode, e.g. an old
    database never opened by a WAL build). This is not the proof that the
    application is stopped (an idle connection holds no SQLite lock) — that
    proof is db_guard; this only catches a foreign process in the middle of
    a transaction."""
    conn = sqlite3.connect(str(db_path), timeout=SQLITE_EXCLUSIVE_TIMEOUT_MS / 1000)
    try:
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_EXCLUSIVE_TIMEOUT_MS}")
        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy is not None and busy[0]:
            raise MaintenanceError(
                "Restore отклонён: база занята — WAL не перенесён в базу, файл открыт процессом вне "
                "приложения (sqlite3, сторонний инструмент). База не изменена.")
        conn.execute("BEGIN IMMEDIATE")
        return conn
    except sqlite3.Error as exc:
        conn.close()
        raise MaintenanceError(
            "Restore отклонён: база занята — файл заблокирован процессом вне приложения (sqlite3, "
            "сторонний инструмент). База не изменена.") from exc
    except BaseException:
        conn.close()
        raise


def _quarantine_dir(db_path: Path) -> Path:
    base = db_path.parent / QUARANTINE_DIR_NAME
    _ensure_private_dir(base)
    target = base / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
    target.mkdir(mode=0o700)
    return target


class _ReplacedButNotDurable(Exception):
    """Internal: os.replace succeeded, the directory sync failed."""

    def __init__(self, cause: OSError, quarantine: Path | None):
        super().__init__(str(cause))
        self.cause = cause
        self.quarantine = quarantine


class _RollbackIncomplete(Exception):
    """Internal: the replacement failed and moved files could not all be put back."""

    def __init__(self, cause: OSError, quarantine: Path | None, left: list[str]):
        super().__init__(str(cause))
        self.cause = cause
        self.quarantine = quarantine
        self.left = left


def _fsync_dir_strict(path: Path) -> None:
    """Like _fsync_dir, but a directory that cannot be opened or synced is an error.

    Only where POSIX directory sync exists; elsewhere (Windows) durability is
    left to the filesystem as before."""
    if not hasattr(os, "O_DIRECTORY"):
        return
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _install_staging(staging: Path, db_path: Path, *, move_main: bool) -> Path | None:
    """Move the old files aside (never delete them) and install the staging file.

    Leftover WAL/SHM always go to quarantine: a WAL of the old file applied
    to the restored one would corrupt it.

    Commit point: os.replace(staging, db_path).
    - Before it (quarantine moves, the replace itself) an OSError puts the
      moved files back and is raised as OSError: the live files are as before;
      if putting back fails, _RollbackIncomplete says which files stay where.
    - After it the restored file is installed; a failed directory sync raises
      _ReplacedButNotDurable — the change happened, its durability is not
      confirmed."""
    to_move = [path for path in _sidecars(db_path).values() if path.exists()]
    if move_main and db_path.exists():
        to_move.insert(0, db_path)
    quarantine = _quarantine_dir(db_path) if to_move else None
    moved: list[tuple[Path, Path]] = []
    try:
        for path in to_move:
            target = quarantine / path.name
            os.rename(path, target)
            moved.append((path, target))
        os.replace(staging, db_path)          # точка фиксации
    except OSError as exc:
        left = []
        for original, target in reversed(moved):
            try:
                os.rename(target, original)
            except OSError:
                left.append(original.name)
        if left:
            raise _RollbackIncomplete(exc, quarantine, sorted(left)) from exc
        raise
    try:
        _fsync_dir_strict(db_path.parent)
        if quarantine is not None:
            _fsync_dir_strict(quarantine)
    except OSError as exc:
        raise _ReplacedButNotDurable(exc, quarantine) from exc
    return quarantine


def _restore_unverified(state: str, cause: OSError, *, db_path: Path, pre_restore_backup,
                        quarantine: Path | None, what: str) -> CommittedButUnverifiedError:
    details = {
        "db_path": str(db_path),
        "pre_restore_backup": str(pre_restore_backup) if pre_restore_backup else None,
        "quarantine": str(quarantine) if quarantine else None,
        "cause": f"{type(cause).__name__}: {cause.strerror or cause}",
    }
    keep = []
    if pre_restore_backup:
        keep.append(f"проверенная копия прежней базы {Path(pre_restore_backup).name}")
    if quarantine:
        keep.append(f"карантин {quarantine}")
    return CommittedButUnverifiedError(
        f"Restore: база ЗАМЕНЕНА восстановленной копией, но {what} ({details['cause']}). "
        "Состояние восстановления требует проверки; повторный restore автоматически не выполняется. "
        "Не запускайте приложение. Проверьте файловую систему (место, ошибки ввода-вывода, права), "
        "затем выполните python -m backend.cli check-db. Если check-db не проходит — повторите restore "
        "вручную. " + ("Сохранено: " + "; ".join(keep) + "." if keep else ""),
        operation="restore", state=state, details=details)


def _restore_locked(backup_path: Path, db_path: Path, *, replace_damaged: bool) -> dict:
    try:
        _ensure_private_dir(db_path.parent)
        stale = _cleanup_stale_files(db_path)
    except OSError as exc:
        raise MaintenanceError(f"Каталог базы недоступен для записи: {type(exc).__name__}. База не изменена.") from exc
    staging = db_path.with_name(f".{db_path.name}.{secrets.token_hex(8)}{RESTORE_STAGING_SUFFIX}")
    pre_restore_backup = None
    lock_conn = None
    quarantine = None
    report: dict
    try:
        with tempfile.TemporaryDirectory(prefix="clinic-restore-", dir=str(db_path.parent)) as tmp_name:
            temp_dir = Path(tmp_name)
            _ensure_private_dir(temp_dir)
            state, live_problems = classify_live_database(db_path, temp_dir)
            if state == "damaged" and not replace_damaged:
                raise MaintenanceError(
                    "Restore отклонён: текущая база повреждена или не поддерживается этой сборкой ("
                    + "; ".join(live_problems) + "). База не изменена. Аварийная замена: повторите с "
                    "--replace-damaged — файл базы и WAL/SHM будут перемещены в "
                    f"{QUARANTINE_DIR_NAME}/, не удалены (см. OPERATIONS.md, «Аварийное восстановление»)."
                )
            plaintext, metadata = _decrypt_to_temp(backup_path, temp_dir)
            if not looks_like_sqlite(plaintext):
                raise MaintenanceError("Расшифрованный backup не является базой SQLite")
            # Миграция идёт на расшифрованной копии; живая база не тронута,
            # пока копия не прошла полную проверку текущей схемы.
            report = _check_or_migrate_plaintext(plaintext, metadata)
            report.update(metadata)

            if state == "healthy":
                lock_conn = _open_live_exclusive(db_path)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                pre_restore_backup = backup_database(
                    db_path,
                    config.BACKUP_DIR / f"pre_restore_{stamp}_{secrets.token_hex(4)}{DEFAULT_BACKUP_SUFFIX}",
                    allow_older_schema=True,
                )
                # Копия текущей базы должна расшифровываться и проходить проверку
                # до того, как база будет заменена.
                _verify_recovery_backup(pre_restore_backup)

            _sqlite_backup_to_file(plaintext, staging)
            _revoke_restored_sessions(staging)
            if lock_conn is not None:
                report["accounts_reconciled"] = _reconcile_account_security(staging, lock_conn)
            _fsync_file(staging)
        if lock_conn is not None:
            # Закрыть соединение до замены файла (последнее закрытие переносит
            # WAL в базу); блокировки db_guard по-прежнему удерживаются.
            lock_conn.rollback()
            lock_conn.close()
            lock_conn = None
        try:
            quarantine = _install_staging(staging, db_path, move_main=state in ("damaged", "empty"))
        except _ReplacedButNotDurable as failure:
            raise _restore_unverified(
                "replaced_not_durable", failure.cause, db_path=db_path, pre_restore_backup=pre_restore_backup,
                quarantine=failure.quarantine,
                what="долговечность записи не подтверждена: синхронизация каталога (fsync) завершилась ошибкой",
            ) from failure.cause
        except _RollbackIncomplete as failure:
            raise MaintenanceError(
                f"Не удалось заменить базу ({type(failure.cause).__name__}), и не все файлы удалось вернуть "
                f"из карантина {failure.quarantine}: {', '.join(failure.left)} остались там. База по пути "
                f"{db_path.name} может отсутствовать. Не запускайте приложение; верните файлы из карантина "
                "вручную или повторите restore. Резервная копия не изменена.") from failure.cause
        except OSError as exc:
            raise MaintenanceError(
                f"Не удалось заменить базу: {type(exc).__name__} ({exc.strerror or exc}). Текущая база "
                "и резервная копия не изменены"
                + (f"; копия текущей базы: {Path(pre_restore_backup).name}" if pre_restore_backup else "")
                + ".") from exc
        # После точки фиксации: права файла подтверждаются строго (не best effort).
        try:
            os.chmod(db_path, 0o600)
        except OSError as exc:
            raise _restore_unverified(
                "replaced_permissions_unconfirmed", exc, db_path=db_path, pre_restore_backup=pre_restore_backup,
                quarantine=quarantine,
                what="права доступа 0600 к восстановленному файлу не подтверждены (chmod завершился ошибкой)",
            ) from exc
    except OSError as exc:
        raise MaintenanceError(
            f"Restore не выполнен: {type(exc).__name__} ({exc.strerror or 'ошибка файловой системы'}). "
            "Текущая база и резервная копия не изменены.") from exc
    finally:
        if lock_conn is not None:
            lock_conn.rollback()
            lock_conn.close()
        staging.unlink(missing_ok=True)

    return {
        "restored_from": backup_path.name,
        "pre_restore_backup": str(pre_restore_backup) if pre_restore_backup else None,
        "patient_count": report.get("patient_count"),
        "schema_version": report.get("schema_version"),
        "migrated_from_schema_version": report.get("migrated_from_schema_version"),
        "format_version": report.get("version"),
        "key_id": report.get("key_id"),
        "accounts_reconciled": report.get("accounts_reconciled", 0),
        "previous_state": state,
        "previous_problems": live_problems,
        "quarantine": str(quarantine) if quarantine else None,
        "stale_files_removed": stale,
    }


# --- explicit migration ----------------------------------------------------

def migrate_database(db_path: Path, *, confirm_offline: bool = False) -> dict:
    """migrate-db: the only operation that upgrades the live schema (AUD-02).

    Under the exclusive maintenance locks: classify the DB on a copy (a
    damaged DB is never migrated), create an encrypted pre-migration backup,
    verify it (decrypt, integrity, trial migration of the decrypted copy),
    migrate the live file in one transaction (init_db) and run the full
    check. A failed migration rolls back and leaves the file as it was; the
    verified pre-migration backup is the rollback path in every case."""
    if not confirm_offline:
        raise MaintenanceError("migrate-db требует --confirm-offline после остановки приложения и worker")
    db_path = _check_target_path(db_path)
    try:
        with db_guard.exclusive_maintenance("migrate-db"):
            from . import operations_state
            _begin_admission("migrate-db")
            return _migrate_locked(db_path)
    except db_guard.DatabaseBusyError as exc:
        raise MaintenanceError(str(exc)) from exc


def _migrate_locked(db_path: Path) -> dict:
    if not db_path.exists():
        raise MaintenanceError(f"База не найдена: {db_path}. Ничего не создано.")
    _cleanup_stale_files(db_path)
    with tempfile.TemporaryDirectory(prefix="clinic-migrate-", dir=str(db_path.parent)) as tmp_name:
        temp_dir = Path(tmp_name)
        _ensure_private_dir(temp_dir)
        state, problems = classify_live_database(db_path, temp_dir)
        if state != "healthy":
            raise MaintenanceError(
                "migrate-db отклонён: база " + ("пуста" if state == "empty" else "повреждена или не поддерживается")
                + (" (" + "; ".join(problems) + ")" if problems else "")
                + ". База не изменена; используйте restore из проверенной копии.")
        copy = _copy_for_diagnosis(db_path, temp_dir)
        before = check_database(copy)
        for path in list(temp_dir.glob("diagnose.sqlite*")):
            path.unlink(missing_ok=True)
    version = before.get("schema_version")
    drift = before.get("trigger_drift") or {}
    drifted = sorted((drift.get("missing") or []) + (drift.get("changed") or []))
    if before.get("schema_status") == "current" and not drifted:
        return {"status": "current", "schema_version": version, "pre_migration_backup": None,
                "check": before}
    # WP-C (AUD-14): текущая схема с подмененной защитой восстанавливается тем же
    # путем, что и миграция: проверенная копия → init_db (эталонные триггеры) → проверка.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = backup_database(
        db_path, config.BACKUP_DIR / f"pre_migrate_v{version}_{stamp}_{secrets.token_hex(4)}{DEFAULT_BACKUP_SUFFIX}",
        allow_older_schema=True)
    verified = _verify_pre_migration_backup(backup)
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as exc:
        raise MaintenanceError(
            f"migrate-db: база не открыта ({type(exc).__name__}); миграция не начиналась, база не изменена. "
            f"Проверенная копия: {backup.name}") from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_EXCLUSIVE_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        init_db(conn)                       # точка фиксации — COMMIT внутри init_db
    except Exception as exc:
        conn.close()
        conn = None
        raise _migration_failure(db_path, version, backup, exc) from exc
    finally:
        if conn is not None:
            conn.close()
    # Миграция зафиксирована. Всё, что ниже, — проверка уже изменённого файла.
    try:
        after = _diagnose_copy(db_path)
    except (OSError, sqlite3.Error, MaintenanceError) as exc:
        raise _migration_unverified(
            db_path, version, backup, "migrated_check_incomplete",
            f"итоговая проверка не завершена ({type(exc).__name__}: {getattr(exc, 'strerror', None) or exc})",
        ) from exc
    if not after["ok"]:
        raise _migration_unverified(
            db_path, version, backup, "migrated_check_failed",
            "итоговая проверка нашла проблемы: " + "; ".join(after["problems"]))
    status = "repaired" if version == config.SCHEMA_VERSION else "migrated"
    return {"status": status, "from_schema_version": version, "schema_version": after["schema_version"],
            "pre_migration_backup": str(backup), "backup_verified": verified, "check": after,
            "repaired_triggers": drifted}


def _file_schema_version(db_path: Path) -> int | None:
    """Schema version as stored in the file now (None if it cannot be read)."""
    try:
        conn = sqlite3.connect(_readonly_uri(db_path), uri=True)
        try:
            row = conn.execute("SELECT version FROM schema_version").fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return None
    return row[0] if row else None


def _migration_failure(db_path: Path, version, backup: Path, exc: BaseException) -> MaintenanceError:
    """init_db raised: report what the file holds now instead of assuming a rollback."""
    actual = _file_schema_version(db_path)
    if actual == version:
        return MaintenanceError(
            f"Миграция v{version} → v{config.SCHEMA_VERSION} не выполнена ({type(exc).__name__}); "
            f"транзакция откатана, в файле прежняя схема v{actual}, база не изменена. "
            f"Проверенная копия: {backup.name}")
    if actual == config.SCHEMA_VERSION:
        return _migration_unverified(
            db_path, version, backup, "migrated_check_incomplete",
            f"после фиксации произошла ошибка ({type(exc).__name__})")
    return CommittedButUnverifiedError(
        f"Миграция v{version} → v{config.SCHEMA_VERSION} прервана ошибкой ({type(exc).__name__}); "
        f"версию схемы в файле подтвердить не удалось ({actual!r}). Состояние базы требует проверки. "
        "Не запускайте приложение; выполните python -m backend.cli check-db. Если проверка не проходит — "
        f"restore проверенной копии {backup.name} (--confirm-offline).",
        operation="migrate-db", state="migration_state_unknown",
        details={"db_path": str(db_path), "pre_migration_backup": str(backup), "from_schema_version": version,
                 "file_schema_version": actual})


def _migration_unverified(db_path: Path, version, backup: Path, state: str, what: str) -> CommittedButUnverifiedError:
    actual = _file_schema_version(db_path)
    return CommittedButUnverifiedError(
        f"Миграция v{version} → v{config.SCHEMA_VERSION} ЗАФИКСИРОВАНА (схема в файле: "
        f"{'v' + str(actual) if actual is not None else 'не прочитана'}), но {what}. Откат не выполнялся; "
        "повтор автоматически не выполняется. Не запускайте приложение. Проверьте файловую систему, затем "
        "python -m backend.cli check-db. Если check-db не проходит — восстановите проверенную копию "
        f"{backup.name} (схема v{version}): restore --confirm-offline (этой сборкой копия будет снова "
        "обновлена на временной копии) или restore прежней сборкой для возврата к ней.",
        operation="migrate-db", state=state,
        details={"db_path": str(db_path), "pre_migration_backup": str(backup), "from_schema_version": version,
                 "file_schema_version": actual})


def _decrypt_and_check_recovery(backup: Path, temp_dir: Path) -> tuple[Path, dict]:
    plaintext, metadata = _decrypt_to_temp(backup, temp_dir)
    problems: list[str] = []
    try:
        stored = _stored_schema_version(plaintext)
        problems = _integrity_problems(plaintext)
    except (MaintenanceError, sqlite3.DatabaseError) as exc:
        stored = None
        problems.append(f"{type(exc).__name__}")
    if stored != metadata["schema_version"]:
        problems.append("header schema version differs from the snapshot")
    if problems:
        raise MaintenanceError("Резервная копия текущей базы не прошла проверку: " + "; ".join(problems)
                               + ". База не изменена.")
    return plaintext, metadata


def _verify_recovery_backup(backup: Path) -> dict:
    """Decrypt a just-written recovery copy and check it (integrity, FK, version)."""
    with tempfile.TemporaryDirectory(prefix="clinic-restore-", dir=str(config.DB_PATH.resolve().parent)) as tmp_name:
        temp_dir = Path(tmp_name)
        _ensure_private_dir(temp_dir)
        _plaintext, metadata = _decrypt_and_check_recovery(backup, temp_dir)
        return {"authenticated": True, "key_id": metadata["key_id"], "schema_version": metadata["schema_version"]}


def _verify_pre_migration_backup(backup: Path) -> dict:
    """Decrypt the new backup and run the same migration on the decrypted copy."""
    verify_parent = config.DB_PATH.resolve().parent
    with tempfile.TemporaryDirectory(prefix="clinic-migrate-", dir=str(verify_parent)) as tmp_name:
        temp_dir = Path(tmp_name)
        _ensure_private_dir(temp_dir)
        plaintext, metadata = _decrypt_and_check_recovery(backup, temp_dir)
        stored = metadata["schema_version"]
        conn = connect_readonly_check(plaintext)
        try:
            init_db(conn)
        except Exception as exc:
            raise MaintenanceError(
                f"Пробная миграция копии не выполнена ({type(exc).__name__}); база не изменена") from exc
        finally:
            conn.close()
        report = check_database(plaintext)
        if not report["ok"]:
            raise MaintenanceError("Пробная миграция копии не прошла проверку: " + "; ".join(report["problems"])
                                   + ". База не изменена.")
        return {"authenticated": True, "key_id": metadata["key_id"], "schema_version": stored,
                "trial_migration_ok": True}


def _revoke_restored_sessions(db_file: Path) -> None:
    """Сессии из снимка не переживают restore: их отзыв после снимка
    (logout, блокировка, сброс пароля) иначе был бы отменён."""
    conn = sqlite3.connect(str(db_file))
    try:
        with conn:
            conn.execute("DELETE FROM sessions")
    finally:
        conn.close()


_STATUS_RESTRICTIVENESS = {"active": 0, "blocked": 1, "deactivated": 2}


def _reconcile_account_security(staging: Path, live: sqlite3.Connection) -> int:
    """Restore не возвращает учёткам менее безопасное состояние, чем текущее.

    Для каждого пользователя, который есть и в снимке, и в текущей базе:
    статус — более строгий из двух (снимок может заблокировать, но не
    разблокировать); пароль — более новый по password_changed_at (сброс после
    снимка сохраняется); роль и связи с отделениями — текущие (снимок не может
    вернуть роль или область видимости). Отделение, архивированное после
    снимка, остаётся в архиве: статус отделения тоже часть области видимости
    заведующего. Пользователи, которых нет в текущей базе, не трогаются, чтобы
    restore на пустую установку не блокировал всех.
    Возвращает число пользователей и отделений, у которых что-то изменилось."""
    live.row_factory = sqlite3.Row
    archived_live = {r["id"] for r in live.execute("SELECT id FROM departments WHERE status = 'archived'")}
    current = {r["id"]: r for r in live.execute(
        "SELECT id, status, role_id, password_hash, must_change_password, password_changed_at FROM users"
    )}
    links = {"user_departments": {}, "department_heads": {}}
    for table in links:
        for r in live.execute(f"SELECT user_id, department_id, created_at FROM {table}"):  # nosec B608 - fixed table names
            links[table].setdefault(r["user_id"], []).append((r["department_id"], r["created_at"]))

    conn = sqlite3.connect(str(staging))
    conn.row_factory = sqlite3.Row
    changed = 0
    try:
        with conn:
            dept_ids = {r[0] for r in conn.execute("SELECT id FROM departments")}
            for dept_id in sorted(archived_live & dept_ids):
                changed += conn.execute(
                    "UPDATE departments SET status = 'archived' WHERE id = ? AND status != 'archived'", (dept_id,)
                ).rowcount
            for snap in conn.execute(
                "SELECT id, status, role_id, password_hash, must_change_password, password_changed_at FROM users"
            ).fetchall():
                cur = current.get(snap["id"])
                if cur is None:
                    continue
                status = max((snap["status"], cur["status"]), key=lambda v: _STATUS_RESTRICTIVENESS.get(v, 2))
                pw = cur if (cur["password_changed_at"] or "") > (snap["password_changed_at"] or "") else snap
                new = (status, cur["role_id"], pw["password_hash"], pw["must_change_password"], pw["password_changed_at"])
                old = (snap["status"], snap["role_id"], snap["password_hash"], snap["must_change_password"], snap["password_changed_at"])
                snap_links = {t: sorted((r[0], r[1]) for r in conn.execute(
                    f"SELECT department_id, created_at FROM {t} WHERE user_id = ?", (snap["id"],)))  # nosec B608 - fixed table names
                    for t in links}
                cur_links = {t: sorted(d for d in links[t].get(snap["id"], []) if d[0] in dept_ids) for t in links}
                if new == old and snap_links == cur_links:
                    continue
                changed += 1
                conn.execute(
                    """UPDATE users SET status = ?, role_id = ?, password_hash = ?,
                              must_change_password = ?, password_changed_at = ? WHERE id = ?""",
                    (*new, snap["id"]),
                )
                for t in links:
                    conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (snap["id"],))  # nosec B608 - fixed table names
                    conn.executemany(
                        f"INSERT INTO {t} (user_id, department_id, created_at) VALUES (?, ?, ?)",  # nosec B608 - fixed table names
                        [(snap["id"], d, c) for d, c in cur_links[t]],
                    )
    finally:
        conn.close()
    return changed


def record_maintenance_audit(event_type: str, *, result="success", detail="") -> None:
    """Narrow technical audit path while normal database admission is blocked.

    No init_db, bootstrap or permit publication. No arbitrary SQL callback.
    Exclusive locks keep this path unavailable to a running application.
    """
    from . import audit
    if event_type not in {audit.RESTORE_PERFORMED, audit.SCHEMA_MIGRATED}:
        raise ValueError("unsupported maintenance audit event")
    with db_guard.exclusive_maintenance("maintenance audit"):
        conn = sqlite3.connect(config.DB_PATH.as_uri() + "?mode=rw", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            audit.record(conn, event_type, result=result, detail=detail)
        finally:
            conn.close()


def _begin_admission(operation: str) -> None:
    try:
        operations_state.begin(operation)
    except (OSError, operations_state.AdmissionRequired) as exc:
        raise MaintenanceError("Журнал допуска недоступен: " + type(exc).__name__ + ". Файлы базы не изменены.") from exc
