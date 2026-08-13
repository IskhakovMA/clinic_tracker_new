"""Read-only AI assistant API."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from .. import config, policy
from ..ai import orchestrator
from ..ai.provider import AIProviderError, AIProviderUnavailable, provider_configured
from ..ai.schemas import AIChatIn, AIChatOut, AIStatusOut
from ..ai.tools import ToolPermissionError
from ..deps import abuse_rate_limit, get_db, require_active_password, require_csrf

router = APIRouter(prefix="/api/ai", tags=["ai"])


@router.get("/status", response_model=AIStatusOut)
def status_endpoint(
    _user: policy.CurrentUser = Depends(require_active_password),
):
    if not config.AI_ENABLED:
        return {"enabled": False, "available": False, "message": "ИИ-помощник не настроен"}
    if not provider_configured():
        return {"enabled": True, "available": False, "message": "ИИ-помощник не настроен"}
    return {"enabled": True, "available": True, "message": "ИИ-помощник доступен"}


@router.post(
    "/chat",
    response_model=AIChatOut,
    dependencies=[
        Depends(require_csrf),
        Depends(abuse_rate_limit("ai_chat", config.AI_RATE_LIMIT_REQUESTS, config.AI_RATE_LIMIT_WINDOW_SECONDS)),
    ],
)
def chat_endpoint(
    body: AIChatIn,
    conn: sqlite3.Connection = Depends(get_db),
    user: policy.CurrentUser = Depends(require_active_password),
):
    try:
        return orchestrator.answer(conn, user, body)
    except ToolPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AIProviderUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except AIProviderError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
