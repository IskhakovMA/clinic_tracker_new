"""Read-only audit view. Системный аудит видит только роль с can_view_audit
(Разработчик); returns anonymised rows only (see app/audit.py)."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import audit, config, policy
from ..deps import get_db, require_active_password

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
def list_audit_events(
    limit: int = Query(50, ge=1, le=config.AUDIT_PAGE_LIMIT_MAX),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if not policy.can_view_audit(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return {
        "total": audit.count_events(conn),
        "limit": limit,
        "offset": offset,
        "events": audit.list_events(conn, limit=limit, offset=offset),
        "retentionDays": config.AUDIT_RETENTION_DAYS,
    }
