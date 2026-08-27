"""OpenAI-compatible provider abstraction for the read-only AI assistant."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from .. import config

MAX_PROVIDER_RESPONSE_BYTES = 1_000_000
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class AIProviderError(RuntimeError):
    """Controlled provider failure; message is safe for API/UI."""


class AIProviderUnavailable(AIProviderError):
    """Provider is disabled, not configured or not reachable."""


@dataclass(frozen=True)
class AIProviderResult:
    content: str
    tool_calls: list[dict[str, Any]]


class AIProvider(ABC):
    @abstractmethod
    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_output_tokens: int,
    ) -> AIProviderResult:
        """Return model content and OpenAI-compatible tool calls."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802 - urllib API
        raise urllib.error.HTTPError(req.full_url, code, "redirects are not allowed", headers, fp)


class OpenAICompatibleAIProvider(AIProvider):
    def __init__(self, *, base_url: str, model: str, api_key: str = "", timeout: int = 45):
        if not base_url or not model:
            raise AIProviderUnavailable("ИИ-помощник не настроен.")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirectHandler)

    def _messages_for_provider(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = [dict(message) for message in messages]
        if self.model.lower().startswith("qwen3") and prepared:
            content = str(prepared[0].get("content") or "")
            if "/no_think" not in content:
                prepared[0]["content"] = "/no_think\n" + content
        return prepared

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_output_tokens: int,
    ) -> AIProviderResult:
        payload = {
            "model": self.model,
            "messages": self._messages_for_provider(messages),
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": max_output_tokens,
            "temperature": 0.2,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
        except TimeoutError as exc:
            raise AIProviderUnavailable("ИИ-помощник сейчас недоступен.") from exc
        except urllib.error.URLError as exc:
            raise AIProviderUnavailable("ИИ-помощник сейчас недоступен.") from exc
        except OSError as exc:
            raise AIProviderUnavailable("ИИ-помощник сейчас недоступен.") from exc
        if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
            raise AIProviderError("Ответ провайдера слишком большой.")
        try:
            data = json.loads(raw.decode("utf-8"))
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError, ValueError, UnicodeDecodeError) as exc:
            raise AIProviderError("Провайдер вернул некорректный ответ.") from exc
        content = THINK_BLOCK_RE.sub("", str(message.get("content") or "")).strip()
        return AIProviderResult(
            content=content,
            tool_calls=message.get("tool_calls") or [],
        )


def provider_reachable(*, timeout: float = 2.0) -> bool:
    if not provider_configured():
        return False
    headers = {"Accept": "application/json"}
    if config.AI_API_KEY:
        headers["Authorization"] = f"Bearer {config.AI_API_KEY}"
    request = urllib.request.Request(
        f"{config.AI_BASE_URL}/models",
        headers=headers,
        method="GET",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    except (TimeoutError, urllib.error.URLError, OSError):
        return False
    if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
        return False
    try:
        data = json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError):
        return False
    models = data.get("data")
    if isinstance(models, list):
        return any(item.get("id") == config.AI_MODEL for item in models if isinstance(item, dict))
    return True


def provider_configured() -> bool:
    return bool(config.AI_ENABLED and config.AI_BASE_URL and config.AI_MODEL)


def default_provider() -> AIProvider:
    if not provider_configured():
        raise AIProviderUnavailable("ИИ-помощник не настроен.")
    return OpenAICompatibleAIProvider(
        base_url=config.AI_BASE_URL,
        model=config.AI_MODEL,
        api_key=config.AI_API_KEY,
        timeout=config.AI_TIMEOUT_SECONDS,
    )
