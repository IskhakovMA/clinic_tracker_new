"""Password hashing, session lifecycle, CSRF tokens, login rate limiting.

Sessions are opaque random tokens; only a SHA-256 hash of the token is
persisted, so a stolen copy of the DB file does not hand out usable
sessions. CSRF uses a per-session token issued alongside the session and
echoed by the client in a header (double-submit, but tied to a real
server-side session rather than being purely stateless).
"""
import hashlib
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash

from . import config
from .db import now_iso

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


_dummy_hash: str | None = None


def dummy_password_hash() -> str:
    """Постоянный хеш для выравнивания времени входа по несуществующему
    логину: одна операция Argon2 (verify), а не две (hash + verify)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash(secrets.token_urlsafe(16))
    return _dummy_hash


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except (VerifyMismatchError, InvalidHash):
        return False


class WeakPasswordError(ValueError):
    """Password rejected by policy — message is safe to show the operator."""


def validate_password_strength(plain: str) -> None:
    if len(plain) < config.MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(
            f"Пароль должен быть не короче {config.MIN_PASSWORD_LENGTH} символов."
        )
    canonical = plain.strip().casefold()
    if not canonical or len(set(canonical)) == 1 or canonical in config.WEAK_PASSWORDS or canonical in PASSWORD_BLOCKLIST:
        raise WeakPasswordError("Этот пароль слишком очевидный, выберите другой.")


def hash_token(token: str) -> str:
    """SHA-256 of a raw session token — this is what is stored and logged."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


_hash_token = hash_token  # internal alias kept for readability below


def revoke_all_sessions(conn: sqlite3.Connection) -> int:
    with conn:
        cur = conn.execute("DELETE FROM sessions")
        return cur.rowcount


class CredentialChanged(Exception):
    """Учётка изменилась между проверкой пароля и выдачей сессии."""


def create_session(
    conn: sqlite3.Connection, user_id: str, *, verified_password_hash: str | None = None,
    browser_token: str | None = None, previous_flow: str | None = None
) -> tuple[str, str, datetime]:
    """Returns (raw_session_token, csrf_token, expires_at). Every session
    belongs to a concrete user account — there are no anonymous sessions.

    При входе передаётся хеш, по которому проверен пароль: в той же
    транзакции, что и вставка, хеш и статус перечитываются. Если пароль
    сменили или учётку заблокировали, пока шла проверка, сессия не
    создаётся — иначе она пережила бы уже выполненный отзыв сессий."""
    from . import mfa
    conn.execute("BEGIN IMMEDIATE")
    try:
        if mfa.required(conn, user_id):
            raise CredentialChanged()
        if verified_password_hash is not None:
            row = conn.execute("SELECT password_hash, status FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None or row["status"] != "active" or row["password_hash"] != verified_password_hash:
                raise CredentialChanged()
        mfa.retire_browser_context(conn,user_id,browser_token,previous_flow)
        issued = _insert_session(conn, user_id)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return issued


def _insert_session(conn, user_id):
    """Caller-owned transaction. MFA callers must atomically add assurance/audit."""
    if not conn.in_transaction:
        raise RuntimeError('session insert requires transaction')
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = utc_now()
    expires_at = now + timedelta(seconds=config.SESSION_TTL_SECONDS)
    conn.execute(
        "INSERT INTO sessions (token_hash, csrf_token, created_at, last_seen_at, expires_at, user_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (_hash_token(raw_token), csrf_token, now.isoformat(), now.isoformat(), expires_at.isoformat(), user_id))
    return raw_token, csrf_token, expires_at


def validate_session(conn: sqlite3.Connection, raw_token: str | None, *, remove_expired: bool = True) -> sqlite3.Row | None:
    if not raw_token:
        return None
    token_hash = _hash_token(raw_token)
    row = conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)).fetchone()
    if row is None:
        return None
    try:
        now = utc_now()
        expires = datetime.fromisoformat(row['expires_at'])
        seen = datetime.fromisoformat(row['last_seen_at'])
        created = datetime.fromisoformat(row['created_at'])
        valid = all(t.tzinfo is not None for t in (expires, seen, created))
        valid = valid and created <= seen <= now < expires and now < seen + timedelta(seconds=config.SESSION_IDLE_SECONDS)
    except (ValueError, TypeError, OverflowError):
        valid = False
    if not valid:
        if remove_expired and not conn.in_transaction:
            with conn:
                conn.execute('DELETE FROM sessions WHERE token_hash = ?', (token_hash,))
        return None
    from . import mfa
    if not mfa.session_allowed(conn, row):
        return None
    return row


