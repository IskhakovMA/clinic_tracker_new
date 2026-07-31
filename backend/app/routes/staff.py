"""Управление сотрудниками. Доступно только роли с can_manage_users
(Разработчик). Пароли не возвращаются ни в одном ответе."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, policy, security, users
from ..deps import get_db, require_active_password, require_csrf
from ..models import ResetPasswordIn, UserCreateIn, UserUpdateIn

router = APIRouter(prefix="/api/users", tags=["staff"])


def require_user_admin(
    user: policy.CurrentUser = Depends(require_active_password),
    conn: sqlite3.Connection = Depends(get_db),
) -> policy.CurrentUser:
    if not policy.can_manage_users(user):
        audit.record(conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED,
                     detail="area=users", **audit.actor_ctx(user))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


def _user_or_404(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    row = users.get_user(conn, user_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return row


@router.get("")
def list_users(
    query: str | None = Query(None, max_length=200),
    role: str | None = Query(None, max_length=64),
    department: str | None = Query(None, max_length=100),
    user_status: str | None = Query(None, alias="status", max_length=20),
    conn: sqlite3.Connection = Depends(get_db),
    admin: policy.CurrentUser = Depends(require_user_admin),
):
    sql = "SELECT u.* FROM users u WHERE 1 = 1"
    params: list = []
    if query:
        sql += " AND (u.full_name LIKE ? OR u.username LIKE ?)"
        like = f"%{query.strip()}%"
        params += [like, like]
    if role:
        sql += " AND u.role_id = ?"
        params.append(role)
    if user_status:
        sql += " AND u.status = ?"
        params.append(user_status)
    if department:
        sql += (" AND u.id IN (SELECT user_id FROM user_departments WHERE department_id = ?"
                " UNION SELECT user_id FROM department_heads WHERE department_id = ?)")
        params += [department, department]
    sql += " ORDER BY u.full_name"
    rows = conn.execute(sql, params).fetchall()
    return {"users": [users.user_to_dict(conn, r) for r in rows]}


@router.get("/doctors")
def list_selectable_doctors(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    """Врачи, доступные текущему пользователю для назначения лечащим.
    Заведующему — только врачи его отделений; Разработчику — все активные."""
    if user.scope_strategy == policy.SCOPE_GLOBAL:
        rows = users.selectable_doctors(conn)
    elif user.scope_strategy == policy.SCOPE_BY_DEPARTMENT:
        rows = users.selectable_doctors(conn, list(user.department_ids))
    else:
        # Врач закрепляет пациентов только за собой; read-only ролям селект
        # не нужен — чужие врачи в недоступных селектах не раскрываются.
        rows = []
    return {"doctors": [{"id": r["id"], "fullName": r["full_name"]} for r in rows]}


@router.post("", dependencies=[Depends(require_csrf)], status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreateIn,
    conn: sqlite3.Connection = Depends(get_db),
    admin: policy.CurrentUser = Depends(require_user_admin),
):
    try:
        uid = users.create_user(
            conn, username=body.username, full_name=body.fullName, role_id=body.roleId,
            password=body.password, must_change_password=body.mustChangePassword,
        )
        _apply_departments(conn, uid, body.roleId, body.departmentIds)
    except (users.UserError, security.WeakPasswordError) as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    audit.record(conn, audit.USER_CREATED, detail=f"role={body.roleId}",
                 target_user_id=uid, **audit.actor_ctx(admin))
    return users.user_to_dict(conn, users.get_user(conn, uid))


def _apply_departments(conn: sqlite3.Connection, user_id: str, role_id: str, dept_ids: list[str]) -> None:
    """Синхронизировать связи с отделениями: врач — членство, заведующий —
    управление (несколько отделений допустимы)."""
    table = "department_heads" if role_id == "department_head" else "user_departments"
    other = "user_departments" if table == "department_heads" else "department_heads"
    with conn:
        conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute(f"DELETE FROM {other} WHERE user_id = ?", (user_id,))
    for dept_id in dept_ids:
        dept = users.get_department(conn, dept_id)
        if dept is None or dept["status"] != "active":
            raise users.UserError("Отделение не найдено или архивировано.")
        with conn:
            conn.execute(
                f"INSERT OR IGNORE INTO {table} (user_id, department_id, created_at) VALUES (?, ?, ?)",
                (user_id, dept_id, users.now_iso()),
            )


@router.patch("/{user_id}", dependencies=[Depends(require_csrf)])
def update_user(
    user_id: str,
    body: UserUpdateIn,
    conn: sqlite3.Connection = Depends(get_db),
    admin: policy.CurrentUser = Depends(require_user_admin),
):
    row = _user_or_404(conn, user_id)
    old_role, old_status = row["role_id"], row["status"]
    try:
        users.update_user_fields(
            conn, user_id, full_name=body.fullName, role_id=body.roleId, status=body.status,
        )
        if body.departmentIds is not None:
            role_now = body.roleId or old_role
            _apply_departments(conn, user_id, role_now, body.departmentIds)
    except users.UserError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))

    ctx = audit.actor_ctx(admin)
    if body.roleId and body.roleId != old_role:
        # Критическое изменение роли — завершить активные сессии немедленно.
        users.terminate_sessions(conn, user_id)
        audit.record(conn, audit.ROLE_CHANGED, detail=f"from={old_role} to={body.roleId}",
                     target_user_id=user_id, **ctx)
    if body.status and body.status != old_status:
        if body.status in ("blocked", "deactivated"):
            users.terminate_sessions(conn, user_id)
        event = {"blocked": audit.USER_BLOCKED, "deactivated": audit.USER_DEACTIVATED,
                 "active": audit.USER_RESTORED}[body.status]
        audit.record(conn, event, target_user_id=user_id, **ctx)
    audit.record(conn, audit.USER_UPDATED, target_user_id=user_id, **ctx)
    return users.user_to_dict(conn, users.get_user(conn, user_id))


@router.post("/{user_id}/reset-password", dependencies=[Depends(require_csrf)])
def reset_user_password(
    user_id: str,
    body: ResetPasswordIn,
    conn: sqlite3.Connection = Depends(get_db),
    admin: policy.CurrentUser = Depends(require_user_admin),
):
    _user_or_404(conn, user_id)
    try:
        users.reset_password(conn, user_id, body.newPassword,
                             must_change_password=body.mustChangePassword)
    except (users.UserError, security.WeakPasswordError) as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    users.terminate_sessions(conn, user_id)
    audit.record(conn, audit.PASSWORD_RESET, target_user_id=user_id, **audit.actor_ctx(admin))
    return {"reset": True}


@router.post("/{user_id}/terminate-sessions", dependencies=[Depends(require_csrf)])
def terminate_user_sessions(
    user_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    admin: policy.CurrentUser = Depends(require_user_admin),
):
    _user_or_404(conn, user_id)
    count = users.terminate_sessions(conn, user_id)
    audit.record(conn, audit.SESSIONS_TERMINATED, detail=f"count={count}",
                 target_user_id=user_id, **audit.actor_ctx(admin))
    return {"terminated": count}

