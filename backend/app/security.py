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
    if plain.strip().lower() in config.WEAK_PASSWORDS:
        raise WeakPasswordError("Этот пароль слишком очевидный, выберите другой.")


def hash_token(token: str) -> str:
    """SHA-256 of a raw session token — this is what is stored and logged."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


_hash_token = hash_token  # internal alias kept for readability below


def revoke_all_sessions(conn: sqlite3.Connection) -> int:
    with conn:
        cur = conn.execute("DELETE FROM sessions")
        return cur.rowcount


def create_session(conn: sqlite3.Connection, user_id: str) -> tuple[str, str, datetime]:
    """Returns (raw_session_token, csrf_token, expires_at). Every session
    belongs to a concrete user account — there are no anonymous sessions."""
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=config.SESSION_TTL_SECONDS)
    with conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, csrf_token, created_at, last_seen_at, expires_at, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_hash_token(raw_token), csrf_token, now.isoformat(), now.isoformat(),
             expires_at.isoformat(), user_id),
        )
    return raw_token, csrf_token, expires_at


def validate_session(conn: sqlite3.Connection, raw_token: str | None) -> sqlite3.Row | None:
    if not raw_token:
        return None
    token_hash = _hash_token(raw_token)
    row = conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)).fetchone()
    if row is None:
        return None
    expires_at = datetime.fromisoformat(row["expires_at"])
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires_at:
        with conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        return None
    return row


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


def rate_limit_subject(value: str) -> str:
    """Opaque stable bucket suffix for user-controlled rate-limit subjects."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def _prune_buckets(
    buckets: dict[str, list[float]],
    *,
    now: float,
    window_seconds: int,
    max_buckets: int,
) -> None:
    window_start = now - window_seconds
    for key in list(buckets.keys()):
        recent = [t for t in buckets[key] if t >= window_start]
        if recent:
            buckets[key] = recent
        else:
            buckets.pop(key, None)
    if len(buckets) <= max_buckets:
        return
    ordered = sorted(buckets.items(), key=lambda item: item[1][-1] if item[1] else 0.0)
    for key, _ in ordered[:len(buckets) - max_buckets]:
        buckets.pop(key, None)


def check_rate_limit(key: str = "global") -> float:
    """Returns seconds to wait before the next attempt is allowed (0 if OK now)."""
    with _rate_limit_lock:
        now = time.monotonic()
        _prune_buckets(
            _failed_attempts,
            now=now,
            window_seconds=config.LOGIN_WINDOW_SECONDS,
            max_buckets=config.LOGIN_RATE_LIMIT_MAX_BUCKETS,
        )
        attempts = _failed_attempts.get(key, [])
        if len(attempts) < config.LOGIN_MAX_ATTEMPTS:
            return 0.0
        backoff = config.LOGIN_BASE_BACKOFF_SECONDS * (2 ** (len(attempts) - config.LOGIN_MAX_ATTEMPTS))
        last_attempt = attempts[-1]
        wait_until = last_attempt + backoff
        remaining = wait_until - now
        return max(0.0, remaining)


def record_failed_attempt(key: str = "global") -> None:
    with _rate_limit_lock:
        now = time.monotonic()
        _prune_buckets(
            _failed_attempts,
            now=now,
            window_seconds=config.LOGIN_WINDOW_SECONDS,
            max_buckets=config.LOGIN_RATE_LIMIT_MAX_BUCKETS,
        )
        _failed_attempts.setdefault(key, []).append(now)


def reset_attempts(key: str = "global") -> None:
    with _rate_limit_lock:
        _failed_attempts.pop(key, None)


def check_request_rate_limit(key: str, max_requests: int, window_seconds: int) -> float:
    """Generic bounded request limiter for authenticated expensive endpoints.

    It is intentionally in-memory and per-process; deployment-level/persistent
    throttling remains an operational control from SEC-2026-003.
    """
    with _rate_limit_lock:
        now = time.monotonic()
        _prune_buckets(
            _request_attempts,
            now=now,
            window_seconds=window_seconds,
            max_buckets=config.REQUEST_RATE_LIMIT_MAX_BUCKETS,
        )
        attempts = _request_attempts.setdefault(key, [])
        if len(attempts) < max_requests:
            attempts.append(now)
            if len(_request_attempts) > config.REQUEST_RATE_LIMIT_MAX_BUCKETS:
                _prune_buckets(
                    _request_attempts,
                    now=now,
                    window_seconds=window_seconds,
                    max_buckets=config.REQUEST_RATE_LIMIT_MAX_BUCKETS,
                )
            return 0.0
        return max(0.0, window_seconds - (now - attempts[0]))


def revoke_session_hash(conn: sqlite3.Connection, token_hash: str | None) -> None:
    if not token_hash:
        return
    with conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
