"""Pydantic request/response schemas.

Field limits are deliberately generous but bounded (see the brief's
suggested limits) — enough to never bite a legitimate clinical record,
tight enough to bound memory/storage use from a hostile or corrupt import.
All server-authoritative fields (id, timestamps, matchedPlan derivation)
are computed server-side and never trusted from the client.
"""
from __future__ import annotations

from datetime import date as date_type
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

PRIORITIES = ("urgent", "high", "normal")
STAGES = (
    "primary-appointment",
    "exam",
    "appointment",
    "consult",
    "treatment-prep",
    "treatment",
    "completed",
)
DISEASE_STAGES = ("", "I", "II", "III", "IV")
TREATMENT_TYPES = ("", "surgery", "radiation", "chemo")

Priority = Literal["urgent", "high", "normal"]
Stage = Literal[
    "primary-appointment", "exam", "appointment", "consult", "treatment-prep", "treatment", "completed"
]


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


class EventIn(BaseModel):
    date: Optional[str] = Field(None, max_length=10)
    time: Optional[str] = Field(None, max_length=5)
    treatmentType: Optional[str] = Field(None, max_length=20)
    actualDate: Optional[str] = Field(None, max_length=10)
    actualTime: Optional[str] = Field(None, max_length=5)
    matchedPlan: Optional[bool] = None
    noPlan: Optional[bool] = False

    @field_validator("date", "actualDate")
    @classmethod
    def _valid_date(cls, v):
        if v is None or v == "":
            return v
        try:
            date_type.fromisoformat(v)
        except ValueError:
            raise ValueError("date must be YYYY-MM-DD")
        return v

    @field_validator("treatmentType")
    @classmethod
    def _valid_treatment_type(cls, v):
        if v is None:
            return v
        if v not in TREATMENT_TYPES:
            raise ValueError(f"treatmentType must be one of {TREATMENT_TYPES}")
        return v


class StageChangeIn(BaseModel):
    newStage: Stage
    lastEventUpdate: Optional[EventIn] = None


class NoteIn(BaseModel):
    note: str = Field("", max_length=5000)

    @field_validator("note")
    @classmethod
    def _norm(cls, v):
        return _normalize_multiline(v)


class ChecklistItemIn(BaseModel):
    item: str = Field(..., min_length=1, max_length=200)
    checked: bool


class EventFieldUpdateIn(BaseModel):
    field: Literal["date", "time", "treatmentType", "actualDate", "actualTime"]
    value: str = Field("", max_length=20)


class FuturePlanIn(BaseModel):
    field: Literal["date", "time", "treatmentType"]
    value: str = Field("", max_length=20)


class PatientFieldUpdateIn(BaseModel):
    field: Literal[
        "name", "age", "phone", "diagnosis", "nosology", "diseaseStage", "priority", "doctor", "head", "complaint"
    ]
    value: str | int


class PatientCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    age: int = Field(0, ge=0, le=150)
    phone: str = Field("", max_length=50)
    priority: Priority
    stage: Stage
    diagnosis: str = Field(..., min_length=1, max_length=500)
    # В UI — «Профиль клиентского пути»; legacy-имя `nosology` сохранено намеренно
    # для совместимости с существующей БД и форматом импорта/экспорта (см. README).
    nosology: str = Field("", max_length=100)
    diseaseStage: str = Field("", max_length=10)
    doctor: str = Field("", max_length=200)
    head: str = Field(..., min_length=1, max_length=200)
    complaint: str = Field("", max_length=5000)
    events: list[EventIn] = Field(default_factory=list)
    # Стабильная связь с учётной записью врача; правила назначения — в policy.py
    # (врач — только себя, заведующий — врача своего отделения, Разработчик — любого).
    treatingDoctorUserId: Optional[str] = Field(None, max_length=100)

    @field_validator("name", "doctor", "head")
    @classmethod
    def _norm_text(cls, v):
        return _normalize_text(v)

    @field_validator("complaint")
    @classmethod
    def _norm_multiline(cls, v):
        return _normalize_multiline(v)

    @field_validator("diseaseStage")
    @classmethod
    def _valid_disease_stage(cls, v):
        if v not in DISEASE_STAGES:
            raise ValueError(f"diseaseStage must be one of {DISEASE_STAGES}")
        return v


class EventOut(BaseModel):
    date: Optional[str] = None
    time: Optional[str] = None
    treatmentType: Optional[str] = None
    actualDate: Optional[str] = None
    actualTime: Optional[str] = None
    matchedPlan: Optional[bool] = None
    noPlan: bool = False
    setAt: Optional[str] = None


class HistoryEntryOut(BaseModel):
    stage: str
    enteredAt: str
    events: list[EventOut] = Field(default_factory=list)
    checklist: dict[str, bool] = Field(default_factory=dict)
    note: str = ""


class FuturePlanOut(BaseModel):
    date: str = ""
    time: str = ""
    treatmentType: str = ""


class PatientOut(BaseModel):
    id: str
    name: str
    age: int
    priority: str
    stage: str
    phone: str
    complaint: str
    diagnosis: str
    nosology: str
    diseaseStage: str
    arrival: str
    doctor: str
    head: str
    updatedAt: Optional[str] = None
    history: list[HistoryEntryOut] = Field(default_factory=list)
    futurePlans: dict[str, FuturePlanOut] = Field(default_factory=dict)
    treatingDoctorUserId: Optional[str] = None
    treatingDoctorName: Optional[str] = None


