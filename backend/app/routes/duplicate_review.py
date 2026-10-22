"""Реестр возможных дублей пациентов (WP2.4-D).

Каждый обработчик проверяет права сам (в сервисе, под блокировкой записи).
Роль без доступа к реестру получает один и тот же 403 на любой путь реестра,
без сведений о кандидатах; неизвестный кандидат у роли с доступом — 404.
Ответы не кэшируются (Cache-Control: no-store для /api/).
"""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from .. import audit, policy
from ..deps import abuse_rate_limit, get_db, require_active_password, require_csrf
from ..models import (DuplicateClinicalDecisionIn, DuplicateMarkIn, DuplicateTechnicalReviewIn,
                      DuplicateUnmarkIn)
from ..services import duplicate_review as registry

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])

FORBIDDEN = "Недостаточно прав для реестра возможных дублей"


def _call(conn: sqlite3.Connection, user: policy.CurrentUser, action: str, fn, *args, **kwargs):
    try:
        return fn(conn, user, *args, **kwargs)
    except registry.RegistryForbidden:
        audit.record(conn, audit.ACCESS_DENIED, result=audit.RESULT_DENIED,
                     detail=f"action=duplicate_registry_{action}", **audit.actor_ctx(user))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)
    except registry.CandidateNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Кандидат не найден")
    except registry.RegistryInvalid as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))
    except registry.RegistryConflict as e:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT,
                            content={"detail": str(e), "code": e.code, **e.extra})
    except audit.AuditWriteFailed:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Действие не выполнено: журнал аудита недоступен. Повторите попытку позже.")
    except sqlite3.OperationalError as e:
        code = getattr(e, "sqlite_errorcode", None)
        if code is not None and (code & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="База данных временно занята, повторите попытку") from e
        raise


@router.post("/scans", dependencies=[Depends(require_csrf), Depends(abuse_rate_limit("duplicate_scan", 6))])
def start_scan(conn: sqlite3.Connection = Depends(get_db), user: policy.CurrentUser = Depends(require_active_password)):
    return _call(conn, user, "scan", registry.run_scan)


@router.get("/scans", dependencies=[Depends(abuse_rate_limit("duplicate_registry_view", 120))])
def scans(limit: int = Query(20, ge=1, le=100), conn: sqlite3.Connection = Depends(get_db),
          user: policy.CurrentUser = Depends(require_active_password)):
    return _call(conn, user, "scans", registry.list_scans, limit=limit)


@router.get("/candidates", dependencies=[Depends(abuse_rate_limit("duplicate_registry_view", 120))])
def candidates(
    state: Literal["open", "under_review", "needs_clinical_review", "not_duplicate", "resolved"] | None = None,
    strength: Literal["strong", "medium", "weak"] | None = None,
    reason: Literal["policy", "name_age", "name_diagnosis", "name_nosology", "phone"] | None = None,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=1_000_000),
    conn: sqlite3.Connection = Depends(get_db), user: policy.CurrentUser = Depends(require_active_password),
):
    return _call(conn, user, "list", registry.list_candidates, state=state, strength=strength, reason=reason,
                 limit=limit, offset=offset)


@router.get("/candidates/{candidate_id}", dependencies=[Depends(abuse_rate_limit("duplicate_candidate_view", 120))])
def candidate(candidate_id: str, conn: sqlite3.Connection = Depends(get_db),
              user: policy.CurrentUser = Depends(require_active_password)):
    return _call(conn, user, "view", registry.get_candidate, candidate_id)


@router.post("/candidates/{candidate_id}/technical-review",
             dependencies=[Depends(require_csrf), Depends(abuse_rate_limit("duplicate_registry_write", 60))])
def technical_review(candidate_id: str, body: DuplicateTechnicalReviewIn, conn: sqlite3.Connection = Depends(get_db),
                     user: policy.CurrentUser = Depends(require_active_password)):
    return _call(conn, user, "technical_review", registry.technical_review, candidate_id, body.action, body.comment,
                 body.expectedVersion)


@router.post("/candidates/{candidate_id}/decision",
             dependencies=[Depends(require_csrf), Depends(abuse_rate_limit("duplicate_registry_write", 60))])
def decision(candidate_id: str, body: DuplicateClinicalDecisionIn, conn: sqlite3.Connection = Depends(get_db),
             user: policy.CurrentUser = Depends(require_active_password)):
    return _call(conn, user, "decision", registry.clinical_decision, candidate_id, body.decision, body.reason,
                 body.expectedVersion)


@router.post("/candidates/{candidate_id}/mark",
             dependencies=[Depends(require_csrf), Depends(abuse_rate_limit("duplicate_registry_write", 60))])
def mark(candidate_id: str, body: DuplicateMarkIn, conn: sqlite3.Connection = Depends(get_db),
         user: policy.CurrentUser = Depends(require_active_password)):
    return _call(conn, user, "mark", registry.mark_erroneous, candidate_id, body.patientId, body.reason,
                 body.expectedVersion)


@router.get("/erroneous", dependencies=[Depends(abuse_rate_limit("duplicate_registry_view", 120))])
def erroneous(conn: sqlite3.Connection = Depends(get_db), user: policy.CurrentUser = Depends(require_active_password)):
    return _call(conn, user, "erroneous", registry.list_erroneous)


@router.post("/erroneous/{patient_id}/unmark",
             dependencies=[Depends(require_csrf), Depends(abuse_rate_limit("duplicate_registry_write", 60))])
def unmark(patient_id: str, body: DuplicateUnmarkIn, conn: sqlite3.Connection = Depends(get_db),
           user: policy.CurrentUser = Depends(require_active_password)):
    return _call(conn, user, "unmark", registry.unmark_erroneous, patient_id, body.markId, body.reason)


@router.post("/candidates/{candidate_id}/confirmations/{confirmation_id}/reason",
             dependencies=[Depends(require_csrf), Depends(abuse_rate_limit("duplicate_confirmation_reason", 30))])
def confirmation_reason(candidate_id: str, confirmation_id: str, conn: sqlite3.Connection = Depends(get_db),
                        user: policy.CurrentUser = Depends(require_active_password)):
    """POST с CSRF: чтение причины — аудируемое действие, а не кэшируемый GET."""
    return _call(conn, user, "confirmation_reason", registry.read_confirmation_reason, candidate_id, confirmation_id)
