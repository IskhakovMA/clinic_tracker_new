"""Personal oncology-consilium appointment scheduling."""
from __future__ import annotations

import sqlite3
import uuid
import json
from datetime import datetime, timedelta
from pathlib import Path

from .. import policy
from ..db import in_chunks, now_iso
from ..models import ConsiliumAppointmentIn


GROUPS = (
    ("general", "Общеонкологическая"),
    ("thoracic", "Торакальная"),
    ("abdominal", "Абдоминальная"),
    ("colorectal", "Колопроктологическая"),
    ("breast", "Маммологическая"),
    ("urology", "Онкоурологическая"),
    ("gynecology", "Онкогинекологическая"),
    ("head_neck", "Опухоли головы и шеи"),
)
GROUP_LABELS = dict(GROUPS)
TIME_SLOTS = tuple(f"{hour:02d}:{minute:02d}" for hour in range(9, 17) for minute in (0, 30))
STATUS_LABELS = {
    "recorded": "Записан",
    "in_review": "На рассмотрении",
    "needs_revision": "На доработке",
    "revised": "Возвращен с доработки",
    "signed": "Подписан",
}
TREATMENT_LABELS = {
    "radiotherapy": "Лучевая терапия",
    "chemotherapy": "Химиотерапевтическое лечение",
    "surgery": "Хирургическое лечение",
    "active_observation": "Активное наблюдение",
    "dispensary_observation": "Диспансерное наблюдение",
}
class ConsiliumStateError(ValueError):
    """The requested transition is not allowed from the record's current state."""


# Хранимый status: recorded → in_review → signed. Возврат на доработку
# оставляет status = 'recorded' с revision_comment; пока врач не отправил
# исправления (revision_submitted_at IS NULL), рассмотрение начать нельзя.
_AWAITING_CHAIR = "status = 'recorded' AND (revision_comment IS NULL OR revision_submitted_at IS NOT NULL)"


def public_status(row: sqlite3.Row) -> str:
    if row["status"] == "signed":
        return "signed"
    if row["status"] == "in_review":
        return "in_review"
    if row["revision_comment"]:
        return "revised" if row["revision_submitted_at"] else "needs_revision"
    return "recorded"


TREATMENT_SCHEMES = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "treatment_schemes.json").read_text(encoding="utf-8")
)


def metadata() -> dict:
    return {
        "groups": [{"value": value, "label": label} for value, label in GROUPS],
        "timeSlots": list(TIME_SLOTS),
        "treatmentSchemes": TREATMENT_SCHEMES,
    }


