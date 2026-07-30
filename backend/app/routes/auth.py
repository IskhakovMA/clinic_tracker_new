import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from .. import audit, config, security
from ..deps import get_db
from ..models import LoginIn

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


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    rate_key = request.client.host if request.client else "unknown"
    request_id = audit.new_request_id()

    wait = security.check_rate_limit(rate_key)
    if wait > 0:
        audit.record(conn, audit.LOGIN_RATE_LIMITED, result=audit.RESULT_DENIED, request_id=request_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please wait before trying again.",
            headers={"Retry-After": str(int(wait) + 1)},
        )

    password_hash = security.get_password_hash(conn)
    if password_hash is None:
        # Not a credential-enumeration signal: this is an operator/setup
        # state, not a per-user account state.
        audit.record(
            conn, audit.LOGIN_FAILURE, result=audit.RESULT_DENIED,
            request_id=request_id, detail="password_not_configured",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Application password is not configured yet. Run: python -m backend.cli set-password",
        )

    if not security.verify_password(body.password, password_hash):
        security.record_failed_attempt(rate_key)
        audit.record(conn, audit.LOGIN_FAILURE, result=audit.RESULT_DENIED, request_id=request_id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")

    security.reset_attempts(rate_key)
    security.purge_expired_sessions(conn)
    session_token, csrf_token, _expires = security.create_session(conn)
    _set_auth_cookies(response, session_token, csrf_token)
    audit.record(
        conn, audit.LOGIN_SUCCESS,
        actor=audit.actor_from_token_hash(security.hash_token(session_token)),
        request_id=request_id,
    )
    return {"authenticated": True}


@router.post("/logout")
def logout(request: Request, response: Response, conn: sqlite3.Connection = Depends(get_db)):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    actor = audit.actor_from_token_hash(security.hash_token(token)) if token else None
    security.revoke_session(conn, token)
    _clear_auth_cookies(response)
    audit.record(conn, audit.LOGOUT, actor=actor)
    return {"authenticated": False}


@router.get("/me")
def me(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    session = security.validate_session(conn, token)
    return {"authenticated": session is not None}
