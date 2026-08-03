"""Shared FastAPI dependencies: DB connection, session auth, CSRF, current user."""
import sqlite3

from fastapi import Depends, HTTPException, Request, status

from . import audit, config, policy, security
from .db import _connect, init_db


def get_db():
    conn = _connect(config.DB_PATH)
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


def require_session(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    session = security.validate_session(conn, token)
    if session is None:
        # Same response for missing, expired and forged sessions — nothing here
        # tells a caller which of the three it was.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return session


def require_user(
    session: sqlite3.Row = Depends(require_session),
    conn: sqlite3.Connection = Depends(get_db),
) -> policy.CurrentUser:
    """Пользователь текущей сессии. Статус и роль перечитываются из БД на
    каждый запрос, поэтому блокировка, деактивация и смена роли действуют
    немедленно, без ожидания истечения сессии."""
    user_id = session["user_id"]
    user = policy.load_current_user(conn, user_id) if user_id else None
    if user is None or user.status != "active":
        # Сессия неактивного пользователя удаляется — блокировка окончательна.
        if user_id:
            with conn:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_active_password(user: policy.CurrentUser = Depends(require_user)) -> policy.CurrentUser:
    """Для всех рабочих endpoints: при обязательной смене временного пароля
    доступ ограничен до /api/auth/change-password."""
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required",
        )
    return user


def current_actor(session: sqlite3.Row = Depends(require_session)) -> str | None:
    """Anonymised audit actor for the calling session."""
    return audit.actor_from_token_hash(session["token_hash"])


def require_csrf(request: Request, session: sqlite3.Row = Depends(require_session)) -> None:
    header_token = request.headers.get("x-csrf-token")
    if not header_token or header_token != session["csrf_token"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid")
