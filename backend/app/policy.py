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

# WP2.4-D: «активная карточка» — карточка, не помеченная ошибочной. Единая
# политика: условие входит в patient_scope_filter, поэтому помеченная карточка
# одновременно исчезает из списков, поиска, объектных проверок (чтение и запись —
# 404), экспорта, аналитики, «Качества данных», уведомлений, календаря,
# консилиума, AI-инструментов и проверки совпадений WP2.4-C. Сама карточка и ее
# история не меняются; доступ к ней — только через реестр дублей (developer,
# deputy_chief_oncology) по его отдельным проверкам прав.
ACTIVE_PATIENT_SQL = "patients.id NOT IN (SELECT patient_id FROM patient_erroneous_state)"


def patient_scope_filter(user: CurrentUser) -> tuple[str, list]:
    """SQL-условие видимости пациента для WHERE. Применяется ко ВСЕМ выборкам:
    список, поиск, объектная проверка, экспорт, статистика, календарь.
    Включает условие активной карточки (WP2.4-D, ACTIVE_PATIENT_SQL)."""
    if user.scope_strategy in (SCOPE_GLOBAL, SCOPE_GLOBAL_READONLY):
        return ACTIVE_PATIENT_SQL, []
    if user.scope_strategy == SCOPE_BY_DEPARTMENT:
        if not user.department_ids:
            return "0 = 1", []
        marks = ",".join("?" for _ in user.department_ids)
        return (
            f"""patients.treating_doctor_user_id IN (
                    SELECT ud.user_id FROM user_departments ud
                    JOIN departments d ON d.id = ud.department_id AND d.status = 'active'
                    WHERE ud.department_id IN ({marks})
                ) AND {ACTIVE_PATIENT_SQL}""",
            list(user.department_ids),
        )
    if user.scope_strategy == SCOPE_BY_TREATING_DOCTOR:
        return f"patients.treating_doctor_user_id = ? AND {ACTIVE_PATIENT_SQL}", [user.id]
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
    return _writes_allowed(user) and can_read_patient(conn, user, patient_id)


def can_write_patient_data(user: CurrentUser) -> bool:
    """Роль может изменять медицинские данные в своей области (объектную
    проверку делает can_write_patient). Прежний общий признак записи: им же
    проверяется право записи в карточку при работе с консилиумом."""
    return _writes_allowed(user)


def can_create_patient_card(user: CurrentUser) -> bool:
    """Создание новой карточки пациента (WP2.4-A, R6). Председатель консилиума
    новые карточки не создает; его права на уже существующие карточки, где он
    лечащий врач, и права в консилиуме не меняются (can_write_patient_data)."""
    return _writes_allowed(user) and user.role_id != ROLE_CONSILIUM_CHAIR


def can_delete_patient(conn: sqlite3.Connection, user: CurrentUser, patient_id: str) -> bool:
    return can_write_patient(conn, user, patient_id)


def resolve_create_doctor(
    conn: sqlite3.Connection, user: CurrentUser, requested_doctor_user_id: str | None
) -> tuple[str | None, str | None]:
    """Кто станет лечащим врачом создаваемого пациента.

    Возвращает (doctor_user_id | None, error | None). None без ошибки —
    пациент создаётся несопоставленным (доступно только Разработчику).
    """
    if not can_create_patient_card(user):
        return None, "Ваша роль не позволяет создавать пациентов."
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


def can_manage_portal_access(conn: sqlite3.Connection, user: CurrentUser, patient_id: str) -> bool:
    """Выдать или сменить адрес мобильного доступа.

    Это административное действие по идентификации человека, а не часть
    лечения, поэтому внутри прав записи оно дополнительно ограничено ролями
    Разработчика и заведующего отделением. Новая роль и новая стратегия
    scope не вводятся: область по-прежнему считает can_write_patient.
    """
    return user.role_id in (ROLE_DEVELOPER, ROLE_DEPARTMENT_HEAD) and can_write_patient(conn, user, patient_id)


def can_revoke_portal_access(conn: sqlite3.Connection, user: CurrentUser, patient_id: str) -> bool:
    """Отключить мобильный доступ.

    Защитное действие: прекращение доступа должно быть доступно быстрее, чем
    выдача, поэтому его может выполнить любая роль с правом записи на этого
    пациента — включая лечащего врача для своих пациентов.
    """
    return can_write_patient(conn, user, patient_id)


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


