"""Network-free notification provider for the MVP."""
from __future__ import annotations

import hashlib

from .. import config
from .provider import NotificationContext, ProviderResult


class MockNotificationProvider:
    """Deterministic provider used in all MVP environments.

    Modes:
    - success: always returns a fake provider id;
    - temporary_failure: returns a retryable safe error;
    - permanent_failure: returns a non-retryable safe error.
    """

    def __init__(self, mode: str | None = None):
        self.mode = (mode or config.NOTIFICATION_MOCK_MODE or "success").strip().lower()

    def send(self, context: NotificationContext) -> ProviderResult:
        if self.mode == "temporary_failure":
            return ProviderResult(False, provider_status="temporary_failed", error_code="mock_temporary", temporary=True)
        if self.mode == "permanent_failure":
            return ProviderResult(False, provider_status="permanent_failed", error_code="mock_permanent", temporary=False)
        digest = hashlib.sha256(f"mock:{context.notification_id}".encode("utf-8")).hexdigest()[:16]
        return ProviderResult(True, provider_message_id=f"mock_{digest}", provider_status="processed_by_mock")


def provider_from_config() -> MockNotificationProvider:
    if config.NOTIFICATION_PROVIDER != "mock":
        raise RuntimeError("notification provider is not configured for safe mock mode")
    return MockNotificationProvider()
