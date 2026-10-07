"""Pydantic request/response schemas.

Field limits are deliberately generous but bounded (see the brief's
suggested limits) — enough to never bite a legitimate clinical record,
tight enough to bound memory/storage use from a hostile or corrupt import.
All server-authoritative fields (id, timestamps, matchedPlan derivation)
are computed server-side and never trusted from the client.
"""
from __future__ import annotations

import re
from datetime import date as date_type
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .email_address import EMAIL_MAX_LENGTH, email_is_valid, normalize_email

PRIORITIES = ("urgent", "high", "normal")
STAGES = (
    "primary-appointment",
    "exam",
    "appointment",
    "consult",
    "treatment-prep",
    "treatment",
    "completed",
    "dispensary-observation",
    "diagnosis-not-confirmed",
)
TERMINAL_STAGES = ("completed", "dispensary-observation", "diagnosis-not-confirmed")
DISEASE_STAGES = ("", "I", "II", "III", "IV")
TREATMENT_TYPES = ("", "surgery", "radiation", "chemo", "hormone")
CONSILIUM_DECISIONS = ("treatment_strategy_defined", "treatment_strategy_changed", "dispensary_observation")
PGI_RESULTS = ("", "confirmed", "not_confirmed")
POLICY_NUMBER_MAX_LENGTH = 32
POLICY_NUMBER_INPUT_MAX_LENGTH = 64
ATTENTION_COMMENT_MAX_LENGTH = 800
ATTENTION_REASON_CODES = (
    "emergency_hospitalization",
    "route_change",
    "critical_overdue",
    "doctor_decision_required",
    "department_head_decision_required",
    "organizational_issue",
    "documents_or_data_issue",
    "patient_unavailable",
    "other",
)

