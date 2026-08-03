"""Server-side data quality evaluation.

Findings are computed from current scoped patient data on every request. There
is deliberately no persistent findings table: after a patient is corrected via
the existing patient endpoints, the finding disappears on the next read.
"""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from .. import policy
from ..models import STAGES

SEVERITIES = ("critical", "warning", "info")
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}

STAGE_ORDER = list(STAGES)
STAGE_INDEX = {stage: i for i, stage in enumerate(STAGE_ORDER)}

COMPLETENESS_FIELDS = (
    ("name", "ФИО", "critical"),
    ("phone", "Телефон", "warning"),
    ("diagnosis", "Диагноз", "critical"),
    ("nosology", "Профиль клиентского пути", "warning"),
    ("stage", "Текущий этап", "critical"),
    ("treatingDoctorUserId", "Лечащий врач", "warning"),
)

QUALITY_RULES = {
    "missing_treating_doctor": "Пациент не сопоставлен с учётной записью лечащего врача.",
    "missing_required_field": "Поле нужно для корректной регистрационной карточки.",
    "missing_client_path_profile": "Профиль клиентского пути нужен для регламентных сроков и чек-листа.",
    "missing_diagnosis": "Диагноз обязателен для новых записей и нужен для корректной карточки.",
    "missing_stage": "Текущий этап нужен для маршрута лечения.",
    "invalid_phone": "Заполненный телефон должен быть похож на корректный номер.",
    "actual_date_in_future": "Фактическая дата не должна быть в будущем.",
    "planned_date_in_past": "Будущий план с прошедшей датой требует актуализации.",
    "stage_sequence_violation": "История этапов противоречит маршруту лечения.",
    "overdue_event": "Плановое событие в прошлом не имеет фактического выполнения.",
    "incomplete_required_checklist": "Обязательные пункты чек-листа не отмечены выполненными.",
    "possible_duplicate": "Найдены записи с совпадающими объяснимыми признаками.",
}

