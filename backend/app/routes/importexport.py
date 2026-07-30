import json
import sqlite3
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from .. import audit, config, importexport, store
from ..deps import current_actor, get_db, require_csrf, require_session
from ..models import ImportCommitRequest, ImportPreviewRequest

router = APIRouter(prefix="/api", tags=["import-export"], dependencies=[Depends(require_session)])


@router.post("/import/preview")
def import_preview(
    body: ImportPreviewRequest,
    conn: sqlite3.Connection = Depends(get_db),
    actor: str | None = Depends(current_actor),
):
    try:
        document, errors = importexport.validate_import(body.raw)
    except importexport.ImportStructureError as e:
        audit.record(conn, audit.IMPORT_PREVIEW, actor=actor, result=audit.RESULT_DENIED, detail="structure_rejected")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if document is None:
        audit.record(
            conn, audit.IMPORT_PREVIEW, actor=actor, result=audit.RESULT_DENIED,
            detail=f"invalid_records={len(errors)}",
        )
        return {"valid": False, "errors": errors}
    report = importexport.preview_report(conn, document)
    audit.record(conn, audit.IMPORT_PREVIEW, actor=actor, detail=f"records={report['totalRecords']}")
    return {"valid": True, "errors": [], **report}


@router.post("/import/commit", dependencies=[Depends(require_csrf)])
def import_commit(
    body: ImportCommitRequest,
    conn: sqlite3.Connection = Depends(get_db),
    actor: str | None = Depends(current_actor),
):
    try:
        document, errors = importexport.validate_import(body.raw)
    except importexport.ImportStructureError as e:
        audit.record(
            conn, audit.IMPORT_COMMITTED, actor=actor, result=audit.RESULT_DENIED,
            detail=f"mode={body.mode} structure_rejected",
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if document is None:
        audit.record(
            conn, audit.IMPORT_COMMITTED, actor=actor, result=audit.RESULT_DENIED,
            detail=f"mode={body.mode} invalid_records={len(errors)}",
        )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"errors": errors})
    result = importexport.commit_import(conn, document, body.mode)
    audit.record(
        conn, audit.IMPORT_COMMITTED, actor=actor,
        detail=f"mode={body.mode} added={result['added']} updated={result['updated']} skipped={result['skipped']}",
    )
    return result


def _safe_export_filename() -> str:
    return f"patients_export_{date.today().isoformat()}.json"


@router.get("/export")
def export_patients(
    ids: str | None = Query(None, description="Comma-separated patient ids; omit for all"),
    conn: sqlite3.Connection = Depends(get_db),
    actor: str | None = Depends(current_actor),
):
    all_patients = store.list_patients(conn)
    if ids:
        wanted = {i for i in ids.split(",") if i}
        selected = [p for p in all_patients if p["id"] in wanted]
    else:
        selected = all_patients

    payload = {
        "schemaVersion": config.SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "patients": selected,
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    audit.record(conn, audit.EXPORT, actor=actor, detail=f"records={len(selected)}")
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{_safe_export_filename()}"',
            "Cache-Control": "no-store",
        },
    )
