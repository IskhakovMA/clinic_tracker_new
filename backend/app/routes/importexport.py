"""Импорт и экспорт. Оба ограничены областью видимости актора:
экспорт фильтруется тем же scope-условием, что и список пациентов;
импорт не может закрепить пациента за врачом вне scope актора.
"""
import json
import sqlite3
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from .. import audit, config, export_signing, importexport, policy, store
from ..deps import abuse_rate_limit, get_db, require_active_password, require_csrf
from ..models import ImportCommitRequest, ImportPreviewRequest

router = APIRouter(prefix="/api", tags=["import-export"])


def _deny(conn: sqlite3.Connection, user: policy.CurrentUser, detail: str):
    audit.record(conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED,
                 detail=detail, **audit.actor_ctx(user))
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _provenance_view(provenance: dict) -> dict:
    """Статус происхождения для интерфейса — без секретов и данных файла."""
    return {
        "status": provenance["status"],
        "message": provenance["message"],
        "keyId": provenance.get("keyId"),
        "generatedAt": provenance.get("generatedAt"),
        "fileSha256": provenance["fileSha256"],
        "confirmationRequired": provenance["confirmationRequired"],
    }


PRESERVE_IDS_MESSAGE = (
    "Будут созданы карточки с сохранением внутренних идентификаторов из подписанного файла. "
    "Это не подтверждает, что именно эти карточки ранее существовали в базе. Нужна отдельная "
    "отметка подтверждения разработчика."
)


def _id_preservation(inspection, document, mode: str) -> dict:
    """Нужно ли подтверждение создания карточек с прежними id (только числа)."""
    required = bool(inspection.ids.verified and mode in ("add", "replace") and document.patients)
    return {"required": required, "patients": len(document.patients) if required else 0,
            "message": PRESERVE_IDS_MESSAGE if required else ""}


