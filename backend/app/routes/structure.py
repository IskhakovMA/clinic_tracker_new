"""Управление отделениями и составом (раздел «Структура»).
Доступно только роли с can_manage_departments (Разработчик)."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, policy, users
from ..db import transaction
from ..deps import get_db, require_active_password, require_csrf
from ..models import DepartmentCreateIn, DepartmentDoctorIn, DepartmentUpdateIn

router = APIRouter(prefix="/api/departments", tags=["structure"])


def require_structure_admin(
    user: policy.CurrentUser = Depends(require_active_password),
    conn: sqlite3.Connection = Depends(get_db),
) -> policy.CurrentUser:
    if not policy.can_manage_departments(user):
        audit.record(conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED,
                     detail="area=departments", **audit.actor_ctx(user))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return user


def _department_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    heads = conn.execute(
        """SELECT u.id, u.full_name FROM department_heads dh
           JOIN users u ON u.id = dh.user_id WHERE dh.department_id = ? ORDER BY u.full_name""",
        (row["id"],),
    ).fetchall()
    doctors = conn.execute(
        """SELECT u.id, u.full_name, u.status FROM user_departments ud
           JOIN users u ON u.id = ud.user_id WHERE ud.department_id = ? ORDER BY u.full_name""",
        (row["id"],),
    ).fetchall()
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "heads": [{"id": h["id"], "fullName": h["full_name"]} for h in heads],
        "doctors": [{"id": d["id"], "fullName": d["full_name"], "status": d["status"]} for d in doctors],
        "doctorCount": len(doctors),
    }


@router.get("")
def list_departments(
    conn: sqlite3.Connection = Depends(get_db),
    admin: policy.CurrentUser = Depends(require_structure_admin),
):
    rows = conn.execute("SELECT * FROM departments ORDER BY name").fetchall()
    departments = [_department_payload(conn, r) for r in rows]
    orphans = conn.execute(
        """SELECT u.id, u.full_name FROM users u
           WHERE u.role_id = 'doctor' AND u.status = 'active'
             AND u.id NOT IN (SELECT user_id FROM user_departments)
           ORDER BY u.full_name"""
    ).fetchall()
    return {
        "departments": departments,
        "doctorsWithoutDepartment": [{"id": o["id"], "fullName": o["full_name"]} for o in orphans],
    }


@router.post("", dependencies=[Depends(require_csrf)], status_code=status.HTTP_201_CREATED)
def create_department(
    body: DepartmentCreateIn,
    conn: sqlite3.Connection = Depends(get_db),
    admin: policy.CurrentUser = Depends(require_structure_admin),
):
    try:
        with transaction(conn):
            dept_id = users.create_department(conn, body.name)
            audit.record_required_or_fail(conn, audit.DEPARTMENT_CREATED, target_department_id=dept_id,
                                          **audit.actor_ctx(admin))
    except users.UserError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    return _department_payload(conn, users.get_department(conn, dept_id))


@router.patch("/{dept_id}", dependencies=[Depends(require_csrf)])
def update_department(
    dept_id: str,
    body: DepartmentUpdateIn,
    conn: sqlite3.Connection = Depends(get_db),
    admin: policy.CurrentUser = Depends(require_structure_admin),
):
    dept = users.get_department(conn, dept_id)
    if dept is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    ctx = audit.actor_ctx(admin)
    try:
        # WP-C (AUD-06): все изменения отделения и записи аудита — одна транзакция.
        with transaction(conn):
            if body.name is not None or body.status is not None:
                users.update_department(conn, dept_id, name=body.name, status=body.status)
                audit.record_required_or_fail(conn, audit.DEPARTMENT_UPDATED, target_department_id=dept_id, **ctx)
            if body.clearHead:
                users.set_department_head(conn, dept_id, None)
                audit.record_required_or_fail(conn, audit.HEAD_ASSIGNED, detail="cleared",
                                              target_department_id=dept_id, **ctx)
            elif body.headUserId is not None:
                head = users.get_user(conn, body.headUserId)
                if head is None or head["role_id"] != policy.ROLE_DEPARTMENT_HEAD or head["status"] != "active":
                    raise users.UserError("Заведующим можно назначить только активного пользователя с ролью «Заведующий отделением».")
                users.set_department_head(conn, dept_id, body.headUserId)
                audit.record_required_or_fail(conn, audit.HEAD_ASSIGNED, target_department_id=dept_id,
                                              target_user_id=body.headUserId, **ctx)
    except users.UserError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    return _department_payload(conn, users.get_department(conn, dept_id))


@router.delete("/{dept_id}", dependencies=[Depends(require_csrf)])
def archive_department(
    dept_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    admin: policy.CurrentUser = Depends(require_structure_admin),
):
    """Физическое удаление запрещено: отделение со связями (врачи,
    заведующие, пациенты, история) архивируется и выпадает из scope."""
    dept = users.get_department(conn, dept_id)
    if dept is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
    with transaction(conn):
        users.update_department(conn, dept_id, status="archived")
        audit.record_required_or_fail(conn, audit.DEPARTMENT_UPDATED, detail="archived",
                                      target_department_id=dept_id, **audit.actor_ctx(admin))
    return {"archived": True}


@router.post("/{dept_id}/doctors", dependencies=[Depends(require_csrf)])
def add_doctor(
    dept_id: str,
    body: DepartmentDoctorIn,
    conn: sqlite3.Connection = Depends(get_db),
    admin: policy.CurrentUser = Depends(require_structure_admin),
):
    try:
        with transaction(conn):
            users.add_doctor_to_department(conn, body.userId, dept_id)
            audit.record_required_or_fail(conn, audit.MEMBERSHIP_CHANGED, detail="added",
                                          target_user_id=body.userId, target_department_id=dept_id,
                                          **audit.actor_ctx(admin))
    except users.UserError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    return _department_payload(conn, users.get_department(conn, dept_id))


@router.delete("/{dept_id}/doctors/{user_id}", dependencies=[Depends(require_csrf)])
def remove_doctor(
    dept_id: str,
    user_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    admin: policy.CurrentUser = Depends(require_structure_admin),
):
    with transaction(conn):
        if not users.remove_doctor_from_department(conn, user_id, dept_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link not found")
        audit.record_required_or_fail(conn, audit.MEMBERSHIP_CHANGED, detail="removed",
                                      target_user_id=user_id, target_department_id=dept_id,
                                      **audit.actor_ctx(admin))
    dept = users.get_department(conn, dept_id)
    return _department_payload(conn, dept)
