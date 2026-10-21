"""Пациенты. Каждый endpoint работает через централизованную политику:
список фильтруется patient_scope_filter, объектные операции проходят
can_read/can_write; недоступный пациент неотличим от несуществующего (404).
"""
import sqlite3

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from .. import audit, config, idempotency, policy, store
from ..deps import get_db, require_active_password, require_csrf, user_rate_limit
from ..models import (
    ChecklistItemIn,
    ConsiliumDecisionMarkIn,
    EventAddIn,
    EventFieldUpdateIn,
    EventVersionIn,
    AttentionIssueCloseIn,
    AttentionIssueCreateIn,
    NoteIn,
    PatientCreateIn,
    PatientFieldUpdateIn,
    StageChangeIn,
    TreatingDoctorIn,
)
from ..services import patient_attention, patient_create

router = APIRouter(prefix="/api/patients", tags=["patients"])

# WP2.4-A: не больше config.PATIENT_CREATE_RATE_LIMIT созданий в минуту на пользователя.
_CREATE_LIMIT = Depends(user_rate_limit(
    "patient_create",
    lambda: config.PATIENT_CREATE_RATE_LIMIT,
    lambda: config.PATIENT_CREATE_RATE_LIMIT_WINDOW_SECONDS,
))


def _not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")


def _forbidden(conn: sqlite3.Connection, user: policy.CurrentUser, detail: str):
    """Отказ по правам для видимого объекта/действия. Фиксируется в аудите
    без каких-либо медицинских данных."""
    audit.record(conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED,
                 detail=detail, **audit.actor_ctx(user))
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _require_readable(conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str) -> None:
    if not policy.can_read_patient(conn, user, patient_id):
        _not_found()


def _require_writable(conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str) -> None:
    # Сначала видимость (иначе 404 — существование не раскрывается),
    # затем право записи (read-only роли получают 403).
    _require_readable(conn, user, patient_id)
    if not policy.can_write_patient(conn, user, patient_id):
        _forbidden(conn, user, "action=patient_write")


@router.get("")
def list_patients(
    attention: Literal["active", "none"] | None = None,
    attention_reason: str | None = Query(None, max_length=80),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    if attention_reason and attention_reason not in patient_attention.REASON_LABELS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="unknown attention reason")
    return store.list_patients(
        conn,
        policy.patient_scope_filter(user),
        attention=attention,
        attention_reason=attention_reason,
    )


@router.get("/attention/reasons")
def attention_reasons(
    user: policy.CurrentUser = Depends(require_active_password),
):
    return {"reasons": patient_attention.reason_options(), "commentMaxLength": patient_attention.comment_max_length()}


@router.get("/unresolved")
def list_unresolved(
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    """Очередь ручного сопоставления legacy-врачей. Просмотр — Разработчик
    и глобальные read-only роли; само сопоставление — только Разработчик
    (PUT /{id}/treating-doctor)."""
    if not policy.can_view_unresolved(user):
        _forbidden(conn, user, "area=unresolved")
    return {"patients": store.unresolved_patients(conn)}


def _database_busy(exc: sqlite3.OperationalError) -> None:
    """503 только для ожидаемой блокировки SQLite (SQLITE_BUSY/SQLITE_LOCKED):
    транзакция уже откатана, запрос можно повторить. Иная ошибка SQL — дефект (500)."""
    code = getattr(exc, "sqlite_errorcode", None)
    if code is None or (code & 0xFF) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        raise exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="База данных временно занята. Пациент не создан, повторите попытку",
    ) from exc