# Server-side MVP configuration copied from the current checklist rules used by
# the product. It covers required items only; optional items are not quality
# findings. Unknown profiles simply do not get checklist findings.
REQUIRED_CHECKLISTS: dict[str, dict[str, list[str]]] = {
    "prostate": {
        "exam": ["Направление на биопсию", "Выполнение биопсии", "Гистологическое исследование", "Оценка результатов гистологии"],
    },
    "breast": {
        "exam": ["УЗИ молочной железы и лимфатических узлов", "Биопсия молочной железы и/или л/у (или пункция)", "Гистологическое исследование", "Общий анализ крови", "Биохимический анализ крови", "Коагулограмма", "Анализы на ВИЧ, гепатит В и С", "УЗИ ОБП и малого таза", "ЭКГ", "КТ или рентген ОГК"],
    },
    "stomach": {
        "exam": ["КТ ОГК, ОБП, ОМТ с контрастом", "Гистологическое исследование"],
        "consult": ["Дообследование по функциональному статусу (при необходимости)", "ИГХ/МГИ исследования (по показаниям)"],
    },
    "colon": {
        "exam": ["КТ ОГК, ОБП, ОМТ с контрастом", "МРТ ОМТ с контрастом (при поражении ректосигмоида/прямой кишки)", "Маркеры крови СА19.9, РЭА", "Общий анализ крови", "Коагулограмма", "Анализы на ВИЧ, RW, гепатиты В и С", "ЭКГ", "Гистологическое исследование"],
    },
    "ovary": {
        "exam": ["ЭКГ", "МРТ ОМТ с контрастом", "КТ ОГК и ОБП с контрастом", "Колоноскопия + гастроскопия", "УЗИ вен нижних конечностей", "УЗИ молочных желез/маммография"],
        "consult": ["Цитологическое исследование (если делался забор асцитической жидкости)", "Функциональная оценка статуса"],
    },
    "uterus": {
        "exam": ["МРТ ОМТ с контрастом", "КТ ОГК, ОБП с контрастом", "Колоноскопия + гастроскопия", "Биохимический анализ крови", "Коагулограмма"],
    },
    "skin": {
        "exam": ["Общий анализ крови", "Анализы на ВИЧ, гепатиты В и С", "Коагулограмма", "Биохимический анализ крови", "Забор материала на цитологию", "Биопсия", "Гистологическое исследование"],
    },
    "lung12": {
        "exam": ["КТ ОГК и ОБП с контрастом", "Анализ на ВИЧ", "Гепатит В и С", "Общий анализ крови", "Биохимический анализ крови", "Коагулограмма", "ЭКГ", "ФВД", "Консультация терапевта/пульмонолога", "ЭГДС", "Колоноскопия"],
    },
    "lung34": {
        "exam": ["Рентген ОГК или КТ ОГК (солидный очаг >9мм)", "КТ ОГК и ОБП с контрастом", "ВИЧ, гепатит В и С", "Общий анализ крови", "Биохимический анализ", "Коагулограмма", "ЭКГ", "ФВД", "Консультация терапевта/пульмонолога", "Направление на бронхоскопию", "УЗИ лимфоузлов", "ЭГДС + колоноскопия", "УЗДГ вен нижних конечностей", "УЗИ ОБП и лимфоузлов", "Консультации профильных специалистов", "Гистология (после бронхоскопии)", "ИГХ и генетические исследования"],
    },
    "melanoma": {
        "exam": ["Общий анализ крови", "Биохимический анализ крови", "Коагулограмма", "ЭКГ", "УЗИ регионарных лимфатических узлов", "Гистологическое исследование", "При >5мм: КТ ОГК/ОБП/ОМТ с контрастом, МРТ ГМ с контрастом, пункция л/у", "При <5мм: эксцизионная панч-биопсия"],
    },
    "bladder": {
        "exam": ["Цистоскопия с биопсией/ТУР", "Гистологическое исследование"],
    },
    "headneckConfirmed": {
        "exam": ["УЗИ лимфатических узлов", "ЭГДС", "КТ органов грудной клетки", "КТ ОБП/ЗП/ОМТ с контрастом (или УЗИ)", "ЭКГ", "Направление на биопсию", "Выполнение биопсии", "Гистологическое исследование"],
    },
    "headneckSuspected": {
        "exam": ["Биопсия образования", "Гистологическое исследование", "УЗИ лимфатических узлов", "ЭГДС", "КТ органов грудной клетки", "КТ ОБП/ОМТ с контрастом (или УЗИ)"],
    },
    "liver": {
        "exam": ["Онкомаркер СА-19-9", "Онкомаркер АФП", "Онкомаркер РЭА", "КТ ОГК и ОМТ с контрастом"],
    },
    "pancreas": {
        "exam": ["Онкомаркер СА-19,9", "EUS + биопсия (или транскутанная биопсия)", "Цитологическое исследование", "Гистологическое исследование"],
    },
    "thyroidSuspected": {
        "exam": ["Пункция узла щитовидной железы и/или лимфоузла", "Цитологическое исследование", "ЭГДС", "КТ ОГК с контрастом", "КТ ОБП/ОМТ с контрастом (или УЗИ)", "ЭКГ"],
    },
    "thyroidConfirmed": {
        "exam": ["ЭГДС", "КТ ОГК с контрастом", "КТ ОБП/ОМТ с контрастом (или УЗИ)", "ЭКГ"],
    },
    "cervixInvasive": {
        "exam": ["МРТ ОМТ с контрастом", "КТ ОГК", "УЗИ ОБП", "УЗИ ЗП", "Колоноскопия", "Гастроскопия", "УЗИ вен нижних конечностей", "Анализ крови на SCC"],
    },
    "cervixCytology": {
        "exam": ["МРТ ОМТ с контрастом", "КТ ОГК", "УЗИ ОБП", "УЗИ ЗП", "Колоноскопия", "Гастроскопия", "УЗИ вен нижних конечностей", "Анализ крови на SCC"],
    },
    "kidney": {
        "exam": ["Гистологическое исследование"],
    },
    "upperUrinary": {
        "exam": ["Цистоскопия", "Гистологическое исследование"],
    },
}


