"""Shared FastAPI dependencies: DB connection, session auth, CSRF."""
import sqlite3

from fastapi import Depends, HTTPException, Request, status

from . import audit, config, security
from .db import get_connection


def get_db() -> sqlite3.Connection:
    return get_connection()


def require_session(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    session = security.validate_session(conn, token)
    if session is None:
        # Same response for missing, expired and forged sessions — nothing here
        # tells a caller which of the three it was.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return session


def current_actor(session: sqlite3.Row = Depends(require_session)) -> str | None:
    """Anonymised audit actor for the calling session."""
    return audit.actor_from_token_hash(session["token_hash"])


def require_csrf(request: Request, session: sqlite3.Row = Depends(require_session)) -> None:
    header_token = request.headers.get("x-csrf-token")
    if not header_token or header_token != session["csrf_token"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid")