@router.post("/import/preview", dependencies=[Depends(abuse_rate_limit("import_preview", 20))])
def import_preview(
    body: ImportPreviewRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if not policy.can_import(user):
        _deny(conn, user, "action=import")
    try:
        inspection = importexport.inspect_import(body.raw)
    except importexport.ImportStructureError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    provenance = _provenance_view(inspection.provenance)
    if inspection.ids.internal_ids and not importexport.internal_ids_allowed(user):
        # Решение B: до любых обращений к данным — ответ не зависит от состояния базы.
        return {"valid": False, "code": "internal_ids_forbidden", "provenance": provenance,
                "errors": [{"index": None, "field": "id", "message": importexport.INTERNAL_IDS_MESSAGE}]}
    document = inspection.document
    if document is None:
        return {"valid": False, "errors": inspection.errors, "provenance": provenance}
    scope_errors = importexport.scope_errors(conn, document, user)
    if scope_errors:
        return {"valid": False, "errors": scope_errors, "provenance": provenance}
    report = importexport.preview_report(conn, document, user)
    modes = [mode for mode in ("add", "update", "replace") if policy.can_import(user, mode)]
    # Что каждый доступный пользователю режим сделал бы с записями консилиума —
    # тот же расчёт, которым commit_import отклоняет операцию (только числа).
    report["consiliumImpact"] = {mode: importexport.consilium_import_impact(conn, document, mode) for mode in modes}
    # То же для удалённых событий медицинской истории (по карточкам и по точным
    # id событий файла) и для повторного импорта: только числа и правила.
    report["archivedEventsImpact"] = {
        mode: importexport.archive_import_impact(conn, document, mode, user, inspection.ids) for mode in modes
    }
    report["duplicateImpact"] = {
        mode: importexport.duplicate_import_impact(conn, inspection.ids, mode, user) for mode in modes
    }
    # Совпадения с карточками вне области пользователя: только обобщённый отказ.
    report["accessImpact"] = {
        mode: importexport.restricted_import_impact(conn, inspection.ids, mode, user) for mode in modes
    }
    # Решение A: что update/replace сделали бы с существующими карточками.
    report["overwriteImpact"] = {
        mode: importexport.overwrite_impact(conn, document, mode, user, inspection.ids)
        for mode in modes if mode in ("update", "replace")
    }
    # Решение C: создание карточек с сохранением id подписанного файла.
    report["idPreservation"] = {mode: _id_preservation(inspection, document, mode) for mode in modes}
    return {"valid": True, "errors": [], "provenance": provenance, **report}


@router.post("/import/commit", dependencies=[Depends(require_csrf), Depends(abuse_rate_limit("import_commit", 10))])
def import_commit(
    body: ImportCommitRequest,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if not policy.can_import(user, body.mode):
        _deny(conn, user, f"action=import mode={body.mode}")
    ctx = audit.actor_ctx(user)
    try:
        inspection = importexport.inspect_import(body.raw)
    except importexport.ImportStructureError as e:
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} structure_rejected", **ctx)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    provenance = inspection.provenance
    file_ref = f"provenance={provenance['status']} file_sha256={provenance['fileSha256']}"
    if inspection.ids.internal_ids and not importexport.internal_ids_allowed(user):
        # Решение B: одинаковый отказ для своих, чужих, удалённых и несуществующих id.
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} {file_ref} reason=internal_ids_forbidden", **ctx)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=importexport.INTERNAL_IDS_MESSAGE)
    if provenance["status"] in (export_signing.INVALID, export_signing.UNSUPPORTED):
        # Подпись есть, но не проверяется: файл отклоняется, а не импортируется
        # как неподписанный.
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} {file_ref} key_id={provenance.get('keyId') or '-'}", **ctx)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=provenance["message"])
    document = inspection.document
    if document is None:
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} invalid_records={len(inspection.errors)} {file_ref}", **ctx)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail={"errors": inspection.errors})
    confirmed = False
    if provenance["confirmationRequired"]:
        confirmation = body.unsignedConfirmation
        if confirmation is None:
            audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                         detail=f"mode={body.mode} {file_ref} confirmed=0 reason=confirmation_required", **ctx)
            raise HTTPException(status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                                detail=importexport.UNSIGNED_WARNING)
        if confirmation.fileSha256 != provenance["fileSha256"] or confirmation.mode != body.mode:
            audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                         detail=f"mode={body.mode} {file_ref} confirmed=0 reason=confirmation_mismatch", **ctx)
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail="Подтверждение относится к другому файлу или режиму. Проверьте файл заново и подтвердите импорт.",
            )
        confirmed = True
    preserve = _id_preservation(inspection, document, body.mode)
    # Решение C: отдельное подтверждение разработчика, привязанное к файлу,
    # режиму и действию; подтверждение происхождения его не заменяет.
    # Проверяется в транзакции импорта после всех конфликтов.
    confirmation = body.preserveIdsConfirmation
    preserve_confirmed = bool(
        confirmation is not None and confirmation.fileSha256 == provenance["fileSha256"]
        and confirmation.mode == body.mode and importexport.internal_ids_allowed(user)
    )
    scope_errors = importexport.scope_errors(conn, document, user)
    if scope_errors:
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} scope_rejected={len(scope_errors)} {file_ref}", **ctx)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail={"errors": scope_errors})

    patients, events = len(document.patients), importexport.document_event_count(document)

    def audit_hook(result: dict) -> None:
        # Обязательная запись (fail-closed) в транзакции импорта: без неё
        # импорт не сохраняется. Только счётчики, статус и хеш файла.
        audit.record_required(
            conn, audit.IMPORT_COMMITTED,
            detail=(f"mode={body.mode} added={result['added']} updated={result['updated']} "
                    f"skipped={result['skipped']} unchanged={result['unchanged']} "
                    f"appended_events={result['appendedEvents']} {file_ref} "
                    f"key_id={provenance.get('keyId') or '-'} "
                    f"unsigned_confirmed={int(confirmed)} patients={patients} events={events}"),
            **ctx,
        )
        if preserve["required"]:
            # По одной обязательной записи на каждую карточку, созданную с прежним
            # id (patient_id указан — граница видна в журнале). Формулировка
            # нейтральная: не утверждается, что это та же ранее удалённая карточка.
            for created_id in result["createdIds"]:
                audit.record_required(
                    conn, audit.IMPORT_IDS_PRESERVED, patient_id=created_id,
                    detail=(f"action=create_with_internal_ids mode={body.mode} cards={result['added']} "
                            f"{file_ref} key_id={provenance.get('keyId') or '-'} preserve_confirmed=1"),
                    **ctx,
                )

    try:
        result = importexport.commit_import(conn, document, body.mode, user, ids=inspection.ids,
                                            audit_hook=audit_hook, preserve_ids_confirmed=preserve_confirmed)
    except importexport.ImportPreserveIdsRequired:
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} {file_ref} reason=preserve_ids_confirmation_required", **ctx)
        raise HTTPException(status_code=status.HTTP_428_PRECONDITION_REQUIRED, detail=PRESERVE_IDS_MESSAGE)
    except importexport.ImportConflictError as e:
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} consilium_records={e.records} signed={e.signed} {file_ref}", **ctx)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except importexport.ImportArchiveConflictError as e:
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} archived_event_patients={e.patients} archived_events={e.events} {file_ref}",
                     **ctx)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except importexport.ImportDuplicateError as e:
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} existing_records={e.records} {file_ref}", **ctx)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except importexport.ImportOverwriteError as e:
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} overwrite_conflicts={e.patients} {file_ref}", **ctx)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except importexport.ImportInternalIdsError:
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} {file_ref} reason=internal_ids_forbidden", **ctx)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=importexport.INTERNAL_IDS_MESSAGE)
    except importexport.ImportRestrictedError as e:
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_DENIED,
                     detail=f"mode={body.mode} restricted_conflicts={e.records} {file_ref}", **ctx)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except sqlite3.Error:
        audit.record(conn, audit.IMPORT_COMMITTED, result=audit.RESULT_ERROR,
                     detail=f"mode={body.mode} {file_ref} reason=not_saved", **ctx)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Импорт не сохранён: не удалось записать данные вместе с журналом аудита. Данные не изменены.",
        )
    return {**result, "provenance": provenance["status"]}


