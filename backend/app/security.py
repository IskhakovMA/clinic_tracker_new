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
    with conn:
        conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (now_iso(), token_hash))
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


def check_rate_limit(key: str = "global") -> float:
    """Returns seconds to wait before the next attempt is allowed (0 if OK now)."""
    now = time.monotonic()
    window_start = now - config.LOGIN_WINDOW_SECONDS
    attempts = [t for t in _failed_attempts.get(key, []) if t >= window_start]
    _failed_attempts[key] = attempts
    if len(attempts) < config.LOGIN_MAX_ATTEMPTS:
        return 0.0
    backoff = config.LOGIN_BASE_BACKOFF_SECONDS * (2 ** (len(attempts) - config.LOGIN_MAX_ATTEMPTS))
    last_attempt = attempts[-1]
    wait_until = last_attempt + backoff
    remaining = wait_until - now
    return max(0.0, remaining)


def record_failed_attempt(key: str = "global") -> None:
    _failed_attempts.setdefault(key, []).append(time.monotonic())


def reset_attempts(key: str = "global") -> None:
    _failed_attempts.pop(key, None)
