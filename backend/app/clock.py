"""Institution clock: Europe/Moscow, independent of server and client settings.

Every scheduling decision of the oncology consilium (future-only booking,
schedule generation, the "today" of the calendar and analytics) asks this
module for the current moment. Tests replace now_moscow() with a fixed value.

The institution time zone is a constant, not an environment setting. When the
tz database is missing (for example Windows without the tzdata package) the
fixed offset UTC+3 is used explicitly: it is exact for Moscow since 26.10.2014.
There is no silent fallback to UTC.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from contextvars import ContextVar
from functools import wraps
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

INSTITUTION_TIMEZONE = "Europe/Moscow"
_MOSCOW_FIXED = timezone(timedelta(hours=3), "MSK")


def institution_tz() -> tzinfo:
    try:
        return ZoneInfo(INSTITUTION_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        return _MOSCOW_FIXED


def now_moscow() -> datetime:
    """Current moment in the institution time zone (aware datetime)."""
    return datetime.now(timezone.utc).astimezone(institution_tz())


_calculation_now: ContextVar[datetime | None] = ContextVar("institution_calculation_now", default=None)


def current_moscow() -> datetime:
    """Один момент для связанных чтений, без изменения системных часов."""
    return _calculation_now.get() or now_moscow()


def consistent(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _calculation_now.get() is not None:
            return function(*args, **kwargs)
        token = _calculation_now.set(now_moscow())
        try:
            return function(*args, **kwargs)
        finally:
            _calculation_now.reset(token)
    return wrapped


def today_moscow() -> date:
    return current_moscow().date()


def slot_moment(scheduled_date: str, scheduled_time: str) -> datetime | None:
    """Aware moment of a slot (canonical YYYY-MM-DD and HH:MM), or None for
    values that are not canonical (legacy rows are never compared)."""
    try:
        day = date.fromisoformat(scheduled_date) if len(scheduled_date) == 10 else None
        hours, minutes = scheduled_time.split(":")
        moment = time(int(hours), int(minutes)) if len(scheduled_time) == 5 else None
    except (TypeError, ValueError):
        return None
    if day is None or moment is None:
        return None
    return datetime.combine(day, moment, tzinfo=institution_tz())


def is_strictly_future(scheduled_date: str, scheduled_time: str, now: datetime | None = None) -> bool:
    """True when the slot starts strictly after `now` (Moscow). At 12:03 the
    slot 12:30 is in the future, 12:00 and 12:03 are not."""
    moment = slot_moment(scheduled_date, scheduled_time)
    return moment is not None and moment > (now or now_moscow())


def is_strictly_past(scheduled_date: str, scheduled_time: str, now: datetime | None = None) -> bool:
    """True when the slot started strictly before `now` (Moscow). At 12:00 the
    slot 11:59 is in the past, 12:00 is not (WP2.3-B3, B3-04)."""
    moment = slot_moment(scheduled_date, scheduled_time)
    return moment is not None and moment < (now or now_moscow())
