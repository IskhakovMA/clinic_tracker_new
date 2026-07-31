"""Учётные записи, отделения и связи (RBAC-модель данных).

Всё серверное: id и timestamps генерируются здесь, пароли хранятся только
как Argon2id-хеш и никогда не возвращаются наружу. Реальные ФИО сотрудников
и названия отделений — бизнес-конфигурация, которая вносится через
интерфейс Разработчика или локальный `python -m backend.cli import-structure`;
в коде и тестах они не зашиваются.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from typing import Optional

from . import security
from .db import now_iso

USER_STATUSES = ("active", "blocked", "deactivated")
DEPARTMENT_STATUSES = ("active", "archived")

USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,64}$")


class UserError(ValueError):
    """Операторская ошибка — текст безопасен для показа."""


def new_id() -> str:
    return uuid.uuid4().hex


def _validate_username(username: str) -> str:
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise UserError(
            "Логин: 3–64 символа, только латиница, цифры, точка, дефис и подчёркивание."
        )
    return username


def _validate_full_name(full_name: str) -> str:
    full_name = " ".join(full_name.split())
    if not (1 <= len(full_name) <= 200):
        raise UserError("ФИО обязательно и не длиннее 200 символов.")
    return full_name


def role_exists(conn: sqlite3.Connection, role_id: str) -> bool:
    return conn.execute("SELECT 1 FROM roles WHERE id = ?", (role_id,)).fetchone() is not None


def get_user(conn: sqlite3.Connection, user_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_username(conn: sqlite3.Connection, username: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
    ).fetchone()


def create_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    full_name: str,
    role_id: str,
    password: str,
    must_change_password: bool = False,
) -> str:
    username = _validate_username(username)
    full_name = _validate_full_name(full_name)
    if not role_exists(conn, role_id):
        raise UserError("Неизвестная роль.")
    security.validate_password_strength(password)
    if get_user_by_username(conn, username) is not None:
        raise UserError("Логин уже занят.")
    uid = new_id()
    now = now_iso()
    with conn:
        conn.execute(
            """INSERT INTO users (id, username, full_name, role_id, status, password_hash,
                                  must_change_password, created_at, updated_at, password_changed_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
            (uid, username, full_name, role_id, security.hash_password(password),
             int(must_change_password), now, now, now),
        )
    return uid


def update_user_fields(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    full_name: Optional[str] = None,
    role_id: Optional[str] = None,
    status: Optional[str] = None,
) -> None:
    user = get_user(conn, user_id)
    if user is None:
        raise UserError("Пользователь не найден.")
    sets, params = [], []
    if full_name is not None:
        sets.append("full_name = ?")
        params.append(_validate_full_name(full_name))
    if role_id is not None:
        if not role_exists(conn, role_id):
            raise UserError("Неизвестная роль.")
        sets.append("role_id = ?")
        params.append(role_id)
    if status is not None:
        if status not in USER_STATUSES:
            raise UserError("Недопустимый статус.")
        sets.append("status = ?")
        params.append(status)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(now_iso())
    params.append(user_id)
    with conn:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)


def reset_password(
    conn: sqlite3.Connection, user_id: str, new_password: str, *, must_change_password: bool = True
) -> None:
    if get_user(conn, user_id) is None:
        raise UserError("Пользователь не найден.")
    security.validate_password_strength(new_password)
    with conn:
        conn.execute(
            """UPDATE users SET password_hash = ?, must_change_password = ?,
                                password_changed_at = ?, updated_at = ? WHERE id = ?""",
            (security.hash_password(new_password), int(must_change_password),
             now_iso(), now_iso(), user_id),
        )


def change_own_password(conn: sqlite3.Connection, user_id: str, new_password: str) -> None:
    security.validate_password_strength(new_password)
    with conn:
        conn.execute(
            """UPDATE users SET password_hash = ?, must_change_password = 0,
                                password_changed_at = ?, updated_at = ? WHERE id = ?""",
            (security.hash_password(new_password), now_iso(), now_iso(), user_id),
        )


def touch_last_login(conn: sqlite3.Connection, user_id: str) -> None:
    with conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), user_id))


def terminate_sessions(conn: sqlite3.Connection, user_id: str) -> int:
    with conn:
        return conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount


def developer_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE role_id = 'developer' AND status = 'active'"
    ).fetchone()["c"]


# --- отделения и связи ---------------------------------------------------

def get_department(conn: sqlite3.Connection, dept_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM departments WHERE id = ?", (dept_id,)).fetchone()


def create_department(conn: sqlite3.Connection, name: str) -> str:
    name = " ".join(name.split())
    if not (1 <= len(name) <= 200):
        raise UserError("Название отделения обязательно и не длиннее 200 символов.")
    if conn.execute("SELECT 1 FROM departments WHERE name = ?", (name,)).fetchone():
        raise UserError("Отделение с таким названием уже существует.")
    dept_id = new_id()
    now = now_iso()
    with conn:
        conn.execute(
            "INSERT INTO departments (id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'active', ?, ?)",
            (dept_id, name, now, now),
        )
    return dept_id


def update_department(
    conn: sqlite3.Connection, dept_id: str, *, name: Optional[str] = None, status: Optional[str] = None
) -> None:
    if get_department(conn, dept_id) is None:
        raise UserError("Отделение не найдено.")
    sets, params = [], []
    if name is not None:
        name = " ".join(name.split())
        if not (1 <= len(name) <= 200):
            raise UserError("Название отделения обязательно и не длиннее 200 символов.")
        clash = conn.execute(
            "SELECT 1 FROM departments WHERE name = ? AND id != ?", (name, dept_id)
        ).fetchone()
        if clash:
            raise UserError("Отделение с таким названием уже существует.")
        sets.append("name = ?")
        params.append(name)
    if status is not None:
        if status not in DEPARTMENT_STATUSES:
            raise UserError("Недопустимый статус отделения.")
        sets.append("status = ?")
        params.append(status)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(now_iso())
    params.append(dept_id)
    with conn:
        conn.execute(f"UPDATE departments SET {', '.join(sets)} WHERE id = ?", params)


def department_has_links(conn: sqlite3.Connection, dept_id: str) -> bool:
    for table in ("user_departments", "department_heads"):
        if conn.execute(f"SELECT 1 FROM {table} WHERE department_id = ?", (dept_id,)).fetchone():
            return True
    return False


def add_doctor_to_department(conn: sqlite3.Connection, user_id: str, dept_id: str) -> None:
    user = get_user(conn, user_id)
    if user is None:
        raise UserError("Пользователь не найден.")
    dept = get_department(conn, dept_id)
    if dept is None or dept["status"] != "active":
        raise UserError("Отделение не найдено или архивировано.")
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_departments (user_id, department_id, created_at) VALUES (?, ?, ?)",
            (user_id, dept_id, now_iso()),
        )