def _to_dict(row: sqlite3.Row, *, favorite: bool = False) -> dict:
    status = public_status(row)
    return {
        "id": row["id"],
        "patientId": row["patient_id"],
        "patientName": row["patient_name"],
        "scheduledDate": row["scheduled_date"],
        "scheduledTime": row["scheduled_time"],
        "consiliumGroup": row["consilium_group"],
        "consiliumGroupLabel": GROUP_LABELS.get(row["consilium_group"], row["consilium_group"]),
        "hasBenefit": bool(row["has_benefit"]),
        "policyNumber": row["policy_number"] or row["patient_policy_number"] or "",
        "diagnosisLocalization": row["diagnosis_localization"] or "",
        # Подписанное решение хранит лечащего врача на момент подписи; текущий
        # врач пациента — отдельным полем, без подмены исторических данных.
        "treatingDoctorName": (
            row["signed_treating_doctor_name"]
            if row["status"] == "signed" and row["signed_treating_doctor_name"]
            else row["current_treating_doctor_name"]
        ),
        "currentTreatingDoctorName": row["current_treating_doctor_name"],
        "primaryDiagnosis": row["primary_diagnosis"],
        "additionalDiagnosis": row["additional_diagnosis"] or "",
        "comment": row["comment"] or "",
        "additionalParticipant": row["additional_participant"] or "",
        "needsSurgeon": bool(row["needs_surgeon"]),
        "needsRadiotherapist": bool(row["needs_radiotherapist"]),
        "status": status,
        "statusLabel": STATUS_LABELS.get(status, status),
        "treatmentType": row["treatment_type"] or "",
        "treatmentTypeLabel": TREATMENT_LABELS.get(row["treatment_type"], "") if row["treatment_type"] else "",
        "treatmentScheme": row["treatment_scheme"] or "",
        "treatmentSchemeCode": row["treatment_scheme_code"] or "",
        "goc": bool(row["goc"]),
        "goc65Plus": bool(row["goc_65_plus"]),
        "uvRuv": bool(row["uv_ruv"]),
        "revisionComment": row["revision_comment"] or "",
        "revisionCount": row["revision_count"],
        "reviewStartedAt": row["review_started_at"],
        "signedAt": row["signed_at"],
        "referralCompleted": bool(row["referral_completed_at"]),
        "referralCompletedAt": row["referral_completed_at"],
        "archived": bool(row["archived_at"]),
        "archivedAt": row["archived_at"],
        "isFavorite": favorite,
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


# Лечащий врач — из актуальной карточки пациента (как в store.patient_to_dict),
# а не автор записи: запись может создать заведующий, пациента могут передать
# другому врачу. a.treating_doctor_name — снимок на момент записи, запасной.
_SELECT = """
    SELECT a.*, patients.name AS patient_name,
           patients.policy_number AS patient_policy_number,
           COALESCE(treating_doctor.full_name, NULLIF(patients.doctor, ''), a.treating_doctor_name)
               AS current_treating_doctor_name
    FROM oncology_consilium_appointments a
    JOIN patients ON patients.id = a.patient_id
    LEFT JOIN users treating_doctor ON treating_doctor.id = patients.treating_doctor_user_id
"""


def _current_treating_doctor_name(conn: sqlite3.Connection, patient_id: str) -> str:
    row = conn.execute(
        """SELECT COALESCE(treating_doctor.full_name, NULLIF(patients.doctor, ''), '') AS name
           FROM patients
           LEFT JOIN users treating_doctor ON treating_doctor.id = patients.treating_doctor_user_id
           WHERE patients.id = ?""",
        (patient_id,),
    ).fetchone()
    return row["name"] if row else ""


def list_for_user(
    conn: sqlite3.Connection, user: policy.CurrentUser, *, archived: bool = False
) -> list[dict]:
    if user.role_id == policy.ROLE_CONSILIUM_CHAIR:
        rows = conn.execute(
            _SELECT + " ORDER BY a.scheduled_date ASC, a.scheduled_time ASC, a.created_at ASC"
        ).fetchall()
        favorite_ids = {
            row["patient_id"] for row in conn.execute(
                "SELECT patient_id FROM consilium_chair_favorites WHERE user_id = ?",
                (user.id,),
            ).fetchall()
        }
        return [_to_dict(row, favorite=row["patient_id"] in favorite_ids) for row in rows]
    condition, params = policy.patient_scope_filter(user)
    visibility_clause = "AND a.status = 'signed' " if archived else "AND a.archived_at IS NULL "
    rows = conn.execute(
        _SELECT + f" WHERE a.created_by_user_id = ? AND {condition} "
        + visibility_clause
        + "ORDER BY a.scheduled_date ASC, a.scheduled_time ASC, a.created_at ASC",
        [user.id, *params],
    ).fetchall()
    return [_to_dict(row) for row in rows]


def booked_patient_ids(conn: sqlite3.Connection, user: policy.CurrentUser) -> list[str]:
    """Return visible patients that cannot be booked a second time."""
    condition, params = policy.patient_scope_filter(user)
    rows = conn.execute(
        """SELECT DISTINCT a.patient_id
           FROM oncology_consilium_appointments a
           JOIN patients ON patients.id = a.patient_id
           WHERE """ + condition,  # nosec B608 - scope condition is generated by policy; values are bound.
        params,
    ).fetchall()
    return [row["patient_id"] for row in rows]


def set_archived(
    conn: sqlite3.Connection,
    user: policy.CurrentUser,
    appointment_id: str,
    archived: bool,
) -> dict | None:
    existing = get_owned(conn, user, appointment_id)
    if existing is None:
        return None
    if existing["status"] != "signed":
        raise ValueError("Only signed consilium appointments can be archived")
    now = now_iso()
    with conn:
        conn.execute(
            """UPDATE oncology_consilium_appointments
               SET archived_at = ?, archived_by_user_id = ?, updated_at = ?
               WHERE id = ? AND created_by_user_id = ?""",
            (now if archived else None, user.id if archived else None, now, appointment_id, user.id),
        )
    return get_owned(conn, user, appointment_id)


def get_for_management(conn: sqlite3.Connection, appointment_id: str) -> dict | None:
    row = conn.execute(_SELECT + " WHERE a.id = ?", (appointment_id,)).fetchone()
    return _to_dict(row) if row else None


def get_owned(conn: sqlite3.Connection, user: policy.CurrentUser, appointment_id: str) -> dict | None:
    condition, params = policy.patient_scope_filter(user)
    row = conn.execute(
        _SELECT + f" WHERE a.id = ? AND a.created_by_user_id = ? AND {condition}",
        [appointment_id, user.id, *params],
    ).fetchone()
    return _to_dict(row) if row else None


def delete_owned(
    conn: sqlite3.Connection, user: policy.CurrentUser, appointment_id: str
) -> dict | None:
    existing = get_owned(conn, user, appointment_id)
    if existing is None:
        return None
    with conn:
        cursor = conn.execute(
            "DELETE FROM oncology_consilium_appointments "
            "WHERE id = ? AND created_by_user_id = ? AND status = 'recorded'",
            (appointment_id, user.id),
        )
    if cursor.rowcount == 0:
        raise ConsiliumStateError(
            "Подписанная запись недоступна для удаления"
            if existing["status"] == "signed"
            else "Запись на рассмотрении у председателя недоступна для удаления"
        )
    return existing


def create(
    conn: sqlite3.Connection, user: policy.CurrentUser, body: ConsiliumAppointmentIn
) -> dict:
    appointment_id = uuid.uuid4().hex
    now = now_iso()
    patient = conn.execute(
        "SELECT policy_number, diagnosis FROM patients WHERE id = ?", (body.patientId,)
    ).fetchone()
    if patient is None:
        raise ValueError("Пациент не найден")
    _require_group_for_patient(patient["diagnosis"], body.consiliumGroup)
    _require_patient_not_booked(conn, body.patientId)
    policy_number = body.policyNumber or (patient["policy_number"] if patient else "") or None
    _require_available_slot(conn, body.scheduledDate, body.scheduledTime, body.consiliumGroup)
    try:
        with conn:
            conn.execute(
                """INSERT INTO oncology_consilium_appointments (
                   id, patient_id, created_by_user_id, scheduled_date, scheduled_time,
                   consilium_group, has_benefit, treating_doctor_name, policy_number, diagnosis_localization,
                   primary_diagnosis, additional_diagnosis, comment,
                   additional_participant, needs_surgeon, needs_radiotherapist, uv_ruv,
                   created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    appointment_id, body.patientId, user.id, body.scheduledDate,
                    body.scheduledTime, body.consiliumGroup, int(body.hasBenefit),
                    _current_treating_doctor_name(conn, body.patientId), policy_number,
                    body.diagnosisLocalization or None,
                    body.primaryDiagnosis, body.additionalDiagnosis or None,
                    body.comment or None, body.additionalParticipant or None,
                    int(body.needsSurgeon), int(body.needsRadiotherapist), int(body.uvRuv), now, now,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError(_integrity_error_message(exc)) from exc
    return get_owned(conn, user, appointment_id)


def _transition(conn: sqlite3.Connection, appointment_id: str, sql: str, params: tuple, refusal: str) -> dict | None:
    """Apply a guarded UPDATE; the WHERE clause is the transition rule.

    The status check and the write happen in one statement, so a concurrent
    request cannot slip between them. Nothing is written when it is refused.
    """
    if get_for_management(conn, appointment_id) is None:
        return None
    with conn:
        cursor = conn.execute(sql, params)
    if cursor.rowcount == 0:
        raise ConsiliumStateError(refusal)
    return get_for_management(conn, appointment_id)


def start_review(conn: sqlite3.Connection, appointment_id: str) -> dict | None:
    now = now_iso()
    return _transition(
        conn, appointment_id,
        f"""UPDATE oncology_consilium_appointments
            SET status = 'in_review', review_started_at = ?, updated_at = ?
            WHERE id = ? AND {_AWAITING_CHAIR}""",
        (now, now, appointment_id),
        "Рассмотрение можно начать только для записи, ожидающей председателя",
    )


def sign(
    conn: sqlite3.Connection,
    user: policy.CurrentUser,
    appointment_id: str,
    treatment_type: str,
    treatment_scheme: str,
    treatment_scheme_code: str,
    goc: bool,
    goc_65_plus: bool,
) -> dict | None:
    now = now_iso()
    return _transition(
        conn, appointment_id,
        """UPDATE oncology_consilium_appointments
           SET status = 'signed', treatment_type = ?, treatment_scheme = ?, treatment_scheme_code = ?,
               goc = ?, goc_65_plus = ?,
               revision_comment = NULL, signed_at = ?, signed_by_user_id = ?, updated_at = ?,
               signed_treating_doctor_user_id = (
                   SELECT patients.treating_doctor_user_id FROM patients
                   WHERE patients.id = oncology_consilium_appointments.patient_id),
               signed_treating_doctor_name = (
                   SELECT COALESCE(treating_doctor.full_name, NULLIF(patients.doctor, ''),
                                   oncology_consilium_appointments.treating_doctor_name)
                   FROM patients
                   LEFT JOIN users treating_doctor ON treating_doctor.id = patients.treating_doctor_user_id
                   WHERE patients.id = oncology_consilium_appointments.patient_id)
           WHERE id = ? AND status = 'in_review'""",
        (treatment_type, treatment_scheme or None, treatment_scheme_code or None,
         int(goc), int(goc_65_plus), now, user.id, now, appointment_id),
        "Подписать можно только запись, рассмотрение которой начато",
    )


class ConsiliumCommentError(ValueError):
    """The revision comment repeats the previous one."""


def return_for_revision(
    conn: sqlite3.Connection, appointment_id: str, comment: str
) -> dict | None:
    """Return a record under review to its author with a new comment.

    Only from in_review: the chair returns what they are reviewing, and a
    resubmitted record has to be reviewed again before a second return. The
    comment is required (model) and must differ from the previous one, so
    each round leaves its own instruction for the doctor.
    """
    existing = conn.execute(
        "SELECT status, revision_comment FROM oncology_consilium_appointments WHERE id = ?",
        (appointment_id,),
    ).fetchone()
    if existing is not None and existing["status"] == "in_review" and existing["revision_comment"] == comment:
        raise ConsiliumCommentError("Комментарий должен отличаться от предыдущего возврата")
    return _transition(
        conn, appointment_id,
        """UPDATE oncology_consilium_appointments
           SET status = 'recorded', revision_comment = ?, revision_submitted_at = NULL,
               review_started_at = NULL, revision_count = revision_count + 1, updated_at = ?
           WHERE id = ? AND status = 'in_review' AND archived_at IS NULL""",
        (comment, now_iso(), appointment_id),
        "Вернуть на доработку можно только запись, рассмотрение которой начато",
    )


def analytics_summary(
    conn: sqlite3.Connection, date_from: str | None = None, date_to: str | None = None
) -> dict:
    rows = conn.execute(
        """SELECT * FROM oncology_consilium_appointments
           WHERE (? IS NULL OR scheduled_date >= ?) AND (? IS NULL OR scheduled_date <= ?)""",
        (date_from, date_from, date_to, date_to),
    ).fetchall()
    total = len(rows)
    signed = sum(row["status"] == "signed" for row in rows)
    in_review = sum(row["status"] == "in_review" for row in rows)
    returned = sum(public_status(row) == "needs_revision" for row in rows)
    specialist = sum(bool(row["needs_surgeon"] or row["needs_radiotherapist"]) for row in rows)
    treatment_counts = {code: 0 for code in TREATMENT_LABELS}
    scheme_counts: dict[tuple[str, str], int] = {}
    for row in rows:
        if row["treatment_type"] in treatment_counts:
            treatment_counts[row["treatment_type"]] += 1
        if row["status"] == "signed" and row["treatment_type"] == "chemotherapy" and row["treatment_scheme"]:
            key = (row["treatment_scheme_code"] or "", row["treatment_scheme"])
            scheme_counts[key] = scheme_counts.get(key, 0) + 1
    active_days = len({row["scheduled_date"] for row in rows})
    capacity = active_days * len(GROUPS) * len(TIME_SLOTS)
    occupied = len({(row["scheduled_date"], row["scheduled_time"], row["consilium_group"]) for row in rows})
    return {
        "summary": {
            "total": total,
            "signed": signed,
            "inReview": in_review,
            "awaiting": total - signed,
            "returned": returned,
            "needsSpecialists": specialist,
        },
        "treatments": [
            {"code": code, "label": label, "count": treatment_counts[code]}
            for code, label in TREATMENT_LABELS.items()
        ],
        "schemes": [
            {"code": code, "label": name, "count": count}
            for (code, name), count in sorted(
                scheme_counts.items(), key=lambda item: (-item[1], item[0][0], item[0][1])
            )
        ],
        "schedule": {
            "activeDays": active_days,
            "occupiedSlots": occupied,
            "capacitySlots": capacity,
            "loadPercent": round((occupied / capacity * 100), 1) if capacity else 0,
        },
    }


def update(
    conn: sqlite3.Connection,
    user: policy.CurrentUser,
    appointment_id: str,
    body: ConsiliumAppointmentIn,
) -> dict | None:
    existing = conn.execute(
        """SELECT id, patient_id, status, scheduled_date, scheduled_time, consilium_group
           FROM oncology_consilium_appointments WHERE id = ? AND created_by_user_id = ?""",
        (appointment_id, user.id),
    ).fetchone()
    if existing is None:
        return None
    if existing["status"] != "recorded":
        raise ConsiliumStateError(
            "Подписанная запись недоступна для редактирования"
            if existing["status"] == "signed"
            else "Запись на рассмотрении у председателя недоступна для редактирования"
        )
    if body.patientId != existing["patient_id"]:
        raise ValueError("При переносе нельзя изменить пациента")
    if body.consiliumGroup != existing["consilium_group"]:
        raise ValueError("Перенос возможен только внутри прежней консилиумной группы")
    patient = conn.execute(
        "SELECT diagnosis FROM patients WHERE id = ?", (existing["patient_id"],)
    ).fetchone()
    if patient is None:
        raise ValueError("Пациент не найден")
    _require_group_for_patient(patient["diagnosis"], body.consiliumGroup)
    same_slot = (
        existing["scheduled_date"] == body.scheduledDate
        and existing["scheduled_time"] == body.scheduledTime
        and existing["consilium_group"] == body.consiliumGroup
    )
    if not same_slot:
        _require_available_slot(
            conn, body.scheduledDate, body.scheduledTime, body.consiliumGroup,
            exclude_appointment_id=appointment_id,
        )
    now = now_iso()
    try:
        with conn:
            cursor = conn.execute(
                """UPDATE oncology_consilium_appointments
               SET scheduled_date = ?, scheduled_time = ?,
                   has_benefit = ?, treating_doctor_name = ?, policy_number = ?, diagnosis_localization = ?,
                   primary_diagnosis = ?, additional_diagnosis = ?, comment = ?,
                   additional_participant = ?, needs_surgeon = ?,
                   needs_radiotherapist = ?, uv_ruv = ?,
                   revision_submitted_at = CASE
                       WHEN revision_comment IS NOT NULL THEN ? ELSE revision_submitted_at END,
                   updated_at = ?
               WHERE id = ? AND created_by_user_id = ? AND status = 'recorded'""",
                (
                body.scheduledDate, body.scheduledTime,
                int(body.hasBenefit), _current_treating_doctor_name(conn, existing["patient_id"]),
                body.policyNumber or None,
                body.diagnosisLocalization or None,
                body.primaryDiagnosis, body.additionalDiagnosis or None,
                body.comment or None, body.additionalParticipant or None,
                int(body.needsSurgeon), int(body.needsRadiotherapist), int(body.uvRuv), now, now,
                appointment_id, user.id,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError(_integrity_error_message(exc)) from exc
    if cursor.rowcount == 0:
        raise ConsiliumStateError("Запись на рассмотрении у председателя недоступна для редактирования")
    return get_owned(conn, user, appointment_id)


def group_for_diagnosis(value: str | None) -> str:
    raw = str(value or "").strip().upper()
    if len(raw) < 3 or raw[0] != "C" or not raw[1:3].isdigit():
        return "general"
    code = int(raw[1:3])
    if 0 <= code <= 14 or 30 <= code <= 32:
        return "head_neck"
    if 15 <= code <= 16 or 22 <= code <= 25:
        return "abdominal"
    if code == 34:
        return "thoracic"
    if 51 <= code <= 57:
        return "gynecology"
    if 60 <= code <= 68:
        return "urology"
    if code == 50:
        return "breast"
    if 17 <= code <= 20:
        return "colorectal"
    return "general"


def _require_group_for_patient(diagnosis: str | None, consilium_group: str) -> None:
    if group_for_diagnosis(diagnosis) != consilium_group:
        raise ValueError("Консилиумная группа должна соответствовать коду МКБ-10 из карточки пациента")


def _require_patient_not_booked(conn: sqlite3.Connection, patient_id: str) -> None:
    if conn.execute(
        "SELECT 1 FROM oncology_consilium_appointments WHERE patient_id = ? LIMIT 1",
        (patient_id,),
    ).fetchone():
        raise ValueError(
            "Пациент уже записан на онкологический консилиум. Используйте перенос существующей записи."
        )


def _integrity_error_message(exc: sqlite3.IntegrityError) -> str:
    text = str(exc)
    if "consilium_patient_already_booked" in text:
        return "Пациент уже записан на онкологический консилиум. Используйте перенос существующей записи."
    if "scheduled_date" in text and "scheduled_time" in text and "consilium_group" in text:
        return "Выбранный слот уже занят"
    return "Не удалось сохранить запись: нарушено ограничение уникальности"


def _require_available_slot(
    conn: sqlite3.Connection,
    scheduled_date: str,
    scheduled_time: str,
    consilium_group: str,
    *,
    exclude_appointment_id: str | None = None,
) -> None:
    if consilium_group not in GROUP_LABELS:
        raise ValueError("Выберите действующую консилиумную группу")
    configured = conn.execute("SELECT COUNT(*) AS c FROM consilium_schedule_slots").fetchone()["c"]
    if configured:
        slot = conn.execute(
            """SELECT id FROM consilium_schedule_slots
               WHERE scheduled_date = ? AND scheduled_time = ? AND consilium_group = ?""",
            (scheduled_date, scheduled_time, consilium_group),
        ).fetchone()
        if slot is None and not _is_current_week_default_slot(scheduled_date, scheduled_time, consilium_group):
            raise ValueError("Выбранный слот отсутствует в расписании председателя")
    occupied = conn.execute(
        """SELECT id FROM oncology_consilium_appointments
           WHERE scheduled_date = ? AND scheduled_time = ? AND consilium_group = ?
             AND (? IS NULL OR id != ?) LIMIT 1""",
        (scheduled_date, scheduled_time, consilium_group, exclude_appointment_id, exclude_appointment_id),
    ).fetchone()
    if occupied is not None:
        raise ValueError("Выбранный слот уже занят")


def _current_week_dates() -> list[str]:
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    return [(monday + timedelta(days=offset)).isoformat() for offset in range(7)]


def _is_current_week_default_slot(scheduled_date: str, scheduled_time: str, consilium_group: str) -> bool:
    if scheduled_date not in _current_week_dates() or consilium_group not in GROUP_LABELS:
        return False
    try:
        parsed = datetime.strptime(scheduled_time, "%H:%M")
    except ValueError:
        return False
    return parsed.hour in range(9, 17) and parsed.minute in (0, 15, 30, 45)


def _slot_appointment(row: sqlite3.Row, user: policy.CurrentUser, readable_ids: set[str]) -> dict | None:
    """Details of the appointment occupying a slot, or None.

    - the chair and the developer (schedule management) and the author of
      the record while the patient is in their scope: full slot details;
    - a department head, for a patient of their department (existing patient
      scope): only who, when, treating doctor and status — no diagnosis,
      policy number, patient or record ids, urgency mark;
    - everyone else: None, the slot is reported only as occupied.
    """
    is_mine = row["created_by_user_id"] == user.id
    in_scope = row["patient_id"] in readable_ids
    status = public_status(row)
    if not (policy.can_manage_consilium(user) or policy.can_decide_consilium(user) or (is_mine and in_scope)):
        if user.role_id == policy.ROLE_DEPARTMENT_HEAD and in_scope:
            return {
                "patientName": row["patient_name"],
                "treatingDoctorName": row["treating_doctor_name"],
                "status": status,
                "statusLabel": STATUS_LABELS.get(status, status),
                "isMine": False,
                "view": "department",
            }
        return None
    return {
        "id": row["id"],
        "patientId": row["patient_id"],
        "patientName": row["patient_name"],
        "primaryDiagnosis": row["primary_diagnosis"],
        "treatingDoctorName": row["treating_doctor_name"],
        "policyNumber": row["policy_number"],
        "status": status,
        "statusLabel": STATUS_LABELS.get(status, status),
        "uvRuv": bool(row["uv_ruv"]),
        "createdByUserId": row["created_by_user_id"],
        "isMine": is_mine,
        "view": "full",
    }


def _readable_patient_ids(conn: sqlite3.Connection, user: policy.CurrentUser, patient_ids: set[str]) -> set[str]:
    if not patient_ids:
        return set()
    condition, params = policy.patient_scope_filter(user)
    readable: set[str] = set()
    for chunk in in_chunks(conn, patient_ids, reserve=len(params) + 8):
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT patients.id FROM patients WHERE patients.id IN ({marks}) AND {condition}",  # nosec B608 - only "?" placeholders and the policy scope condition are interpolated; values are bound.
            [*chunk, *params],
        ).fetchall()
        readable.update(row["id"] for row in rows)
    return readable


def schedule_slots(conn: sqlite3.Connection, user: policy.CurrentUser) -> list[dict]:
    slot_rows = conn.execute(
        """SELECT * FROM consilium_schedule_slots
           ORDER BY scheduled_date ASC, scheduled_time ASC, consilium_group ASC"""
    ).fetchall()
    week_dates = _current_week_dates()
    appointment_rows = conn.execute(
        """SELECT a.id, a.patient_id, a.created_by_user_id, a.scheduled_date, a.scheduled_time,
                  a.consilium_group, a.primary_diagnosis,
                  COALESCE(
                      CASE WHEN a.status = 'signed' THEN a.signed_treating_doctor_name END,
                      treating_doctor.full_name, NULLIF(patients.doctor, ''), a.treating_doctor_name
                  ) AS treating_doctor_name,
                  COALESCE(a.policy_number, patients.policy_number, '') AS policy_number, a.status,
                  a.uv_ruv, a.revision_comment, a.revision_submitted_at, patients.name AS patient_name
           FROM oncology_consilium_appointments a
           JOIN patients ON patients.id = a.patient_id
           LEFT JOIN users treating_doctor ON treating_doctor.id = patients.treating_doctor_user_id"""
    ).fetchall()
    appointments = {
        (row["scheduled_date"], row["scheduled_time"], row["consilium_group"]): row
        for row in appointment_rows
    }
    # Область видимости проверяется для своих записей, а заведующему — для
    # всех: он видит записи пациентов своего отделения в сокращённом виде.
    head = user.role_id == policy.ROLE_DEPARTMENT_HEAD
    readable_ids = _readable_patient_ids(
        conn, user,
        {row["patient_id"] for row in appointment_rows if head or row["created_by_user_id"] == user.id},
    )

    def appointment_for(key: tuple[str, str, str]) -> dict | None:
        row = appointments.get(key)
        return _slot_appointment(row, user, readable_ids) if row else None

    slots = []
    for row in slot_rows:
        if row["consilium_group"] not in GROUP_LABELS:
            continue
        key = (row["scheduled_date"], row["scheduled_time"], row["consilium_group"])
        slots.append({
            "id": row["id"],
            "scheduledDate": row["scheduled_date"],
            "scheduledTime": row["scheduled_time"],
            "consiliumGroup": row["consilium_group"],
            "consiliumGroupLabel": GROUP_LABELS.get(row["consilium_group"], row["consilium_group"]),
            "occupied": key in appointments,
            "appointment": appointment_for(key),
            "isDefaultWeek": False,
        })
    persisted_keys = {
        (slot["scheduledDate"], slot["scheduledTime"], slot["consiliumGroup"])
        for slot in slots
    }
    for scheduled_date in week_dates:
        for consilium_group, group_label in GROUPS:
            for hour in range(9, 17):
                for minute in (0, 15, 30, 45):
                    scheduled_time = f"{hour:02d}:{minute:02d}"
                    key = (scheduled_date, scheduled_time, consilium_group)
                    if key in persisted_keys:
                        continue
                    slots.append({
                        "id": f"current-week-{scheduled_date}-{scheduled_time}-{consilium_group}",
                        "scheduledDate": scheduled_date,
                        "scheduledTime": scheduled_time,
                        "consiliumGroup": consilium_group,
                        "consiliumGroupLabel": group_label,
                        "occupied": key in appointments,
                        "appointment": appointment_for(key),
                        "isDefaultWeek": True,
                    })
    return sorted(slots, key=lambda slot: (
        slot["scheduledDate"], slot["scheduledTime"], slot["consiliumGroup"]
    ))


def generate_schedule(
    conn: sqlite3.Connection,
    user: policy.CurrentUser,
    scheduled_date: str,
    consilium_group: str,
    start_time: str,
    end_time: str,
    interval_minutes: int,
) -> int:
    if consilium_group not in GROUP_LABELS:
        raise ValueError("Unknown consilium group")
    start = datetime.strptime(f"{scheduled_date} {start_time}", "%Y-%m-%d %H:%M")
    end = datetime.strptime(f"{scheduled_date} {end_time}", "%Y-%m-%d %H:%M")
    if end <= start:
        raise ValueError("Время окончания должно быть позже времени начала")
    if end - start > timedelta(hours=12):
        raise ValueError("Сетка расписания не может быть длиннее 12 часов")
    created = 0
    now = now_iso()
    with conn:
        moment = start
        while moment < end:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO consilium_schedule_slots
                   (id, scheduled_date, scheduled_time, consilium_group, created_by_user_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, scheduled_date, moment.strftime("%H:%M"), consilium_group, user.id, now),
            )
            created += cursor.rowcount
            moment += timedelta(minutes=interval_minutes)
    return created


def delete_schedule_slot(conn: sqlite3.Connection, slot_id: str) -> bool:
    slot = conn.execute(
        "SELECT * FROM consilium_schedule_slots WHERE id = ?", (slot_id,)
    ).fetchone()
    if slot is None:
        return False
    occupied = conn.execute(
        """SELECT 1 FROM oncology_consilium_appointments
           WHERE scheduled_date = ? AND scheduled_time = ? AND consilium_group = ?""",
        (slot["scheduled_date"], slot["scheduled_time"], slot["consilium_group"]),
    ).fetchone()
    if occupied is not None:
        raise ValueError("Нельзя удалить занятый слот")
    with conn:
        conn.execute("DELETE FROM consilium_schedule_slots WHERE id = ?", (slot_id,))
    return True


def add_favorite(
    conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str
) -> bool:
    exists = conn.execute("SELECT 1 FROM patients WHERE id = ?", (patient_id,)).fetchone()
    if exists is None:
        return False
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO consilium_chair_favorites
               (user_id, patient_id, created_at) VALUES (?, ?, ?)""",
            (user.id, patient_id, now_iso()),
        )
    return True


def remove_favorite(
    conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str
) -> bool:
    with conn:
        cursor = conn.execute(
            "DELETE FROM consilium_chair_favorites WHERE user_id = ? AND patient_id = ?",
            (user.id, patient_id),
        )
    return cursor.rowcount > 0


def set_referral_completed(
    conn: sqlite3.Connection,
    user: policy.CurrentUser,
    appointment_id: str,
    completed: bool,
) -> dict | None:
    existing = get_for_management(conn, appointment_id)
    if existing is None:
        return None
    if existing["status"] != "signed" or existing["treatmentType"] != "surgery":
        raise ValueError("Only signed surgical referrals can be completed")
    now = now_iso()
    with conn:
        conn.execute(
            """UPDATE oncology_consilium_appointments
               SET referral_completed_at = ?, referral_completed_by_user_id = ?, updated_at = ?
               WHERE id = ?""",
            (now if completed else None, user.id if completed else None, now, appointment_id),
        )
    return get_for_management(conn, appointment_id)


def registry_rows(
    conn: sqlite3.Connection, treatment_type: str, scheduled_date: str | None = None
) -> list[dict]:
    params: list[str] = [treatment_type]
    date_clause = ""
    if scheduled_date:
        date_clause = " AND a.scheduled_date = ?"
        params.append(scheduled_date)
    rows = conn.execute(
        _SELECT
        + " WHERE a.status = 'signed' AND a.treatment_type = ?"
        + date_clause
        + " ORDER BY a.scheduled_date ASC, a.scheduled_time ASC, patients.name ASC",
        params,
    ).fetchall()
    return [_to_dict(row) for row in rows]


def goc_registry_rows(
    conn: sqlite3.Connection,
    registry_type: str,
    scheduled_date: str | None = None,
) -> list[dict]:
    if registry_type not in {"goc", "goc_65_plus"}:
        raise ValueError("Unknown GOC registry type")
    params: list[str] = []
    date_clause = ""
    if scheduled_date:
        date_clause = " AND a.scheduled_date = ?"
        params.append(scheduled_date)
    rows = conn.execute(
        _SELECT
        + f" WHERE a.status = 'signed' AND a.{registry_type} = 1"
        + date_clause
        + " ORDER BY a.scheduled_date ASC, a.scheduled_time ASC, patients.name ASC",
        params,
    ).fetchall()
    return [_to_dict(row) for row in rows]
