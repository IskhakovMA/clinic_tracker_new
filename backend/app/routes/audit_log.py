"""Read-only audit view. Session-protected like everything else; returns
anonymised rows only (see app/audit.py for what is and isn't recorded)."""
import sqlite3

from fastapi import APIRouter, Depends, Query

from .. import audit, config
from ..deps import get_db, require_session

router = APIRouter(prefix="/api/audit", tags=["audit"], dependencies=[Depends(require_session)])


@router.get("")
def list_audit_events(
    limit: int = Query(50, ge=1, le=config.AUDIT_PAGE_LIMIT_MAX),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
):
    return {
        "total": audit.count_events(conn),
        "limit": limit,
        "offset": offset,
        "events": audit.list_events(conn, limit=limit, offset=offset),
        "retentionDays": config.AUDIT_RETENTION_DAYS,
    }
