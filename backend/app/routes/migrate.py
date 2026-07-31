"""One-time migration of a browser's legacy localStorage patient data into
the server database. Only ever additive, only ever explicit, and only
permitted while the server-side patient table is still empty — this is a
one-shot bootstrap path, not a general-purpose import."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, importexport, policy
from ..deps import get_db, require_active_password, require_csrf
from ..models import ImportCommitRequest, ImportPreviewRequest

router = APIRouter(prefix="/api/migrate/localstorage", tags=["migrate"])


def _patient_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM patients").fetchone()["c"]


@router.post("/preview")
def migrate_preview(
    body: ImportPreviewRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if not policy.can_import(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    try:
        document, errors = importexport.validate_import(body.raw)
    except importexport.ImportStructureError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if document is None:
        return {"valid": False, "errors": errors, "serverHasData": _patient_count(conn) > 0}
    report = importexport.preview_report(conn, document)
    return {"valid": True, "errors": [], "serverHasData": _patient_count(conn) > 0, **report}


@router.post("/commit", dependencies=[Depends(require_csrf)])
def migrate_commit(
    body: ImportCommitRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if not policy.can_import(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if _patient_count(conn) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Server already has patient data; use regular import instead of migration.",
        )
    try:
        document, errors = importexport.validate_import(body.raw)
    except importexport.ImportStructureError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if document is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"errors": errors})
    scope_errors = importexport.scope_errors(conn, document, user)
    if scope_errors:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail={"errors": scope_errors})
    result = importexport.commit_import(conn, document, "add", user)
    audit.record(conn, audit.MIGRATION_COMMITTED, detail=f"added={result['added']}",
                 **audit.actor_ctx(user))
    return result
