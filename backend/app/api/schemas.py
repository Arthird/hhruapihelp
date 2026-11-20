from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class MaxUserPayload(BaseModel):
    id: int = Field(gt=0)
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    is_bot: bool = False


class MaxChatPayload(BaseModel):
    id: int = Field(gt=0)
    type: Literal["dialog", "chat", "channel"]
    title: str | None = None


class MaxMessagePayload(BaseModel):
    id: str | None = None
    text: str = ""
    timestamp: int | None = None

    @field_validator("text")
    @classmethod
    def limit_text(cls, value: str) -> str:
        if len(value) > 4000:
            raise ValueError("message text exceeds MAX limit of 4000 characters")
        return value


class CallbackPayload(BaseModel):
    callback_id: str
    payload: str = ""
    timestamp: int | None = None


class UpdateIn(BaseModel):
    update_type: str
    update_timestamp: int
    user_locale: str | None = None
    chat: MaxChatPayload
    user: MaxUserPayload
    message: MaxMessagePayload | None = None
    callback: CallbackPayload | None = None


class AttachmentOut(BaseModel):
    type: Literal["file"] = "file"
    filename: str
    mime_type: str
    content: str


class ReplyOut(BaseModel):
    text: str
    format: Literal["markdown", "html"] = "markdown"
    attachments: list[AttachmentOut] = Field(default_factory=list)


class UpdateOut(BaseModel):
    reply: ReplyOut | None = None
    already_delivered: bool = False


class DeliveredOut(BaseModel):
    delivered: bool


class ClearOut(BaseModel):
    closed: int


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    knowledge_version_id: int | None = None