def utc_now():
    """Injectable server UTC clock, shared by issue/validate/activity."""
    return datetime.now(timezone.utc)


def touch_activity(conn, raw_token):
    from fastapi import HTTPException
    from .db import transaction
    with transaction(conn):
        session = validate_session(conn, raw_token, remove_expired=False)
        if session is None:
            raise HTTPException(status_code=401, detail='Not authenticated')
        conn.execute('UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?',
                     (utc_now().isoformat(), session['token_hash']))
        return min(datetime.fromisoformat(session['expires_at']),
                   utc_now() + timedelta(seconds=config.SESSION_IDLE_SECONDS)).isoformat()


def revoke_session(conn: sqlite3.Connection, raw_token: str | None) -> None:
    if not raw_token:
        return
    with conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(raw_token),))


def purge_expired_sessions(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso(),))


# --- Login rate limiting -----------------------------------------------
# In-memory, per-process. Resets on restart; acceptable for a single local
# process serving one operator at a time (documented limitation).

_failed_attempts: dict[str, list[float]] = {}
_request_attempts: dict[str, list[float]] = {}
_rate_limit_lock = threading.RLock()


def _now() -> float:
    """Единая точка времени для лимитеров (монотонные часы). Тесты подменяют
    её, чтобы проверять backoff без зависимости от скорости машины."""
    return time.monotonic()


