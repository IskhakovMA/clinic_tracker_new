"""Shared analytics query filters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, Query, status

from .. import config
from ..models import STAGES
from ..services import data_quality, notifications, patient_attention

SORT_FIELDS = (
    "name",
    "count",
    "stage",
    "days_overdue",
    "scheduled_at",
    "severity",
    "workload",
)
ORDER_FIELDS = ("asc", "desc")
GROUP_BY_FIELDS = ("stage", "doctor", "department", "severity", "code", "status", "template", "source", "overdue_bucket")


def app_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(config.APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def now_local() -> datetime:
    return datetime.now(app_timezone())


def today_local() -> date:
    return now_local().date()


def parse_date_param(value: str | None, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field} must be YYYY-MM-DD",
        ) from exc


def local_day_bounds(day: date) -> tuple[str, str]:
    tz = app_timezone()
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(ZoneInfo("UTC"))
    end = datetime.combine(day, time.max, tzinfo=tz).astimezone(ZoneInfo("UTC"))
    return start.isoformat(), end.isoformat()


def default_period() -> tuple[date, date]:
    end = today_local()
    return end - timedelta(days=29), end


@dataclass(frozen=True)
class AnalyticsFilters:
    date_from: date | None = None
    date_to: date | None = None
    department_id: str | None = None
    doctor_user_id: str | None = None
    stage: str | None = None
    nosology: str | None = None
    has_overdue: bool | None = None
    data_quality_severity: str | None = None
    notification_status: str | None = None
    attention_reason: str | None = None
    group_by: str | None = None
    limit: int = 50
    offset: int = 0
    sort: str = "name"
    order: str = "asc"

    def period_or_default(self) -> tuple[date, date]:
        start, end = self.date_from, self.date_to
        if start is None or end is None:
            default_start, default_end = default_period()
            start = start or default_start
            end = end or default_end
        return start, end

    def as_dict(self) -> dict:
        return {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "department_id": self.department_id,
            "doctor_user_id": self.doctor_user_id,
            "stage": self.stage,
            "nosology": self.nosology,
            "has_overdue": self.has_overdue,
            "data_quality_severity": self.data_quality_severity,
            "notification_status": self.notification_status,
            "attention_reason": self.attention_reason,
            "group_by": self.group_by,
            "limit": self.limit,
            "offset": self.offset,
            "sort": self.sort,
            "order": self.order,
        }


def analytics_filters(
    date_from: str | None = Query(None, max_length=10),
    date_to: str | None = Query(None, max_length=10),
    department_id: str | None = Query(None, max_length=100),
    doctor_user_id: str | None = Query(None, max_length=100),
    stage: str | None = Query(None, max_length=40),
    nosology: str | None = Query(None, max_length=100),
    has_overdue: bool | None = None,
    data_quality_severity: str | None = Query(None, max_length=20),
    notification_status: str | None = Query(None, max_length=20),
    attention_reason: str | None = Query(None, max_length=80),
    group_by: str | None = Query(None, max_length=40),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("name", max_length=40),
    order: Literal["asc", "desc"] = "asc",
) -> AnalyticsFilters:
    start = parse_date_param(date_from, "date_from")
    end = parse_date_param(date_to, "date_to")
    if start and end and start > end:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="date_from must be <= date_to")
    if stage and stage not in STAGES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown stage")
    if data_quality_severity and data_quality_severity not in data_quality.SEVERITIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown data quality severity")
    if notification_status and notification_status not in notifications.QUEUE_STATUSES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown notification status")
    if attention_reason and attention_reason not in patient_attention.REASON_LABELS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown attention reason")
    if group_by and group_by not in GROUP_BY_FIELDS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown group_by")
    if sort not in SORT_FIELDS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown sort")
    return AnalyticsFilters(
        date_from=start,
        date_to=end,
        department_id=department_id,
        doctor_user_id=doctor_user_id,
        stage=stage,
        nosology=nosology,
        has_overdue=has_overdue,
        data_quality_severity=data_quality_severity,
        notification_status=notification_status,
        attention_reason=attention_reason,
        group_by=group_by,
        limit=limit,
        offset=offset,
        sort=sort,
        order=order,
    )
