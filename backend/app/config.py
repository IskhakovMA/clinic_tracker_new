"""Environment-driven configuration. Local-first, safe defaults.

Единственный источник настроек — окружение процесса (AUD-19). Приложение и
CLI сами не читают файлы .env: значения из файла попадают в окружение только
через systemd `EnvironmentFile=`, `uvicorn --env-file` или `set -a; . файл;
set +a` в shell. Чтобы администратор не считал применённым то, что процесс
не видит, backend и CLI отказываются запускаться, если файл `.env` рядом с
кодом (backend/.env или .env в корне репозитория) задаёт значение, которого
нет в окружении процесса или которое отличается (см. check_env_files).
"""
import os
import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_FILE_CANDIDATES = (BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigError(RuntimeError):
    """Configuration that the process would silently ignore — refuse to start."""


def _parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines as systemd EnvironmentFile / `set -a; .` read them.

    Blank lines and lines starting with # are skipped, an optional `export `
    prefix is allowed, one pair of matching surrounding quotes is removed.
    Anything else is an error (by line number; values are never printed)."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Файл окружения {path} не прочитан: {type(exc).__name__}") from exc
    for number, line in enumerate(lines, start=1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[len("export "):].lstrip()
        name, sep, value = text.partition("=")
        name = name.strip()
        if not sep or not _ENV_NAME.fullmatch(name):
            raise ConfigError(f"Файл окружения {path}: строка {number} не разобрана (ожидается ИМЯ=значение)")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def check_env_files(candidates=None, environ=None) -> None:
    """Refuse a .env file whose values the process does not actually have.

    Names of the differing variables are reported, values never are (the
    file can hold keys)."""
    environ = os.environ if environ is None else environ
    for path in (ENV_FILE_CANDIDATES if candidates is None else candidates):
        if not path.is_file():
            continue
        differing = sorted(name for name, value in _parse_env_file(path).items()
                           if environ.get(name) != value)
        if differing:
            raise ConfigError(
                f"Файл {path} задаёт настройки, которых нет в окружении процесса: "
                f"{', '.join(differing)}. Приложение и CLI не читают .env сами. Загрузите файл "
                "(systemd EnvironmentFile=, uvicorn --env-file или `set -a; . файл; set +a`), "
                "исправьте расхождение или удалите/переименуйте файл. Запуск остановлен."
            )

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
# Предел тела любого запроса (до маршрутизации и аутентификации). С запасом
# над самым большим легитимным телом — импортом (raw до 6M символов, UTF-8).
MAX_REQUEST_BODY_BYTES = int(os.environ.get("CLINIC_MAX_REQUEST_BODY_BYTES", str(16 * 1024 * 1024)))
MAX_IMPORT_PATIENTS = 500
MAX_EVENTS_PER_HISTORY_ENTRY = 200
MAX_HISTORY_ENTRIES_PER_PATIENT = 200

# v9: Doctor Feedback Iteration 2 — localization-first active model,
# PGI result date; disease_stage/future_plans are retired from active contracts.
# v10–v17: oncology consilium (see db.migrate_v9_to_v10 … migrate_v16_to_v17).
# v18: consilium resubmission marker and immutable signed decisions.
# v19: route context of signed decisions, soft deletion keeping return history.
# v20: soft deletion and row version of patient events.
# v21: separate manual OK decision — author, time and source; immutable once recorded.
# v22: repeated consiliums (one unfinished per patient), record version, resubmissions.
# v23: provenance of patient cards — author and source of creation (WP2.4-A).
# v24: idempotency keys of creation and import, history of processed import files (WP2.4-B).
# v25: confirmations of possible duplicates (WP2.4-C).
# v26: registry of possible duplicates, erroneous marks of patient cards (WP2.4-D).
# v27: patient_portal_access — managed mobile access for an existing patient card
#      (Patient Portal stage 1; built as v9→v10 on the earlier line, carried here as an
#      additive migration after v26 — see db.migrate_v26_to_v27).
SCHEMA_VERSION = 27
# Импорт принимает и прежние выгрузки (v0/v1) — формат обратно совместим.
IMPORT_ACCEPTED_SCHEMA_VERSIONS = tuple(range(0, SCHEMA_VERSION + 1))

# Онкоконсилиум: лимиты на пользователя и адрес клиента за окно (deps.abuse_rate_limit
# ведёт отдельный счётчик на «эндпоинт:пользователь:адрес», общего счётчика нет).
CONSILIUM_RATE_LIMIT_WINDOW_SECONDS = max(1, int(os.environ.get("CLINIC_CONSILIUM_RATE_LIMIT_WINDOW_SECONDS", "60")))
CONSILIUM_WRITE_RATE_LIMIT = max(1, int(os.environ.get("CLINIC_CONSILIUM_WRITE_RATE_LIMIT", "60")))
CONSILIUM_EXPORT_RATE_LIMIT = max(1, int(os.environ.get("CLINIC_CONSILIUM_EXPORT_RATE_LIMIT", "20")))
CONSILIUM_SCHEDULE_RATE_LIMIT = max(1, int(os.environ.get("CLINIC_CONSILIUM_SCHEDULE_RATE_LIMIT", "20")))

# Создание карточек пациентов (WP2.4-A): не больше N созданий в минуту на пользователя.
# Счетчик ведется по пользователю, а не по адресу клиента или данным карточки.
PATIENT_CREATE_RATE_LIMIT = max(1, int(os.environ.get("CLINIC_PATIENT_CREATE_RATE_LIMIT", "60")))
PATIENT_CREATE_RATE_LIMIT_WINDOW_SECONDS = 60

# Ключи идемпотентности (WP2.4-B): срок хранения утвержден владельцем — 7 дней.
# История обработанных файлов импорта хранится отдельно и этим сроком не удаляется.
IDEMPOTENCY_TTL_DAYS = 7

INTERNAL_NOTIFICATION_UPCOMING_DAYS = int(os.environ.get("INTERNAL_NOTIFICATION_UPCOMING_DAYS", "3"))

NOTIFICATION_PROVIDER = os.environ.get("NOTIFICATION_PROVIDER", "mock").strip().lower()
NOTIFICATION_DEFAULT_REMINDER_HOURS = int(os.environ.get("NOTIFICATION_DEFAULT_REMINDER_HOURS", "24"))
NOTIFICATION_MAX_ATTEMPTS = int(os.environ.get("NOTIFICATION_MAX_ATTEMPTS", "3"))
NOTIFICATION_BATCH_LIMIT_MAX = 200
NOTIFICATION_MOCK_MODE = os.environ.get("NOTIFICATION_MOCK_MODE", "success").strip().lower()
NOTIFICATION_PROCESSING_LEASE_SECONDS = int(os.environ.get("NOTIFICATION_PROCESSING_LEASE_SECONDS", "300"))
# Ручные SMS: не дальше года вперёд и не больше N ожидающих на пациента,
# чтобы один автор не мог заполнить общую очередь.
NOTIFICATION_MAX_SCHEDULE_DAYS = int(os.environ.get("NOTIFICATION_MAX_SCHEDULE_DAYS", "366"))
NOTIFICATION_MAX_PENDING_MANUAL_PER_PATIENT = int(os.environ.get("NOTIFICATION_MAX_PENDING_MANUAL_PER_PATIENT", "20"))
NOTIFICATION_CREATE_RATE_LIMIT = int(os.environ.get("NOTIFICATION_CREATE_RATE_LIMIT", "30"))
# Напоминания о событиях: не больше N ожидающих на пациента (защита общей очереди).
NOTIFICATION_MAX_PENDING_EVENT_PER_PATIENT = int(os.environ.get("NOTIFICATION_MAX_PENDING_EVENT_PER_PATIENT", "50"))
# Верхняя граница на одного лечащего врача: один врач не может занять всю
# общую очередь напоминаниями своих пациентов (owner decision, Audit 3).
NOTIFICATION_MAX_PENDING_EVENT_PER_DOCTOR = int(os.environ.get("NOTIFICATION_MAX_PENDING_EVENT_PER_DOCTOR", "1000"))

APP_ENV = os.environ.get("CLINIC_ENV", "local").strip().lower()

# Read-only AI assistant. In local development, the backend may auto-detect an
# LM Studio OpenAI-compatible server. Production deployments must use explicit
# configuration or explicitly disable AI.
AI_MODE = os.environ.get("CLINIC_AI_MODE", "").strip().lower()
AI_ENABLED_RAW = os.environ.get("CLINIC_AI_ENABLED")
AI_ENABLED = (AI_ENABLED_RAW or "").strip().lower() in ("1", "true", "yes")
AI_BASE_URL_EXPLICIT = "CLINIC_AI_BASE_URL" in os.environ
AI_MODEL_EXPLICIT = "CLINIC_AI_MODEL" in os.environ
AI_BASE_URL = os.environ.get("CLINIC_AI_BASE_URL", "http://127.0.0.1:1234/v1").strip().rstrip("/")
AI_MODEL = os.environ.get("CLINIC_AI_MODEL", "").strip()
AI_LOCAL_BASE_URL = os.environ.get("CLINIC_AI_LOCAL_BASE_URL", "http://127.0.0.1:1234/v1").strip().rstrip("/")
AI_LOCAL_MODEL = os.environ.get("CLINIC_AI_LOCAL_MODEL", "qwen3-8b").strip()
AI_API_KEY = os.environ.get("CLINIC_AI_API_KEY", "")
# Разрешить http:// к неloopback-хосту ИИ (PHI и ключ пойдут открытым текстом).
AI_ALLOW_INSECURE_TRANSPORT = os.environ.get("CLINIC_AI_ALLOW_INSECURE_TRANSPORT", "").strip().lower() in ("1", "true", "yes")
AI_TIMEOUT_SECONDS = max(1, min(int(os.environ.get("CLINIC_AI_TIMEOUT_SECONDS", "45")), 120))
AI_HEALTH_TIMEOUT_SECONDS = max(1, min(int(os.environ.get("CLINIC_AI_HEALTH_TIMEOUT_SECONDS", "2")), 10))
AI_HEALTH_CACHE_TTL_SECONDS = max(1, min(int(os.environ.get("CLINIC_AI_HEALTH_CACHE_TTL_SECONDS", "10")), 300))
AI_MAX_OUTPUT_TOKENS = max(128, min(int(os.environ.get("CLINIC_AI_MAX_OUTPUT_TOKENS", "1200")), 4000))
AI_MAX_TOOL_CALLS = max(1, min(int(os.environ.get("CLINIC_AI_MAX_TOOL_CALLS", "8")), 16))
AI_MAX_HISTORY_MESSAGES = max(0, min(int(os.environ.get("CLINIC_AI_MAX_HISTORY_MESSAGES", "12")), 24))
AI_MAX_USER_MESSAGE_CHARS = max(200, min(int(os.environ.get("CLINIC_AI_MAX_USER_MESSAGE_CHARS", "4000")), 12000))
AI_RATE_LIMIT_REQUESTS = max(1, min(int(os.environ.get("CLINIC_AI_RATE_LIMIT_REQUESTS", "30")), 120))
# Одновременных запросов к провайдеру: блокирующий вызов держит поток из
# общего пула, который нужен и остальным запросам (включая вход).
AI_MAX_CONCURRENT_REQUESTS = max(1, min(int(os.environ.get("CLINIC_AI_MAX_CONCURRENT_REQUESTS", "4")), 32))
AI_RATE_LIMIT_WINDOW_SECONDS = max(60, min(int(os.environ.get("CLINIC_AI_RATE_LIMIT_WINDOW_SECONDS", "300")), 3600))
