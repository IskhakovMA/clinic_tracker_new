"""SQLite storage layer: connection handling, schema init, migrations."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS app_login (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    age INTEGER,
    priority TEXT NOT NULL,
    stage TEXT NOT NULL,
    phone TEXT,
    complaint TEXT,
    diagnosis TEXT,
    nosology TEXT,
    disease_stage TEXT,
    arrival TEXT NOT NULL,
    doctor TEXT,
    head TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS history_entries (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    stage TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    checklist_json TEXT,
    note TEXT,
    UNIQUE (patient_id, seq)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    history_entry_id TEXT NOT NULL REFERENCES history_entries(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    date TEXT,
    time TEXT,
    treatment_type TEXT,
    actual_date TEXT,
    actual_time TEXT,
    matched_plan INTEGER,
    no_plan INTEGER NOT NULL DEFAULT 0,
    set_at TEXT,
    UNIQUE (history_entry_id, seq)
);

CREATE TABLE IF NOT EXISTS future_plans (
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    template_index INTEGER NOT NULL,
    date TEXT,
    time TEXT,
    treatment_type TEXT,
    PRIMARY KEY (patient_id, template_index)
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT,
    patient_id TEXT,
    result TEXT NOT NULL,
    request_id TEXT,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_patient ON history_entries(patient_id);
CREATE INDEX IF NOT EXISTS idx_events_history ON events(history_entry_id);
CREATE INDEX IF NOT EXISTS idx_future_plans_patient ON future_plans(patient_id);
CREATE INDEX IF NOT EXISTS idx_patients_name ON patients(name);
CREATE INDEX IF NOT EXISTS idx_patients_head ON patients(head);
CREATE INDEX IF NOT EXISTS idx_patients_stage ON patients(stage);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);
"""

REQUIRED_TABLES = (
    "schema_version",
    "app_login",
    "sessions",
    "patients",
    "history_entries",
    "events",
    "future_plans",
    "audit_events",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # Wait instead of failing immediately when another writer (a CLI backup,
    # a second uvicorn worker) briefly holds the write lock.
    conn.execute(f"PRAGMA busy_timeout = {config.BUSY_TIMEOUT_MS}")
    return conn


def connect_readonly_check(db_path: Path) -> sqlite3.Connection:
    """Plain connection for integrity tooling — no schema creation side effects."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation + first-run bootstrap. Safe on an empty DB
    and safe to call repeatedly (CREATE TABLE IF NOT EXISTS everywhere)."""
    with conn:
        conn.executescript(SCHEMA_SQL)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (config.SCHEMA_VERSION,))


_singleton_conn: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    """Process-wide connection to the configured DB path (used by the app).
    Tests should build their own via `_connect` + `init_db` against a temp path."""
    global _singleton_conn
    if _singleton_conn is None:
        _singleton_conn = _connect(config.DB_PATH)
        init_db(_singleton_conn)
    return _singleton_conn


def reset_singleton_for_tests() -> None:
    global _singleton_conn
    if _singleton_conn is not None:
        _singleton_conn.close()
    _singleton_conn = None


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Explicit transaction boundary for multi-statement writes (import etc.)."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