@dataclass
class PatientRecord:
    id: str
    name: str
    age: int
    phone: str
    diagnosis: str
    nosology: str
    stage: str
    priority: str
    arrival: str
    head: str
    treating_doctor_user_id: str | None
    treating_doctor_name: str
    department_ids: list[str] = field(default_factory=list)
    department_names: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    future_plans: dict[str, dict[str, str]] = field(default_factory=dict)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _safe_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _normalize_phone(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits


def phone_is_valid(value: str) -> bool:
    raw = (value or "").strip()
    if not raw:
        return True
    if re.search(r"[A-Za-zА-Яа-я]", raw):
        return False
    digits = _normalize_phone(raw)
    return 10 <= len(digits) <= 15


def _normalize_name(value: str) -> str:
    return " ".join((value or "").strip().lower().replace("ё", "е").split())


def _can_write_visible_patients(user: policy.CurrentUser) -> bool:
    return user.scope_strategy in (
        policy.SCOPE_GLOBAL,
        policy.SCOPE_BY_DEPARTMENT,
        policy.SCOPE_BY_TREATING_DOCTOR,
    )


def _action(label: str, tab: str, can_write: bool, extra: dict[str, Any] | None = None) -> dict:
    data = {"label": label, "tab": tab, "can_write": can_write}
    if extra:
        data.update(extra)
    return data


def _finding(
    *,
    patient: PatientRecord,
    code: str,
    category: str,
    severity: str,
    title: str,
    description: str,
    field_name: str | None = None,
    details: dict[str, Any] | None = None,
    suggested_action: dict[str, Any],
) -> dict:
    return {
        "code": code,
        "category": category,
        "severity": severity,
        "title": title,
        "description": description,
        "patient_id": patient.id,
        "field": field_name,
        "details": details or {},
        "suggested_action": suggested_action,
    }


def _load_scoped_patients(conn: sqlite3.Connection, user: policy.CurrentUser) -> list[PatientRecord]:
    cond, params = policy.patient_scope_filter(user)
    rows = conn.execute(
        f"""SELECT patients.*,
                   u.full_name AS treating_doctor_name,
                   GROUP_CONCAT(DISTINCT d.id) AS department_ids,
                   GROUP_CONCAT(DISTINCT d.name) AS department_names
            FROM patients
            LEFT JOIN users u ON u.id = patients.treating_doctor_user_id
            LEFT JOIN user_departments ud ON ud.user_id = patients.treating_doctor_user_id
            LEFT JOIN departments d ON d.id = ud.department_id AND d.status = 'active'
            WHERE {cond}
            GROUP BY patients.id
            ORDER BY patients.arrival ASC""",
        params,
    ).fetchall()
    records = [
        PatientRecord(
            id=r["id"],
            name=r["name"] or "",
            age=r["age"] or 0,
            phone=r["phone"] or "",
            diagnosis=r["diagnosis"] or "",
            nosology=r["nosology"] or "",
            stage=r["stage"] or "",
            priority=r["priority"] or "",
            arrival=r["arrival"] or "",
            head=r["head"] or "",
            treating_doctor_user_id=r["treating_doctor_user_id"],
            treating_doctor_name=r["treating_doctor_name"] or r["doctor"] or "",
            department_ids=[x for x in (r["department_ids"] or "").split(",") if x],
            department_names=[x for x in (r["department_names"] or "").split(",") if x],
        )
        for r in rows
    ]
    _attach_history_and_plans(conn, records)
    return records


def _attach_history_and_plans(conn: sqlite3.Connection, patients: list[PatientRecord]) -> None:
    if not patients:
        return
    patient_by_id = {p.id: p for p in patients}
    marks = ",".join("?" for _ in patient_by_id)
    history_rows = conn.execute(
        f"""SELECT * FROM history_entries
            WHERE patient_id IN ({marks}) ORDER BY patient_id, seq ASC""",
        list(patient_by_id),
    ).fetchall()
    history_by_entry = {}
    for h in history_rows:
        entry = {
            "id": h["id"],
            "seq": h["seq"],
            "stage": h["stage"] or "",
            "enteredAt": h["entered_at"] or "",
            "checklist": json.loads(h["checklist_json"]) if h["checklist_json"] else {},
            "events": [],
        }
        patient_by_id[h["patient_id"]].history.append(entry)
        history_by_entry[h["id"]] = entry

    if history_by_entry:
        marks = ",".join("?" for _ in history_by_entry)
        event_rows = conn.execute(
            f"""SELECT * FROM events
                WHERE history_entry_id IN ({marks}) ORDER BY history_entry_id, seq ASC""",
            list(history_by_entry),
        ).fetchall()
        for e in event_rows:
            history_by_entry[e["history_entry_id"]]["events"].append({
                "seq": e["seq"],
                "date": e["date"] or "",
                "time": e["time"] or "",
                "actualDate": e["actual_date"] or "",
                "actualTime": e["actual_time"] or "",
                "noPlan": bool(e["no_plan"]),
                "setAt": e["set_at"] or "",
            })

    plan_rows = conn.execute(
        f"""SELECT * FROM future_plans
            WHERE patient_id IN ({','.join('?' for _ in patient_by_id)})
            ORDER BY patient_id, template_index ASC""",
        list(patient_by_id),
    ).fetchall()
    for fp in plan_rows:
        patient_by_id[fp["patient_id"]].future_plans[str(fp["template_index"])] = {
            "date": fp["date"] or "",
            "time": fp["time"] or "",
            "treatmentType": fp["treatment_type"] or "",
        }


def completeness(patient: PatientRecord) -> dict:
    missing = []
    present = 0
    values = {
        "name": patient.name,
        "phone": patient.phone,
        "diagnosis": patient.diagnosis,
        "nosology": patient.nosology,
        "stage": patient.stage,
        "treatingDoctorUserId": patient.treating_doctor_user_id,
    }
    for field_name, label, severity in COMPLETENESS_FIELDS:
        if values.get(field_name):
            present += 1
        else:
            missing.append({"field": field_name, "label": label, "severity": severity})
    return {
        "score": round(present / len(COMPLETENESS_FIELDS) * 100),
        "present": present,
        "total": len(COMPLETENESS_FIELDS),
        "missing": missing,
        "formula": "равные доли 6 полей: ФИО, телефон, диагноз, Профиль клиентского пути, текущий этап, лечащий врач",
    }


def _patient_findings(patient: PatientRecord, *, today: date, can_write: bool, can_change_doctor: bool) -> list[dict]:
    issues = []
    comp = completeness(patient)
    missing_by_field = {m["field"]: m for m in comp["missing"]}

    if "name" in missing_by_field:
        issues.append(_finding(
            patient=patient, code="missing_required_field", category="completeness", severity="critical",
            title="Не заполнено ФИО", description="ФИО нужно для идентификации регистрационной карточки.",
            field_name="name", details={"field_label": "ФИО"},
            suggested_action=_action("Заполнить ФИО во вкладке «Данные»", "data", can_write),
        ))
    if "stage" in missing_by_field:
        issues.append(_finding(
            patient=patient, code="missing_stage", category="completeness", severity="critical",
            title="Не указан текущий этап", description="Маршрут лечения не может быть построен без текущего этапа.",
            field_name="stage", details={"field_label": "Текущий этап"},
            suggested_action=_action("Проверить этап во вкладке «События и даты»", "events", can_write),
        ))
    if "diagnosis" in missing_by_field:
        issues.append(_finding(
            patient=patient, code="missing_diagnosis", category="completeness", severity="critical",
            title="Не указан диагноз", description="Диагноз обязателен для новых записей и нужен для корректной карточки.",
            field_name="diagnosis", details={"field_label": "Диагноз"},
            suggested_action=_action("Заполнить диагноз во вкладке «Данные»", "data", can_write),
        ))
    if "phone" in missing_by_field:
        issues.append(_finding(
            patient=patient, code="missing_required_field", category="completeness", severity="warning",
            title="Не заполнен телефон", description="Телефон нужен для полноты регистрационной карточки.",
            field_name="phone", details={"field_label": "Телефон"},
            suggested_action=_action("Уточнить телефон во вкладке «Данные»", "data", can_write),
        ))
    if "nosology" in missing_by_field:
        issues.append(_finding(
            patient=patient, code="missing_client_path_profile", category="completeness", severity="warning",
            title="Не указан профиль клиентского пути",
            description="Профиль клиентского пути используется для регламентных сроков и чек-листов.",
            field_name="nosology", details={"field_label": "Профиль клиентского пути"},
            suggested_action=_action("Выбрать профиль во вкладке «Данные»", "data", can_write),
        ))
    if "treatingDoctorUserId" in missing_by_field:
        issues.append(_finding(
            patient=patient, code="missing_treating_doctor", category="assignment", severity="warning",
            title="Не назначен лечащий врач",
            description="Пациент не сопоставлен с учётной записью лечащего врача; для врачей и заведующих такая запись не входит в scope.",
            field_name="treatingDoctorUserId", details={"unresolved": True},
            suggested_action=_action(
                "Перейти к существующему сопоставлению" if can_change_doctor else "Обратиться к Разработчику для сопоставления",
                "structure" if can_change_doctor else "data",
                can_change_doctor,
                {"workflow": "unresolved_treating_doctor"},
            ),
        ))

    if patient.phone and not phone_is_valid(patient.phone):
        issues.append(_finding(
            patient=patient, code="invalid_phone", category="format", severity="warning",
            title="Телефон выглядит некорректно",
            description="Номер можно записывать с пробелами, скобками или дефисами, но он должен содержать 10–15 цифр без букв.",
            field_name="phone", details={"normalized_digits": len(_normalize_phone(patient.phone))},
            suggested_action=_action("Проверить телефон во вкладке «Данные»", "data", can_write),
        ))

    arrival_date = _safe_date(patient.arrival)
    prev_stage_index = -1
    prev_entered = None
    last_stage = patient.history[-1]["stage"] if patient.history else None
    if last_stage is not None and patient.stage and last_stage != patient.stage:
        issues.append(_finding(
            patient=patient, code="stage_sequence_violation", category="stage", severity="critical",
            title="Текущий этап не совпадает с историей",
            description="Последняя запись маршрута отличается от текущего этапа пациента.",
            field_name="stage", details={"current_stage": patient.stage, "last_history_stage": last_stage},
            suggested_action=_action("Проверить маршрут во вкладке «События и даты»", "events", can_write),
        ))
    for occ_index, occ in enumerate(patient.history):
        stage = occ["stage"]
        entered = _safe_date(occ["enteredAt"])
        if stage not in STAGE_INDEX:
            issues.append(_finding(
                patient=patient, code="stage_sequence_violation", category="stage", severity="critical",
                title="В истории есть неизвестный этап",
                description="Событие маршрута привязано к этапу, которого нет в текущей модели.",
                field_name="history", details={"occurrence_index": occ_index, "stage": stage},
                suggested_action=_action("Проверить маршрут во вкладке «События и даты»", "events", can_write),
            ))
        else:
            current_stage_index = STAGE_INDEX[stage]
            if current_stage_index < prev_stage_index:
                issues.append(_finding(
                    patient=patient, code="stage_sequence_violation", category="stage", severity="warning",
                    title="Этапы идут не по маршруту",
                    description="Более ранний этап записан после более позднего.",
                    field_name="history", details={"occurrence_index": occ_index, "stage": stage},
                    suggested_action=_action("Проверить последовательность этапов", "events", can_write),
                ))
            prev_stage_index = max(prev_stage_index, current_stage_index)
        if entered and prev_entered and entered < prev_entered:
            issues.append(_finding(
                patient=patient, code="stage_sequence_violation", category="stage", severity="critical",
                title="Дата начала этапа раньше предыдущего",
                description="Дата начала этапа в истории нарушает хронологию маршрута.",
                field_name="history", details={"occurrence_index": occ_index},
                suggested_action=_action("Проверить даты этапов", "events", can_write),
            ))
        if entered:
            prev_entered = entered

        for event_index, event in enumerate(occ["events"]):
            actual = _safe_date(event.get("actualDate"))
            planned = _safe_date(event.get("date"))
            if event.get("actualDate") and actual is None:
                issues.append(_finding(
                    patient=patient, code="actual_date_in_future", category="dates", severity="critical",
                    title="Фактическая дата некорректна",
                    description="Фактическая дата должна быть календарной датой в формате ГГГГ-ММ-ДД.",
                    field_name="actualDate", details={"occurrence_index": occ_index, "event_index": event_index},
                    suggested_action=_action("Исправить дату во вкладке «События и даты»", "events", can_write),
                ))
            elif actual and actual > today:
                issues.append(_finding(
                    patient=patient, code="actual_date_in_future", category="dates", severity="critical",
                    title="Фактическая дата в будущем",
                    description="Фактическое выполнение не может находиться позже сегодняшней даты.",
                    field_name="actualDate", details={"occurrence_index": occ_index, "event_index": event_index},
                    suggested_action=_action("Проверить фактическую дату", "events", can_write),
                ))
            if actual and arrival_date and actual < arrival_date:
                issues.append(_finding(
                    patient=patient, code="stage_sequence_violation", category="dates", severity="warning",
                    title="Фактическая дата раньше создания карточки",
                    description="Фактическое выполнение предшествует созданию пациента в системе.",
                    field_name="actualDate", details={"occurrence_index": occ_index, "event_index": event_index},
                    suggested_action=_action("Проверить фактическую дату", "events", can_write),
                ))
            if actual and entered and actual < entered:
                issues.append(_finding(
                    patient=patient, code="stage_sequence_violation", category="dates", severity="warning",
                    title="Фактическая дата раньше начала этапа",
                    description="Фактическое выполнение предшествует началу этапа.",
                    field_name="actualDate", details={"occurrence_index": occ_index, "event_index": event_index},
                    suggested_action=_action("Проверить даты этапа", "events", can_write),
                ))
            if event.get("date") and planned is None:
                issues.append(_finding(
                    patient=patient, code="planned_date_in_past", category="dates", severity="warning",
                    title="Плановая дата некорректна",
                    description="Плановая дата должна быть календарной датой в формате ГГГГ-ММ-ДД.",
                    field_name="date", details={"occurrence_index": occ_index, "event_index": event_index},
                    suggested_action=_action("Исправить плановую дату", "events", can_write),
                ))
            elif planned and planned < today and not actual and not event.get("noPlan"):
                issues.append(_finding(
                    patient=patient, code="overdue_event", category="dates", severity="warning",
                    title="Плановое событие просрочено",
                    description="Плановая дата прошла, но фактическое выполнение не отмечено.",
                    field_name="date", details={"occurrence_index": occ_index, "event_index": event_index, "days_overdue": (today - planned).days},
                    suggested_action=_action("Отметить факт или актуализировать план", "events", can_write),
                ))

    for index, plan in patient.future_plans.items():
        planned = _safe_date(plan.get("date"))
        if plan.get("date") and planned is None:
            issues.append(_finding(
                patient=patient, code="planned_date_in_past", category="dates", severity="warning",
                title="Дата будущего плана некорректна",
                description="Дата будущего плана должна быть календарной датой в формате ГГГГ-ММ-ДД.",
                field_name="futurePlans", details={"template_index": index},
                suggested_action=_action("Исправить будущий план", "plans", can_write),
            ))
        elif planned and planned < today and patient.stage != "completed":
            issues.append(_finding(
                patient=patient, code="planned_date_in_past", category="dates", severity="warning",
                title="Будущий план в прошлом",
                description="Предварительная дата уже прошла и требует ручной актуализации.",
                field_name="futurePlans", details={"template_index": index, "days_overdue": (today - planned).days},
                suggested_action=_action("Актуализировать будущий план", "plans", can_write),
            ))

    latest = patient.history[-1] if patient.history else None
    if latest:
        required = REQUIRED_CHECKLISTS.get(patient.nosology, {}).get(latest["stage"], [])
        if required:
            checklist = latest.get("checklist") or {}
            missing = [item for item in required if checklist.get(item) is not True]
            if missing:
                issues.append(_finding(
                    patient=patient, code="incomplete_required_checklist", category="checklist", severity="warning",
                    title="Обязательный чек-лист не завершён",
                    description="Есть обязательные пункты чек-листа, которые не отмечены выполненными.",
                    field_name="checklist",
                    details={"missing_count": len(missing), "total_required": len(required), "sample_missing": missing[:3]},
                    suggested_action=_action("Открыть чек-лист пациента", "checklist", can_write),
                ))
    return issues


def _duplicate_groups(patients: list[PatientRecord]) -> list[dict]:
    buckets: dict[tuple[str, str], list[PatientRecord]] = defaultdict(list)
    for p in patients:
        phone = _normalize_phone(p.phone)
        name = _normalize_name(p.name)
        diagnosis = " ".join(p.diagnosis.strip().lower().split())
        nosology = p.nosology.strip().lower()
        if phone and len(phone) >= 10:
            buckets[("phone", phone)].append(p)
        if name and p.age:
            buckets[("name_age", f"{name}|{p.age}")].append(p)
        if name and diagnosis:
            buckets[("name_diagnosis", f"{name}|{diagnosis}")].append(p)
        if name and nosology:
            buckets[("name_nosology", f"{name}|{nosology}")].append(p)

    reason_labels = {
        "phone": "Совпадает телефон",
        "name_age": "Совпадают ФИО и возраст",
        "name_diagnosis": "Совпадают ФИО и диагноз",
        "name_nosology": "Совпадают ФИО и Профиль клиентского пути",
    }
    seen = set()
    groups = []
    for (kind, _key), members in buckets.items():
        unique = {p.id: p for p in members}
        if len(unique) < 2:
            continue
        ids = tuple(sorted(unique))
        signature = (kind, ids)
        if signature in seen:
            continue
        seen.add(signature)
        patient_refs = [
            {"patient_id": p.id, "patient_name": p.name, "completeness": completeness(p)["score"]}
            for p in sorted(unique.values(), key=lambda x: x.name)
        ]
        digest = hashlib.sha256(("|".join((kind, *ids))).encode("utf-8")).hexdigest()[:12]
        groups.append({
            "id": f"{kind}:{digest}",
            "reason_code": kind,
            "reason": reason_labels[kind],
            "explanation": "Совпадение не означает, что записи являются дублями. Требуется ручная проверка.",
            "patient_ids": list(ids),
            "patients": patient_refs,
        })
    groups.sort(key=lambda g: (g["reason"], [p["patient_name"] for p in g["patients"]]))
    return groups


def evaluate(conn: sqlite3.Connection, user: policy.CurrentUser, *, today: date | None = None) -> dict:
    today = today or _today()
    patients = _load_scoped_patients(conn, user)
    can_write = _can_write_visible_patients(user)
    can_change_doctor = policy.can_change_treating_doctor(user)

    issues: list[dict] = []
    completeness_by_patient: dict[str, dict] = {}
    for patient in patients:
        comp = completeness(patient)
        completeness_by_patient[patient.id] = comp
        issues.extend(_patient_findings(
            patient,
            today=today,
            can_write=can_write,
            can_change_doctor=can_change_doctor,
        ))

    duplicate_groups = _duplicate_groups(patients)
    duplicate_by_patient: dict[str, list[dict]] = defaultdict(list)
    for group in duplicate_groups:
        for patient_id in group["patient_ids"]:
            duplicate_by_patient[patient_id].append(group)

    patient_by_id = {p.id: p for p in patients}
    for patient_id, groups in duplicate_by_patient.items():
        patient = patient_by_id[patient_id]
        issues.append(_finding(
            patient=patient, code="possible_duplicate", category="duplicates", severity="info",
            title="Возможный дубль",
            description="Внутри доступной области видимости есть записи с совпадающими объяснимыми признаками.",
            field_name=None,
            details={"group_ids": [g["id"] for g in groups], "reasons": sorted({g["reason"] for g in groups})},
            suggested_action=_action("Сравнить записи вручную", "data", False),
        ))

    issues.sort(key=lambda i: (SEVERITY_RANK[i["severity"]], i["category"], i["code"], patient_by_id[i["patient_id"]].name))
    issues_by_patient: dict[str, list[dict]] = defaultdict(list)
    for issue in issues:
        issues_by_patient[issue["patient_id"]].append(issue)

    patient_summaries = []
    for patient in patients:
        p_issues = issues_by_patient.get(patient.id, [])
        patient_summaries.append({
            "patient_id": patient.id,
            "patient_name": patient.name,
            "treating_doctor_name": patient.treating_doctor_name or "Не назначен",
            "department_names": patient.department_names,
            "completeness": completeness_by_patient[patient.id],
            "issue_count": len(p_issues),
            "max_severity": p_issues[0]["severity"] if p_issues else None,
            "top_issues": [
                {"code": issue["code"], "severity": issue["severity"], "title": issue["title"]}
                for issue in p_issues[:3]
            ],
            "can_edit": can_write,
        })
    patient_summaries.sort(key=lambda p: (
        p["issue_count"] == 0,
        SEVERITY_RANK.get(p["max_severity"] or "info", 3),
        p["completeness"]["score"],
        p["patient_name"],
    ))

    summary = {
        "total_patients": len(patients),
        "patients_with_issues": sum(1 for p in patient_summaries if p["issue_count"] > 0),
        "total_issues": len(issues),
        "critical_count": sum(1 for i in issues if i["severity"] == "critical"),
        "warning_count": sum(1 for i in issues if i["severity"] == "warning"),
        "info_count": sum(1 for i in issues if i["severity"] == "info"),
        "possible_duplicate_groups": len(duplicate_groups),
        "unresolved_patients": sum(1 for p in patients if not p.treating_doctor_user_id),
        "average_completeness": round(sum(p["completeness"]["score"] for p in patient_summaries) / len(patient_summaries)) if patient_summaries else 100,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "summary": summary,
        "issues": issues,
        "patients": patient_summaries,
        "duplicate_groups": duplicate_groups,
        "rules": QUALITY_RULES,
    }


def filter_result(
    result: dict,
    *,
    severity: str | None = None,
    category: str | None = None,
    code: str | None = None,
    patient_id: str | None = None,
    treating_doctor: str | None = None,
    department: str | None = None,
    search: str | None = None,
    sort: str = "severity",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    patients_by_id = {p["patient_id"]: p for p in result["patients"]}
    issues = list(result["issues"])
    if severity:
        issues = [i for i in issues if i["severity"] == severity]
    if category:
        issues = [i for i in issues if i["category"] == category]
    if code:
        issues = [i for i in issues if i["code"] == code]
    if patient_id:
        issues = [i for i in issues if i["patient_id"] == patient_id]
    if treating_doctor:
        needle = treating_doctor.lower()
        issues = [i for i in issues if needle in patients_by_id[i["patient_id"]]["treating_doctor_name"].lower()]
    if department:
        issues = [
            i for i in issues
            if department in patients_by_id[i["patient_id"]]["department_names"]
        ]
    if search:
        needle = search.lower()
        issues = [i for i in issues if needle in patients_by_id[i["patient_id"]]["patient_name"].lower()]

    patient_ids = {i["patient_id"] for i in issues}
    if not any([severity, category, code, patient_id, treating_doctor, department, search]):
        filtered_patients = list(result["patients"])
    else:
        filtered_patients = [p for p in result["patients"] if p["patient_id"] in patient_ids]

    if sort == "issues":
        filtered_patients.sort(key=lambda p: (-p["issue_count"], p["patient_name"]))
    elif sort == "completeness":
        filtered_patients.sort(key=lambda p: (p["completeness"]["score"], p["patient_name"]))
    else:
        filtered_patients.sort(key=lambda p: (
            SEVERITY_RANK.get(p["max_severity"] or "info", 3),
            p["completeness"]["score"],
            p["patient_name"],
        ))
    total = len(issues)
    return {
        "issues": issues[offset:offset + limit],
        "patients": filtered_patients,
        "duplicate_groups": [
            g for g in result["duplicate_groups"]
            if any(pid in {p["patient_id"] for p in filtered_patients} for pid in g["patient_ids"])
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
        "rules": result["rules"],
    }