@router.post("", dependencies=[Depends(require_csrf), _CREATE_LIMIT], status_code=status.HTTP_201_CREATED)
def create_patient(
    body: PatientCreateIn,
    response: Response,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
    idempotency_key: str | None = Header(None, alias=idempotency.KEY_HEADER),
):
    """Создание карточки (WP2.4-A): права, выбор врача, карточка, маршрут,
    события, обязательный аудит и ответ — одна транзакция BEGIN IMMEDIATE
    в services.patient_create. Автора и источник (form) записывает сервер.

    WP2.4-B: с заголовком Idempotency-Key (UUID v4) повтор того же запроса
    возвращает исходную карточку (201, Idempotent-Replayed: true) без нового
    создания; тот же ключ с другими данными — 422 IDEMPOTENCY_KEY_REUSED.
    Без заголовка — прежние правила WP2.4-A (совместимость внешних клиентов,
    решение U5): гарантии идемпотентности нет."""
    # Быстрый отказ до блокировки записи; окончательная проверка — в транзакции.
    if not policy.can_create_patient_card(user):
        _forbidden(conn, user, "action=patient_create")
    try:
        key = idempotency.parse_key(idempotency_key)
    except idempotency.InvalidIdempotencyKey as e:
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            content={"detail": str(e), "code": idempotency.INVALID_KEY_CODE})

    try:
        if key is None:
            return patient_create.create_patient_card(conn, user, body)
        card, replayed = patient_create.create_patient_card_idempotent(conn, user, body, key)
        if replayed:
            response.headers[idempotency.REPLAYED_HEADER] = "true"
        return card
    except idempotency.IdempotencyKeyReused as e:
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            content={"detail": str(e), "code": idempotency.REUSED_KEY_CODE})
    except patient_create.PatientReplayNotFound:
        _not_found()
    except patient_create.ReplayAuditUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось подтвердить результат запроса: журнал аудита недоступен. "
                   "Повторите попытку позже тем же запросом",
        )
    except patient_create.PatientCreateForbidden as e:
        audit.record(conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED,
                     detail=e.detail, **audit.actor_ctx(user))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)
    except audit.AuditWriteFailed:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось сохранить пациента вместе с записью журнала аудита. "
                   "Пациент не создан, повторите попытку позже",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    except sqlite3.OperationalError as e:
        _database_busy(e)


