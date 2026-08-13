"""AI assistant API schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .. import config
from ..models import StrictRequestModel


class AIHistoryMessage(StrictRequestModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=config.AI_MAX_USER_MESSAGE_CHARS)

    @field_validator("content")
    @classmethod
    def _trim_content(cls, value: str) -> str:
        return value.strip()


class AIChatIn(StrictRequestModel):
    message: str = Field(..., min_length=1, max_length=config.AI_MAX_USER_MESSAGE_CHARS)
    history: list[AIHistoryMessage] = Field(default_factory=list)
    patientContextId: str | None = Field(None, max_length=100)

    @field_validator("message")
    @classmethod
    def _trim_message(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _bounded_history(self):
        if len(self.history) > config.AI_MAX_HISTORY_MESSAGES:
            raise ValueError("history is too long")
        return self


class AIReference(StrictRequestModel):
    type: Literal["patient"]
    patientId: str = Field(..., min_length=1, max_length=100)
    label: str = Field("Открыть пациента", max_length=80)


class AIUsage(StrictRequestModel):
    toolCalls: int = Field(0, ge=0)


class AIChatOut(StrictRequestModel):
    answer: str
    references: list[AIReference] = Field(default_factory=list)
    usage: AIUsage


class AIStatusOut(StrictRequestModel):
    enabled: bool
    available: bool
    message: str