# --- Import/export -------------------------------------------------------

ImportMode = Literal["add", "update", "replace"]


DANGEROUS_KEYS = {"__proto__", "constructor", "prototype"}


def _reject_dangerous_keys(d: dict) -> None:
    if DANGEROUS_KEYS & set(d.keys()):
        raise ValueError("payload contains disallowed keys")


class ImportHistoryEntry(BaseModel):
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


class ImportFuturePlan(BaseModel):
    date: str = Field("", max_length=20)
    time: str = Field("", max_length=20)
    treatmentType: str = Field("", max_length=20)


class ImportPatientIn(BaseModel):
    """Mirrors PatientOut shape but every field is independently validated;
    a client-sent `id` is only ever used to *match* an existing server
    record in `update` mode — it is never trusted to assign a new identity."""

    id: Optional[str] = Field(None, max_length=100)
    name: str = Field(..., min_length=1, max_length=200)
    age: int = Field(0, ge=0, le=150)
    priority: str
    stage: str
    phone: str = Field("", max_length=50)
    complaint: str = Field("", max_length=5000)
    diagnosis: str = Field("", max_length=500)
    # legacy-имя `nosology` (UI: «Профиль клиентского пути») — часть формата
    # импорта/экспорта, менять нельзя без миграции формата (см. README).
    nosology: str = Field("", max_length=100)
    diseaseStage: str = Field("", max_length=10)
    arrival: Optional[str] = Field(None, max_length=40)
    doctor: str = Field("", max_length=200)
    head: str = Field("", max_length=200)
    updatedAt: Optional[str] = Field(None, max_length=40)
    history: list[ImportHistoryEntry] = Field(default_factory=list)
    futurePlans: dict[str, ImportFuturePlan] = Field(default_factory=dict)
    # Появилось в схеме v2; старые выгрузки без этого поля остаются валидными.
    # Значение никогда не принимается на веру — сервер проверяет его по scope актора.
    treatingDoctorUserId: Optional[str] = Field(None, max_length=100)

    @field_validator(
        "phone", "complaint", "diagnosis", "nosology", "diseaseStage", "doctor", "head", mode="before"
    )
    @classmethod
    def _none_to_empty(cls, v):
        return "" if v is None else v

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


class ImportDocument(BaseModel):
    schemaVersion: int = 0
    patients: list[ImportPatientIn]

    @field_validator("patients")
    @classmethod
    def _bounded_patients(cls, v):
        if len(v) > 500:
            raise ValueError("too many patients in import (max 500)")
        return v


class ImportPreviewRequest(BaseModel):
    raw: str = Field(..., max_length=6_000_000)  # ~5MB text + margin


class ImportCommitRequest(BaseModel):
    raw: str = Field(..., max_length=6_000_000)
    mode: ImportMode


# --- Notifications ------------------------------------------------------

NotificationStatus = Literal["pending", "processing", "sent", "failed", "cancelled", "skipped"]
NotificationChannel = Literal["sms"]
NotificationSourceType = Literal["manual", "event"]
ConsentStatus = Literal["not_recorded", "granted", "revoked"]


class NotificationPreferenceIn(BaseModel):
    smsEnabled: bool
    consentStatus: ConsentStatus


class NotificationCreateIn(BaseModel):
    patientId: str = Field(..., min_length=1, max_length=100)
    templateCode: str = Field(..., min_length=1, max_length=80)
    scheduledAt: str = Field(..., min_length=1, max_length=40)


class NotificationTemplateUpdateIn(BaseModel):
    body: Optional[str] = Field(None, min_length=1, max_length=500)
    enabled: Optional[bool] = None


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=200)


class ChangePasswordIn(BaseModel):
    currentPassword: str = Field(..., min_length=1, max_length=200)
    newPassword: str = Field(..., min_length=1, max_length=200)


# --- RBAC: сотрудники и структура ----------------------------------------

RoleId = Literal["developer", "deputy_chief_oncology", "caop_head", "department_head", "doctor"]


class UserCreateIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    fullName: str = Field(..., min_length=1, max_length=200)
    roleId: RoleId
    password: str = Field(..., min_length=1, max_length=200)
    mustChangePassword: bool = True
    departmentIds: list[str] = Field(default_factory=list, max_length=50)


class UserUpdateIn(BaseModel):
    fullName: Optional[str] = Field(None, min_length=1, max_length=200)
    roleId: Optional[RoleId] = None
    status: Optional[Literal["active", "blocked", "deactivated"]] = None
    departmentIds: Optional[list[str]] = Field(None, max_length=50)


class ResetPasswordIn(BaseModel):
    newPassword: str = Field(..., min_length=1, max_length=200)
    mustChangePassword: bool = True


class DepartmentCreateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class DepartmentUpdateIn(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    status: Optional[Literal["active", "archived"]] = None
    headUserId: Optional[str] = Field(None, max_length=100)
    clearHead: bool = False


class DepartmentDoctorIn(BaseModel):
    userId: str = Field(..., min_length=1, max_length=100)


class TreatingDoctorIn(BaseModel):
    userId: Optional[str] = Field(None, max_length=100)
