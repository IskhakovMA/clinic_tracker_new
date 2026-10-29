import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .. import audit, config, idempotency, policy, security, users, session_context
from ..db import transaction
from ..deps import get_db, require_csrf, require_user
from ..models import ChangePasswordIn, LoginIn

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_auth_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    common = dict(
        httponly=True,
        samesite="lax",
        secure=config.HTTPS,
        path="/",
        max_age=config.SESSION_TTL_SECONDS,
    )
    response.set_cookie(config.SESSION_COOKIE_NAME, session_token, **common)
    response.set_cookie(
        config.CSRF_COOKIE_NAME,
        csrf_token,
        httponly=False,  # frontend JS must read this to echo it back in a header
        samesite="lax",
        secure=config.HTTPS,
        path="/",
        max_age=config.SESSION_TTL_SECONDS,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(config.SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(config.CSRF_COOKIE_NAME, path="/")


def _profile_payload(conn: sqlite3.Connection, user: policy.CurrentUser) -> dict:
    dept_rows = users.user_department_rows(conn, user.id, user.role_id)
    return {
        "id": user.id,
        "accessContext": session_context.access_context(user),
        "username": user.username,
        "fullName": user.full_name,
        "roleId": user.role_id,
        "roleLabel": user.role_label,
        "mustChangePassword": user.must_change_password,
        "departments": [{"id": d["id"], "name": d["name"]} for d in dept_rows],
        "permissions": policy.permissions_payload(user),
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    client_host = request.client.host if request.client else "unknown"
    # Ключ включает и хост, и логин: перебор одного логина не блокирует
    # вход остальных сотрудников, а перебор со сменой логина ограничен хостом.
    subject = security.rate_limit_subject(body.username)
    rate_key = f"{client_host}:{subject}"
    request_id = audit.new_request_id()

    # Попытка резервируется (учитывается как неудачная) до проверки пароля:
    # параллельная серия запросов с одного хоста не проходит лимит целиком.
    wait = security.reserve_login_attempt(rate_key, client_host)
    if wait > 0:
        if security.should_audit_rate_limited(client_host):
            audit.record(conn, audit.LOGIN_RATE_LIMITED, result=audit.RESULT_DENIED, request_id=request_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait before trying again.",
            headers={"Retry-After": str(int(wait) + 1)},
        )

    row = users.get_user_by_username(conn, body.username)
    # Единый ответ для «нет такого логина», «неверный пароль» и «не active» —
    # без сигнала перебора учётных записей. Хеширование выполняется всегда.
    password_ok = security.verify_password(
        body.password, row["password_hash"] if row else security.dummy_password_hash()
    )
    if row is None or not password_ok or row["status"] != "active":
        audit.record(
            conn, audit.LOGIN_FAILURE, result=audit.RESULT_DENIED, request_id=request_id,
            target_user_id=row["id"] if row else None,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # Сбрасывается только бакет этой учётки: успешный вход доказывает знание
    # своего пароля, но не отменяет неудачные попытки с хоста к чужим логинам.
    security.reset_attempts(rate_key)
    security.release_login_attempt(client_host)
    security.purge_expired_sessions(conn)
    idempotency.purge_expired(conn)   # WP2.4-B: только истекшие ключи
    try:
        session_token, csrf_token, _expires = security.create_session(
            conn, row["id"], verified_password_hash=row["password_hash"]
        )
    except security.CredentialChanged:
        audit.record(
            conn, audit.LOGIN_FAILURE, result=audit.RESULT_DENIED, request_id=request_id,
            target_user_id=row["id"],
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    users.touch_last_login(conn, row["id"])
    _set_auth_cookies(response, session_token, csrf_token)
    user = policy.load_current_user(conn, row["id"])
    audit.record(
        conn, audit.LOGIN_SUCCESS,
        actor=audit.actor_from_token_hash(security.hash_token(session_token)),
        request_id=request_id, **audit.actor_ctx(user),
    )
    return {"authenticated": True, "user": _profile_payload(conn, user)}


@router.post("/logout")
def logout(
    response: Response,
    session: sqlite3.Row = Depends(require_csrf),
    conn: sqlite3.Connection = Depends(get_db),
):
    actor = audit.actor_from_token_hash(session["token_hash"])
    security.revoke_session_hash(conn, session["token_hash"])
    _clear_auth_cookies(response)
    audit.record(conn, audit.LOGOUT, actor=actor)
    return {"authenticated": False}


@router.get("/me")
def me(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    session = security.validate_session(conn, token)
    if session is None or not session["user_id"]:
        return {"authenticated": False}
    user = policy.load_current_user(conn, session["user_id"])
    if user is None or user.status != "active":
        return {"authenticated": False}
    return {"authenticated": True, "user": _profile_payload(conn, user)}


@router.post("/change-password", dependencies=[Depends(require_csrf)])
def change_password(
    body: ChangePasswordIn,
    response: Response,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_user),
):
    """Смена собственного пароля. Доступна и при must_change_password —
    это единственный разрешённый в таком состоянии endpoint.

    После смены все сессии пользователя завершаются (включая открытые по
    временному паролю), а вызывающему выдаётся новая сессия и CSRF-токен."""
    # Проверка текущего пароля — такой же оракул подбора, как вход: попытки
    # ограничены на учётку (не на сессию или хост) по той же политике.
    reauth_key = f"reauth:{user.id}"
    wait = security.reserve_login_attempt(reauth_key)
    if wait > 0:
        if security.should_audit_rate_limited(reauth_key):
            audit.record(conn, audit.PASSWORD_CHANGED, result=audit.RESULT_DENIED,
                         detail="rate_limited", **audit.actor_ctx(user))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait before trying again.",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    row = users.get_user(conn, user.id)
    if not security.verify_password(body.currentPassword, row["password_hash"]):
        audit.record(conn, audit.PASSWORD_CHANGED, result=audit.RESULT_DENIED,
                     **audit.actor_ctx(user))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    security.reset_attempts(reauth_key)
    try:
        # WP-C (AUD-06): новый пароль, завершение прежних сессий и запись аудита — одна
        # транзакция; без записи аудита пароль не меняется (503).
        with transaction(conn):
            users.change_own_password(conn, user.id, body.newPassword)
            users.terminate_sessions(conn, user.id, commit=False)
            audit.record_required_or_fail(conn, audit.PASSWORD_CHANGED, **audit.actor_ctx(user))
    except security.WeakPasswordError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    session_token, csrf_token, _expires = security.create_session(conn, user.id)
    _set_auth_cookies(response, session_token, csrf_token)
    return {"changed": True}
