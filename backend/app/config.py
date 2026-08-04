"""Environment-driven configuration. Local-first, safe defaults."""
import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CLINIC_DATA_DIR", BACKEND_DIR / "data")).resolve()
DB_PATH = DATA_DIR / "clinic.db"
BACKUP_DIR = DATA_DIR / "backups"

HOST = os.environ.get("CLINIC_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLINIC_PORT", "8420"))

# Only set this to true when actually served over HTTPS (e.g. behind a
# reverse proxy terminating TLS). Never enable for plain local HTTP.
HTTPS = os.environ.get("CLINIC_HTTPS", "false").strip().lower() in ("1", "true", "yes")

DEBUG = os.environ.get("CLINIC_DEBUG", "false").strip().lower() in ("1", "true", "yes")
APP_TIMEZONE = os.environ.get("APP_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"

# Прежний общий пароль (CLINIC_ADMIN_PASSWORD + app_login) выведен из
# эксплуатации: вход возможен только по персональной учётной записи,
# первый Разработчик создаётся через `python -m backend.cli create-developer`.

SESSION_TTL_SECONDS = int(os.environ.get("CLINIC_SESSION_TTL_SECONDS", str(8 * 60 * 60)))  # 8h idle/absolute
SESSION_COOKIE_NAME = "clinic_session"
CSRF_COOKIE_NAME = "clinic_csrf"

BUSY_TIMEOUT_MS = int(os.environ.get("CLINIC_BUSY_TIMEOUT_MS", "5000"))

# Audit retention: events older than this are removed by
# `python -m backend.cli audit-prune`. Nothing deletes them automatically.
AUDIT_RETENTION_DAYS = int(os.environ.get("CLINIC_AUDIT_RETENTION_DAYS", "365"))
AUDIT_PAGE_LIMIT_MAX = 200

MIN_PASSWORD_LENGTH = 8
WEAK_PASSWORDS = frozenset(
    {
        "password",
        "пароль",
        "12345678",
        "123456789",
        "1234567890",
        "qwertyui",
        "qwerty123",
        "clinic123",
        "admin123",
        "adminadmin",
        "changeme",
        "letmein1",
    }
)

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_BASE_BACKOFF_SECONDS = 1.0
LOGIN_RATE_LIMIT_MAX_BUCKETS = 1_000
REQUEST_RATE_LIMIT_MAX_BUCKETS = 1_000

MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024  # 5MB
MAX_IMPORT_PATIENTS = 500
MAX_EVENTS_PER_HISTORY_ENTRY = 200
MAX_HISTORY_ENTRIES_PER_PATIENT = 200

# v3: notification preferences, templates and queue.
SCHEMA_VERSION = 3
# Импорт принимает и прежние выгрузки (v0/v1) — формат обратно совместим.
IMPORT_ACCEPTED_SCHEMA_VERSIONS = (0, 1, 2, 3)

NOTIFICATION_PROVIDER = os.environ.get("NOTIFICATION_PROVIDER", "mock").strip().lower()
NOTIFICATION_DEFAULT_REMINDER_HOURS = int(os.environ.get("NOTIFICATION_DEFAULT_REMINDER_HOURS", "24"))
NOTIFICATION_MAX_ATTEMPTS = int(os.environ.get("NOTIFICATION_MAX_ATTEMPTS", "3"))
NOTIFICATION_BATCH_LIMIT_MAX = 200
NOTIFICATION_MOCK_MODE = os.environ.get("NOTIFICATION_MOCK_MODE", "success").strip().lower()
NOTIFICATION_PROCESSING_LEASE_SECONDS = int(os.environ.get("NOTIFICATION_PROCESSING_LEASE_SECONDS", "300"))