def remove_doctor_from_department(conn: sqlite3.Connection, user_id: str, dept_id: str) -> bool:
    with conn:
        cur = conn.execute(
            "DELETE FROM user_departments WHERE user_id = ? AND department_id = ?",
            (user_id, dept_id),
        )
        return cur.rowcount > 0


def set_department_head(conn: sqlite3.Connection, dept_id: str, user_id: Optional[str]) -> None:
    """Назначить заведующего отделению (или снять, если user_id is None).
    Один заведующий может управлять несколькими отделениями."""
    dept = get_department(conn, dept_id)
    if dept is None:
        raise UserError("Отделение не найдено.")
    with conn:
        conn.execute("DELETE FROM department_heads WHERE department_id = ?", (dept_id,))
        if user_id is not None:
            if get_user(conn, user_id) is None:
                raise UserError("Пользователь не найден.")
            conn.execute(
                "INSERT INTO department_heads (user_id, department_id, created_at) VALUES (?, ?, ?)",
                (user_id, dept_id, now_iso()),
            )


def doctor_department_ids(conn: sqlite3.Connection, user_id: str) -> list[str]:
    """Активные отделения, в которых состоит врач."""
    return [
        r["department_id"]
        for r in conn.execute(
            """SELECT ud.department_id FROM user_departments ud
               JOIN departments d ON d.id = ud.department_id AND d.status = 'active'
               WHERE ud.user_id = ?""",
            (user_id,),
        ).fetchall()
    ]


def managed_department_ids(conn: sqlite3.Connection, user_id: str) -> list[str]:
    """Активные отделения, которыми управляет заведующий."""
    return [
        r["department_id"]
        for r in conn.execute(
            """SELECT dh.department_id FROM department_heads dh
               JOIN departments d ON d.id = dh.department_id AND d.status = 'active'
               WHERE dh.user_id = ?""",
            (user_id,),
        ).fetchall()
    ]


def user_department_rows(conn: sqlite3.Connection, user_id: str, role_id: str) -> list[sqlite3.Row]:
    """Отделения пользователя для профиля: врач — членство, заведующий — управление."""
    table = "department_heads" if role_id == "department_head" else "user_departments"
    return conn.execute(
        f"""SELECT d.id, d.name FROM {table} link
            JOIN departments d ON d.id = link.department_id AND d.status = 'active'
            WHERE link.user_id = ? ORDER BY d.name""",
        (user_id,),
    ).fetchall()


def selectable_doctors(
    conn: sqlite3.Connection, department_ids: Optional[list[str]] = None
) -> list[sqlite3.Row]:
    """Активные врачи для назначения лечащим: все или из указанных отделений."""
    if department_ids is None:
        return conn.execute(
            """SELECT u.id, u.full_name FROM users u
               WHERE u.role_id = 'doctor' AND u.status = 'active'
               ORDER BY u.full_name"""
        ).fetchall()
    if not department_ids:
        return []
    marks = ",".join("?" for _ in department_ids)
    return conn.execute(
        f"""SELECT DISTINCT u.id, u.full_name FROM users u
            JOIN user_departments ud ON ud.user_id = u.id
            JOIN departments d ON d.id = ud.department_id AND d.status = 'active'
            WHERE u.role_id = 'doctor' AND u.status = 'active' AND ud.department_id IN ({marks})
            ORDER BY u.full_name""",
        department_ids,
    ).fetchall()


def doctor_in_departments(conn: sqlite3.Connection, user_id: str, department_ids: list[str]) -> bool:
    if not department_ids:
        return False
    marks = ",".join("?" for _ in department_ids)
    return conn.execute(
        f"""SELECT 1 FROM user_departments ud
            JOIN departments d ON d.id = ud.department_id AND d.status = 'active'
            WHERE ud.user_id = ? AND ud.department_id IN ({marks})""",
        [user_id, *department_ids],
    ).fetchone() is not None


def user_to_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Публичное представление пользователя: без password_hash."""
    role = conn.execute("SELECT label FROM roles WHERE id = ?", (row["role_id"],)).fetchone()
    departments = [
        {"id": d["id"], "name": d["name"]}
        for d in user_department_rows(conn, row["id"], row["role_id"])
    ]
    return {
        "id": row["id"],
        "username": row["username"],
        "fullName": row["full_name"],
        "roleId": row["role_id"],
        "roleLabel": role["label"] if role else row["role_id"],
        "status": row["status"],
        "mustChangePassword": bool(row["must_change_password"]),
        "createdAt": row["created_at"],
        "lastLoginAt": row["last_login_at"],
        "departments": departments,
    }
