"""One-time migration of a browser's legacy localStorage patient data into
the server database. Only ever additive, only ever explicit, and only
permitted while the server-side patient table is still empty — this is a
one-shot bootstrap path, not a general-purpose import."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from .. import audit, importexport
from ..deps import current_actor, get_db, require_csrf, require_session
from ..models import ImportCommitRequest, ImportPreviewRequest

router = APIRouter(prefix="/api/migrate/localstorage", tags=["migrate"], dependencies=[Depends(require_session)])


def _patient_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM patients").fetchone()["c"]


@router.post("/preview")
def migrate_preview(body: ImportPreviewRequest, conn: sqlite3.Connection = Depends(get_db)):
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
    actor: str | None = Depends(current_actor),
):
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
    result = importexport.commit_import(conn, document, "add")
    audit.record(conn, audit.MIGRATION_COMMITTED, actor=actor, detail=f"added={result['added']}")
    return result