Priority = Literal["urgent", "high", "normal"]
ConsiliumDecision = Literal["treatment_strategy_defined", "treatment_strategy_changed", "dispensary_observation"]
PgiResult = Literal["", "confirmed", "not_confirmed"]
Stage = Literal[
    "primary-appointment",
    "exam",
    "appointment",
    "consult",
    "treatment-prep",
    "treatment",
    "completed",
    "dispensary-observation",
    "diagnosis-not-confirmed",
]


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _normalize_text(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    return " ".join(v.strip().split()) if v.strip() else ""


def _normalize_multiline(v: Optional[str]) -> Optional[str]:
    """Like _normalize_text but preserves internal newlines (for notes/complaints)."""
    if v is None:
        return None
    lines = [" ".join(line.split()) for line in v.strip().splitlines()]
    return "\n".join(lines)


def normalize_policy_number(v: Optional[str]) -> str:
    if v is None:
        return ""
    raw = str(v).strip()
    if not raw:
        return ""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise ValueError("Номер полиса ОМС содержит недопустимые управляющие символы")
    if "<" in raw or ">" in raw:
        raise ValueError("Номер полиса ОМС не должен содержать HTML или JavaScript")
    value = re.sub(r"[ -]+", "", raw)
    if len(value) > POLICY_NUMBER_MAX_LENGTH:
        raise ValueError(f"Номер полиса ОМС не должен быть длиннее {POLICY_NUMBER_MAX_LENGTH} символов")
    return value


_EVENT_TIME_RE = re.compile(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$")


def validate_event_value(field: str, value: str | None) -> str | None:
    """Проверка значения поля события: дата — YYYY-MM-DD, время — HH:MM
    в пределах суток. Пустое значение допустимо (поле очищается)."""
    if value is None or value == "":
        return value
    if field in ("date", "actualDate"):
        try:
            date_type.fromisoformat(value)
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD")
    elif field in ("time", "actualTime"):
        if not _EVENT_TIME_RE.match(value):
            raise ValueError("time must be HH:MM (00:00-23:59)")
    return value


class EventIn(StrictRequestModel):
    date: Optional[str] = Field(None, max_length=10)
    time: Optional[str] = Field(None, max_length=5)
    treatmentType: Optional[str] = Field(None, max_length=20)
    actualDate: Optional[str] = Field(None, max_length=10)
    actualTime: Optional[str] = Field(None, max_length=5)
    matchedPlan: Optional[bool] = None
    noPlan: Optional[bool] = False
    consiliumDecision: Optional[ConsiliumDecision] = None
    # Present in v3 export; accepted for roundtrip compatibility and ignored
    # on write because event timestamps are server-authoritative.
    setAt: Optional[str] = Field(None, max_length=40)

    @field_validator("date", "actualDate")
    @classmethod
    def _valid_date(cls, v):
        return validate_event_value("date", v)

    @field_validator("time", "actualTime")
    @classmethod
    def _valid_time(cls, v):
        return validate_event_value("time", v)

    @field_validator("treatmentType")
    @classmethod
    def _valid_treatment_type(cls, v):
        if v is None:
            return v
        if v not in TREATMENT_TYPES:
            raise ValueError(f"treatmentType must be one of {TREATMENT_TYPES}")
        return v


class StageChangeIn(StrictRequestModel):
    newStage: Stage
    lastEventUpdate: Optional[EventIn] = None


class NoteIn(StrictRequestModel):
    note: str = Field("", max_length=5000)

    @field_validator("note")
    @classmethod
    def _norm(cls, v):
        return _normalize_multiline(v)


class ChecklistItemIn(StrictRequestModel):
    item: str = Field(..., min_length=1, max_length=200)
    checked: bool


class EventFieldUpdateIn(StrictRequestModel):
    field: Literal["date", "time", "treatmentType", "actualDate", "actualTime"]
    value: str = Field("", max_length=20)


class PatientFieldUpdateIn(StrictRequestModel):
    field: Literal[
        "name",
        "age",
        "phone",
        "policyNumber",
        "diagnosis",
        "pgiResult",
        "pgiResultDate",
        "nosology",
        "priority",
        "doctor",
        "head",
        "complaint",
    ]
    value: str | int | dict[str, str]


class PatientCreateIn(StrictRequestModel):
    name: str = Field(..., min_length=1, max_length=200)
    age: int = Field(0, ge=0, le=150)
    phone: str = Field("", max_length=50)
    policyNumber: str = Field("", max_length=POLICY_NUMBER_INPUT_MAX_LENGTH)
    priority: Priority
    stage: Stage
    diagnosis: str = Field(..., min_length=1, max_length=500)
    # Deprecated v7 compatibility fields. Active UI no longer sends them,
    # but v7 clients/imports are accepted without data loss.
    consiliumCode: str = Field("", max_length=100)
    pgiCode: str = Field("", max_length=100)
    pgiResult: PgiResult = ""
    pgiResultDate: Optional[str] = Field(None, max_length=10)
    # В UI — локализация/клиентский путь; legacy-имя `nosology` сохранено намеренно
    # для совместимости с существующей БД и форматом импорта/экспорта (см. README).
    nosology: str = Field("", max_length=100)
    doctor: str = Field("", max_length=200)
    head: str = Field(..., min_length=1, max_length=200)
    complaint: str = Field("", max_length=5000)
    events: list[EventIn] = Field(default_factory=list)

    @field_validator("events")
    @classmethod
    def _bounded_create_events(cls, v):
        from . import config
        if len(v) > config.MAX_EVENTS_PER_HISTORY_ENTRY:
            raise ValueError(f"no more than {config.MAX_EVENTS_PER_HISTORY_ENTRY} events per history entry")
        return v
    # Стабильная связь с учётной записью врача; правила назначения — в policy.py
    # (врач — только себя, заведующий — врача своего отделения, Разработчик — любого).
    treatingDoctorUserId: Optional[str] = Field(None, max_length=100)

    @field_validator("name", "doctor", "head", "consiliumCode", "pgiCode")
    @classmethod
    def _norm_text(cls, v):
        return _normalize_text(v)

    @field_validator("complaint")
    @classmethod
    def _norm_multiline(cls, v):
        return _normalize_multiline(v)

    @field_validator("policyNumber", mode="before")
    @classmethod
    def _norm_policy_number(cls, v):
        return normalize_policy_number(v)

    @field_validator("pgiResultDate")
    @classmethod
    def _valid_pgi_result_date(cls, v):
        if v in (None, ""):
            return None
        try:
            date_type.fromisoformat(v)
        except ValueError:
            raise ValueError("pgiResultDate must be YYYY-MM-DD")
        return v

    @model_validator(mode="after")
    def _valid_pgi_pair(self):
        if self.pgiResult and not self.pgiResultDate:
            raise ValueError("Дата результата ПГИ обязательна, если выбран результат ПГИ")
        if not self.pgiResult and self.pgiResultDate:
            self.pgiResultDate = None
        return self


class EventOut(BaseModel):
    date: Optional[str] = None
    time: Optional[str] = None
    treatmentType: Optional[str] = None
    actualDate: Optional[str] = None
    actualTime: Optional[str] = None
    matchedPlan: Optional[bool] = None
    noPlan: bool = False
    consiliumDecision: Optional[str] = None
    setAt: Optional[str] = None


class HistoryEntryOut(BaseModel):
    stage: str
    enteredAt: str
    events: list[EventOut] = Field(default_factory=list)
    checklist: dict[str, bool] = Field(default_factory=dict)
    note: str = ""


class DiagnosisHistoryOut(BaseModel):
    id: str
    patientId: str
    changedAt: str
    actorUserId: Optional[str] = None
    oldDiagnosis: str = ""
    newDiagnosis: str = ""


class PatientOut(BaseModel):
    id: str
    name: str
    age: int
    priority: str
    stage: str
    phone: str
    policyNumber: str
    complaint: str
    diagnosis: str
    consiliumCode: str = ""
    pgiCode: str = ""
    pgiResult: str = ""
    pgiResultDate: Optional[str] = None
    nosology: str
    arrival: str
    doctor: str
    head: str
    updatedAt: Optional[str] = None
    history: list[HistoryEntryOut] = Field(default_factory=list)
    diagnosisHistory: list[DiagnosisHistoryOut] = Field(default_factory=list)
    treatingDoctorUserId: Optional[str] = None
    treatingDoctorName: Optional[str] = None
    attention: dict = Field(default_factory=dict)


class PortalAccessLinkIn(StrictRequestModel):
    """Привязка адреса мобильного доступа к существующей карточке.

    Адрес нормализуется до валидации, чтобы проверка и хранение работали с
    одной и той же строкой, а UNIQUE-индекс выражал «один email — одна карточка».
    """

    email: str = Field(..., min_length=3, max_length=EMAIL_MAX_LENGTH)

    @field_validator("email")
    @classmethod
    def _norm_email(cls, v):
        address = normalize_email(v)
        if not email_is_valid(address):
            raise ValueError("invalid email")
        return address


class AttentionIssueCreateIn(StrictRequestModel):
    reasonCode: str = Field(..., min_length=1, max_length=80)
    comment: str = Field("", max_length=ATTENTION_COMMENT_MAX_LENGTH)
    eventDate: Optional[str] = Field(None, max_length=10)

    @field_validator("reasonCode")
    @classmethod
    def _valid_reason(cls, v):
        if v not in ATTENTION_REASON_CODES:
            raise ValueError("unknown attention reason")
        return v

    @field_validator("comment")
    @classmethod
    def _norm_comment(cls, v):
        return _normalize_multiline(v) or ""

    @field_validator("eventDate")
    @classmethod
    def _valid_event_date(cls, v):
        if v in (None, ""):
            return None
        try:
            date_type.fromisoformat(v)
        except ValueError:
            raise ValueError("eventDate must be YYYY-MM-DD")
        return v


class AttentionIssueCloseIn(StrictRequestModel):
    resolutionComment: str = Field("", max_length=ATTENTION_COMMENT_MAX_LENGTH)

    @field_validator("resolutionComment")
    @classmethod
    def _norm_resolution(cls, v):
        return _normalize_multiline(v) or ""


# --- Import/export -------------------------------------------------------

ImportMode = Literal["add", "update", "replace"]


DANGEROUS_KEYS = {"__proto__", "constructor", "prototype"}


def _reject_dangerous_keys(d: dict) -> None:
    if DANGEROUS_KEYS & set(d.keys()):
        raise ValueError("payload contains disallowed keys")


class ImportHistoryEntry(StrictRequestModel):
    stage: str
    enteredAt: str = Field(..., max_length=40)
    events: list[EventIn] = Field(default_factory=list)
    checklist: dict[str, bool] = Field(default_factory=dict)
    note: str = Field("", max_length=5000)

    @field_validator("stage")
    @classmethod
    def _valid_stage(cls, v):
        if v not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}")
        return v

    @field_validator("events")
    @classmethod
    def _bounded_events(cls, v):
        if len(v) > 200:
            raise ValueError("too many events in a history entry")
        return v

    @field_validator("checklist")
    @classmethod
    def _bounded_checklist(cls, v):
        if len(v) > 100:
            raise ValueError("too many checklist items")
        for key in v:
            if len(key) > 200:
                raise ValueError("checklist item name too long")
        _reject_dangerous_keys(v)
        return v


class ImportFuturePlan(StrictRequestModel):
    date: str = Field("", max_length=20)
    time: str = Field("", max_length=20)
    treatmentType: str = Field("", max_length=20)


class ImportPatientIn(StrictRequestModel):
    """Mirrors PatientOut shape but every field is independently validated;
    a client-sent `id` is only ever used to *match* an existing server
    record in `update` mode — it is never trusted to assign a new identity."""

    id: Optional[str] = Field(None, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    age: int = Field(0, ge=0, le=150)
    priority: str
    stage: str
    phone: str = Field("", max_length=50)
    policyNumber: str = Field("", max_length=POLICY_NUMBER_INPUT_MAX_LENGTH)
    complaint: str = Field("", max_length=5000)
    diagnosis: str = Field("", max_length=500)
    consiliumCode: str = Field("", max_length=100)
    pgiCode: str = Field("", max_length=100)
    pgiResult: PgiResult = ""
    pgiResultDate: Optional[str] = Field(None, max_length=10)
    # legacy-имя `nosology` (UI: локализация/клиентский путь) — часть формата
    # импорта/экспорта, менять нельзя без миграции формата (см. README).
    nosology: str = Field("", max_length=100)
    diseaseStage: str = Field("", max_length=10)  # accepted only for legacy import compatibility
    arrival: Optional[str] = Field(None, max_length=40)
    doctor: str = Field("", max_length=200)
    head: str = Field("", max_length=200)
    updatedAt: Optional[str] = Field(None, max_length=40)
    history: list[ImportHistoryEntry] = Field(default_factory=list)
    diagnosisHistory: list[DiagnosisHistoryOut] = Field(default_factory=list)
    # Legacy localStorage/create-payload shape kept events at the patient root.
    # It is a known compatibility field, not an open-ended extra.
    events: list[EventIn] = Field(default_factory=list)
    futurePlans: dict[str, ImportFuturePlan] = Field(default_factory=dict)  # accepted, retired on import
    # Появилось в схеме v2; старые выгрузки без этого поля остаются валидными.
    # Значение никогда не принимается на веру — сервер проверяет его по scope актора.
    treatingDoctorUserId: Optional[str] = Field(None, max_length=100)
    # Present in v3 export for display only; import never trusts it for assignment.
    treatingDoctorName: Optional[str] = Field(None, max_length=200)

    @field_validator(
        "phone",
        "complaint",
        "diagnosis",
        "consiliumCode",
        "pgiCode",
        "pgiResult",
        "pgiResultDate",
        "nosology",
        "diseaseStage",
        "doctor",
        "head",
        mode="before",
    )
    @classmethod
    def _none_to_empty(cls, v):
        return "" if v is None else v

    @field_validator("policyNumber", mode="before")
    @classmethod
    def _norm_policy_number(cls, v):
        return normalize_policy_number(v)

    @field_validator("priority")
    @classmethod
    def _valid_priority(cls, v):
        if v not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")
        return v

    @field_validator("stage")
    @classmethod
    def _valid_stage(cls, v):
        if v not in STAGES:
            raise ValueError(f"stage must be one of {STAGES}")
        return v

    @field_validator("diseaseStage")
    @classmethod
    def _valid_disease_stage(cls, v):
        if v not in DISEASE_STAGES:
            raise ValueError(f"diseaseStage must be one of {DISEASE_STAGES}")
        return v

    @field_validator("pgiResultDate")
    @classmethod
    def _valid_import_pgi_result_date(cls, v):
        if v in (None, ""):
            return None
        try:
            date_type.fromisoformat(v)
        except ValueError:
            raise ValueError("pgiResultDate must be YYYY-MM-DD")
        return v

    @model_validator(mode="after")
    def _valid_import_pgi_pair(self):
        # v8 exports can contain structured PGI result before v9 introduced
        # pgiResultDate. Import keeps them compatible and stores NULL date.
        if not self.pgiResult and self.pgiResultDate:
            self.pgiResultDate = None
        return self

    @field_validator("history")
    @classmethod
    def _bounded_history(cls, v):
        if len(v) > 200:
            raise ValueError("too many history entries")
        return v

    @field_validator("futurePlans")
    @classmethod
    def _bounded_future_plans(cls, v):
        _reject_dangerous_keys(v)
        return v


class ImportDocument(StrictRequestModel):
    schemaVersion: int = 0
    generatedAt: Optional[str] = Field(None, max_length=40)
    patients: list[ImportPatientIn]

    @field_validator("patients")
    @classmethod
    def _bounded_patients(cls, v):
        if len(v) > 500:
            raise ValueError("too many patients in import (max 500)")
        return v


class ImportPreviewRequest(StrictRequestModel):
    raw: str = Field(..., max_length=6_000_000)  # ~5MB text + margin


class ImportCommitRequest(StrictRequestModel):
    raw: str = Field(..., max_length=6_000_000)
    mode: ImportMode


# --- Notifications ------------------------------------------------------

NotificationStatus = Literal["pending", "processing", "sent", "failed", "cancelled", "skipped"]
NotificationChannel = Literal["sms"]
NotificationSourceType = Literal["manual", "event"]
ConsentStatus = Literal["not_recorded", "granted", "revoked"]


class NotificationPreferenceIn(StrictRequestModel):
    smsEnabled: bool
    consentStatus: ConsentStatus


class NotificationCreateIn(StrictRequestModel):
    patientId: str = Field(..., min_length=1, max_length=100)
    templateCode: str = Field(..., min_length=1, max_length=80)
    scheduledAt: str = Field(..., min_length=1, max_length=40)


class NotificationTemplateUpdateIn(StrictRequestModel):
    body: Optional[str] = Field(None, min_length=1, max_length=500)
    enabled: Optional[bool] = None


class LoginIn(StrictRequestModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=200)


class ChangePasswordIn(StrictRequestModel):
    currentPassword: str = Field(..., min_length=1, max_length=200)
    newPassword: str = Field(..., min_length=1, max_length=200)


# --- RBAC: сотрудники и структура ----------------------------------------

RoleId = Literal["developer", "deputy_chief_oncology", "caop_head", "department_head", "doctor"]


class UserCreateIn(StrictRequestModel):
    username: str = Field(..., min_length=3, max_length=64)
    fullName: str = Field(..., min_length=1, max_length=200)
    roleId: RoleId
    password: str = Field(..., min_length=1, max_length=200)
    mustChangePassword: bool = True
    departmentIds: list[str] = Field(default_factory=list, max_length=50)


class UserUpdateIn(StrictRequestModel):
    fullName: Optional[str] = Field(None, min_length=1, max_length=200)
    roleId: Optional[RoleId] = None
    status: Optional[Literal["active", "blocked", "deactivated"]] = None
    departmentIds: Optional[list[str]] = Field(None, max_length=50)


class ResetPasswordIn(StrictRequestModel):
    newPassword: str = Field(..., min_length=1, max_length=200)
    mustChangePassword: bool = True


class DepartmentCreateIn(StrictRequestModel):
    name: str = Field(..., min_length=1, max_length=200)


class DepartmentUpdateIn(StrictRequestModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    status: Optional[Literal["active", "archived"]] = None
    headUserId: Optional[str] = Field(None, max_length=100)
    clearHead: bool = False


class DepartmentDoctorIn(StrictRequestModel):
    userId: str = Field(..., min_length=1, max_length=100)


class TreatingDoctorIn(StrictRequestModel):
    userId: Optional[str] = Field(None, max_length=100)
