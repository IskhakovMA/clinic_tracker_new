"""Канонизация признаков пациента и поиск возможных дублей в области доступа (WP2.4-C).

Канонизация только для сравнения: исходные значения карточек не меняются.

- Номер полиса ОМС: удаляются пробельные символы и дефисы (в том числе
  типографские тире), буквы приводятся к одному регистру, визуально одинаковые
  кириллические и латинские буквы сводятся к одной форме. Формат номера не
  проверяется (U2): исторические нестандартные значения сравниваются как есть.
  Пустой номер — не признак.
- ФИО: регистр, «ё» → «е», визуально одинаковые латинские буквы → кириллица,
  повторяющиеся пробелы. Транслитерации нет. Совпадение ФИО само по себе
  доказательством не является и предупреждения 409 не вызывает.

Поиск совпадений выполняется ОДНИМ запросом, в котором условие области доступа
сотрудника (policy.patient_scope_filter) стоит рядом с условием совпадения: база
не ищет по всем карточкам с последующей фильтрацией, и карточки вне области не
влияют ни на результат, ни на ответ.
"""
from __future__ import annotations

import re
import sqlite3

from . import policy

# Визуально одинаковые буквы: латиница → кириллица (верхний и нижний регистр).
_LATIN_TO_CYRILLIC = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М", "O": "О", "P": "Р", "T": "Т",
    "X": "Х", "Y": "У", "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
})
_SEPARATORS = re.compile(r"[\s\-‐‑‒–—―−]+")
_SPACES = re.compile(r"\s+")

MATCH_LIMIT = 50
SQL_CANONICAL_POLICY = "clinic_canonical_policy"


class MatchLimitExceeded(Exception):
    """В области сотрудника совпадений больше MATCH_LIMIT (C-01).

    Первые MATCH_LIMIT карточек — не полный список: подтверждать их нельзя.
    Сами карточки сверх лимита не читаются и не возвращаются: запрос берет
    MATCH_LIMIT + 1 строку только чтобы узнать, что лимит превышен."""


def canonical_policy(value) -> str:
    """Каноническая форма номера полиса для сравнения ('' — номера нет)."""
    if value is None:
        return ""
    text = _SEPARATORS.sub("", str(value)).upper()
    return text.translate(_LATIN_TO_CYRILLIC).replace("Ё", "Е")


def canonical_name(value) -> str:
    """Каноническая форма ФИО для сравнения ('' — ФИО нет)."""
    if value is None:
        return ""
    text = _SPACES.sub(" ", str(value)).strip()
    return text.translate(_LATIN_TO_CYRILLIC).casefold().replace("ё", "е")


def ensure_sql_functions(conn: sqlite3.Connection) -> None:
    """Регистрирует детерминированную функцию канонизации полиса в соединении."""
    conn.create_function(SQL_CANONICAL_POLICY, 1, canonical_policy, deterministic=True)


def policy_matches(
    conn: sqlite3.Connection,
    actor: policy.CurrentUser,
    policy_number,
    *,
    exclude_patient_id: str | None = None,
) -> list[dict]:
    """Карточки ОБЛАСТИ ДОСТУПА actor с тем же каноническим номером полиса.

    Возвращает минимальные сведения, которые сотрудник и так вправе читать:
    ID, ФИО, возраст, лечащий врач. Ни полиса, ни диагнозов, ни истории."""
    canon = canonical_policy(policy_number)
    if not canon:
        return []
    ensure_sql_functions(conn)
    cond, params = policy.patient_scope_filter(actor)
    sql = (
        "SELECT patients.id, patients.name, patients.age, "
        "COALESCE(u.full_name, NULLIF(patients.doctor, '')) AS treating_doctor_name "
        "FROM patients LEFT JOIN users u ON u.id = patients.treating_doctor_user_id "
        f"WHERE ({cond}) AND patients.policy_number IS NOT NULL AND patients.policy_number <> '' "  # nosec B608
        f"AND {SQL_CANONICAL_POLICY}(patients.policy_number) = ?"
    )
    args = [*params, canon]
    if exclude_patient_id is not None:
        sql += " AND patients.id <> ?"
        args.append(exclude_patient_id)
    sql += " ORDER BY patients.created_at, patients.id LIMIT ?"
    args.append(MATCH_LIMIT + 1)
    rows = conn.execute(sql, args).fetchall()
    if len(rows) > MATCH_LIMIT:
        raise MatchLimitExceeded()
    return [
        {"id": r["id"], "name": r["name"], "age": r["age"] or 0, "treatingDoctorName": r["treating_doctor_name"] or ""}
        for r in rows
    ]


def policy_matches_many(conn: sqlite3.Connection, actor: policy.CurrentUser, policy_numbers) -> dict[str, list[dict]]:
    """Совпадения для набора номеров (импорт): канонический номер → карточки
    ОБЛАСТИ ДОСТУПА actor. Тот же запрос с условием области, порциями номеров.

    Для каждого номера читается не более MATCH_LIMIT + 1 карточки (оконная
    функция). Номер, у которого совпадений больше MATCH_LIMIT, попадает в
    результат как None: его неполный список не возвращается (C-01)."""
    canons = sorted({c for c in (canonical_policy(v) for v in policy_numbers) if c})
    found: dict[str, list[dict] | None] = {}
    if not canons:
        return found
    ensure_sql_functions(conn)
    cond, params = policy.patient_scope_filter(actor)
    for start in range(0, len(canons), 400):
        chunk = canons[start:start + 400]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            "SELECT id, name, age, treating_doctor_name, canon FROM ("
            "SELECT patients.id, patients.name, patients.age, "
            "COALESCE(u.full_name, NULLIF(patients.doctor, '')) AS treating_doctor_name, "
            f"{SQL_CANONICAL_POLICY}(patients.policy_number) AS canon, "
            f"ROW_NUMBER() OVER (PARTITION BY {SQL_CANONICAL_POLICY}(patients.policy_number) "
            "ORDER BY patients.created_at, patients.id) AS position "
            "FROM patients LEFT JOIN users u ON u.id = patients.treating_doctor_user_id "
            f"WHERE ({cond}) AND patients.policy_number IS NOT NULL AND patients.policy_number <> '' "  # nosec B608
            f"AND {SQL_CANONICAL_POLICY}(patients.policy_number) IN ({marks})"
            ") WHERE position <= ? ORDER BY canon, position",
            [*params, *chunk, MATCH_LIMIT + 1],
        ).fetchall()
        for r in rows:
            bucket = found.setdefault(r["canon"], [])
            if bucket is None:
                continue
            if len(bucket) == MATCH_LIMIT:
                found[r["canon"]] = None          # больше лимита — списка нет
                continue
            bucket.append({"id": r["id"], "name": r["name"], "age": r["age"] or 0,
                           "treatingDoctorName": r["treating_doctor_name"] or ""})
    return found
