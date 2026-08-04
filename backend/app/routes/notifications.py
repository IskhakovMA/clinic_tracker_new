"""Notifications API: preferences, queue, templates and manual worker runs."""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, policy
from ..deps import abuse_rate_limit, get_db, require_active_password, require_csrf
from ..models import NotificationCreateIn, NotificationPreferenceIn, NotificationTemplateUpdateIn
from ..services import notifications

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
templates_router = APIRouter(prefix="/api", tags=["notifications"])

StatusParam = Literal["pending", "processing", "sent", "failed", "cancelled", "skipped"]
ChannelParam = Literal["sms"]
SourceParam = Literal["manual", "event"]
SortParam = Literal["scheduled_desc", "scheduled_asc", "status"]


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _forbidden(conn: sqlite3.Connection, user: policy.CurrentUser, detail: str):
    audit.record(conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED, detail=detail, **audit.actor_ctx(user))
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _require_patient_read(conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str):
    if not policy.can_read_patient(conn, user, patient_id):
        _not_found()


def _require_patient_write(conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str):
    _require_patient_read(conn, user, patient_id)
    if not policy.can_write_patient(conn, user, patient_id):
        _forbidden(conn, user, "area=notifications action=write")


def _require_developer(conn: sqlite3.Connection, user: policy.CurrentUser, action: str):
    if user.role_id != policy.ROLE_DEVELOPER:
        _forbidden(conn, user, f"area=notifications action={action}")


@router.get("/summary")
def summary(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    return notifications.summary(conn, user)


@router.get("")
def list_queue(
    status: StatusParam | None = None,
    channel: ChannelParam | None = None,
    patient_id: str | None = Query(None, max_length=100),
    date_from: str | None = Query(None, max_length=40),
    date_to: str | None = Query(None, max_length=40),
    template_code: str | None = Query(None, max_length=80),
    source_type: SourceParam | None = None,
    search: str | None = Query(None, max_length=120),
    treating_doctor: str | None = Query(None, max_length=120),
    department: str | None = Query(None, max_length=120),
    sort: SortParam = "scheduled_desc",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if patient_id and not policy.can_read_patient(conn, user, patient_id):
        return {"notifications": [], "total": 0, "limit": limit, "offset": offset}
    return notifications.list_notifications(
        conn,
        user,
        status=status,
        channel=channel,
        patient_id=patient_id,
        date_from=date_from,
        date_to=date_to,
        template_code=template_code,
        source_type=source_type,
        search=search,
        treating_doctor=treating_doctor,
        department=department,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.get("/patients/{patient_id}")
def patient_notifications(
    patient_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_patient_read(conn, user, patient_id)
    pref = notifications.get_preference(conn, patient_id)
    if not policy.can_manage_users(user):
        pref["consent_recorded_by_name"] = None
    queue = notifications.list_notifications(conn, user, patient_id=patient_id, limit=20)
    upcoming = notifications.list_notifications(
        conn, user, patient_id=patient_id, status="pending", sort="scheduled_asc", limit=1
    )
    return {
        "preference": pref,
        "notifications": queue["notifications"],
        "upcoming": upcoming["notifications"][0] if upcoming["notifications"] else None,
        "can_write": policy.can_write_patient(conn, user, patient_id),
    }


@router.put("/patients/{patient_id}/preferences", dependencies=[Depends(require_csrf)])
def update_patient_preferences(
    patient_id: str,
    body: NotificationPreferenceIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_patient_write(conn, user, patient_id)
    return notifications.set_preference(
        conn,
        patient_id=patient_id,
        sms_enabled=body.smsEnabled,
        consent_status=body.consentStatus,
        user=user,
    )


@router.get("/templates")
def templates(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    return {
        "templates": notifications.list_templates(conn, include_disabled=user.role_id == policy.ROLE_DEVELOPER),
        "placeholders": sorted(notifications.ALLOWED_PLACEHOLDERS),
    }


@templates_router.get("/notification-templates")
def notification_templates_alias(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    return templates(conn, user)


@router.put("/templates/{code}", dependencies=[Depends(require_csrf)])
def update_template(
    code: str,
    body: NotificationTemplateUpdateIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_developer(conn, user, "template_update")
    try:
        return notifications.update_template(conn, code=code, body=body.body, enabled=body.enabled, user=user)
    except KeyError:
        _not_found()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))


@router.get("/{notification_id}")
def detail(
    notification_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    item = notifications.get_notification_scoped(conn, user, notification_id)
    if item is None:
        _not_found()
    return item


@router.post("", dependencies=[Depends(require_csrf)], status_code=status.HTTP_201_CREATED)
def create_manual(
    body: NotificationCreateIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_patient_write(conn, user, body.patientId)
    scheduled = notifications.parse_dt(body.scheduledAt)
    if scheduled is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="scheduledAt must be ISO datetime")
    if scheduled < notifications.utcnow():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="scheduledAt must not be in the past")
    try:
        item, reason = notifications.create_notification(
            conn,
            patient_id=body.patientId,
            template_code=body.templateCode,
            scheduled_at=scheduled.isoformat(),
            source_type="manual",
            source_id=notifications.MANUAL_SOURCE_ID,
            created_by_user_id=user.id,
            audit_user=user,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if reason and reason != "duplicate":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=reason)
    return {"notification": item, "duplicate": reason == "duplicate"}


@router.post("/{notification_id}/cancel", dependencies=[Depends(require_csrf)])
def cancel(
    notification_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    item = notifications.get_notification_scoped(conn, user, notification_id)
    if item is None:
        _not_found()
    _require_patient_write(conn, user, item["patient_id"])
    if not notifications.cancel_notification(conn, notification_id=notification_id, user=user):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Notification cannot be cancelled")
    return {"cancelled": True}


@router.post("/{notification_id}/retry", dependencies=[Depends(require_csrf)])
def retry(
    notification_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_developer(conn, user, "retry")
    item = notifications.get_notification_scoped(conn, user, notification_id)
    if item is None:
        _not_found()
    if not notifications.retry_notification(conn, notification_id=notification_id, user=user):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Notification cannot be retried")
    return {"retry": True}


@router.post("/generate-due", dependencies=[Depends(require_csrf), Depends(abuse_rate_limit("notifications_generate_due", 30))])
def generate_due(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_developer(conn, user, "generate_due")
    return notifications.generate_due_reminders(conn, user)


@router.post("/process-due", dependencies=[Depends(require_csrf), Depends(abuse_rate_limit("notifications_process_due", 30))])
def process_due(
    limit: int = Query(100, ge=1, le=200),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_developer(conn, user, "process_due")
    return notifications.process_due(conn, limit=limit)
