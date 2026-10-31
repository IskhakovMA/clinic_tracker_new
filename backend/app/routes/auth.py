import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .. import audit, config, idempotency, policy, security, users, session_context, mfa
from ..db import transaction
from ..deps import get_db, require_csrf, require_user, require_active_password, abuse_rate_limit
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
    response.delete_cookie(config.SESSION_COOKIE_NAME, path="/", secure=config.HTTPS, httponly=True, samesite="lax")
    response.delete_cookie(config.CSRF_COOKIE_NAME, path="/", secure=config.HTTPS, samesite="lax")


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
    previous = request.cookies.get(mfa.PREAUTH_COOKIE)
    browser_token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if mfa.required(conn, row["id"]):
        token, csrf, options = mfa.begin(conn, row["id"], row["password_hash"], browser_token=browser_token, previous_flow=previous)
        _clear_auth_cookies(response)
        response.set_cookie(mfa.PREAUTH_COOKIE, token, httponly=True, secure=config.HTTPS,
                            samesite='strict', path='/api/auth', max_age=mfa.FLOW_SECONDS)
        response.set_cookie(mfa.PREAUTH_CSRF_COOKIE, csrf, httponly=False, secure=config.HTTPS,
                            samesite='strict', path='/', max_age=mfa.FLOW_SECONDS)
        return {"authenticated": False, "stage": "webauthn", "publicKey": options['publicKey']}
    try:
        session_token, csrf_token, _expires = security.create_session(
            conn, row["id"], verified_password_hash=row["password_hash"],
            browser_token=browser_token, previous_flow=previous
        )
    except security.CredentialChanged:
        audit.record(
            conn, audit.LOGIN_FAILURE, result=audit.RESULT_DENIED, request_id=request_id,
            target_user_id=row["id"],
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    response.delete_cookie(mfa.PREAUTH_COOKIE,path='/api/auth',secure=config.HTTPS,httponly=True,samesite='strict')
    response.delete_cookie(mfa.PREAUTH_CSRF_COOKIE,path='/',secure=config.HTTPS,samesite='strict')
    users.touch_last_login(conn, row["id"])
    _set_auth_cookies(response, session_token, csrf_token)
    user = policy.load_current_user(conn, row["id"])
    audit.record(
        conn, audit.LOGIN_SUCCESS,
        actor=audit.actor_from_token_hash(security.hash_token(session_token)),
        request_id=request_id, **audit.actor_ctx(user),
    )
    issued = conn.execute('SELECT * FROM sessions WHERE token_hash = ?', (security.hash_token(session_token),)).fetchone()
    return {"authenticated": True, "user": _profile_payload(conn, user), "idleDeadline": security.session_deadline(issued), "idleRemainingSeconds": security.remaining_seconds(issued)}


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
    return {"authenticated": True, "user": _profile_payload(conn, user), "idleDeadline": security.session_deadline(session), "idleRemainingSeconds": security.remaining_seconds(session)}


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
    if mfa.required(conn, user.id):
        _clear_auth_cookies(response)
        return {"changed": True, "authenticated": False, "reauthenticationRequired": True}
    session_token, csrf_token, _expires = security.create_session(conn, user.id)
    _set_auth_cookies(response, session_token, csrf_token)
    return {"changed": True}


@router.post('/activity', dependencies=[Depends(require_csrf), Depends(abuse_rate_limit('session_activity', 60, 60))])
def activity(request: Request, conn: sqlite3.Connection = Depends(get_db),
             user: policy.CurrentUser = Depends(require_active_password)):
    deadline = security.touch_activity(conn, request.cookies.get(config.SESSION_COOKIE_NAME))
    return {'idleDeadline': deadline, 'idleRemainingSeconds': min(config.SESSION_IDLE_SECONDS, max(0, (datetime.fromisoformat(deadline) - security.utc_now()).total_seconds()))}


def require_pre_auth_csrf(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """Explicit limited-flow CSRF, independent of full-session dependencies.

    claim repeats these checks under the write lock before consuming the flow.
    """
    import secrets
    token=request.cookies.get(mfa.PREAUTH_COOKIE)
    flow=conn.execute('SELECT * FROM mfa_flows WHERE token_hash=?',
                      (security.hash_token(token or ''),)).fetchone()
    if flow is None:
        raise HTTPException(401,'MFA_FLOW_UNAVAILABLE')
    csrf=request.headers.get('x-csrf-token')
    if not csrf or not secrets.compare_digest(flow['csrf_hash'],security.hash_token(csrf)):
        raise HTTPException(403,'CSRF validation failed')
    if request.headers.get('origin')!=mfa.load_policy(conn)['origin']:
        raise HTTPException(403,'MFA_ORIGIN_MISMATCH')


@router.post('/mfa/authenticate', dependencies=[Depends(require_pre_auth_csrf)])
def mfa_authenticate(request: Request, response: Response, body: dict,
                     conn: sqlite3.Connection = Depends(get_db)):
    host = request.client.host if request.client else 'unknown'
    wait = security.check_request_rate_limit('mfa_finish:' + host, 20, 60)
    if wait > 0:
        raise HTTPException(429, 'Too many attempts', headers={'Retry-After': str(int(wait)+1)})
    if len(str(body)) > 32768:
        raise HTTPException(422, 'MFA_RESPONSE_TOO_LARGE')
    token, csrf, _ = mfa.finish(conn, request.cookies.get(mfa.PREAUTH_COOKIE),
        request.headers.get('x-csrf-token'), request.headers.get('origin'), body)
    _set_auth_cookies(response, token, csrf)
    response.delete_cookie(mfa.PREAUTH_COOKIE, path='/api/auth', secure=config.HTTPS, httponly=True, samesite='strict')
    response.delete_cookie(mfa.PREAUTH_CSRF_COOKIE, path='/', secure=config.HTTPS, samesite='strict')
    session = security.validate_session(conn, token)
    if session is None:
        raise HTTPException(401, 'MFA_SESSION_UNAVAILABLE')
    user = policy.load_current_user(conn, session['user_id'])
    return {'authenticated': True, 'user': _profile_payload(conn, user),
            'idleDeadline': security.session_deadline(session), 'idleRemainingSeconds': security.remaining_seconds(session)}


@router.post('/mfa/register/options')
def mfa_register_options(response: Response, session: sqlite3.Row = Depends(require_csrf),
                         user: policy.CurrentUser = Depends(require_user), conn: sqlite3.Connection = Depends(get_db)):
    row=users.get_user(conn,user.id)
    token,csrf,options=mfa.begin(conn,user.id,row['password_hash'],purpose='register',parent=session['token_hash'])
    response.set_cookie(mfa.PREAUTH_COOKIE,token,httponly=True,secure=config.HTTPS,samesite='strict',path='/api/auth',max_age=mfa.FLOW_SECONDS)
    response.set_cookie(mfa.PREAUTH_CSRF_COOKIE,csrf,secure=config.HTTPS,samesite='strict',path='/',max_age=mfa.FLOW_SECONDS)
    return options


@router.post('/mfa/register', dependencies=[Depends(require_csrf), Depends(abuse_rate_limit('mfa_register',20,60))])
def mfa_register(request: Request,body: dict,session: sqlite3.Row = Depends(require_csrf),
                 conn: sqlite3.Connection = Depends(get_db)):
    if len(str(body))>32768:
        raise HTTPException(422,'MFA_RESPONSE_TOO_LARGE')
    return mfa.finish_registration(conn,request.cookies.get(mfa.PREAUTH_COOKIE),
        request.headers.get('x-mfa-csrf-token'),request.headers.get('origin'),body,parent=session['token_hash'])