# Онкоконсилиум — минимальные полномочия; роль вне списков доступа не получает.
#   запись пациентов: врач и заведующий в пределах своего scope (объектная
#     проверка can_write_patient), разработчик — по административной модели;
#   медицинские решения (рассмотрение, подпись, возврат): только председатель —
#     техническая роль разработчика их не наследует;
#   отметка исполнения хирургического направления в реестре председателя и
#   избранное председателя — два отдельных права (сейчас оба у председателя);
#     избранное — закладка и доступа к карточке не расширяет;
#   администрирование консилиума (сетка расписания, реестры, XLSX,
#     аналитика): председатель и разработчик.
# Глобальные read-only роли (заместитель главного врача, заведующий ЦАОП)
# в консилиум не допускаются.
_CONSILIUM_BOOKING_ROLES = (ROLE_DEVELOPER, ROLE_DOCTOR, ROLE_DEPARTMENT_HEAD)
_CONSILIUM_MANAGING_ROLES = (ROLE_DEVELOPER, ROLE_CONSILIUM_CHAIR)


def can_book_consilium(user: CurrentUser) -> bool:
    return user.role_id in _CONSILIUM_BOOKING_ROLES


def can_manage_consilium(user: CurrentUser) -> bool:
    return user.role_id in _CONSILIUM_MANAGING_ROLES


def can_decide_consilium(user: CurrentUser) -> bool:
    return user.role_id == ROLE_CONSILIUM_CHAIR


# Записи консилиума, которыми сотрудник управляет (WP2.3-B2): правка, перенос,
# отмена, архив, восстановление, повторная подача, история возвратов.
# - врач — записи своих пациентов (актуальный лечащий врач), заведующий —
#   пациентов своих отделений; автор записи значения не имеет;
# - разработчик — как до B2: только созданные им записи в своей области;
# - остальные роли (председатель, read-only) — нет: председатель работает с
#   записями через рассмотрение и подпись.
_CONSILIUM_RECORD_SCOPE_ROLES = (ROLE_DOCTOR, ROLE_DEPARTMENT_HEAD)


def consilium_record_filter(user: CurrentUser) -> tuple[str, list]:
    """SQL-условие для записей `a` (oncology_consilium_appointments JOIN patients)."""
    scope, params = patient_scope_filter(user)
    if user.role_id in _CONSILIUM_RECORD_SCOPE_ROLES:
        return scope, params
    if user.role_id == ROLE_DEVELOPER:
        return f"a.created_by_user_id = ? AND {scope}", [user.id, *params]
    return "0 = 1", []


def can_create_backdated_slot(user: CurrentUser) -> bool:
    """Исторический слот на прошедшее время (WP2.3-B3) — только председатель."""
    return user.role_id == ROLE_CONSILIUM_CHAIR


def can_backdate_consilium(user: CurrentUser) -> bool:
    """Запись задним числом в исторический слот (WP2.3-B3) — только заведующий
    отделением и только для пациентов своих отделений (объектная проверка в
    транзакции). Врач, разработчик и председатель — нет."""
    return user.role_id == ROLE_DEPARTMENT_HEAD


def can_read_backdating_reasons(user: CurrentUser) -> bool:
    """Текст причин (WP2.3-B3): председатель — любые; заведующий — записей
    своих отделений (объектная проверка). Врач видит только отметку, остальные
    роли — ничего."""
    return user.role_id in (ROLE_CONSILIUM_CHAIR, ROLE_DEPARTMENT_HEAD)


def reload_current_user(conn: sqlite3.Connection, user: CurrentUser) -> CurrentUser | None:
    """Полномочия, перечитанные из БД внутри транзакции изменения: роль,
    статус и отделения могли смениться после загрузки пользователя запросом.
    None — сотрудник больше не может действовать (заблокирован, роль сменена)."""
    fresh = load_current_user(conn, user.id)
    if fresh is None or fresh.status != "active" or fresh.role_id != user.role_id:
        return None
    return fresh


# Ручная отметка решения ОК — медицинское действие (утверждено владельцем, WP2.2-B):
# врач, заведующий и председатель консилиума, только для пациентов, которых они
# могут изменять (объектная проверка can_write_patient — область видимости не
# расширяется). Разработчик (техническая роль) и глобальные read-only роли — нет.
_CONSILIUM_DECISION_MARK_ROLES = (ROLE_DOCTOR, ROLE_DEPARTMENT_HEAD, ROLE_CONSILIUM_CHAIR)


def can_mark_consilium_decision(user: CurrentUser) -> bool:
    return user.role_id in _CONSILIUM_DECISION_MARK_ROLES and _writes_allowed(user)


