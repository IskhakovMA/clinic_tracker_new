"""Provider protocol for notification delivery.

Providers receive a normalized recipient and rendered body from the worker, but
the queue never stores those values. Implementations must not log PHI.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NotificationContext:
    notification_id: str
    channel: str
    recipient: str
    body: str


@dataclass(frozen=True)
class ProviderResult:
    success: bool
    provider_message_id: str | None = None
    provider_status: str = ""
    error_code: str = ""
    temporary: bool = False


class NotificationProvider(Protocol):
    def send(self, context: NotificationContext) -> ProviderResult:
        """Send one notification or return a safe failure result."""
