"""Data quality center API.

All endpoints are read-only and operate only on the current user's patient
scope. There are no write endpoints here; corrections go through the existing
patient endpoints and keep their existing RBAC, CSRF and audit behavior.
"""
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import policy
from ..deps import abuse_rate_limit, get_db, require_active_password, search_text
from ..services import data_quality

router = APIRouter(prefix="/api/data-quality", tags=["data-quality"])

QualityCategory = Literal["assignment", "checklist", "completeness", "dates", "duplicates", "format", "stage"]
QualityCode = Literal[
    "missing_treating_doctor",
    "missing_required_field",
    "missing_policy_number",
    "missing_diagnosis",
    "missing_stage",
    "invalid_phone",
    "actual_date_in_future",
    "stage_sequence_violation",
    "overdue_event",
    "incomplete_required_checklist",
    "possible_duplicate",
]


@router.get("/summary", dependencies=[Depends(abuse_rate_limit("data_quality_summary", 60))])
def summary(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    return data_quality.evaluate(conn, user)["summary"]


@router.get("/issues", dependencies=[Depends(abuse_rate_limit("data_quality_issues", 60))])
def issues(
    severity: Literal["critical", "warning", "info"] | None = None,
    category: QualityCategory | None = None,
    code: QualityCode | None = None,
    patient_id: str | None = Query(None, max_length=100),
    treating_doctor: str | None = Query(None, max_length=120),
    department: str | None = Query(None, max_length=120),
    search: str | None = Depends(search_text),
    sort: Literal["severity", "issues", "completeness"] = "severity",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    result = data_quality.evaluate(conn, user)
    if patient_id and not policy.can_read_patient(conn, user, patient_id):
        # Same contract as /api/patients/{id}: outside scope is indistinguishable
        # from absent, and filters cannot reveal hidden patient ids.
        return {
            "issues": [],
            "patients": [],
            "duplicate_groups": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "rules": result["rules"],
        }
    return data_quality.filter_result(
        result,
        severity=severity,
        category=category,
        code=code,
        patient_id=patient_id,
        treating_doctor=treating_doctor,
        department=department,
        search=search,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.get("/patients/{patient_id}", dependencies=[Depends(abuse_rate_limit("data_quality_patient", 120))])
def patient_quality(
    patient_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if not policy.can_read_patient(conn, user, patient_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    result = data_quality.evaluate(conn, user)
    patient = next((p for p in result["patients"] if p["patient_id"] == patient_id), None)
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return {
        "patient": patient,
        "issues": [i for i in result["issues"] if i["patient_id"] == patient_id],
        "duplicate_groups": [
            g for g in result["duplicate_groups"] if patient_id in g["patient_ids"]
        ],
        "rules": result["rules"],
    }
