"""Shared FastAPI dependencies: DB connection, session auth, CSRF, current user."""
import secrets
import sqlite3
from typing import Callable
from urllib.parse import unquote

from fastapi import Depends, Header, HTTPException, Request, status

from . import request_security, audit, config, policy, security, session_context, db_guard, operations_state
from .db import _connect, connect_live


_initialized_db_paths: set[str] = set()


def get_db():
    # Схема и сиды проверяются один раз на путь в процессе (lifespan тоже
    # вызывает init_db), а не отдельной пишущей транзакцией на каждый запрос.
    # Старая схема не обновляется неявно (connect_live, AUD-02).
    with db_guard.shared():
        operations_state.require_permit()
        key = str(config.DB_PATH)
        if key not in _initialized_db_paths:
            conn = connect_live(config.DB_PATH)
            _initialized_db_paths.add(key)
        else:
            conn = _connect(config.DB_PATH)
        try:
            from . import quality_revision
            quality_revision.require_valid(conn)
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
    state = request_security.current.get()
    if state is not None:
        state.token = token
        state.patient_id = request.path_params.get('patient_id')
        state.write_request = request.method not in {'GET', 'HEAD'}
        route = request.scope.get('route')
        if route is not None:
            state.resource = route.path
        state.protect_commit = request.url.path.startswith(('/api/patients', '/api/consilium', '/api/import', '/api/migrate', '/api/notifications'))
    request.state.clinic_idle_deadline = security.session_deadline(session)
    request.state.clinic_idle_remaining = security.remaining_seconds(session)
    request.state.clinic_user = session["user_id"]
    expected = request.headers.get(session_context.USER_HEADER)
    if expected is not None and expected != session["user_id"]:
        raise session_context.ContextChanged("SESSION_IDENTITY_CHANGED")
    return session


def require_user(
    request: Request,
    session: sqlite3.Row = Depends(require_session),
    conn: sqlite3.Connection = Depends(get_db),
) -> policy.CurrentUser:
    """Пользователь текущей сессии. Статус и роль перечитываются из БД на
    каждый запрос, поэтому блокировка, деактивация и смена роли действуют
    немедленно, без ожидания истечения сессии."""
    user_id = session["user_id"]
    user = policy.load_current_user(conn, user_id) if user_id else None
    state = request_security.current.get()
    if state is not None:
        state.user = user
    if user is None or user.status != "active":
        # Сессия неактивного пользователя удаляется — блокировка окончательна.
        if user_id:
            # This narrow security cleanup must be able to revoke its own
            # session; it cannot authorize a medical operation after the 401.
            guard = request_security.current.set(None)
            try:
                with conn:
                    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            finally:
                request_security.current.reset(guard)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    stamp = session_context.access_context(user)
    state = request_security.current.get()
    if state is not None:
        state.user, state.access = user, stamp
    request.state.clinic_access = stamp
    expected = request.headers.get(session_context.ACCESS_HEADER)
    if expected is not None and expected != stamp:
        raise session_context.ContextChanged("ACCESS_CONTEXT_CHANGED")
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


def require_csrf(request: Request, session: sqlite3.Row = Depends(require_session)) -> sqlite3.Row:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        current_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and origin != current_origin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-site request rejected")
        if referer and not referer.startswith(current_origin + "/"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-site request rejected")
    header_token = request.headers.get("x-csrf-token")
    if not header_token or not secrets.compare_digest(header_token, session["csrf_token"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid")
    return session


def abuse_rate_limit(name: str, max_requests: int, window_seconds: int = 60):
    def dependency(request: Request, user: policy.CurrentUser = Depends(require_active_password)) -> None:
        client_host = request.client.host if request.client else "unknown"
        key = f"{name}:{user.id}:{client_host}"
        wait = security.check_request_rate_limit(key, max_requests, window_seconds)
        if wait > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please wait before trying again.",
                headers={"Retry-After": str(int(wait) + 1)},
            )
    return dependency


def user_rate_limit(name: str, limit: Callable[[], int], window_seconds: Callable[[], int]):
    """Лимит частоты на пользователя (WP2.4-A): ключ — только операция и ID
    пользователя, без адреса клиента и без данных запроса (ФИО, полис), поэтому
    смена адреса или содержимого счетчик не обходит. Порог читается при каждом
    запросе. После превышения — 429 до выполнения операции."""
    def dependency(user: policy.CurrentUser = Depends(require_active_password)) -> None:
        wait = security.check_request_rate_limit(f"{name}:{user.id}", limit(), window_seconds())
        if wait > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please wait before trying again.",
                headers={"Retry-After": str(int(wait) + 1)},
            )
    return dependency


def search_text(
    request: Request,
    x_search: str | None = Header(None, alias="X-Search", max_length=512),
) -> str | None:
    """Текст поиска по пациенту принимается только в заголовке X-Search
    (percent-encoded UTF-8), а не в строке запроса: URL целиком пишут
    журналы reverse proxy, и ФИО пациента не должно в них попадать."""
    if "search" in request.query_params:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="search must be sent in the X-Search header")
    if not x_search:
        return None
    try:
        value = unquote(x_search, errors="strict")
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid X-Search encoding")
    value = value.strip()
    if len(value) > 120:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="search too long")
    return value or None
