from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VOCPostCreate(BaseModel):
    """The client may submit only the message; identity is server-owned."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(max_length=10_000)

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("content")
    @classmethod
    def require_safe_content(cls, value: str) -> str:
        if not value:
            raise ValueError("content must not be blank")
        if "\x00" in value:
            raise ValueError("content must not contain NUL")
        return value


class VOCPostResponse(BaseModel):
    id: str
    author_user_id: str
    author_username: str
    author_display_name: str
    content: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class VOCPostListResponse(BaseModel):
    items: list[VOCPostResponse]
    total: int
    limit: int
    offset: int
