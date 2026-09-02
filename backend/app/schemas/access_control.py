from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


AccountStatus = Literal["PENDING", "ACTIVE", "SUSPENDED"]
ProjectRole = Literal["general", "power", "admin"]
InvitationStatus = Literal["PENDING_ACCOUNT", "PENDING_APPROVAL", "READY", "COMPLETED", "CANCELLED"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountStatusUpdate(StrictModel):
    account_status: AccountStatus
    expected_updated_at: datetime
    reason: str = Field(min_length=2, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("reason must contain at least two non-whitespace characters")
        return normalized


class GlobalAdminUpdate(StrictModel):
    is_global_admin: bool
    expected_updated_at: datetime
    reason: str = Field(min_length=2, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("reason must contain at least two non-whitespace characters")
        return normalized


class ProjectMemberCreate(StrictModel):
    user_id: str = Field(min_length=3, max_length=120)
    role: ProjectRole


class ProjectMemberUpdate(StrictModel):
    role: ProjectRole
    expected_updated_at: datetime | None = None


class InvitationCreate(StrictModel):
    employee_id: str = Field(min_length=1, max_length=80)
    desired_role: ProjectRole


class MenuPolicyUpdate(StrictModel):
    expected_version: int = Field(ge=1)
    change_note: str = Field(min_length=2, max_length=500)
    visibility: dict[ProjectRole, dict[str, bool]]

    @field_validator("change_note")
    @classmethod
    def normalize_change_note(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("change_note must contain at least two non-whitespace characters")
        return normalized


class MenuVisibility(BaseModel):
    general: bool
    power: bool
    admin: bool


class MenuPolicyItem(BaseModel):
    id: str
    label: str
    required_permission: str
    context_kind: Literal["company", "project", "system"]
    sequence_no: int
    is_policy_editable: bool
    visibility: MenuVisibility


class MenuPolicyResponse(BaseModel):
    version: int
    updated_by: str
    updated_at: datetime
    menus: list[MenuPolicyItem]


class MenuPolicyVersionSummary(BaseModel):
    version: int
    created_by: str
    created_at: datetime
    source_version: int | None = None
    change_note: str | None = None


class MenuPolicyVersionResponse(MenuPolicyVersionSummary):
    visibility: dict[ProjectRole, dict[str, bool]]