@router.get("/{patient_id}")
def get_patient(
    patient_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_readable(conn, user, patient_id)
    p = store.patient_to_dict(conn, patient_id)
    if p is None:
        _not_found()
    return p


@router.get("/{patient_id}/attention")
def get_patient_attention(
    patient_id: str,
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    result = patient_attention.list_for_patient(conn, user, patient_id, limit=limit, offset=offset)
    if not result.get("found"):
        _not_found()
    return result


@router.post("/{patient_id}/attention", dependencies=[Depends(require_csrf)], status_code=status.HTTP_201_CREATED)
def create_attention_issue(
    patient_id: str,
    body: AttentionIssueCreateIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    try:
        issue = patient_attention.create_issue(conn, user, patient_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if issue is None:
        _not_found()
    return {"issue": issue, "attention": patient_attention.list_for_patient(conn, user, patient_id)}


@router.post("/{patient_id}/attention/{issue_id}/close", dependencies=[Depends(require_csrf)])
def close_attention_issue(
    patient_id: str,
    issue_id: str,
    body: AttentionIssueCloseIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    try:
        issue = patient_attention.close_issue(conn, user, patient_id, issue_id, body)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if issue is None:
        _not_found()
    return {"issue": issue, "attention": patient_attention.list_for_patient(conn, user, patient_id)}


@router.patch("/{patient_id}", dependencies=[Depends(require_csrf)])
def update_patient_field(
    patient_id: str,
    body: PatientFieldUpdateIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    if body.field == "doctor" and not policy.can_change_treating_doctor(user):
        # Legacy-текст врача участвует в отображении ответственных; менять его
        # (как и само закрепление) может только Разработчик.
        _forbidden(conn, user, "action=patient_doctor_text")
    try:
        ok = store.update_patient_field_with_actor(conn, patient_id, body.field, body.value, actor_user_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    if not ok:
        _not_found()
    # Field *name* only — never the value, which is patient data. Diagnosis
    # and physician-requested structured values get explicit safe event types
    # without detail.
    if body.field == "diagnosis":
        audit.record(conn, audit.PATIENT_DIAGNOSIS_CHANGED, patient_id=patient_id, **audit.actor_ctx(user))
    elif body.field in {"pgiResult", "pgiResultDate"}:
        audit.record(conn, audit.PGI_RESULT_CHANGED, patient_id=patient_id, **audit.actor_ctx(user))
    else:
        audit.record(conn, audit.PATIENT_UPDATED, patient_id=patient_id,
                     detail=f"field={body.field}", **audit.actor_ctx(user))
    return store.patient_to_dict(conn, patient_id)


@router.put("/{patient_id}/treating-doctor", dependencies=[Depends(require_csrf)])
def change_treating_doctor(
    patient_id: str,
    body: TreatingDoctorIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    """Смена лечащего врача — только Разработчик. Прежний врач и его
    заведующий теряют доступ немедленно (scope вычисляется на каждый запрос)."""
    if not policy.can_change_treating_doctor(user):
        _require_readable(conn, user, patient_id)  # вне scope — 404, не 403
        _forbidden(conn, user, "action=change_treating_doctor")
    if body.userId is not None:
        doctor_user_id, error = policy.resolve_create_doctor(conn, user, body.userId)
        if error is not None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=error)
    else:
        doctor_user_id = None
    if not store.set_treating_doctor(conn, patient_id, doctor_user_id):
        _not_found()
    audit.record(conn, audit.TREATING_DOCTOR_CHANGED, patient_id=patient_id,
                 target_user_id=doctor_user_id, **audit.actor_ctx(user))
    return store.patient_to_dict(conn, patient_id)


@router.delete("/{patient_id}", dependencies=[Depends(require_csrf)])
def delete_patient(
    patient_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    # Удаление карточки каскадом удалило бы записи консилиума, подписанные
    # решения и удалённые (архивные) события; история ради удаления не
    # уничтожается. Проверка и удаление — одна транзакция в store.
    try:
        deleted = store.delete_patient(conn, patient_id)
    except store.PatientDeleteBlocked as e:
        audit.record(conn, audit.PATIENT_DELETED, result=audit.RESULT_DENIED, patient_id=patient_id,
                     detail=f"{e.reason}={e.count}", **audit.actor_ctx(user))
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    if not deleted:
        _not_found()
    audit.record(conn, audit.PATIENT_DELETED, patient_id=patient_id, **audit.actor_ctx(user))
    return {"deleted": True}


@router.post("/{patient_id}/stage", dependencies=[Depends(require_csrf)])
def change_stage(
    patient_id: str,
    body: StageChangeIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)

    def hook(outcome: dict) -> None:
        # Перевод — в той же транзакции, что и изменение: без записи аудита этап,
        # история и событие не сохраняются. Решение ОК перевод не записывает.
        audit.record_required_or_fail(conn, audit.PATIENT_UPDATED, patient_id=patient_id,
                                      detail=f"stage={body.newStage}", **audit.actor_ctx(user))

    try:
        ok = store.change_stage(conn, patient_id, body.newStage, body.lastEventUpdate,
                                expected_event_id=body.lastEventId, expected_event_version=body.lastEventVersion,
                                expected_stage=body.expectedStage,
                                expected_history_entry_id=body.expectedHistoryEntryId,
                                audit_hook=hook)
    except store.FactConflict as e:
        # detail — текст для пользователя (его показывает действующий клиент), code — для программ.
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(e), "code": e.code})
    except store.EventConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except store.DecisionRequiresSeparateOperation as e:
        # Прежний контракт «решение вместе с переводом» больше не действует: явный отказ, не игнорирование.
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": str(e), "code": e.code})
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    except audit.AuditWriteFailed:
        _audit_unavailable("Этап не изменен")
    except sqlite3.OperationalError:
        _database_busy("Этап не изменен")
    if not ok:
        p = store.patient_to_dict(conn, patient_id)
        if p is None:
            _not_found()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Stage unchanged")
    return store.patient_to_dict(conn, patient_id)


def _history_audit_hook(conn, user, patient_id: str, occ_index: int, action: str):
    """Обязательный аудит правки истории маршрута в транзакции самой правки."""
    def hook() -> None:
        audit.record_required_or_fail(conn, audit.PATIENT_UPDATED, patient_id=patient_id,
                                      detail=f"history={occ_index};action={action}", **audit.actor_ctx(user))
    return hook


@router.patch("/{patient_id}/history/{occ_index}/note", dependencies=[Depends(require_csrf)])
def update_note(
    patient_id: str,
    occ_index: int,
    body: NoteIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    try:
        ok = store.update_note(conn, patient_id, occ_index, body.note,
                               audit_hook=_history_audit_hook(conn, user, patient_id, occ_index, "note"))
    except audit.AuditWriteFailed:
        _audit_unavailable("Заметка не сохранена")
    except sqlite3.OperationalError:
        _database_busy("Заметка не сохранена")
    if not ok:
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.patch("/{patient_id}/history/{occ_index}/checklist", dependencies=[Depends(require_csrf)])
def update_checklist(
    patient_id: str,
    occ_index: int,
    body: ChecklistItemIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    try:
        ok = store.update_checklist_item(conn, patient_id, occ_index, body.item, body.checked,
                                         audit_hook=_history_audit_hook(conn, user, patient_id, occ_index, "checklist"))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    except audit.AuditWriteFailed:
        _audit_unavailable("Чек-лист не изменен")
    except sqlite3.OperationalError:
        _database_busy("Чек-лист не изменен")
    if not ok:
        _not_found()
    return store.patient_to_dict(conn, patient_id)


@router.post("/{patient_id}/history/{occ_index}/events", dependencies=[Depends(require_csrf)])
def add_event(
    patient_id: str,
    occ_index: int,
    body: Optional[EventAddIn] = None,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    action = "event_placeholder" if body and body.purpose == "placeholder" else "event_add"

    def hook(event_id: str) -> None:
        audit.record_required_or_fail(conn, audit.PATIENT_UPDATED, patient_id=patient_id,
                                      detail=f"history={occ_index};event_id={event_id};action={action}",
                                      **audit.actor_ctx(user))

    try:
        event_id = store.add_event(
            conn, patient_id, occ_index, body.expectedEventIds if body else None,
            placeholder=bool(body and body.purpose == "placeholder"), audit_hook=hook,
        )
    except store.EventConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    except audit.AuditWriteFailed:
        _audit_unavailable("Событие не добавлено")
    except sqlite3.OperationalError:
        _database_busy("Событие не добавлено")
    if event_id is None:
        _not_found()
    # createdEventId: какое событие создал именно этот запрос — клиент правит
    # его, а не «последнее» в ответе (оно может быть чужим).
    return {**store.patient_to_dict(conn, patient_id), "createdEventId": event_id}


def _event_gone():
    # Прежняя адресация по позиции могла изменить другое событие после
    # правки списка, поэтому она отключена: события меняются только по id.
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Событие адресуется по идентификатору. Обновите страницу",
    )


@router.delete("/{patient_id}/history/{occ_index}/events/{event_index}", dependencies=[Depends(require_csrf)])
def remove_event_by_position(
    patient_id: str,
    occ_index: int,
    event_index: int,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    _event_gone()


@router.patch("/{patient_id}/history/{occ_index}/events/{event_index}", dependencies=[Depends(require_csrf)])
def update_event_by_position(
    patient_id: str,
    occ_index: int,
    event_index: int,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    _event_gone()


def _event_not_found():
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")


def _event_audit_hook(conn: sqlite3.Connection, user, patient_id: str, event_type: str, action: str):
    """Аудит удаления и восстановления: id события и признаки (есть ли плановая
    и фактическая даты, отметка решения ОК) — без самих дат, решений и ФИО.

    Запись обязательна и пишется в транзакции изменения: если она не
    удалась, событие остаётся в прежнем состоянии."""
    def hook(row) -> None:
        facts = store.event_facts(row)
        detail = (f"event_id={row['id']};history={row['history_seq']};action={action};"
                  f"planned={facts['planned']};actual={facts['actual']};decision={facts['decision']}")
        try:
            audit.record_required(conn, event_type, patient_id=patient_id, detail=detail,
                                  **audit.actor_ctx(user))
        except sqlite3.Error as exc:
            raise _AuditWriteFailed() from exc
    return hook


# Обязательная запись аудита не сохранена — изменение откатывается (общий класс audit.py).
_AuditWriteFailed = audit.AuditWriteFailed


def _audit_unavailable(what: str = "Событие не изменено"):
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Не удалось сохранить изменение вместе с записью журнала аудита. {what}, повторите попытку позже",
    )


def _database_busy(what: str = "Событие не изменено"):
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"База данных временно занята. {what}, повторите попытку",
    )


@router.patch("/{patient_id}/events/{event_id}", dependencies=[Depends(require_csrf)])
def update_event(
    patient_id: str,
    event_id: str,
    body: EventFieldUpdateIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)

    def hook(row) -> None:
        audit.record_required_or_fail(
            conn, audit.PATIENT_UPDATED, patient_id=patient_id,
            detail=f"event_id={event_id};history={row['history_seq']};action=event_update;field={body.field}",
            **audit.actor_ctx(user))

    try:
        row = store.update_event(conn, patient_id, event_id, body.field, body.value, body.version, audit_hook=hook)
    except store.EventConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    except audit.AuditWriteFailed:
        _audit_unavailable()
    except sqlite3.OperationalError:
        _database_busy()
    if row is None:
        _event_not_found()
    return store.patient_to_dict(conn, patient_id)


@router.delete("/{patient_id}/events/{event_id}", dependencies=[Depends(require_csrf)])
def delete_event(
    patient_id: str,
    event_id: str,
    version: int = Query(..., ge=1, le=2**63 - 1),
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    hook = _event_audit_hook(conn, user, patient_id, audit.PATIENT_EVENT_DELETED, "event_delete")
    try:
        row = store.delete_event(conn, patient_id, event_id, version, user.id, audit_hook=hook)
    except store.EventConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except _AuditWriteFailed:
        _audit_unavailable()
    except sqlite3.OperationalError:
        _database_busy()
    if row is None:
        _event_not_found()
    return store.patient_to_dict(conn, patient_id)


@router.post("/{patient_id}/events/{event_id}/restore", dependencies=[Depends(require_csrf)])
def restore_event(
    patient_id: str,
    event_id: str,
    body: EventVersionIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    _require_writable(conn, user, patient_id)
    hook = _event_audit_hook(conn, user, patient_id, audit.PATIENT_EVENT_RESTORED, "event_restore")
    try:
        row = store.restore_event(conn, patient_id, event_id, body.version, audit_hook=hook)
    except store.EventConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    except _AuditWriteFailed:
        _audit_unavailable()
    except sqlite3.OperationalError:
        _database_busy()
    if row is None:
        _event_not_found()
    return store.patient_to_dict(conn, patient_id)


@router.post("/{patient_id}/events/{event_id}/consilium-decision", dependencies=[Depends(require_csrf)])
def mark_consilium_decision(
    patient_id: str,
    event_id: str,
    body: ConsiliumDecisionMarkIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    """Ручная отметка решения ОК в явно выбранном событии текущего этапа ОК (WP2.2-B).

    Порядок проверок как у остальных правок пациента: сначала видимость (404 —
    существование чужого пациента не раскрывается), затем право записи и право
    отметки решения (403). Этап пациента, факт события и решения председателя
    не меняются; повторная отметка — 409."""
    _require_writable(conn, user, patient_id)
    if not policy.can_mark_consilium_decision(user):
        _forbidden(conn, user, "action=consilium_decision_mark")

    def hook(result: dict) -> None:
        # Решение и запись аудита — одна транзакция: без записи журнала решение не сохраняется.
        detail = f"event_id={result['event_id']};history={result['history_seq']};source=manual"
        if result["archived_acknowledged"]:
            # Сотрудник подтвердил, что видел архивные решения этого этапа (только id событий).
            detail += (f";archived_decisions_acknowledged={len(result['archived_acknowledged'])}"
                       f";archived_event_ids={','.join(result['archived_acknowledged'])}")
        audit.record_required_or_fail(
            conn, audit.CONSILIUM_DECISION_RECORDED, patient_id=patient_id, detail=detail,
            **audit.actor_ctx(user))

    try:
        result = store.mark_consilium_decision(
            conn, patient_id, event_id, body.decision, body.version,
            expected_stage=body.expectedStage, expected_history_entry_id=body.expectedHistoryEntryId,
            user_id=user.id, acknowledged_archived_event_ids=body.acknowledgedArchivedEventIds, audit_hook=hook)
    except store.DecisionConflict as e:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT,
                            content={"detail": str(e), "code": e.code, **e.extra})
    except audit.AuditWriteFailed:
        _audit_unavailable("Решение ОК не записано")
    except sqlite3.OperationalError:
        _database_busy("Решение ОК не записано")
    if result is None:
        _event_not_found()
    return store.patient_to_dict(conn, patient_id)


@router.get("/{patient_id}/events/deleted")
def list_deleted_events(
    patient_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    # Удалённые события видит и восстанавливает тот, кто может править пациента.
    _require_writable(conn, user, patient_id)
    return {"events": store.deleted_events(conn, patient_id)}
