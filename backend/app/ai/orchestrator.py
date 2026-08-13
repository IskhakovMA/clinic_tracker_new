"""Tool-calling orchestration for the read-only AI assistant."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from .. import config, policy
from . import prompts, tools
from .provider import AIProvider, AIProviderError, AIProviderUnavailable, default_provider
from .schemas import AIChatIn

logger = logging.getLogger("clinic.ai")


READ_ONLY_FALLBACK = (
    "Я работаю только в режиме просмотра и не могу изменять данные. "
    "Могу подсказать, где в системе посмотреть нужную информацию."
)


def _history_messages(body: AIChatIn) -> list[dict[str, str]]:
    return [{"role": item.role, "content": item.content} for item in body.history[-config.AI_MAX_HISTORY_MESSAGES:]]


def _references_from_tool_result(result: Any) -> list[dict[str, str]]:
    refs: dict[str, dict[str, str]] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            pid = value.get("patientId") or value.get("patient_id")
            if isinstance(pid, str) and pid:
                refs.setdefault(pid, {"type": "patient", "patientId": pid, "label": "Открыть пациента"})
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(result)
    return [refs[key] for key in sorted(refs)]


def _tool_call_parts(call: dict[str, Any]) -> tuple[str, str, str | dict | None]:
    call_id = str(call.get("id") or "")
    fn = call.get("function") or {}
    return call_id, str(fn.get("name") or ""), fn.get("arguments") or "{}"


def answer(
    conn: sqlite3.Connection,
    user: policy.CurrentUser,
    body: AIChatIn,
    *,
    provider: AIProvider | None = None,
) -> dict:
    if not config.AI_ENABLED:
        raise AIProviderUnavailable("ИИ-помощник не настроен.")
    if body.patientContextId and not policy.can_read_patient(conn, user, body.patientContextId):
        raise tools.ToolPermissionError("Пациент недоступен текущей роли.")

    provider = provider or default_provider()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompts.SYSTEM_PROMPT},
        {"role": "system", "content": prompts.context_prompt(user, body.patientContextId)},
        *_history_messages(body),
        {"role": "user", "content": body.message},
    ]
    definitions = tools.tool_definitions()
    references: dict[str, dict[str, str]] = {}
    tool_call_count = 0
    started = time.monotonic()

    for _ in range(config.AI_MAX_TOOL_CALLS + 1):
        result = provider.chat(
            messages=messages,
            tools=definitions,
            max_output_tokens=config.AI_MAX_OUTPUT_TOKENS,
        )
        if not result.tool_calls:
            elapsed = round(time.monotonic() - started, 3)
            logger.info("ai assistant success user_id=%s role=%s elapsed=%s tool_calls=%s",
                        user.id, user.role_id, elapsed, tool_call_count)
            return {
                "answer": result.content or "В доступных данных системы такой информации нет.",
                "references": [references[key] for key in sorted(references)],
                "usage": {"toolCalls": tool_call_count},
            }
        messages.append({
            "role": "assistant",
            "content": result.content or "",
            "tool_calls": result.tool_calls,
        })
        for call in result.tool_calls:
            if tool_call_count >= config.AI_MAX_TOOL_CALLS:
                raise AIProviderError("Достигнут лимит обращений к данным. Уточните вопрос.")
            call_id, name, raw_args = _tool_call_parts(call)
            try:
                tool_result = tools.execute_tool(name, raw_args, conn, user)
            except tools.ToolPermissionError as exc:
                tool_result = {"error": str(exc)}
            except tools.ToolExecutionError as exc:
                tool_result = {"error": str(exc)}
            tool_call_count += 1
            for ref in _references_from_tool_result(tool_result):
                references.setdefault(ref["patientId"], ref)
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": json.dumps(tool_result, ensure_ascii=False),
            })
    raise AIProviderError("Достигнут лимит обращений к данным. Уточните вопрос.")

