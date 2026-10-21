"""SQLite storage layer: connection handling, schema init, migrations."""
import hashlib
import os
import re
import sqlite3
import time
import uuid
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
    policy_number TEXT,
    complaint TEXT,
    diagnosis TEXT,
    consilium_code TEXT,
    pgi_code TEXT,
    pgi_malignancy_confirmed INTEGER CHECK (
        pgi_malignancy_confirmed IN (0, 1) OR pgi_malignancy_confirmed IS NULL
    ),
    pgi_result_date TEXT,
    nosology TEXT,
    disease_stage TEXT,
    arrival TEXT NOT NULL,
    doctor TEXT,
    head TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    treating_doctor_user_id TEXT REFERENCES users(id),
    created_by_user_id TEXT REFERENCES users(id),
    creation_source TEXT NOT NULL DEFAULT 'legacy'
        CHECK (creation_source IN ('form', 'import', 'migration', 'legacy'))
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
    consilium_decision TEXT CHECK (
        consilium_decision IN ('treatment_strategy_defined', 'treatment_strategy_changed', 'dispensary_observation')
        OR consilium_decision IS NULL
    ),
    set_at TEXT,
    -- Мягкое удаление: событие остаётся в истории и восстанавливается.
    deleted_at TEXT,
    deleted_by_user_id TEXT REFERENCES users(id),
    -- Версия строки для защиты от устаревших правок (optimistic locking).
    version INTEGER NOT NULL DEFAULT 1,
    -- Ручная отметка решения ОК (v21): автор и время назначает сервер, источник —
    -- manual (отдельная операция), legacy (записано до v21), import (из файла).
    consilium_decision_by_user_id TEXT REFERENCES users(id),
    consilium_decision_at TEXT,
    consilium_decision_source TEXT CHECK (
        consilium_decision_source IN ('manual', 'legacy', 'import') OR consilium_decision_source IS NULL
    ),
    -- Перенос исторического решения из файла (source = import): исполнитель импорта,
    -- время и идентификатор операции импорта. Это не автор и не время решения.
    consilium_decision_imported_by_user_id TEXT REFERENCES users(id),
    consilium_decision_imported_at TEXT,
    consilium_decision_import_id TEXT,
    UNIQUE (history_entry_id, seq)
);

CREATE TABLE IF NOT EXISTS patient_diagnosis_history (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    changed_at TEXT NOT NULL,
    actor_user_id TEXT REFERENCES users(id),
    old_diagnosis TEXT,
    new_diagnosis TEXT
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

CREATE TABLE IF NOT EXISTS patient_notification_preferences (
    patient_id TEXT PRIMARY KEY REFERENCES patients(id) ON DELETE CASCADE,
    sms_enabled INTEGER NOT NULL DEFAULT 0 CHECK (sms_enabled IN (0, 1)),
    consent_status TEXT NOT NULL DEFAULT 'not_recorded'
        CHECK (consent_status IN ('not_recorded', 'granted', 'revoked')),
    consent_recorded_at TEXT,
    consent_recorded_by_user_id TEXT REFERENCES users(id),
    consent_revoked_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_templates (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('sms')),
    body TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('sms')),
    template_id TEXT REFERENCES notification_templates(id),
    template_code TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('manual', 'event')),
    source_id TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'sent', 'failed', 'cancelled', 'skipped')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TEXT,
    processing_started_at TEXT,
    provider_message_id TEXT,
    provider_status TEXT,
    error_code TEXT,
    error_message_safe TEXT,
    created_by_user_id TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    cancelled_at TEXT,
    cancelled_by_user_id TEXT REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS internal_notification_state (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    patient_id TEXT REFERENCES patients(id) ON DELETE CASCADE,
    notification_type TEXT NOT NULL CHECK (notification_type IN ('overdue', 'today', 'upcoming', 'checklist', 'data_quality', 'attention', 'consilium_revision')),
    source_type TEXT NOT NULL CHECK (source_type IN ('event', 'future_plan', 'checklist', 'data_quality', 'attention', 'consilium_revision')),
    source_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    read_at TEXT,
    resolved_at TEXT,
    UNIQUE (user_id, stable_key)
);