def rate_limit_subject(value: str) -> str:
    """Opaque stable bucket suffix for user-controlled rate-limit subjects."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


_request_windows: dict[str, int] = {}
_rate_limited_audit_at: dict[str, float] = {}
RATE_LIMITED_AUDIT_INTERVAL_SECONDS = 60


def _prune_buckets(
    buckets: dict[str, list[float]],
    *,
    now: float,
    window_seconds: int,
    max_buckets: int,
    windows: dict[str, int] | None = None,
) -> None:
    """Drop timestamps outside each bucket's own window (``windows`` maps a
    key to the window it was recorded with; otherwise ``window_seconds``)."""
    for key in list(buckets.keys()):
        window = windows.get(key, window_seconds) if windows is not None else window_seconds
        window_start = now - window
        recent = [t for t in buckets[key] if t >= window_start]
        if recent:
            buckets[key] = recent
        else:
            buckets.pop(key, None)
            if windows is not None:
                windows.pop(key, None)
    if len(buckets) <= max_buckets:
        return
    ordered = sorted(buckets.items(), key=lambda item: item[1][-1] if item[1] else 0.0)
    for key, _ in ordered[:len(buckets) - max_buckets]:
        buckets.pop(key, None)
        if windows is not None:
            windows.pop(key, None)


def _login_wait(key: str, now: float) -> float:
    attempts = _failed_attempts.get(key, [])
    if len(attempts) < config.LOGIN_MAX_ATTEMPTS:
        return 0.0
    backoff = config.LOGIN_BASE_BACKOFF_SECONDS * (2 ** (len(attempts) - config.LOGIN_MAX_ATTEMPTS))
    return max(0.0, attempts[-1] + backoff - now)


def check_rate_limit(key: str = "global") -> float:
    """Returns seconds to wait before the next attempt is allowed (0 if OK now)."""
    with _rate_limit_lock:
        now = _now()
        _prune_buckets(
            _failed_attempts,
            now=now,
            window_seconds=config.LOGIN_WINDOW_SECONDS,
            max_buckets=config.LOGIN_RATE_LIMIT_MAX_BUCKETS,
        )
        return _login_wait(key, now)


def reserve_login_attempt(*keys: str) -> float:
    """Atomically check every key and, if all allow an attempt, record it as
    a failure before the password is verified. Returns the wait (0 when the
    attempt was reserved). A successful login clears the keys with
    reset_attempts, so concurrent requests cannot all pass the check before
    any failure is recorded."""
    with _rate_limit_lock:
        now = _now()
        _prune_buckets(
            _failed_attempts,
            now=now,
            window_seconds=config.LOGIN_WINDOW_SECONDS,
            max_buckets=config.LOGIN_RATE_LIMIT_MAX_BUCKETS,
        )
        wait = max((_login_wait(key, now) for key in keys), default=0.0)
        if wait > 0:
            return wait
        for key in keys:
            _failed_attempts.setdefault(key, []).append(now)
        return 0.0


def should_audit_rate_limited(key: str) -> bool:
    """At most one LOGIN_RATE_LIMITED audit row per key per interval, so a
    flood of rejected requests cannot grow the audit table without bound."""
    with _rate_limit_lock:
        now = _now()
        for k, t in list(_rate_limited_audit_at.items()):
            if now - t >= RATE_LIMITED_AUDIT_INTERVAL_SECONDS:
                _rate_limited_audit_at.pop(k, None)
        if key in _rate_limited_audit_at:
            return False
        if len(_rate_limited_audit_at) >= config.LOGIN_RATE_LIMIT_MAX_BUCKETS:
            return False
        _rate_limited_audit_at[key] = now
        return True


def record_failed_attempt(key: str = "global") -> None:
    with _rate_limit_lock:
        now = _now()
        _prune_buckets(
            _failed_attempts,
            now=now,
            window_seconds=config.LOGIN_WINDOW_SECONDS,
            max_buckets=config.LOGIN_RATE_LIMIT_MAX_BUCKETS,
        )
        _failed_attempts.setdefault(key, []).append(now)


def release_login_attempt(key: str) -> None:
    """Undo one reservation made by reserve_login_attempt for a request that
    turned out to be a successful login, without clearing other failures
    recorded under the same key (e.g. other usernames from this host)."""
    with _rate_limit_lock:
        attempts = _failed_attempts.get(key)
        if attempts:
            attempts.pop()
            if not attempts:
                _failed_attempts.pop(key, None)


def reset_attempts(key: str = "global") -> None:
    with _rate_limit_lock:
        _failed_attempts.pop(key, None)


def check_request_rate_limit(key: str, max_requests: int, window_seconds: int) -> float:
    """Generic bounded request limiter for authenticated expensive endpoints.

    It is intentionally in-memory and per-process; deployment-level/persistent
    throttling remains an operational control from SEC-2026-003.
    """
    with _rate_limit_lock:
        now = _now()
        _prune_buckets(
            _request_attempts,
            now=now,
            window_seconds=window_seconds,
            max_buckets=config.REQUEST_RATE_LIMIT_MAX_BUCKETS,
            windows=_request_windows,
        )
        attempts = _request_attempts.setdefault(key, [])
        _request_windows[key] = window_seconds
        if len(attempts) < max_requests:
            attempts.append(now)
            if len(_request_attempts) > config.REQUEST_RATE_LIMIT_MAX_BUCKETS:
                _prune_buckets(
                    _request_attempts,
                    now=now,
                    window_seconds=window_seconds,
                    max_buckets=config.REQUEST_RATE_LIMIT_MAX_BUCKETS,
                    windows=_request_windows,
                )
            return 0.0
        return max(0.0, window_seconds - (now - attempts[0]))


def revoke_session_hash(conn: sqlite3.Connection, token_hash: str | None) -> None:
    if not token_hash:
        return
    with conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

# Local-only contextual/common passphrases; never queries a breach service.
PASSWORD_BLOCKLIST = frozenset({
    'passwordpassword', 'password123456789', '123456789012345',
    '1234567890123456', 'qwertyuiopasdfgh', 'qwertyuiop123456',
    'adminadminadmin', 'administrator123', 'changemechangeme',
    'letmeinletmein123', 'clinictracker123', 'clinictracker2026',
    'парольпарольпароль', 'йцукенгшщзхъфыва', 'aaaaaaaaaaaaaaa',
})


def session_deadline(session):
    return min(datetime.fromisoformat(session['expires_at']),
               datetime.fromisoformat(session['last_seen_at']) + timedelta(seconds=config.SESSION_IDLE_SECONDS)).isoformat()


def remaining_seconds(session):
    return max(0, (datetime.fromisoformat(session_deadline(session)) - utc_now()).total_seconds())
