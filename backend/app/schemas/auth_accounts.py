from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from .auth_limits import PASSWORD_MAX_LENGTH, PASSWORD_USERNAME_MAX_LENGTH


class StrictModel(BaseModel):
    """Authentication payloads must not accept privilege-bearing fields."""

    model_config = ConfigDict(extra="forbid")


class RegistrationPayload(StrictModel):
    username: str = Field(min_length=3, max_length=PASSWORD_USERNAME_MAX_LENGTH, pattern=r"^[a-z0-9][a-z0-9._-]{2,79}$")
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("display_name", mode="before")
    @classmethod
    def normalize_display_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("display_name")
    @classmethod
    def require_display_name(cls, value: str) -> str:
        if not value:
            raise ValueError("display_name must not be blank")
        return value


class PasswordChangePayload(StrictModel):
    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(min_length=12, max_length=PASSWORD_MAX_LENGTH)


class RegistrationResponse(BaseModel):
    user_id: str
    username: str
    account_status: Literal["PENDING"]
    message: str


class PasswordChangeResponse(BaseModel):
    ok: Literal[True]
