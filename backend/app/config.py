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

# Bootstrap-only: read once by `python -m backend.cli set-password` (or by
# db.py on first run if no password hash exists yet). Never read at request
# time and never stored anywhere in plaintext after the hash is written.
BOOTSTRAP_PASSWORD = os.environ.get("CLINIC_ADMIN_PASSWORD")

SESSION_TTL_SECONDS = int(os.environ.get("CLINIC_SESSION_TTL_SECONDS", str(8 * 60 * 60)))  # 8h idle/absolute
SESSION_COOKIE_NAME = "clinic_session"
CSRF_COOKIE_NAME = "clinic_csrf"

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_BASE_BACKOFF_SECONDS = 1.0

MAX_IMPORT_FILE_BYTES = 5 * 1024 * 1024  # 5MB
MAX_IMPORT_PATIENTS = 500
MAX_EVENTS_PER_HISTORY_ENTRY = 200
MAX_HISTORY_ENTRIES_PER_PATIENT = 200

SCHEMA_VERSION = 1