def can_mark_referral_completion(user: CurrentUser) -> bool:
    """Отметка исполнения хирургического направления по подписанному решению:
    по логике реестров её ведёт председатель."""
    return user.role_id == ROLE_CONSILIUM_CHAIR


def can_use_chair_favorites(user: CurrentUser) -> bool:
    """Личные закладки председателя внутри консилиума."""
    return user.role_id == ROLE_CONSILIUM_CHAIR


def can_access_consilium(user: CurrentUser) -> bool:
    return can_book_consilium(user) or can_manage_consilium(user) or can_decide_consilium(user)


# --- реестр возможных дублей (WP2.4-D; U1, U3, U7) -------------------------
# Глобальный реестр видят только разработчик и заместитель главного врача по
# онкологии. Права реестра не дают никаких прав на изменение диагнозов,
# лечения, маршрутов, консилиумов и медицинской истории.

def can_view_duplicate_registry(user: CurrentUser) -> bool:
    return user.role_id in (ROLE_DEVELOPER, ROLE_DEPUTY_CHIEF)


def can_scan_duplicates(user: CurrentUser) -> bool:
    """Глобальное сканирование — техническая операция разработчика."""
    return user.role_id == ROLE_DEVELOPER


def can_technically_review_duplicates(user: CurrentUser) -> bool:
    """Технический разбор и комментарии; клинических решений не дает."""
    return user.role_id == ROLE_DEVELOPER


def can_decide_duplicates(user: CurrentUser) -> bool:
    """Клиническое решение по кандидату («не дубль», клиническое рассмотрение)."""
    return user.role_id == ROLE_DEPUTY_CHIEF


def can_mark_erroneous(user: CurrentUser) -> bool:
    """Пометка карточки ошибочной (только без клинических зависимостей — сервис)."""
    return user.role_id == ROLE_DEPUTY_CHIEF


def can_unmark_erroneous(user: CurrentUser) -> bool:
    return user.role_id == ROLE_DEPUTY_CHIEF


def can_read_duplicate_confirmation_reasons(user: CurrentUser) -> bool:
    """Причины подтверждений WP2.4-C — только в контексте клинического разбора."""
    return user.role_id == ROLE_DEPUTY_CHIEF


def can_import(user: CurrentUser, mode: str = "add") -> bool:
    if user.role_id == ROLE_CONSILIUM_CHAIR:
        return False
    if user.role_id == ROLE_DEVELOPER:
        return True
    # replace удаляет все данные, update переписывает записи по id —
    # оба режима шире любого частичного scope и остаются за Разработчиком.
    if mode != "add":
        return False
    if user.scope_strategy == SCOPE_BY_DEPARTMENT:
        return bool(user.department_ids)  # no managed departments means no import scope
    return user.scope_strategy == SCOPE_BY_TREATING_DOCTOR


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
        "createPatients": can_create_patient_card(user),
        "editPatients": _writes_allowed(user),
        "deletePatients": _writes_allowed(user),
        "changeTreatingDoctor": can_change_treating_doctor(user),
        # Подсказка интерфейсу: объектная проверка всё равно выполняется на сервере.
        "managePortalAccess": user.role_id in (ROLE_DEVELOPER, ROLE_DEPARTMENT_HEAD),
        "revokePortalAccess": _writes_allowed(user),
        "manageUsers": can_manage_users(user),
        "manageDepartments": can_manage_departments(user),
        "viewAudit": can_view_audit(user),
        "importData": can_import(user),
        "exportData": can_export(user),
        "manageNotifications": _writes_allowed(user),
        "runNotificationWorker": can_run_notification_worker(user),
        "manageNotificationTemplates": can_manage_notification_templates(user),
        "accessConsilium": can_access_consilium(user),
        "bookConsilium": can_book_consilium(user),
        "manageConsilium": can_manage_consilium(user),
        "decideConsilium": can_decide_consilium(user),
        "markConsiliumDecision": can_mark_consilium_decision(user),
        "markReferralCompletion": can_mark_referral_completion(user),
        "useConsiliumFavorites": can_use_chair_favorites(user),
        "consiliumChair": user.role_id == ROLE_CONSILIUM_CHAIR,
        "viewDuplicateRegistry": can_view_duplicate_registry(user),
        "scanDuplicates": can_scan_duplicates(user),
        "reviewDuplicatesTechnically": can_technically_review_duplicates(user),
        "decideDuplicates": can_decide_duplicates(user),
        "markErroneous": can_mark_erroneous(user),
        "readDuplicateConfirmationReasons": can_read_duplicate_confirmation_reasons(user),
    }
