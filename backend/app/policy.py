"""Централизованная политика доступа (RBAC).

Единственный источник правил «кто что может»: endpoints не содержат
самостоятельных проверок `if role == ...`, а вызывают функции отсюда.

Область видимости пациентов задаётся стратегией роли (roles.scope_strategy):

- ``global``          — все пациенты, полный CRUD (Разработчик);
- ``global_readonly`` — все пациенты, только чтение;
- ``by_department``   — пациенты, чей лечащий врач состоит в отделениях,
                        которыми управляет заведующий;
- ``by_treating_doctor`` — только пациенты, где лечащий врач — сам пользователь.

Будущее расширение (патоморфологи, доступ химиков с этапа лекарственной
терапии) добавляется как новая стратегия: новая ветка в
``patient_scope_filter`` + запись в roles — endpoints не переписываются.

Утечка существования чужого пациента исключается единым поведением:
объект вне scope неотличим от несуществующего (404), а фильтрация списков,
поиска, статистики, календаря и экспорта выполняется одним и тем же
SQL-условием ``patient_scope_filter``.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from . import users

ROLE_DEVELOPER = "developer"
ROLE_DEPUTY_CHIEF = "deputy_chief_oncology"
ROLE_CAOP_HEAD = "caop_head"
ROLE_DEPARTMENT_HEAD = "department_head"
ROLE_DOCTOR = "doctor"
ROLE_CONSILIUM_CHAIR = "consilium_chair"

SCOPE_GLOBAL = "global"
SCOPE_GLOBAL_READONLY = "global_readonly"
SCOPE_BY_DEPARTMENT = "by_department"
SCOPE_BY_TREATING_DOCTOR = "by_treating_doctor"


@dataclass(frozen=True)
class CurrentUser:
    """Снимок пользователя на момент запроса (читается из БД каждый запрос,
    поэтому блокировка/смена роли действуют немедленно)."""

    id: str
    username: str
    full_name: str
    role_id: str
    role_label: str
    scope_strategy: str
    status: str
    must_change_password: bool
    # by_department: отделения, которыми управляет; by_treating_doctor: членство.
    department_ids: tuple[str, ...] = field(default_factory=tuple)


def load_current_user(conn: sqlite3.Connection, user_id: str) -> CurrentUser | None:
    row = conn.execute(
        """SELECT u.*, r.label AS role_label, r.scope_strategy
           FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = ?""",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    if row["scope_strategy"] == SCOPE_BY_DEPARTMENT:
        dept_ids = tuple(users.managed_department_ids(conn, user_id))
    elif row["scope_strategy"] == SCOPE_BY_TREATING_DOCTOR:
        dept_ids = tuple(users.doctor_department_ids(conn, user_id))
    else:
        dept_ids = ()
    return CurrentUser(
        id=row["id"],
        username=row["username"],
        full_name=row["full_name"],
        role_id=row["role_id"],
        role_label=row["role_label"],
        scope_strategy=row["scope_strategy"],
        status=row["status"],
        must_change_password=bool(row["must_change_password"]),
        department_ids=dept_ids,
    )


# --- область видимости пациентов ----------------------------------------

def patient_scope_filter(user: CurrentUser) -> tuple[str, list]:
    """SQL-условие видимости пациента для WHERE. Применяется ко ВСЕМ выборкам:
    список, поиск, объектная проверка, экспорт, статистика, календарь."""
    if user.scope_strategy in (SCOPE_GLOBAL, SCOPE_GLOBAL_READONLY):
        return "1 = 1", []
    if user.scope_strategy == SCOPE_BY_DEPARTMENT:
        if not user.department_ids:
            return "0 = 1", []
        marks = ",".join("?" for _ in user.department_ids)
        return (
            f"""patients.treating_doctor_user_id IN (
                    SELECT ud.user_id FROM user_departments ud
                    JOIN departments d ON d.id = ud.department_id AND d.status = 'active'
                    WHERE ud.department_id IN ({marks})
                )""",
            list(user.department_ids),
        )
    if user.role_id == ROLE_CONSILIUM_CHAIR:
        return (
            "(patients.treating_doctor_user_id = ? OR EXISTS ("
            "SELECT 1 FROM consilium_chair_favorites f "
            "WHERE f.user_id = ? AND f.patient_id = patients.id))",
            [user.id, user.id],
        )
    if user.scope_strategy == SCOPE_BY_TREATING_DOCTOR:
        return "patients.treating_doctor_user_id = ?", [user.id]
    # Неизвестная стратегия — fail-closed.
    return "0 = 1", []


def can_read_patient(conn: sqlite3.Connection, user: CurrentUser, patient_id: str) -> bool:
    cond, params = patient_scope_filter(user)
    return conn.execute(
        f"SELECT 1 FROM patients WHERE id = ? AND {cond}", [patient_id, *params]
    ).fetchone() is not None


def _writes_allowed(user: CurrentUser) -> bool:
    return user.scope_strategy in (SCOPE_GLOBAL, SCOPE_BY_DEPARTMENT, SCOPE_BY_TREATING_DOCTOR)


def can_write_patient(conn: sqlite3.Connection, user: CurrentUser, patient_id: str) -> bool:
    """Объектная проверка на изменение/удаление: видимость + право записи."""
    if user.role_id == ROLE_CONSILIUM_CHAIR:
        return conn.execute(
            "SELECT 1 FROM patients WHERE id = ? AND treating_doctor_user_id = ?",
            (patient_id, user.id),
        ).fetchone() is not None
    return _writes_allowed(user) and can_read_patient(conn, user, patient_id)


def can_create_patient(user: CurrentUser) -> bool:
    return _writes_allowed(user)


def can_delete_patient(conn: sqlite3.Connection, user: CurrentUser, patient_id: str) -> bool:
    return can_write_patient(conn, user, patient_id)


def resolve_create_doctor(
    conn: sqlite3.Connection, user: CurrentUser, requested_doctor_user_id: str | None
) -> tuple[str | None, str | None]:
    """Кто станет лечащим врачом создаваемого пациента.

    Возвращает (doctor_user_id | None, error | None). None без ошибки —
    пациент создаётся несопоставленным (доступно только Разработчику).
    """
    if user.scope_strategy == SCOPE_BY_TREATING_DOCTOR:
        # Врач всегда закрепляет пациента за собой и не может выбрать коллегу.
        if requested_doctor_user_id and requested_doctor_user_id != user.id:
            return None, "Врач может создать пациента только с закреплением за собой."
        return user.id, None
    if user.scope_strategy == SCOPE_BY_DEPARTMENT:
        if not requested_doctor_user_id:
            return None, "Выберите лечащего врача из вашего отделения."
        if not users.doctor_in_departments(
            conn, requested_doctor_user_id, list(user.department_ids)
        ):
            return None, "Лечащий врач должен быть из отделения, которым вы управляете."
        return requested_doctor_user_id, None
    if user.scope_strategy == SCOPE_GLOBAL:
        if not requested_doctor_user_id:
            return None, None  # временно без сопоставленного врача — разрешено явно
        doctor = users.get_user(conn, requested_doctor_user_id)
        if doctor is None or doctor["role_id"] != ROLE_DOCTOR or doctor["status"] != "active":
            return None, "Выбранный пользователь не является активным врачом."
        return requested_doctor_user_id, None
    return None, "Ваша роль не позволяет создавать пациентов."


# --- административные права ----------------------------------------------

def can_change_treating_doctor(user: CurrentUser) -> bool:
    return user.role_id == ROLE_DEVELOPER


def can_view_unresolved(user: CurrentUser) -> bool:
    """Список несопоставленных пациентов: Разработчик (для сопоставления)
    и глобальные read-only роли (чтение — они и так видят этих пациентов
    в общем списке). Врачам и заведующим unresolved-пациенты не видны."""
    return user.role_id == ROLE_DEVELOPER or user.scope_strategy == SCOPE_GLOBAL_READONLY


def can_manage_users(user: CurrentUser) -> bool:
    return user.role_id == ROLE_DEVELOPER


def can_manage_departments(user: CurrentUser) -> bool:
    return user.role_id == ROLE_DEVELOPER


def can_view_audit(user: CurrentUser) -> bool:
    return user.role_id == ROLE_DEVELOPER


def can_run_notification_worker(user: CurrentUser) -> bool:
    return user.role_id == ROLE_DEVELOPER


def can_manage_notification_templates(user: CurrentUser) -> bool:
    return user.role_id == ROLE_DEVELOPER


def can_manage_consilium(user: CurrentUser) -> bool:
    return user.role_id in (ROLE_DEVELOPER, ROLE_CONSILIUM_CHAIR)


def can_import(user: CurrentUser, mode: str = "add") -> bool:
    if user.role_id == ROLE_CONSILIUM_CHAIR:
        return False
    if user.role_id == ROLE_DEVELOPER:
        return True
    # replace удаляет все данные, update переписывает записи по id —
    # оба режима шире любого частичного scope и остаются за Разработчиком.
    if mode != "add":
        return False
    return user.scope_strategy in (SCOPE_BY_DEPARTMENT, SCOPE_BY_TREATING_DOCTOR)


def can_export(user: CurrentUser) -> bool:
    # Экспорт — отдельное permission, не следствие чтения: глобальным
    # read-only ролям массовый экспорт не предоставляется.
    return _writes_allowed(user) and user.role_id != ROLE_CONSILIUM_CHAIR


def is_read_only(user: CurrentUser) -> bool:
    return user.scope_strategy == SCOPE_GLOBAL_READONLY


def permissions_payload(user: CurrentUser) -> dict:
    """Флаги для frontend. Только подсказка интерфейсу: сервер всё равно
    проверяет каждое действие заново."""
    return {
        "readOnly": is_read_only(user),
        "createPatients": can_create_patient(user),
        "editPatients": _writes_allowed(user),
        "deletePatients": _writes_allowed(user),
        "changeTreatingDoctor": can_change_treating_doctor(user),
        "manageUsers": can_manage_users(user),
        "manageDepartments": can_manage_departments(user),
        "viewAudit": can_view_audit(user),
        "importData": can_import(user),
        "exportData": can_export(user),
        "manageNotifications": _writes_allowed(user),
        "runNotificationWorker": can_run_notification_worker(user),
        "manageNotificationTemplates": can_manage_notification_templates(user),
        "manageConsilium": can_manage_consilium(user),
        "consiliumChair": user.role_id == ROLE_CONSILIUM_CHAIR,
    }
