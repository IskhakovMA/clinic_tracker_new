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

CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    scope_strategy TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL,
    role_id TEXT NOT NULL REFERENCES roles(id),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'blocked', 'deactivated')),
    password_hash TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT,
    password_changed_at TEXT
);

CREATE TABLE IF NOT EXISTS departments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_departments (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    department_id TEXT NOT NULL REFERENCES departments(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, department_id)
);

CREATE TABLE IF NOT EXISTS department_heads (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    department_id TEXT NOT NULL REFERENCES departments(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, department_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE
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
    updated_at TEXT,
    treating_doctor_user_id TEXT REFERENCES users(id)
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
    detail TEXT,
    actor_user_id TEXT,
    actor_role TEXT,
    actor_department_id TEXT,
    target_user_id TEXT,
    target_department_id TEXT
);

"""

# Индексы создаются после миграции: часть из них ссылается на колонки,
# которые в v1-базе появляются только после guarded-ALTER.
INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_history_patient ON history_entries(patient_id);
CREATE INDEX IF NOT EXISTS idx_events_history ON events(history_entry_id);
CREATE INDEX IF NOT EXISTS idx_future_plans_patient ON future_plans(patient_id);
CREATE INDEX IF NOT EXISTS idx_patients_name ON patients(name);
CREATE INDEX IF NOT EXISTS idx_patients_head ON patients(head);
CREATE INDEX IF NOT EXISTS idx_patients_stage ON patients(stage);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_patients_treating_doctor ON patients(treating_doctor_user_id);
CREATE INDEX IF NOT EXISTS idx_user_departments_dept ON user_departments(department_id);
CREATE INDEX IF NOT EXISTS idx_department_heads_dept ON department_heads(department_id);
"""

# Роли — справочник, а не бизнес-конфигурация: наполняется всегда.
# scope_strategy — стратегия области видимости пациентов; новые стратегии
# (например stage-based для лекарственной терапии) добавляются сюда и в
# policy.py, не переписывая endpoints.
ROLE_SEED = (
    ("developer", "Разработчик", "global"),
    ("deputy_chief_oncology", "Заместитель главного врача по онкологии", "global_readonly"),
    ("caop_head", "Заведующий ЦАОП", "global_readonly"),
    ("department_head", "Заведующий отделением", "by_department"),
    ("doctor", "Врач", "by_treating_doctor"),
)

REQUIRED_TABLES = (
    "schema_version",
    "app_login",
    "roles",
    "users",
    "departments",
    "user_departments",
    "department_heads",
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


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    existing = _table_columns(conn, table)
    if not existing:
        return  # таблицы ещё нет — её создаст SCHEMA_SQL сразу в новой форме
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _seed_roles(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO roles (id, label, scope_strategy) VALUES (?, ?, ?)", ROLE_SEED
    )


def migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """v1 → v2: RBAC-таблицы и связи. Безопасна для повторного запуска.

    Данные пациентов не трогаются: legacy-текст `doctor` сохраняется как есть,
    новая связь `treating_doctor_user_id` появляется пустой (unresolved) и
    заполняется только вручную Разработчиком. Сессии старой (общей) модели
    входа не переносимы на персональные учётные записи и удаляются.
    """
    _add_column_if_missing(conn, "sessions", "user_id",
                           "user_id TEXT REFERENCES users(id) ON DELETE CASCADE")
    _add_column_if_missing(conn, "patients", "treating_doctor_user_id",
                           "treating_doctor_user_id TEXT REFERENCES users(id)")
    for col in ("actor_user_id", "actor_role", "actor_department_id",
                "target_user_id", "target_department_id"):
        _add_column_if_missing(conn, "audit_events", col, f"{col} TEXT")
    # Сессии без user_id принадлежат снятой с эксплуатации общей модели входа.
    if "user_id" in _table_columns(conn, "sessions"):
        conn.execute("DELETE FROM sessions WHERE user_id IS NULL")


def init_db(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation + migration. Safe on an empty DB
    and safe to call repeatedly (CREATE TABLE IF NOT EXISTS + guarded ALTERs).

    Порядок важен: сначала ALTER-ы существующих v1-таблиц (иначе индексы по
    новым колонкам из SCHEMA_SQL не создадутся), затем создание недостающих
    таблиц и индексов."""
    with conn:
        conn.executescript(SCHEMA_SQL)   # создаёт недостающие таблицы (IF NOT EXISTS)
        migrate_v1_to_v2(conn)           # добавляет новые колонки в v1-таблицы
        conn.executescript(INDEX_SQL)    # индексы — когда все колонки уже есть
        _seed_roles(conn)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (config.SCHEMA_VERSION,))
        elif row["version"] == 1:
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))


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
