"""OpenAI-compatible provider abstraction for the read-only AI assistant."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from .. import config
from .transport import DirectOpener, normalize

MAX_PROVIDER_RESPONSE_BYTES = 1_000_000
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
LOCAL_ENVS = {"local", "dev", "development", "test"}


class AIProviderError(RuntimeError):
    """Controlled provider failure; message is safe for API/UI."""


class AIProviderUnavailable(AIProviderError):
    """Provider is disabled, not configured or not reachable."""


@dataclass(frozen=True)
class AIProviderResult:
    content: str
    tool_calls: list[dict[str, Any]]


@dataclass(frozen=True)
class AIProviderSettings:
    mode: Literal["configured", "auto"]
    base_url: str
    model: str
    api_key: str


@dataclass(frozen=True)
class AIProviderHealth:
    state: Literal["disabled", "probing", "available", "unavailable"]
    available: bool
    message: str
    reason: str
    settings: AIProviderSettings | None = None


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


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _require_safe_transport(base_url: str) -> None:
    """Canonical transport syntax; no insecure remote override."""
    try:
        destination = normalize(base_url)
        if destination.scheme != 'https' and destination.host not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('insecure_transport')
    except ValueError as exc:
        raise AIProviderUnavailable('ИИ-помощник не настроен: недопустимый адрес.') from exc


class OpenAICompatibleAIProvider(AIProvider):
    requires_minimized_dto = True
    def __init__(self, *, base_url: str, model: str, api_key: str = "", timeout: int = 45):
        if not base_url or not model:
            raise AIProviderUnavailable("ИИ-помощник не настроен.")
        _require_safe_transport(base_url)
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._opener = DirectOpener(base_url)

    def _messages_for_provider(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared = [dict(message) for message in messages]
        if needs_no_think_hint(self.model) and prepared:
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


_health_cache: dict[tuple[str, str, str], tuple[float, AIProviderHealth]] = {}


def needs_no_think_hint(model: str) -> bool:
    return model.lower().startswith("qwen3")


def _explicit_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return None


def _local_development_allowed() -> bool:
    return config.APP_ENV in LOCAL_ENVS and config.HOST in {"127.0.0.1", "localhost"} and not config.HTTPS


def resolve_provider_settings() -> AIProviderSettings | None:
    if config.AI_DATA_MODE != 'synthetic' or config.APP_ENV not in {'local', 'test'}:
        return None
    mode = config.AI_MODE or ""
    enabled = _explicit_bool(config.AI_ENABLED_RAW)
    explicit_provider = bool(config.AI_MODEL or config.AI_MODEL_EXPLICIT or config.AI_BASE_URL_EXPLICIT)
    if mode in {"disabled", "off", "false"} or enabled is False:
        return None
    if mode in {"configured", "explicit"} or enabled is True or config.AI_ENABLED or explicit_provider:
        return AIProviderSettings(
            mode="configured",
            base_url=config.AI_BASE_URL,
            model=config.AI_MODEL,
            api_key=config.AI_API_KEY,
        )
    if mode and mode != "auto":
        return None
    if _local_development_allowed():
        return AIProviderSettings(
            mode="auto",
            base_url=config.AI_LOCAL_BASE_URL,
            model=config.AI_LOCAL_MODEL,
            api_key="",
        )
    return None


def _probe_provider(settings: AIProviderSettings, *, timeout: float) -> AIProviderHealth:
    if not settings.base_url or not settings.model:
        return AIProviderHealth(
            state="unavailable",
            available=False,
            message="ИИ-помощник временно недоступен",
            reason="not_configured",
            settings=settings,
        )
    try:
        _require_safe_transport(settings.base_url)
    except AIProviderUnavailable:
        return AIProviderHealth('unavailable', False, 'ИИ-помощник временно недоступен', 'destination_refused')
    headers = {"Accept": "application/json"}
    if settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
    request = urllib.request.Request(
        f"{settings.base_url}/models",
        headers=headers,
        method="GET",
    )
    opener = DirectOpener(settings.base_url)
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_PROVIDER_RESPONSE_BYTES + 1)
    except TimeoutError:
        return AIProviderHealth(
            state="unavailable",
            available=False,
            message="ИИ-помощник временно недоступен",
            reason="timeout",
            settings=settings,
        )
    except (urllib.error.URLError, OSError):
        return AIProviderHealth(
            state="unavailable",
            available=False,
            message="ИИ-помощник временно недоступен",
            reason="provider_unreachable",
            settings=settings,
        )
    if len(raw) > MAX_PROVIDER_RESPONSE_BYTES:
        return AIProviderHealth(
            state="unavailable",
            available=False,
            message="ИИ-помощник временно недоступен",
            reason="provider_error",
            settings=settings,
        )
    try:
        data = json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError):
        return AIProviderHealth(
            state="unavailable",
            available=False,
            message="ИИ-помощник временно недоступен",
            reason="provider_error",
            settings=settings,
        )
    models = data.get("data")
    if isinstance(models, list):
        if not any(item.get("id") == settings.model for item in models if isinstance(item, dict)):
            message = (
                f"Локальная модель {settings.model} не загружена."
                if settings.mode == "auto"
                else "ИИ-помощник временно недоступен"
            )
            return AIProviderHealth(
                state="unavailable",
                available=False,
                message=message,
                reason="model_missing",
                settings=settings,
            )
    return AIProviderHealth(
        state="available",
        available=True,
        message="ИИ-помощник доступен",
        reason="ok",
        settings=settings,
    )


def provider_health(*, force_refresh: bool = False) -> AIProviderHealth:
    settings = resolve_provider_settings()
    if settings is None:
        return AIProviderHealth(
            state="disabled",
            available=False,
            message="ИИ-помощник временно недоступен",
            reason="disabled",
        )
    key = (settings.mode, settings.base_url, settings.model)
    now = time.monotonic()
    cached = _health_cache.get(key)
    if not force_refresh and cached and now - cached[0] < config.AI_HEALTH_CACHE_TTL_SECONDS:
        return cached[1]
    health = _probe_provider(settings, timeout=config.AI_HEALTH_TIMEOUT_SECONDS)
    _health_cache[key] = (now, health)
    return health


def clear_health_cache() -> None:
    _health_cache.clear()


def provider_reachable(*, timeout: float = 2.0) -> bool:
    settings = resolve_provider_settings()
    if settings is None:
        return False
    return _probe_provider(settings, timeout=timeout).available


def provider_configured() -> bool:
    return resolve_provider_settings() is not None


def default_provider() -> AIProvider:
    health = provider_health(force_refresh=True)
    if not health.available or not health.settings:
        raise AIProviderUnavailable(health.message)
    return OpenAICompatibleAIProvider(
        base_url=health.settings.base_url,
        model=health.settings.model,
        api_key=health.settings.api_key,
        timeout=config.AI_TIMEOUT_SECONDS,
    )