CREATE TABLE IF NOT EXISTS patient_attention_issues (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    reason_code TEXT NOT NULL CHECK (reason_code IN (
        'emergency_hospitalization',
        'route_change',
        'critical_overdue',
        'doctor_decision_required',
        'department_head_decision_required',
        'organizational_issue',
        'documents_or_data_issue',
        'patient_unavailable',
        'other'
    )),
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    comment TEXT,
    event_date TEXT,
    created_at TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    closed_at TEXT,
    closed_by_user_id TEXT REFERENCES users(id),
    resolution_comment TEXT,
    CHECK (status = 'open' OR (closed_at IS NOT NULL AND closed_by_user_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS oncology_consilium_appointments (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    created_by_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scheduled_date TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    consilium_group TEXT NOT NULL,
    has_benefit INTEGER NOT NULL CHECK (has_benefit IN (0, 1)),
    treating_doctor_name TEXT NOT NULL,
    policy_number TEXT,
    diagnosis_localization TEXT,
    primary_diagnosis TEXT NOT NULL,
    additional_diagnosis TEXT,
    comment TEXT,
    additional_participant TEXT,
    needs_surgeon INTEGER NOT NULL CHECK (needs_surgeon IN (0, 1)),
    needs_radiotherapist INTEGER NOT NULL CHECK (needs_radiotherapist IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'recorded'
        CHECK (status IN ('recorded', 'in_review', 'signed')),
    treatment_type TEXT,
    treatment_scheme TEXT,
    treatment_scheme_code TEXT,
    goc INTEGER NOT NULL DEFAULT 0 CHECK (goc IN (0, 1)),
    goc_65_plus INTEGER NOT NULL DEFAULT 0 CHECK (goc_65_plus IN (0, 1)),
    uv_ruv INTEGER NOT NULL DEFAULT 0 CHECK (uv_ruv IN (0, 1)),
    revision_comment TEXT,
    revision_submitted_at TEXT,
    revision_count INTEGER NOT NULL DEFAULT 0 CHECK (revision_count >= 0),
    review_started_at TEXT,
    signed_at TEXT,
    signed_by_user_id TEXT REFERENCES users(id),
    signed_treating_doctor_user_id TEXT REFERENCES users(id),
    signed_treating_doctor_name TEXT,
    signed_route_history_entry_id TEXT,
    signed_route_stage TEXT,
    signed_route_entered_at TEXT,
    signed_route_link_source TEXT CHECK (signed_route_link_source IN ('recorded', 'derived', 'unknown')),
    deleted_at TEXT,
    deleted_by_user_id TEXT REFERENCES users(id),
    referral_completed_at TEXT,
    referral_completed_by_user_id TEXT REFERENCES users(id),
    archived_at TEXT,
    archived_by_user_id TEXT REFERENCES users(id),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    backdating_reason_id TEXT REFERENCES consilium_backdating_reasons(id),
    backdated INTEGER NOT NULL DEFAULT 0
        CHECK (backdated IN (0, 1) AND (backdated = 0 OR backdating_reason_id IS NOT NULL)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS consilium_revision_history (
    id TEXT PRIMARY KEY,
    appointment_id TEXT NOT NULL REFERENCES oncology_consilium_appointments(id) ON DELETE CASCADE,
    round INTEGER NOT NULL CHECK (round >= 1),
    comment TEXT NOT NULL,
    returned_by_user_id TEXT REFERENCES users(id),
    returned_at TEXT NOT NULL,
    migrated INTEGER NOT NULL DEFAULT 0 CHECK (migrated IN (0, 1)),
    UNIQUE (appointment_id, round)
);

CREATE TABLE IF NOT EXISTS consilium_chair_favorites (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, patient_id)
);

CREATE TABLE IF NOT EXISTS consilium_schedule_slots (
    id TEXT PRIMARY KEY,
    scheduled_date TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    consilium_group TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    backdating_reason_id TEXT REFERENCES consilium_backdating_reasons(id),
    backdated INTEGER NOT NULL DEFAULT 0
        CHECK (backdated IN (0, 1) AND (backdated = 0 OR backdating_reason_id IS NOT NULL)),
    UNIQUE (scheduled_date, scheduled_time, consilium_group)
);

-- v22 (WP2.3-B1). Причины оформления задним числом: отдельно от audit_events,
-- текст может содержать сведения о пациенте. Таблица подготовлена для WP2.3-B3:
-- ни один API её пока не пишет и не читает.
CREATE TABLE IF NOT EXISTS consilium_backdating_reasons (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('slot', 'appointment')),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

-- v22: повторные подачи после доработки, по одной на раунд возврата.
-- submitted_by_user_id IS NULL только у строк, перенесенных из v21 (migrated = 1):
-- автор прежних подач не хранился и не выдумывается.
CREATE TABLE IF NOT EXISTS consilium_resubmissions (
    id TEXT PRIMARY KEY,
    appointment_id TEXT NOT NULL REFERENCES oncology_consilium_appointments(id) ON DELETE CASCADE,
    round INTEGER NOT NULL CHECK (round >= 1),
    submitted_by_user_id TEXT REFERENCES users(id),
    submitted_at TEXT NOT NULL,
    migrated INTEGER NOT NULL DEFAULT 0 CHECK (migrated IN (0, 1)),
    CHECK (migrated = 1 OR submitted_by_user_id IS NOT NULL),
    UNIQUE (appointment_id, round)
);

-- WP2.4-B (v24). Ключи идемпотентности: только хеши ключа и запроса, без тел
-- запросов и данных пациентов. Одна строка на ключ в области (сотрудник,
-- операция); результат однозначен: у создания — ID карточки, у импорта и
-- переноса — ID выполненного импорта и сохраненный обезличенный итог.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    actor_user_id TEXT NOT NULL REFERENCES users(id),
    operation TEXT NOT NULL CHECK (operation IN ('patient.create', 'import.commit', 'migration.commit')),
    key_hash TEXT NOT NULL CHECK (length(key_hash) = 64),
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    state TEXT NOT NULL CHECK (state IN ('completed', 'refused')),
    status_code INTEGER NOT NULL CHECK (status_code BETWEEN 200 AND 499),
    result_patient_id TEXT,
    result_import_id TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL CHECK (expires_at > created_at),
    PRIMARY KEY (actor_user_id, operation, key_hash),
    CHECK (
        (state = 'refused' AND result_patient_id IS NULL AND result_import_id IS NULL AND result_json IS NULL)
        OR (state = 'completed' AND operation = 'patient.create' AND status_code = 201
            AND result_patient_id IS NOT NULL AND result_import_id IS NULL AND result_json IS NULL)
        OR (state = 'completed' AND operation IN ('import.commit', 'migration.commit') AND status_code = 200
            AND result_patient_id IS NULL AND result_import_id IS NOT NULL AND result_json IS NOT NULL)
    )
);

-- WP2.4-B (v24). Техническая история обработанных файлов импорта и переноса:
-- исполнитель, SHA-256 файла, режим, ID импорта, время и счетчики. Ни
-- содержимого файла, ни значений полей пациентов. Срок ключей ее не удаляет.
CREATE TABLE IF NOT EXISTS import_files_processed (
    import_id TEXT PRIMARY KEY,
    actor_user_id TEXT NOT NULL REFERENCES users(id),
    operation TEXT NOT NULL CHECK (operation IN ('import', 'migration')),
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
    mode TEXT NOT NULL CHECK (mode IN ('add', 'update', 'replace')),
    processed_at TEXT NOT NULL,
    added INTEGER NOT NULL CHECK (added >= 0),
    updated INTEGER NOT NULL CHECK (updated >= 0),
    skipped INTEGER NOT NULL CHECK (skipped >= 0),
    unchanged INTEGER NOT NULL CHECK (unchanged >= 0),
    appended_events INTEGER NOT NULL CHECK (appended_events >= 0),
    repeat_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (repeat_confirmed IN (0, 1))
);

-- WP2.4-C (v25). Осознанное подтверждение возможного дубля: новая или
-- измененная карточка, подтвержденные совпадения (JSON-массив ID видимых
-- сотруднику карточек), причина, автор и серверное время. Только добавление.
-- patient_id без внешнего ключа (как в audit_events): запись остается следом
-- решения и не мешает существующему удалению карточки.
CREATE TABLE IF NOT EXISTS patient_duplicate_confirmations (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL CHECK (operation IN ('create', 'policy_update', 'import')),
    patient_id TEXT NOT NULL CHECK (length(patient_id) BETWEEN 1 AND 64),
    matched_patient_ids TEXT NOT NULL CHECK (
        json_valid(matched_patient_ids) AND json_type(matched_patient_ids) = 'array'
        AND json_array_length(matched_patient_ids) BETWEEN 1 AND 50
    ),
    reason TEXT NOT NULL CHECK (length(trim(reason)) >= 1 AND length(reason) <= 2000),
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    import_id TEXT,
    CHECK ((operation = 'import') = (import_id IS NOT NULL))
);

"""

# Индексы создаются после миграции: часть из них ссылается на колонки,
# которые в v1-базе появляются только после guarded-ALTER.
INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_history_patient ON history_entries(patient_id);
CREATE INDEX IF NOT EXISTS idx_events_history ON events(history_entry_id);
CREATE INDEX IF NOT EXISTS idx_events_history_active ON events(history_entry_id, deleted_at);
CREATE INDEX IF NOT EXISTS idx_future_plans_patient ON future_plans(patient_id);
CREATE INDEX IF NOT EXISTS idx_patients_name ON patients(name);
CREATE INDEX IF NOT EXISTS idx_patients_policy_number ON patients(policy_number);
CREATE INDEX IF NOT EXISTS idx_diagnosis_history_patient ON patient_diagnosis_history(patient_id, changed_at);
CREATE INDEX IF NOT EXISTS idx_patients_head ON patients(head);
CREATE INDEX IF NOT EXISTS idx_patients_stage ON patients(stage);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_patients_treating_doctor ON patients(treating_doctor_user_id);
CREATE INDEX IF NOT EXISTS idx_user_departments_dept ON user_departments(department_id);
CREATE INDEX IF NOT EXISTS idx_department_heads_dept ON department_heads(department_id);
CREATE INDEX IF NOT EXISTS idx_notification_preferences_status ON patient_notification_preferences(consent_status, sms_enabled);
CREATE INDEX IF NOT EXISTS idx_notification_templates_code ON notification_templates(code);
CREATE INDEX IF NOT EXISTS idx_notifications_status_due ON notifications(status, next_attempt_at, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_notifications_patient ON notifications(patient_id);
CREATE INDEX IF NOT EXISTS idx_notifications_source ON notifications(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_notifications_scheduled ON notifications(scheduled_at);
-- Ключ держат живые строки и строки, отменённые сотрудником вручную. Строка,
-- отменённая системой (перенос, факт визита, отзыв согласия) или пропущенная,
-- не мешает заново поставить напоминание на то же время.
CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_idempotency_key ON notifications(idempotency_key)
    WHERE status IN ('pending', 'processing', 'sent', 'failed')
       OR (status = 'cancelled' AND error_code IS NULL);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_idempotency_active
    ON notifications(patient_id, source_type, source_id, template_code, scheduled_at)
    WHERE status IN ('pending', 'processing', 'sent');
CREATE INDEX IF NOT EXISTS idx_internal_notifications_user_active
    ON internal_notification_state(user_id, resolved_at, read_at, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_internal_notifications_patient
    ON internal_notification_state(patient_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_internal_notifications_user_key
    ON internal_notification_state(user_id, stable_key);
CREATE INDEX IF NOT EXISTS idx_attention_patient_status
    ON patient_attention_issues(patient_id, status);
CREATE INDEX IF NOT EXISTS idx_attention_status
    ON patient_attention_issues(status);
CREATE INDEX IF NOT EXISTS idx_attention_reason
    ON patient_attention_issues(reason_code);
CREATE INDEX IF NOT EXISTS idx_attention_created_at
    ON patient_attention_issues(created_at);
CREATE INDEX IF NOT EXISTS idx_consilium_owner_date
    ON oncology_consilium_appointments(created_by_user_id, scheduled_date, scheduled_time);
CREATE INDEX IF NOT EXISTS idx_consilium_patient
    ON oncology_consilium_appointments(patient_id);
CREATE INDEX IF NOT EXISTS idx_consilium_treatment_registry
    ON oncology_consilium_appointments(treatment_type, status, scheduled_date);
CREATE INDEX IF NOT EXISTS idx_consilium_owner_archive_date
    ON oncology_consilium_appointments(created_by_user_id, archived_at, scheduled_date, scheduled_time);
CREATE INDEX IF NOT EXISTS idx_consilium_favorites_patient
    ON consilium_chair_favorites(patient_id);
CREATE INDEX IF NOT EXISTS idx_consilium_schedule_date
    ON consilium_schedule_slots(scheduled_date, scheduled_time, consilium_group);
CREATE INDEX IF NOT EXISTS idx_consilium_resubmissions_appointment
    ON consilium_resubmissions(appointment_id, round);
CREATE INDEX IF NOT EXISTS idx_consilium_patient_open
    ON oncology_consilium_appointments(patient_id, status, deleted_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_consilium_appointment_slot
    ON oncology_consilium_appointments(scheduled_date, scheduled_time, consilium_group)
    WHERE deleted_at IS NULL;
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
    ("consilium_chair", "Председатель онкологического консилиума", "by_treating_doctor"),
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
    "patient_diagnosis_history",
    "future_plans",
    "audit_events",
    "patient_notification_preferences",
    "notification_templates",
    "notifications",
    "internal_notification_state",
    "patient_attention_issues",
    "oncology_consilium_appointments",
    "consilium_chair_favorites",
    "consilium_revision_history",
    "consilium_schedule_slots",
    "consilium_backdating_reasons",
    "consilium_resubmissions",
    "idempotency_keys",
    "import_files_processed",
    "patient_duplicate_confirmations",
)

NOTIFICATION_TEMPLATE_SEED = (
    (
        "appointment_reminder",
        "Напоминание о посещении",
        "sms",
        "Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.",
    ),
    (
        "examination_reminder",
        "Напоминание о посещении",
        "sms",
        "Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.",
    ),
    (
        "custom_reminder",
        "Ручное напоминание",
        "sms",
        "Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.",
    ),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def in_chunks(conn: sqlite3.Connection, values, reserve: int = 8):
    """Split values for `IN (?, ...)` lists so a query never binds more
    variables than this connection's SQLite limit allows (reserve leaves
    room for other parameters of the same statement)."""
    values = list(values)
    limit = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    size = max(1, min(30000, limit - reserve))
    for i in range(0, len(values), size):
        yield values[i:i + size]


def temp_id_table(conn: sqlite3.Connection, values) -> str:
    """Load values into a fresh per-connection TEMP table and return its
    qualified name, for `x IN (SELECT id FROM <name>)` / `NOT IN (...)` over
    sets that may exceed SQLITE_LIMIT_VARIABLE_NUMBER. TEMP tables live in the
    connection's temp database, so no lock is taken on the main database."""
    name = f"temp.ids_{uuid.uuid4().hex}"
    conn.execute(f"CREATE TABLE {name} (id TEXT PRIMARY KEY)")  # nosec B608 - generated identifier
    conn.executemany(f"INSERT OR IGNORE INTO {name} (id) VALUES (?)", ((v,) for v in values))  # nosec B608
    return name


_hardened_paths: set[str] = set()


def _harden_permissions(db_path: Path) -> None:
    """Каталог данных 0700, файл БД 0600 (SQLite создаёт -wal/-shm с правами
    основного файла). Best effort, один раз на путь в процессе: не зависит
    от umask сервиса и чинит права существующей установки при старте."""
    key = str(db_path)
    if key in _hardened_paths:
        return
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not db_path.exists():
        os.close(os.open(str(db_path), os.O_CREAT | os.O_WRONLY, 0o600))
    for path, mode in ((db_path.parent, 0o700), (db_path, 0o600),
                       (db_path.with_name(db_path.name + "-wal"), 0o600),
                       (db_path.with_name(db_path.name + "-shm"), 0o600)):
        try:
            if path.exists():
                path.chmod(mode)
        except (OSError, NotImplementedError):
            pass
    _hardened_paths.add(key)


def _connect(db_path: Path) -> sqlite3.Connection:
    _harden_permissions(db_path)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Wait instead of failing immediately when another writer (a CLI backup,
    # a second uvicorn worker) briefly holds the write lock.
    conn.execute(f"PRAGMA busy_timeout = {config.BUSY_TIMEOUT_MS}")
    _enable_wal(conn)
    return conn


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Switch the file to WAL, waiting for a competing lock within busy_timeout.

    Every database this app creates is already WAL (the mode is kept in the
    file), so nothing is switched then. A file that is not WAL yet needs
    exclusive access to switch. When two processes (the server and a CLI
    command) switch it at the same moment, or another connection holds a
    write lock, SQLite returns SQLITE_BUSY at once instead of using
    busy_timeout, because waiting could deadlock: the caller has to let go
    and try again. The statement runs in autocommit, so a failed attempt
    holds no lock. Attempts stop at the same busy timeout and the last
    error is raised.
    """
    if conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
        return
    deadline = time.monotonic() + config.BUSY_TIMEOUT_MS / 1000
    while True:
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            return
        except sqlite3.OperationalError as exc:
            if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY or time.monotonic() >= deadline:
                raise
        time.sleep(0.01)


def connect_readonly_check(db_path: Path) -> sqlite3.Connection:
    """Plain connection for integrity tooling — no schema creation side effects."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _execute_script(conn: sqlite3.Connection, script: str) -> None:
    """executescript() without its implicit COMMIT.

    sqlite3's executescript() first commits an open transaction, which would
    release init_db's write lock in the middle of a migration. The script is
    split on ';' and every complete statement (sqlite3.complete_statement
    keeps a trigger body or a ';' inside a string or comment together) runs
    in the caller's transaction.
    """
    statement = ""
    parts = script.split(";")
    for index, part in enumerate(parts):
        statement += part if index == len(parts) - 1 else part + ";"
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    if statement.strip():
        conn.execute(statement)


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


def _seed_notification_templates(conn: sqlite3.Connection) -> None:
    now = now_iso()
    for code, name, channel, body in NOTIFICATION_TEMPLATE_SEED:
        conn.execute(
            """INSERT OR IGNORE INTO notification_templates
               (id, code, name, channel, body, enabled, version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?)""",
            (f"tpl_{code}", code, name, channel, body, now, now),
        )


def _notification_idempotency_key(
    patient_id: str, source_type: str, source_id: str, template_code: str, scheduled_at: str
) -> str:
    raw = "\x1f".join((patient_id, source_type, source_id, template_code, scheduled_at))
    return "notif_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ensure_notification_schema(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "notifications")
    if not columns:
        return
    index_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_notifications_idempotency_key'"
    ).fetchone()
    if index_sql and index_sql[0] and "error_code" not in index_sql[0]:
        # Старый полный индекс держал ключ и за отменёнными строками; INDEX_SQL
        # создаст частичный заново.
        conn.execute("DROP INDEX idx_notifications_idempotency_key")
    if "idempotency_key" not in columns:
        conn.execute("ALTER TABLE notifications ADD COLUMN idempotency_key TEXT")
    if "processing_started_at" not in columns:
        conn.execute("ALTER TABLE notifications ADD COLUMN processing_started_at TEXT")
    rows = conn.execute(
        """SELECT id, patient_id, source_type, source_id, template_code, scheduled_at
           FROM notifications
           WHERE idempotency_key IS NULL OR idempotency_key = ''"""
    ).fetchall()
    if not rows:
        # Нечего заполнять: не читать все ключи очереди на каждом запросе.
        return
    used = {
        row["idempotency_key"]
        for row in conn.execute(
            "SELECT idempotency_key FROM notifications WHERE idempotency_key IS NOT NULL AND idempotency_key != ''"
        ).fetchall()
    }
    for row in rows:
        key = _notification_idempotency_key(
            row["patient_id"], row["source_type"], row["source_id"], row["template_code"], row["scheduled_at"]
        )
        if key in used:
            key = f"{key}:legacy:{row['id']}"
        used.add(key)
        conn.execute("UPDATE notifications SET idempotency_key = ? WHERE id = ?", (key, row["id"]))


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


def migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """v2 → v3: notification preferences, safe templates and queue.

    Patient records, users, departments and audit rows are not modified. Consent
    starts as `not_recorded`; phone presence is deliberately not interpreted as
    permission to send SMS.
    """
    _execute_script(conn, SCHEMA_SQL)
    _ensure_notification_schema(conn)
    _seed_notification_templates(conn)


def migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """v3 → v4: nullable OMS policy number on patient cards."""
    _add_column_if_missing(conn, "patients", "policy_number", "policy_number TEXT")


def migrate_v4_to_v5(conn: sqlite3.Connection) -> None:
    """v4 → v5: read-state for computed internal notification conditions."""
    _execute_script(conn, SCHEMA_SQL)


def _rebuild_internal_notification_state_v6(conn: sqlite3.Connection) -> None:
    if "internal_notification_state" not in {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }:
        return
    columns = _table_columns(conn, "internal_notification_state")
    if not columns:
        return
    _execute_script(conn,
        """
        CREATE TABLE IF NOT EXISTS internal_notification_state_v6 (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            stable_key TEXT NOT NULL,
            patient_id TEXT REFERENCES patients(id) ON DELETE CASCADE,
            notification_type TEXT NOT NULL CHECK (notification_type IN ('overdue', 'today', 'upcoming', 'checklist', 'data_quality', 'attention')),
            source_type TEXT NOT NULL CHECK (source_type IN ('event', 'future_plan', 'checklist', 'data_quality', 'attention')),
            source_id TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            read_at TEXT,
            resolved_at TEXT,
            UNIQUE (user_id, stable_key)
        );
        INSERT OR IGNORE INTO internal_notification_state_v6
            (id, user_id, stable_key, patient_id, notification_type, source_type, source_id,
             first_seen_at, last_seen_at, read_at, resolved_at)
        SELECT id, user_id, stable_key, patient_id, notification_type, source_type, source_id,
               first_seen_at, last_seen_at, read_at, resolved_at
        FROM internal_notification_state;
        DROP TABLE internal_notification_state;
        ALTER TABLE internal_notification_state_v6 RENAME TO internal_notification_state;
        """
    )


def migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """v5 → v6: patient attention issues and attention notification type."""
    _rebuild_internal_notification_state_v6(conn)
    _execute_script(conn, SCHEMA_SQL)


def migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """v6 → v7: doctor feedback optional patient codes and diagnosis history."""
    _add_column_if_missing(conn, "patients", "consilium_code", "consilium_code TEXT")
    _add_column_if_missing(conn, "patients", "pgi_code", "pgi_code TEXT")
    _execute_script(conn, SCHEMA_SQL)


def migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """v7 → v8: structured OK decision and PGI result.

    Legacy free-text `consilium_code`/`pgi_code` are preserved as deprecated
    compatibility columns; no automatic conversion is attempted.
    """
    _add_column_if_missing(
        conn,
        "patients",
        "pgi_malignancy_confirmed",
        "pgi_malignancy_confirmed INTEGER CHECK (pgi_malignancy_confirmed IN (0, 1) OR pgi_malignancy_confirmed IS NULL)",
    )
    _add_column_if_missing(
        conn,
        "events",
        "consilium_decision",
        "consilium_decision TEXT CHECK (consilium_decision IN ('treatment_strategy_defined', 'treatment_strategy_changed', 'dispensary_observation') OR consilium_decision IS NULL)",
    )
    _execute_script(conn, SCHEMA_SQL)


def migrate_v8_to_v9(conn: sqlite3.Connection) -> None:
    """v8 → v9: PGI result date and retired active disease/future-plan fields.

    Existing disease_stage and future_plans storage is intentionally left in
    place as deprecated compatibility storage; active API/UI no longer reads
    or writes it. Existing PGI rows receive NULL date, never an invented date.
    """
    _add_column_if_missing(conn, "patients", "pgi_result_date", "pgi_result_date TEXT")
    _execute_script(conn, SCHEMA_SQL)


def migrate_v9_to_v10(conn: sqlite3.Connection) -> None:
    """v9 → v10: персональные записи на онкологический консилиум."""
    _execute_script(conn, SCHEMA_SQL)


def migrate_v10_to_v11(conn: sqlite3.Connection) -> None:
    """v10 → v11: workflow председателя, подписание и возврат на доработку."""
    _add_column_if_missing(conn, "oncology_consilium_appointments", "status", "status TEXT NOT NULL DEFAULT 'recorded' CHECK (status IN ('recorded', 'in_review', 'signed'))")
    _add_column_if_missing(conn, "oncology_consilium_appointments", "treatment_type", "treatment_type TEXT")
    _add_column_if_missing(conn, "oncology_consilium_appointments", "treatment_scheme", "treatment_scheme TEXT")
    _add_column_if_missing(conn, "oncology_consilium_appointments", "revision_comment", "revision_comment TEXT")
    _add_column_if_missing(conn, "oncology_consilium_appointments", "review_started_at", "review_started_at TEXT")
    _add_column_if_missing(conn, "oncology_consilium_appointments", "signed_at", "signed_at TEXT")
    _add_column_if_missing(conn, "oncology_consilium_appointments", "signed_by_user_id", "signed_by_user_id TEXT REFERENCES users(id)")
    _execute_script(conn, SCHEMA_SQL)


def migrate_v11_to_v12(conn: sqlite3.Connection) -> None:
    """v11 → v12: полис, избранное председателя и реестры лечения."""
    _add_column_if_missing(conn, "oncology_consilium_appointments", "policy_number", "policy_number TEXT")
    _add_column_if_missing(conn, "oncology_consilium_appointments", "referral_completed_at", "referral_completed_at TEXT")
    _add_column_if_missing(conn, "oncology_consilium_appointments", "referral_completed_by_user_id", "referral_completed_by_user_id TEXT REFERENCES users(id)")
    _execute_script(conn, SCHEMA_SQL)


def migrate_v12_to_v13(conn: sqlite3.Connection) -> None:
    """v12 → v13: локализация диагноза и код схемы лечения."""
    _add_column_if_missing(conn, "oncology_consilium_appointments", "diagnosis_localization", "diagnosis_localization TEXT")
    _add_column_if_missing(conn, "oncology_consilium_appointments", "treatment_scheme_code", "treatment_scheme_code TEXT")
    _execute_script(conn, SCHEMA_SQL)


def migrate_v13_to_v14(conn: sqlite3.Connection) -> None:
    """v13 → v14: отметки ГОК и ГОК 65+ в решении консилиума."""
    _add_column_if_missing(conn, "oncology_consilium_appointments", "goc", "goc INTEGER NOT NULL DEFAULT 0 CHECK (goc IN (0, 1))")
    _add_column_if_missing(conn, "oncology_consilium_appointments", "goc_65_plus", "goc_65_plus INTEGER NOT NULL DEFAULT 0 CHECK (goc_65_plus IN (0, 1))")
    _execute_script(conn, SCHEMA_SQL)


def migrate_v14_to_v15(conn: sqlite3.Connection) -> None:
    """v14 → v15: personal archive for signed consilium appointments."""
    _add_column_if_missing(conn, "oncology_consilium_appointments", "archived_at", "archived_at TEXT")
    _add_column_if_missing(conn, "oncology_consilium_appointments", "archived_by_user_id", "archived_by_user_id TEXT REFERENCES users(id)")
    _execute_script(conn, SCHEMA_SQL)


def migrate_v15_to_v16(conn: sqlite3.Connection) -> None:
    """v15 → v16: chair schedule grid and consilium revision notifications."""
    current_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'internal_notification_state'"
    ).fetchone()
    current_sql = (current_sql_row["sql"] or "") if current_sql_row else ""
    if current_sql_row and "consilium_revision" not in current_sql:
        _execute_script(conn, """
            CREATE TABLE internal_notification_state_v16 (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                stable_key TEXT NOT NULL,
                patient_id TEXT REFERENCES patients(id) ON DELETE CASCADE,
                notification_type TEXT NOT NULL CHECK (notification_type IN ('overdue', 'today', 'upcoming', 'checklist', 'data_quality', 'attention', 'consilium_revision')),
                source_type TEXT NOT NULL CHECK (source_type IN ('event', 'future_plan', 'checklist', 'data_quality', 'attention', 'consilium_revision')),
                source_id TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                read_at TEXT,
                resolved_at TEXT,
                UNIQUE (user_id, stable_key)
            );
            INSERT OR IGNORE INTO internal_notification_state_v16
                (id, user_id, stable_key, patient_id, notification_type, source_type, source_id,
                 first_seen_at, last_seen_at, read_at, resolved_at)
            SELECT id, user_id, stable_key, patient_id, notification_type, source_type, source_id,
                   first_seen_at, last_seen_at, read_at, resolved_at
            FROM internal_notification_state;
            DROP TABLE internal_notification_state;
            ALTER TABLE internal_notification_state_v16 RENAME TO internal_notification_state;
        """)
    _add_column_if_missing(conn, "oncology_consilium_appointments", "uv_ruv", "uv_ruv INTEGER NOT NULL DEFAULT 0 CHECK (uv_ruv IN (0, 1))")
    _execute_script(conn, SCHEMA_SQL)


def migrate_v16_to_v17(conn: sqlite3.Connection) -> None:
    """v16 → v17: strict slot and patient booking invariants.

    v17 added a trigger guard «one appointment per patient, forever». Since
    v22 the patient guard is «at most one unfinished appointment per patient»
    and is installed by _ensure_consilium_patient_guard (migrate_v21_to_v22).
    Every migration function runs on each start, so the v17 trigger is no
    longer created here — otherwise each start would bring it back. The slot
    uniqueness of v17 is uq_consilium_appointment_slot in INDEX_SQL.
    """


class SchemaMigrationError(RuntimeError):
    """The database holds data that the current schema cannot accept as is."""


def _assert_no_duplicate_consilium_slots(conn: sqlite3.Connection) -> None:
    """Fail with a readable diagnosis before uq_consilium_appointment_slot.

    Builds before v17 allowed two appointments in one slot. Creating the
    unique index over such data raised a bare IntegrityError and the app did
    not start. Medical records are never deleted automatically: the start is
    stopped with the record ids (no patient data) so they can be moved.
    """
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'uq_consilium_appointment_slot'"
    ).fetchone():
        return
    if not _table_columns(conn, "oncology_consilium_appointments"):
        return
    rows = conn.execute(
        """SELECT scheduled_date, scheduled_time, consilium_group,
                  GROUP_CONCAT(id, ',') AS ids, COUNT(*) AS c
           FROM oncology_consilium_appointments
           WHERE deleted_at IS NULL
           GROUP BY scheduled_date, scheduled_time, consilium_group
           HAVING COUNT(*) > 1
           ORDER BY scheduled_date, scheduled_time, consilium_group"""
    ).fetchall()
    if rows:
        slots = "; ".join(
            f"{r['scheduled_date']} {r['scheduled_time']} {r['consilium_group']}: {r['ids']}" for r in rows
        )
        raise SchemaMigrationError(
            f"Нельзя создать уникальный индекс слотов консилиума: {len(rows)} слот(ов) заняты "
            f"несколькими записями ({slots}). Записи не удалены. Перенесите лишние записи в "
            "свободные слоты (или удалите ошибочные) и запустите приложение снова."
        )


# Поля подписанного решения, которые ещё можно менять: личный архив врача и
# отметка исполнения хирургического направления. Всё остальное — решение.
_SIGNED_MUTABLE_COLUMNS = (
    "archived_at", "archived_by_user_id",
    "referral_completed_at", "referral_completed_by_user_id", "updated_at",
    # v22: номер версии растет при архивировании подписанной записи; само
    # решение и остальные столбцы по-прежнему защищены (version только растет —
    # trg_consilium_version_forward).
    "version",
)


def _signed_immutable_trigger_sql(columns: list[str]) -> str:
    guarded = [c for c in columns if c not in _SIGNED_MUTABLE_COLUMNS]
    changed = " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in guarded)
    return f"""
        CREATE TRIGGER IF NOT EXISTS trg_consilium_signed_immutable
        BEFORE UPDATE ON oncology_consilium_appointments
        WHEN OLD.status = 'signed' AND ({changed})
        BEGIN
            SELECT RAISE(ABORT, 'consilium_signed_immutable');
        END;
    """


def _normalized_sql(sql: str | None) -> str:
    text = re.sub(r"\s+", " ", (sql or "").replace("IF NOT EXISTS ", "")).strip()
    return text[:-1].rstrip() if text.endswith(";") else text


def _ensure_trigger(
    conn: sqlite3.Connection, name: str, create_sql: str, backfills: tuple[str, ...] = (),
    pending: str | None = None,
) -> None:
    """Make trigger `name` match `create_sql`, running `backfills` while it is off.

    Runs inside init_db's IMMEDIATE transaction: no other connection can
    write while the trigger is absent, and a run that waited for the lock
    finds the work done. Nothing is touched when the trigger already matches
    and `pending` (an EXISTS query) finds no row to backfill.
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (name,)).fetchone()
    if _normalized_sql(row["sql"] if row else None) == _normalized_sql(create_sql) and not (
        pending and conn.execute(pending).fetchone()
    ):
        return
    conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    for statement in backfills:
        conn.execute(statement)
    conn.execute(create_sql)


def migrate_v17_to_v18(conn: sqlite3.Connection) -> None:
    """v17 → v18: resubmission marker and immutable signed decisions.

    revision_submitted_at separates «returned, waiting for the doctor» from
    «doctor resubmitted, waiting for the chair», so a resubmission no longer
    starts the review on the chair's behalf. The trigger keeps a signed
    decision unchanged even if a future code path forgets the status check.
    """
    _add_column_if_missing(
        conn, "oncology_consilium_appointments", "revision_submitted_at", "revision_submitted_at TEXT",
    )
    _add_column_if_missing(
        conn, "oncology_consilium_appointments", "revision_count",
        "revision_count INTEGER NOT NULL DEFAULT 0 CHECK (revision_count >= 0)",
    )
    _add_column_if_missing(
        conn, "oncology_consilium_appointments", "signed_treating_doctor_user_id",
        "signed_treating_doctor_user_id TEXT REFERENCES users(id)",
    )
    _add_column_if_missing(
        conn, "oncology_consilium_appointments", "signed_treating_doctor_name",
        "signed_treating_doctor_name TEXT",
    )
    columns = sorted(_table_columns(conn, "oncology_consilium_appointments"))
    if not columns:
        return
    # Решения, подписанные сборкой до v18: врач на момент подписи не
    # сохранялся; сборка показывала при подписании treating_doctor_name
    # записи — он и становится историческим значением. id неизвестен.
    # Записи, возвращённые сборкой до v18, уже прошли один раунд доработки.
    _ensure_trigger(
        conn, "trg_consilium_signed_immutable", _signed_immutable_trigger_sql(columns),
        backfills=(
            "UPDATE oncology_consilium_appointments SET signed_treating_doctor_name = treating_doctor_name "
            "WHERE status = 'signed' AND signed_treating_doctor_name IS NULL",
            "UPDATE oncology_consilium_appointments SET revision_count = 1 "
            "WHERE revision_count = 0 AND COALESCE(revision_comment, '') != ''",
        ),
        pending=(
            "SELECT 1 FROM oncology_consilium_appointments "
            "WHERE (status = 'signed' AND signed_treating_doctor_name IS NULL) "
            "OR (revision_count = 0 AND COALESCE(revision_comment, '') != '') LIMIT 1"
        ),
    )
    # История возвратов: до v18 текст прошлых раундов перезаписывался, и
    # восстановить можно только последний комментарий записи (автор неизвестен,
    # migrated = 1). Строки истории неизменны.
    _execute_script(conn, SCHEMA_SQL)
    conn.execute(
        """INSERT OR IGNORE INTO consilium_revision_history
               (id, appointment_id, round, comment, returned_by_user_id, returned_at, migrated)
           SELECT lower(hex(randomblob(16))), id, revision_count, revision_comment, NULL, updated_at, 1
           FROM oncology_consilium_appointments
           WHERE revision_count >= 1 AND COALESCE(revision_comment, '') != ''"""
    )
    _execute_script(conn,
        """
        CREATE TRIGGER IF NOT EXISTS trg_consilium_revision_history_immutable
        BEFORE UPDATE ON consilium_revision_history
        BEGIN
            SELECT RAISE(ABORT, 'consilium_revision_history_immutable');
        END;
        """
    )


_ROUTE_ENTRY_AT_SIGNING = """
    FROM history_entries h
    WHERE h.patient_id = oncology_consilium_appointments.patient_id
      AND h.entered_at <= oncology_consilium_appointments.signed_at
    ORDER BY h.seq DESC LIMIT 1
"""


def migrate_v18_to_v19(conn: sqlite3.Connection) -> None:
    """v18 → v19: route context of signed decisions and soft deletion.

    - signed_route_*: the patient's route entry and stage a decision was
      signed in. New signatures record it (source 'recorded'). For decisions
      signed by older builds it is derived from the route history entry
      current at signed_at ('derived'), or left empty ('unknown') when the
      history has no entry by then. Like the rest of a signed decision it
      cannot change afterwards (the immutability trigger is rebuilt).
    - deleted_at / deleted_by_user_id: a record that already went through a
      return round is soft-deleted, so its return history survives; the slot
      uniqueness and the one-booking-per-patient rule ignore such rows.
    """
    table = "oncology_consilium_appointments"
    if not _table_columns(conn, table):
        return
    for column, ddl in (
        ("signed_route_history_entry_id", "signed_route_history_entry_id TEXT"),
        ("signed_route_stage", "signed_route_stage TEXT"),
        ("signed_route_entered_at", "signed_route_entered_at TEXT"),
        ("signed_route_link_source", "signed_route_link_source TEXT CHECK "
                                     "(signed_route_link_source IN ('recorded', 'derived', 'unknown'))"),
        ("deleted_at", "deleted_at TEXT"),
        ("deleted_by_user_id", "deleted_by_user_id TEXT REFERENCES users(id)"),
    ):
        _add_column_if_missing(conn, table, column, ddl)

    columns = sorted(_table_columns(conn, table))
    _ensure_trigger(
        conn, "trg_consilium_signed_immutable", _signed_immutable_trigger_sql(columns),
        backfills=(
            f"""UPDATE oncology_consilium_appointments SET
                    signed_route_history_entry_id = (SELECT h.id {_ROUTE_ENTRY_AT_SIGNING}),
                    signed_route_stage = (SELECT h.stage {_ROUTE_ENTRY_AT_SIGNING}),
                    signed_route_entered_at = (SELECT h.entered_at {_ROUTE_ENTRY_AT_SIGNING}),
                    signed_route_link_source = CASE
                        WHEN EXISTS (SELECT 1 {_ROUTE_ENTRY_AT_SIGNING}) THEN 'derived' ELSE 'unknown' END
                WHERE status = 'signed' AND signed_route_link_source IS NULL""",  # nosec B608 - fixed SQL fragment, no input.
        ),
        pending="SELECT 1 FROM oncology_consilium_appointments "
                "WHERE status = 'signed' AND signed_route_link_source IS NULL LIMIT 1",
    )

    index = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'uq_consilium_appointment_slot'"
    ).fetchone()
    if index is not None and "deleted_at" not in (index["sql"] or ""):
        conn.execute("DROP INDEX IF EXISTS uq_consilium_appointment_slot")  # INDEX_SQL recreates it partial
    # Защита пациента «одна неудаленная запись» v19 заменена в v22 правилом
    # «не более одной незавершенной записи» (_ensure_consilium_patient_guard).


def migrate_v19_to_v20(conn: sqlite3.Connection) -> None:
    """v19 → v20: soft deletion and row version of patient events.

    - deleted_at / deleted_by_user_id: an event removed from the route stays
      in the database and can be restored; existing events stay active (NULL).
    - version: incremented on every change; edits, deletions and restores
      carry the version the client saw, so a stale card cannot overwrite a
      newer state. Existing events start at 1.
    Columns are only added, never dropped or rewritten.
    """
    for column, ddl in (
        ("deleted_at", "deleted_at TEXT"),
        ("deleted_by_user_id", "deleted_by_user_id TEXT REFERENCES users(id)"),
        ("version", "version INTEGER NOT NULL DEFAULT 1"),
    ):
        _add_column_if_missing(conn, "events", column, ddl)
    # Удалённое событие — часть медицинской истории: ни один путь (каскад от
    # пациента или этапа, импорт, прямой SQL, старая сборка на этой базе) не
    # может стереть его физически. Каскадные удаления тоже вызывают триггер,
    # поэтому вся операция откатывается целиком.
    conn.execute(ARCHIVED_EVENT_DELETE_TRIGGER_SQL)
    # INSERT OR REPLACE удаляет конфликтующую строку без DELETE-триггера
    # (recursive_triggers выключен): вставка поверх удалённого события запрещена.
    conn.execute(ARCHIVED_EVENT_REPLACE_TRIGGER_SQL)
    # Удалённое событие не правится: меняется только отметка удаления
    # (восстановление). Это же не даёт старой сборке, не знающей deleted_at,
    # записать факт в скрытое событие.
    conn.execute(ARCHIVED_EVENT_UPDATE_TRIGGER_SQL)


ARCHIVED_EVENT_DELETE_ERROR = "archived_event_delete_forbidden"
ARCHIVED_EVENT_UPDATE_ERROR = "archived_event_update_forbidden"
ARCHIVED_EVENT_UPDATE_TRIGGER_SQL = f"""
    CREATE TRIGGER IF NOT EXISTS trg_events_archived_not_updated
    BEFORE UPDATE ON events
    WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NOT NULL
    BEGIN
        SELECT RAISE(ABORT, '{ARCHIVED_EVENT_UPDATE_ERROR}');
    END;
"""
ARCHIVED_EVENT_REPLACE_TRIGGER_SQL = f"""
    CREATE TRIGGER IF NOT EXISTS trg_events_archived_not_replaced
    BEFORE INSERT ON events
    WHEN EXISTS (
        SELECT 1 FROM events
        WHERE deleted_at IS NOT NULL
          AND (id = NEW.id OR (history_entry_id = NEW.history_entry_id AND seq = NEW.seq))
    )
    BEGIN
        SELECT RAISE(ABORT, '{ARCHIVED_EVENT_DELETE_ERROR}');
    END;
"""
ARCHIVED_EVENT_DELETE_TRIGGER_SQL = f"""
    CREATE TRIGGER IF NOT EXISTS trg_events_archived_not_deleted
    BEFORE DELETE ON events
    WHEN OLD.deleted_at IS NOT NULL
    BEGIN
        SELECT RAISE(ABORT, '{ARCHIVED_EVENT_DELETE_ERROR}');
    END;
"""


CONSILIUM_DECISION_IMMUTABLE_ERROR = "consilium_decision_immutable"
CONSILIUM_DECISION_UNATTRIBUTED_ERROR = "consilium_decision_unattributed"
# Записанное решение ОК, его автор, время и источник не меняются ни API, ни прямым
# SQL: отметка однократная, исправление — будущая отдельная процедура.
CONSILIUM_DECISION_IMMUTABLE_TRIGGER_SQL = f"""
    CREATE TRIGGER IF NOT EXISTS trg_events_consilium_decision_immutable
    BEFORE UPDATE ON events
    WHEN OLD.consilium_decision IS NOT NULL AND (
        NEW.consilium_decision IS NOT OLD.consilium_decision
        OR NEW.consilium_decision_by_user_id IS NOT OLD.consilium_decision_by_user_id
        OR NEW.consilium_decision_at IS NOT OLD.consilium_decision_at
        OR NEW.consilium_decision_source IS NOT OLD.consilium_decision_source
        OR NEW.consilium_decision_imported_by_user_id IS NOT OLD.consilium_decision_imported_by_user_id
        OR NEW.consilium_decision_imported_at IS NOT OLD.consilium_decision_imported_at
        OR NEW.consilium_decision_import_id IS NOT OLD.consilium_decision_import_id
    )
    BEGIN
        SELECT RAISE(ABORT, '{CONSILIUM_DECISION_IMMUTABLE_ERROR}');
    END;
"""
# Новое решение в существующем событии — только ручная отметка с автором и временем;
# сведения об авторе без самого решения не записываются.
CONSILIUM_DECISION_ATTRIBUTED_UPDATE_TRIGGER_SQL = f"""
    CREATE TRIGGER IF NOT EXISTS trg_events_consilium_decision_attributed_update
    BEFORE UPDATE ON events
    WHEN OLD.consilium_decision IS NULL AND (
        (NEW.consilium_decision IS NOT NULL AND NOT (
            NEW.consilium_decision_source IS 'manual'
            AND NEW.consilium_decision_by_user_id IS NOT NULL
            AND NEW.consilium_decision_at IS NOT NULL
            AND NEW.consilium_decision_imported_by_user_id IS NULL
            AND NEW.consilium_decision_imported_at IS NULL
            AND NEW.consilium_decision_import_id IS NULL))
        OR (NEW.consilium_decision IS NULL AND (
            NEW.consilium_decision_source IS NOT NULL
            OR NEW.consilium_decision_by_user_id IS NOT NULL
            OR NEW.consilium_decision_at IS NOT NULL
            OR NEW.consilium_decision_imported_by_user_id IS NOT NULL
            OR NEW.consilium_decision_imported_at IS NOT NULL
            OR NEW.consilium_decision_import_id IS NOT NULL))
    )
    BEGIN
        SELECT RAISE(ABORT, '{CONSILIUM_DECISION_UNATTRIBUTED_ERROR}');
    END;
"""
# Вставка события с решением: либо ручная отметка с автором и временем, либо перенос
# исторического решения из файла (import) — без автора и времени решения, но с
# исполнителем, временем и идентификатором операции импорта. legacy вставкой не
# создается: этот источник ставит только миграция v20 → v21.
# Сравнения NULL-безопасны (IS, COALESCE): иначе NULL в источнике давал бы NULL в WHEN
# и триггер не срабатывал бы.
CONSILIUM_DECISION_ATTRIBUTED_INSERT_TRIGGER_SQL = f"""
    CREATE TRIGGER IF NOT EXISTS trg_events_consilium_decision_attributed_insert
    BEFORE INSERT ON events
    WHEN (NEW.consilium_decision IS NOT NULL AND NOT (
            (NEW.consilium_decision_source IS 'manual'
             AND NEW.consilium_decision_by_user_id IS NOT NULL
             AND NEW.consilium_decision_at IS NOT NULL
             AND NEW.consilium_decision_imported_by_user_id IS NULL
             AND NEW.consilium_decision_imported_at IS NULL
             AND NEW.consilium_decision_import_id IS NULL)
            OR (NEW.consilium_decision_source IS 'import'
             AND NEW.consilium_decision_by_user_id IS NULL
             AND NEW.consilium_decision_at IS NULL
             AND NEW.consilium_decision_imported_by_user_id IS NOT NULL
             AND NEW.consilium_decision_imported_at IS NOT NULL
             AND NEW.consilium_decision_import_id IS NOT NULL)))
      OR (NEW.consilium_decision IS NULL AND (
            NEW.consilium_decision_source IS NOT NULL
            OR NEW.consilium_decision_by_user_id IS NOT NULL
            OR NEW.consilium_decision_at IS NOT NULL
            OR NEW.consilium_decision_imported_by_user_id IS NOT NULL
            OR NEW.consilium_decision_imported_at IS NOT NULL
            OR NEW.consilium_decision_import_id IS NOT NULL))
    BEGIN
        SELECT RAISE(ABORT, '{CONSILIUM_DECISION_UNATTRIBUTED_ERROR}');
    END;
"""


def migrate_v20_to_v21(conn: sqlite3.Connection) -> None:
    """v20 → v21: separate manual OK decision with author, time and source.

    - consilium_decision_by_user_id / consilium_decision_at / consilium_decision_source
      and, for decisions transferred from an import file, the import executor,
      time and operation id (consilium_decision_imported_*) are only added. Decisions written before v21 keep their code and get
      source = 'legacy'; author and time stay NULL — nothing is invented. Facts,
      versions, deletion marks and the route history are not touched.
    - The backfill runs inside init_db's IMMEDIATE transaction while the
      triggers that refuse metadata-only updates (including the archived-event
      guard of v20) are off, and only when a row still needs it, so a repeated
      init_db changes nothing.
    - Triggers then keep a recorded decision, its author, time and source
      immutable and refuse unattributed decisions, also for direct SQL."""
    for column, ddl in (
        ("consilium_decision_by_user_id", "consilium_decision_by_user_id TEXT REFERENCES users(id)"),
        ("consilium_decision_at", "consilium_decision_at TEXT"),
        ("consilium_decision_source",
         "consilium_decision_source TEXT CHECK (consilium_decision_source IN ('manual', 'legacy', 'import') "
         "OR consilium_decision_source IS NULL)"),
        ("consilium_decision_imported_by_user_id", "consilium_decision_imported_by_user_id TEXT REFERENCES users(id)"),
        ("consilium_decision_imported_at", "consilium_decision_imported_at TEXT"),
        ("consilium_decision_import_id", "consilium_decision_import_id TEXT"),
    ):
        _add_column_if_missing(conn, "events", column, ddl)
    pending = conn.execute(
        "SELECT 1 FROM events WHERE consilium_decision IS NOT NULL AND consilium_decision_source IS NULL LIMIT 1"
    ).fetchone()
    if pending:
        for name in ("trg_events_consilium_decision_immutable", "trg_events_consilium_decision_attributed_update",
                     "trg_events_archived_not_updated"):
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute(
            "UPDATE events SET consilium_decision_source = 'legacy' "
            "WHERE consilium_decision IS NOT NULL AND consilium_decision_source IS NULL"
        )
        conn.execute(ARCHIVED_EVENT_UPDATE_TRIGGER_SQL)
    _ensure_trigger(conn, "trg_events_consilium_decision_immutable", CONSILIUM_DECISION_IMMUTABLE_TRIGGER_SQL)
    _ensure_trigger(conn, "trg_events_consilium_decision_attributed_update",
                    CONSILIUM_DECISION_ATTRIBUTED_UPDATE_TRIGGER_SQL)
    _ensure_trigger(conn, "trg_events_consilium_decision_attributed_insert",
                    CONSILIUM_DECISION_ATTRIBUTED_INSERT_TRIGGER_SQL)


# v22: защита пациента. У пациента может быть сколько угодно подписанных (и
# удаленных) записей, но не более одной незавершенной. Триггер, а не уникальный
# индекс: на старой установке могут быть исторические дубли (до v17), индекс не
# создался бы и остановил запуск; такие дубли не удаляются — они блокируют только
# новые записи пациента. UPDATE проверяется лишь при переходе записи в
# незавершенные (смена пациента, снятие отметки удаления, выход из подписанных),
# чтобы рассмотрение и возврат исторических дублей продолжали работать.
CONSILIUM_PATIENT_GUARD_INSERT_SQL = """
        CREATE TRIGGER IF NOT EXISTS trg_consilium_one_open_per_patient_insert
        BEFORE INSERT ON oncology_consilium_appointments
        WHEN NEW.deleted_at IS NULL AND NEW.status != 'signed' AND EXISTS (
            SELECT 1 FROM oncology_consilium_appointments
            WHERE patient_id = NEW.patient_id AND deleted_at IS NULL AND status != 'signed'
        )
        BEGIN
            SELECT RAISE(ABORT, 'consilium_patient_has_open_record');
        END"""
CONSILIUM_PATIENT_GUARD_UPDATE_SQL = """
        CREATE TRIGGER IF NOT EXISTS trg_consilium_one_open_per_patient_update
        BEFORE UPDATE OF patient_id, deleted_at, status ON oncology_consilium_appointments
        WHEN NEW.deleted_at IS NULL AND NEW.status != 'signed'
            AND (NEW.patient_id IS NOT OLD.patient_id OR OLD.deleted_at IS NOT NULL OR OLD.status = 'signed')
            AND EXISTS (
                SELECT 1 FROM oncology_consilium_appointments
                WHERE patient_id = NEW.patient_id AND id != OLD.id
                  AND deleted_at IS NULL AND status != 'signed'
            )
        BEGIN
            SELECT RAISE(ABORT, 'consilium_patient_has_open_record');
        END"""
_LEGACY_PATIENT_GUARD_TRIGGERS = (
    "trg_consilium_one_appointment_per_patient_insert",
    "trg_consilium_one_appointment_per_patient_update",
)

# v22: версия записи. Растет при каждом изменении строки, в том числе прямым
# SQL и путями кода, которые о ней не знают (AFTER UPDATE поднимает ее, если
# сам оператор этого не сделал), и никогда не уменьшается.
CONSILIUM_VERSION_BUMP_TRIGGER_SQL = """
        CREATE TRIGGER IF NOT EXISTS trg_consilium_version_bump
        AFTER UPDATE ON oncology_consilium_appointments
        WHEN NEW.version = OLD.version
        BEGIN
            UPDATE oncology_consilium_appointments SET version = OLD.version + 1 WHERE id = NEW.id;
        END"""
CONSILIUM_VERSION_FORWARD_TRIGGER_SQL = """
        CREATE TRIGGER IF NOT EXISTS trg_consilium_version_forward
        BEFORE UPDATE OF version ON oncology_consilium_appointments
        WHEN NEW.version < OLD.version
        BEGIN
            SELECT RAISE(ABORT, 'consilium_version_backwards');
        END"""

# v22: история повторных подач и причины задним числом не изменяются и не
# удаляются, в том числе каскадом: запись консилиума с повторной подачей можно
# только пометить удаленной (так и делает delete_owned — у нее есть раунды).
CONSILIUM_APPEND_ONLY_TRIGGERS = (
    ("trg_consilium_resubmissions_no_update", """
        CREATE TRIGGER IF NOT EXISTS trg_consilium_resubmissions_no_update
        BEFORE UPDATE ON consilium_resubmissions
        BEGIN
            SELECT RAISE(ABORT, 'consilium_resubmission_immutable');
        END"""),
    ("trg_consilium_resubmissions_no_delete", """
        CREATE TRIGGER IF NOT EXISTS trg_consilium_resubmissions_no_delete
        BEFORE DELETE ON consilium_resubmissions
        BEGIN
            SELECT RAISE(ABORT, 'consilium_resubmission_immutable');
        END"""),
    ("trg_consilium_backdating_reasons_no_update", """
        CREATE TRIGGER IF NOT EXISTS trg_consilium_backdating_reasons_no_update
        BEFORE UPDATE ON consilium_backdating_reasons
        BEGIN
            SELECT RAISE(ABORT, 'consilium_backdating_reason_immutable');
        END"""),
    ("trg_consilium_backdating_reasons_no_delete", """
        CREATE TRIGGER IF NOT EXISTS trg_consilium_backdating_reasons_no_delete
        BEFORE DELETE ON consilium_backdating_reasons
        BEGIN
            SELECT RAISE(ABORT, 'consilium_backdating_reason_immutable');
        END"""),
)


def _ensure_consilium_patient_guard(conn: sqlite3.Connection) -> None:
    """Install the v22 patient guard; drop the v17/v19 «one forever» triggers.

    On an up-to-date database nothing is dropped or created."""
    if not _table_columns(conn, "oncology_consilium_appointments"):
        return
    for name in _LEGACY_PATIENT_GUARD_TRIGGERS:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?", (name,)).fetchone():
            conn.execute(f"DROP TRIGGER {name}")
    _ensure_trigger(conn, "trg_consilium_one_open_per_patient_insert", CONSILIUM_PATIENT_GUARD_INSERT_SQL)
    _ensure_trigger(conn, "trg_consilium_one_open_per_patient_update", CONSILIUM_PATIENT_GUARD_UPDATE_SQL)


def migrate_v21_to_v22(conn: sqlite3.Connection) -> None:
    """v21 → v22: repeated consiliums, record version, separate resubmission.

    - Patient guard: several signed consiliums per patient, at most one
      unfinished (_ensure_consilium_patient_guard).
    - version: integer row version for optimistic locking; existing rows start
      at 1, every UPDATE raises it (trigger), it never decreases. It is the only
      new column a signed record may change (_SIGNED_MUTABLE_COLUMNS), so the
      signed-decision trigger is rebuilt with the new columns.
    - consilium_resubmissions: one row per resubmitted return round. From v21
      only the latest submission time is known (revision_submitted_at); it is
      carried over with migrated = 1 and no author. Earlier rounds are not
      reconstructed. Rows cannot be changed or deleted, also by cascade.
    - backdated / backdating_reason_id and consilium_backdating_reasons are
      prepared for WP2.3-B3 and stay unused by the API until then.
    Existing clinical rows are not rewritten: columns are only added.
    """
    table = "oncology_consilium_appointments"
    if not _table_columns(conn, table):
        return
    for column, ddl in (
        ("version", "version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)"),
        ("backdating_reason_id", "backdating_reason_id TEXT REFERENCES consilium_backdating_reasons(id)"),
        ("backdated", "backdated INTEGER NOT NULL DEFAULT 0 "
                      "CHECK (backdated IN (0, 1) AND (backdated = 0 OR backdating_reason_id IS NOT NULL))"),
    ):
        _add_column_if_missing(conn, table, column, ddl)
    for column, ddl in (
        ("backdating_reason_id", "backdating_reason_id TEXT REFERENCES consilium_backdating_reasons(id)"),
        ("backdated", "backdated INTEGER NOT NULL DEFAULT 0 "
                      "CHECK (backdated IN (0, 1) AND (backdated = 0 OR backdating_reason_id IS NOT NULL))"),
    ):
        _add_column_if_missing(conn, "consilium_schedule_slots", column, ddl)

    _ensure_consilium_patient_guard(conn)
    _ensure_trigger(conn, "trg_consilium_signed_immutable",
                    _signed_immutable_trigger_sql(sorted(_table_columns(conn, table))))
    _ensure_trigger(conn, "trg_consilium_version_forward", CONSILIUM_VERSION_FORWARD_TRIGGER_SQL)
    _ensure_trigger(conn, "trg_consilium_version_bump", CONSILIUM_VERSION_BUMP_TRIGGER_SQL)
    for name, sql in CONSILIUM_APPEND_ONLY_TRIGGERS:
        _ensure_trigger(conn, name, sql)

    # Известная из v21 повторная подача последнего раунда — только если ее
    # строки еще нет; повторный запуск ничего не добавляет.
    conn.execute(
        """INSERT INTO consilium_resubmissions
               (id, appointment_id, round, submitted_by_user_id, submitted_at, migrated)
           SELECT lower(hex(randomblob(16))), a.id, a.revision_count, NULL, a.revision_submitted_at, 1
           FROM oncology_consilium_appointments a
           WHERE a.revision_submitted_at IS NOT NULL AND a.revision_count > 0
             AND NOT EXISTS (
                 SELECT 1 FROM consilium_resubmissions r
                 WHERE r.appointment_id = a.id AND r.round = a.revision_count)"""
    )


# WP2.4-A (v23): происхождение карточки пациента. Источник и автор задаются
# сервером при создании и больше не меняются; 'legacy' — карточки, созданные до
# v23 (автор неизвестен и не придумывается), и явно разрешенные исторические
# тестовые фикстуры. Новая карточка с источником form/import/migration без
# автора отклоняется базой.
PATIENT_PROVENANCE_TRIGGERS = (
    ("trg_patients_provenance_immutable", """
        CREATE TRIGGER IF NOT EXISTS trg_patients_provenance_immutable
        BEFORE UPDATE OF created_by_user_id, creation_source ON patients
        WHEN NEW.created_by_user_id IS NOT OLD.created_by_user_id
          OR NEW.creation_source IS NOT OLD.creation_source
        BEGIN
            SELECT RAISE(ABORT, 'patient_provenance_immutable');
        END"""),
    ("trg_patients_creation_author_required", """
        CREATE TRIGGER IF NOT EXISTS trg_patients_creation_author_required
        BEFORE INSERT ON patients
        WHEN NEW.creation_source <> 'legacy' AND NEW.created_by_user_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'patient_creation_author_required');
        END"""),
)


def migrate_v22_to_v23(conn: sqlite3.Connection) -> None:
    """v22 → v23: author and source of creation of patient cards (WP2.4-A).

    Only adds: two columns and two triggers. Existing cards get
    creation_source = 'legacy' through the column default and
    created_by_user_id = NULL — no author is guessed, no clinical row is
    rewritten. Repeated runs change nothing."""
    if not _table_columns(conn, "patients"):
        return
    _add_column_if_missing(conn, "patients", "created_by_user_id",
                           "created_by_user_id TEXT REFERENCES users(id)")
    _add_column_if_missing(conn, "patients", "creation_source",
                           "creation_source TEXT NOT NULL DEFAULT 'legacy' "
                           "CHECK (creation_source IN ('form', 'import', 'migration', 'legacy'))")
    for name, sql in PATIENT_PROVENANCE_TRIGGERS:
        _ensure_trigger(conn, name, sql)


# WP2.4-B (v24): завершенный ключ идемпотентности не меняется (его результат —
# единственный ответ на повтор); отказ (refused, для WP2.4-C) может завершиться.
# История обработанных файлов только дополняется.
IDEMPOTENCY_TRIGGERS = (
    ("trg_idempotency_completed_immutable", """
        CREATE TRIGGER IF NOT EXISTS trg_idempotency_completed_immutable
        BEFORE UPDATE ON idempotency_keys
        WHEN OLD.state = 'completed'
        BEGIN
            SELECT RAISE(ABORT, 'idempotency_result_immutable');
        END"""),
    ("trg_import_files_processed_no_update", """
        CREATE TRIGGER IF NOT EXISTS trg_import_files_processed_no_update
        BEFORE UPDATE ON import_files_processed
        BEGIN
            SELECT RAISE(ABORT, 'import_file_history_immutable');
        END"""),
)

IDEMPOTENCY_INDEX_SQL = (
    # Одна карточка и один импорт — результат не более чем одного ключа.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_idempotency_result_patient "
    "ON idempotency_keys(result_patient_id) WHERE result_patient_id IS NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_idempotency_result_import "
    "ON idempotency_keys(result_import_id) WHERE result_import_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_idempotency_expires ON idempotency_keys(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_import_files_actor_sha ON import_files_processed(actor_user_id, file_sha256)",
)


def migrate_v23_to_v24(conn: sqlite3.Connection) -> None:
    """v23 → v24: idempotency of creation and import (WP2.4-B).

    Only adds: the two tables are created by SCHEMA_SQL (IF NOT EXISTS);
    here — their indexes and triggers. No existing row is touched; repeated
    runs change nothing."""
    if not _table_columns(conn, "idempotency_keys") or not _table_columns(conn, "import_files_processed"):
        return
    for sql in IDEMPOTENCY_INDEX_SQL:
        conn.execute(sql)
    for name, sql in IDEMPOTENCY_TRIGGERS:
        _ensure_trigger(conn, name, sql)


# WP2.4-C (v25): подтверждения возможных дублей только добавляются.
DUPLICATE_CONFIRMATION_TRIGGERS = (
    ("trg_duplicate_confirmations_no_update", """
        CREATE TRIGGER IF NOT EXISTS trg_duplicate_confirmations_no_update
        BEFORE UPDATE ON patient_duplicate_confirmations
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_confirmation_immutable');
        END"""),
    ("trg_duplicate_confirmations_no_delete", """
        CREATE TRIGGER IF NOT EXISTS trg_duplicate_confirmations_no_delete
        BEFORE DELETE ON patient_duplicate_confirmations
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_confirmation_immutable');
        END"""),
)


def migrate_v24_to_v25(conn: sqlite3.Connection) -> None:
    """v24 → v25: confirmations of possible duplicates (WP2.4-C).

    Only adds: the table is created by SCHEMA_SQL (IF NOT EXISTS); here — its
    indexes and append-only triggers. No existing row is touched; repeated runs
    change nothing."""
    if not _table_columns(conn, "patient_duplicate_confirmations"):
        return
    conn.execute("CREATE INDEX IF NOT EXISTS idx_duplicate_confirmations_patient "
                 "ON patient_duplicate_confirmations(patient_id)")
    for name, sql in DUPLICATE_CONFIRMATION_TRIGGERS:
        _ensure_trigger(conn, name, sql)


class SchemaVersionError(RuntimeError):
    """The database file was written by a different (newer or unknown) build."""


def _check_schema_version(conn: sqlite3.Connection) -> None:
    """Refuse a database this build does not understand, before any DDL.

    A newer schema can hold data this build would misread: v20 keeps deleted
    patient events in place, and code that ignores deleted_at shows them as
    active. A missing table (new file) or a known older version is migrated
    as before; anything else stops the start instead of serving wrong data."""
    problem = schema_version_problem(conn)
    if problem == "damaged":
        raise SchemaVersionError(
            "Версия схемы базы данных повреждена или не распознана. Запуск остановлен, "
            "база не изменена; проверьте файл командой check-db --no-migrate."
        )
    if problem is not None:
        raise SchemaVersionError(
            f"База данных имеет схему {problem}, а эта сборка поддерживает не выше "
            f"v{config.SCHEMA_VERSION}. Запуск остановлен, база не изменена. Запустите сборку, "
            "которая поддерживает эту схему (откат приложения на старую схему не поддерживается)."
        )


def schema_version_problem(conn: sqlite3.Connection) -> str | None:
    """None if schema_version is absent, empty or one supported integer;
    "damaged" for several rows or a non-integer/non-positive value;
    "v<N>" for a version newer than this build. Shared by start-up,
    check-db and backup validation."""
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if not has_table:
        return None
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_version").fetchall()]
    if not versions:
        return None
    if len(versions) != 1 or type(versions[0]) is not int or versions[0] < 1:
        return "damaged"
    if versions[0] > config.SCHEMA_VERSION:
        return f"v{versions[0]}"
    return None


def init_db(conn: sqlite3.Connection) -> None:
    """Idempotent schema creation + migration. Safe on an empty DB
    and safe to call repeatedly (CREATE TABLE IF NOT EXISTS + guarded ALTERs).

    Порядок важен: сначала ALTER-ы существующих v1-таблиц (иначе индексы по
    новым колонкам из SCHEMA_SQL не создадутся), затем создание недостающих
    таблиц и индексов.

    Everything runs in one IMMEDIATE transaction. The server start, the
    first requests and a CLI command can run init_db on the same file at
    once; each migration step checks the schema and then changes it, so
    the write lock is taken before the first check and held to the end. A
    concurrent run waits (busy_timeout) and then finds the work done, and a
    failed migration leaves the file exactly as it was."""
    if conn.in_transaction:
        raise RuntimeError("init_db must not run inside an open transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        _check_schema_version(conn)        # новая или повреждённая схема — отказ до любых изменений
        _execute_script(conn, SCHEMA_SQL)  # создаёт недостающие таблицы (IF NOT EXISTS)
        migrate_v1_to_v2(conn)           # добавляет новые колонки в v1-таблицы
        migrate_v3_to_v4(conn)           # добавляет nullable policy_number в существующие БД
        migrate_v4_to_v5(conn)           # создаёт read-state для внутреннего центра уведомлений
        migrate_v6_to_v7(conn)           # nullable doctor feedback columns/table
        migrate_v7_to_v8(conn)           # structured OK/PGI physician clarifications
        migrate_v8_to_v9(conn)           # PGI date + retired active fields
        migrate_v9_to_v10(conn)          # oncology consilium appointments
        migrate_v10_to_v11(conn)         # chair workflow and signatures
        migrate_v11_to_v12(conn)         # policy, favorites and treatment registries
        migrate_v12_to_v13(conn)         # localization and treatment scheme code
        migrate_v13_to_v14(conn)         # GOC registry markers
        migrate_v14_to_v15(conn)         # physician archive for signed consiliums
        migrate_v15_to_v16(conn)         # chair schedule and revision notifications
        migrate_v16_to_v17(conn)         # strict slot and patient uniqueness
        migrate_v17_to_v18(conn)         # resubmission marker, immutable signed decisions
        migrate_v18_to_v19(conn)         # route context of decisions, soft deletion
        migrate_v19_to_v20(conn)         # soft deletion and version of patient events
        migrate_v20_to_v21(conn)         # separate manual OK decision: author, time, source
        migrate_v21_to_v22(conn)         # repeated consiliums, record version, resubmissions
        migrate_v22_to_v23(conn)         # provenance of patient cards: author and source
        migrate_v23_to_v24(conn)         # idempotency keys, history of processed import files
        migrate_v24_to_v25(conn)         # confirmations of possible duplicates
        _ensure_notification_schema(conn)
        _assert_no_duplicate_consilium_slots(conn)
        _execute_script(conn, INDEX_SQL)   # индексы — когда все колонки уже есть
        _seed_roles(conn)
        _seed_notification_templates(conn)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (config.SCHEMA_VERSION,))
        elif row["version"] == 1:
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 2:
            migrate_v2_to_v3(conn)
            migrate_v3_to_v4(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 3:
            migrate_v3_to_v4(conn)
            migrate_v4_to_v5(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 4:
            migrate_v4_to_v5(conn)
            migrate_v5_to_v6(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 5:
            migrate_v5_to_v6(conn)
            migrate_v6_to_v7(conn)
            migrate_v7_to_v8(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 6:
            migrate_v6_to_v7(conn)
            migrate_v7_to_v8(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 7:
            migrate_v7_to_v8(conn)
            migrate_v8_to_v9(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 8:
            migrate_v8_to_v9(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 9:
            migrate_v9_to_v10(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 10:
            migrate_v10_to_v11(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 11:
            migrate_v11_to_v12(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 12:
            migrate_v12_to_v13(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 13:
            migrate_v13_to_v14(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 14:
            migrate_v14_to_v15(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 15:
            migrate_v15_to_v16(conn)
            migrate_v16_to_v17(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 16:
            migrate_v16_to_v17(conn)
            migrate_v17_to_v18(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 17:
            migrate_v17_to_v18(conn)
            migrate_v18_to_v19(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 18:
            migrate_v18_to_v19(conn)
            migrate_v19_to_v20(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 19:
            migrate_v19_to_v20(conn)
            migrate_v20_to_v21(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 20:
            migrate_v20_to_v21(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] == 21:
            migrate_v21_to_v22(conn)
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        elif row["version"] < config.SCHEMA_VERSION:
            conn.execute("UPDATE schema_version SET version = ?", (config.SCHEMA_VERSION,))
        # Ветки v4/v5 выше снова вызывают migrate_v5_to_v6, а он пересобирает
        # internal_notification_state по схеме v6 — без типа consilium_revision,
        # уже после безусловного v15→v16. Повтор идемпотентен: таблица
        # пересобирается, только если в её CHECK нет consilium_revision;
        # индексы пересобранной таблицы создаются заново (IF NOT EXISTS).
        migrate_v15_to_v16(conn)
        _execute_script(conn, INDEX_SQL)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


_singleton_conn: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    """Process-wide connection to the configured DB path (used by the app).
    Tests should build their own via `_connect` + `init_db` against a temp path."""
    global _singleton_conn
    if _singleton_conn is None:
        conn = _connect(config.DB_PATH)
        try:
            init_db(conn)
        except BaseException:
            # Не оставлять неинициализированное соединение (например, к базе
            # более новой схемы) для следующих вызовов.
            conn.close()
            raise
        _singleton_conn = conn
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
