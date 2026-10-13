"""Internal employee notification center API."""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import policy
from ..deps import abuse_rate_limit, get_db, require_active_password, require_csrf, search_text
from ..models import StrictRequestModel
from ..services import internal_notifications

router = APIRouter(prefix="/api/internal-notifications", tags=["internal-notifications"])

NotificationType = Literal["overdue", "today", "upcoming", "checklist", "data_quality", "attention", "consilium_revision"]
DrawerFilter = Literal["all", "unread", "today", "overdue", "attention"]


class EmptyActionIn(StrictRequestModel):
    pass


@router.get("/summary", dependencies=[Depends(abuse_rate_limit("internal_notifications_summary", 120))])
def summary(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    return internal_notifications.summary(conn, user)


@router.get("", dependencies=[Depends(abuse_rate_limit("internal_notifications_list", 120))])
def list_items(
    unread_only: bool = False,
    type: NotificationType | None = None,
    drawer_filter: DrawerFilter = "all",
    search: str | None = Depends(search_text),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    return internal_notifications.list_items(
        conn,
        user,
        unread_only=unread_only,
        type=type,
        drawer_filter=drawer_filter,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post("/{notification_id}/read", dependencies=[Depends(require_csrf)])
def mark_read(
    notification_id: str,
    _body: EmptyActionIn | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    item = internal_notifications.mark_read(conn, user, notification_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return {"notification": item}


@router.post("/read-all", dependencies=[Depends(require_csrf)])
def mark_all_read(
    _body: EmptyActionIn | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    return internal_notifications.mark_all_read(conn, user)