def _safe_export_filename() -> str:
    return f"patients_export_{date.today().isoformat()}.json"


# GET, но с обязательным X-CSRF-Token: SameSite=Lax cookie отправляется и при
# межсайтовой навигации верхнего уровня, а навигация не может задать заголовок.
@router.get("/export", dependencies=[Depends(require_csrf)])
def export_patients(
    ids: str | None = Query(None, description="Comma-separated patient ids; omit for all"),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if not policy.can_export(user):
        _deny(conn, user, "action=export")
    # Экспорт всегда фильтруется scope на сервере: изменённые frontend-параметры
    # (чужие ids) молча отбрасываются тем же условием, что и список пациентов.
    scoped = store.list_patients(conn, policy.patient_scope_filter(user))
    if ids:
        wanted = {i for i in ids.split(",") if i}
        selected = [p for p in scoped if p["id"] in wanted]
    else:
        selected = scoped
    exported = []
    for patient in selected:
        row = dict(patient)
        # `attention` is a live UI summary for the current user scope, not part
        # of the import/export patient record format. Historical attention
        # issues remain in the database and backups.
        row.pop("attention", None)
        # Только активные события; стабильные id пациента, записей маршрута и
        # событий входят в подписанный файл (версия — нет). Удалённые события
        # остаются в базе и резервных копиях и в файл не попадают.
        row["history"] = [
            {**entry, "events": [{k: v for k, v in ev.items() if k != "version"} for ev in entry["events"]]}
            for entry in row["history"]
        ]
        exported.append(row)

    try:
        key = export_signing.active_key()
        # Неверно настроенные ключи проверки: эта установка не смогла бы проверить
        # собственную выгрузку — не выпускать её.
        export_signing.keyring()
    except export_signing.SigningKeyError:
        # Без ключа файл не выдаётся вовсе: ни «якобы подписанный», ни со слабым ключом.
        audit.record(conn, audit.EXPORT, result=audit.RESULT_ERROR, detail="reason=signing_key_unavailable",
                     **audit.actor_ctx(user))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Экспорт недоступен: ключ подписи выгрузок не настроен. Обратитесь к администратору.",
        )
    payload = export_signing.sign({
        "schemaVersion": config.SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "patients": exported,
    }, key)
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    audit.record(conn, audit.EXPORT, detail=f"records={len(exported)} key_id={key.key_id}",
                 **audit.actor_ctx(user))
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{_safe_export_filename()}"',
            "Cache-Control": "no-store",
        },
    )
