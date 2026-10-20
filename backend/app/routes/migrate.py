"""One-time migration of a browser's legacy localStorage patient data into
the server database. Only ever additive, only ever explicit, and only
permitted while the server-side patient table is still empty — this is a
one-shot bootstrap path, not a general-purpose import."""
import sqlite3
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from .. import audit, importexport, policy
from ..deps import abuse_rate_limit, get_db, require_active_password, require_csrf
from ..models import ImportCommitRequest, ImportPreviewRequest
from .importexport import decision_audit, historical_decisions_request, historical_decisions_view

router = APIRouter(prefix="/api/migrate/localstorage", tags=["migrate"])


def _patient_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS c FROM patients").fetchone()["c"]


@router.post("/preview", dependencies=[Depends(abuse_rate_limit("migration_preview", 20))])
def migrate_preview(
    body: ImportPreviewRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if not policy.can_import(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    try:
        inspection = importexport.inspect_import(body.raw)
    except importexport.ImportStructureError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if inspection.ids.internal_ids and not importexport.internal_ids_allowed(user):
        # То же правило, что и у обычного импорта: файл с внутренними id — только разработчику.
        # Без сведений о базе: отказ одинаков при любом её состоянии.
        return {"valid": False, "code": "internal_ids_forbidden",
                "errors": [{"index": None, "field": "id", "message": importexport.INTERNAL_IDS_MESSAGE}]}
    document, errors = inspection.document, inspection.errors
    if document is None:
        return {"valid": False, "errors": errors, "serverHasData": _patient_count(conn) > 0}
    scope_errors = importexport.scope_errors(conn, document, user)
    if scope_errors:
        return {"valid": False, "errors": scope_errors, "serverHasData": _patient_count(conn) > 0}
    report = importexport.preview_report(conn, document, user)
    # WP2.2-B: решения ОК из локальных данных переносятся только с отдельным подтверждением.
    report["historicalDecisions"] = {**historical_decisions_view(document, "add"),
                                     "fileSha256": inspection.provenance["fileSha256"]}
    return {"valid": True, "errors": [], "serverHasData": _patient_count(conn) > 0, **report}


@router.post("/commit", dependencies=[Depends(require_csrf), Depends(abuse_rate_limit("migration_commit", 10))])
def migrate_commit(
    body: ImportCommitRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if not policy.can_import(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    try:
        inspection = importexport.inspect_import(body.raw)
    except importexport.ImportStructureError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    # Запрет внутренних id — раньше любых проверок состояния базы.
    if inspection.ids.internal_ids and not importexport.internal_ids_allowed(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=importexport.INTERNAL_IDS_MESSAGE)
    if _patient_count(conn) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Server already has patient data; use regular import instead of migration.",
        )
    document, errors = inspection.document, inspection.errors
    if document is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"errors": errors})
    scope_errors = importexport.scope_errors(conn, document, user)
    if scope_errors:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail={"errors": scope_errors})
    import_id = uuid.uuid4().hex
    historical = historical_decisions_request(body, inspection.provenance, import_id)

    def audit_hook(result: dict) -> None:
        # Перенос локальных данных — неподписанный импорт: запись аудита
        # обязательна и сохраняется вместе с данными.
        ctx = audit.actor_ctx(user)
        audit.record_required(conn, audit.MIGRATION_COMMITTED,
                              detail=(f"added={result['added']} provenance=local_migration "
                                      f"file_status={inspection.provenance['status']} "
                                      f"file_sha256={inspection.provenance['fileSha256']} "
                                      f"import_id={import_id} imported_decisions={result['importedDecisions']}"),
                              **ctx)
        decision_audit(conn, ctx, result, import_id=import_id, file_sha256=inspection.provenance["fileSha256"])

    try:
        # Пустота базы проверяется повторно под блокировкой записи.
        result = importexport.commit_import(conn, document, "add", user, audit_hook=audit_hook,
                                            require_empty=True, historical_decisions=historical)
    except importexport.ImportHistoricalDecisionsRequired as e:
        return JSONResponse(status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                            content={"detail": str(e), "code": e.code, "decisions": e.decisions})
    except importexport.ImportNotEmptyError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except (importexport.ImportConflictError, importexport.ImportArchiveConflictError,
            importexport.ImportDuplicateError, importexport.ImportRestrictedError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except sqlite3.Error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Перенос не сохранён: не удалось записать данные вместе с журналом аудита.")
    return result
