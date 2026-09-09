from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PairingCreate(StrictModel):
    device_id: str = Field(min_length=8, max_length=200)


class PairingTokenResponse(StrictModel):
    pairing_token: str
    expires_at: datetime

    _normalize_expiry = field_validator("expires_at")(_utc)


class DevicePairPreview(StrictModel):
    device_id: str = Field(min_length=8, max_length=200)


class DevicePair(StrictModel):
    device_id: str = Field(min_length=8, max_length=200)
    host_name: str = Field(min_length=1, max_length=255)
    device_secret: str = Field(min_length=43, max_length=512)


class DeviceRead(StrictModel):
    id: str
    device_id: str
    host_name: str
    user_id: str
    created_at: datetime
    revoked_at: datetime | None = None

    _normalize_dates = field_validator("created_at", "revoked_at")(_utc)


class DeviceSessionResponse(StrictModel):
    token: str
    expires_at: datetime
    binding_id: str
    user_id: str

    _normalize_expiry = field_validator("expires_at")(_utc)


class ManagedContext(StrictModel):
    request_id: str = Field(min_length=1, max_length=200)
    work_item_id: str = Field(min_length=1, max_length=200)
    task_name: str = Field(min_length=1, max_length=500)
    actor: str = Field(min_length=1, max_length=200)


class DeviceAuthorize(StrictModel):
    binding_id: str = Field(min_length=1, max_length=200)
    session_token: str = Field(min_length=1, max_length=512)
    action: Literal["catalog", "history", "execute", "retry", "complete"]
    context: ManagedContext | None = None


class DeviceAuthorizeResponse(StrictModel):
    user_id: str
    display_name: str
    context: ManagedContext | None = None
    grant_id: str | None = None


class LocalRun(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    source_run_id: str | None = Field(default=None, max_length=200)
    batch_id: str | None = Field(default=None, max_length=200)
    mode: Literal["DIRECT", "BATCH"]
    status: Literal["QUEUED", "RUNNING", "AWAITING_COMPLETION", "SUCCEEDED", "FAILED", "COMPLETED", "INTERRUPTED"]
    program_name: str = Field(min_length=1, max_length=120)
    program_version: str = Field(min_length=1, max_length=80)
    input_path: str = Field(max_length=2048)
    working_directory: str = Field(max_length=2048)
    created_at: str = Field(min_length=1, max_length=80)
    started_at: str | None = Field(default=None, max_length=80)
    completed_at: str | None = Field(default=None, max_length=80)
    exit_code: int | None = None
    note: str | None = Field(default=None, max_length=500)
    error: str | None = Field(default=None, max_length=2000)
    context: ManagedContext
    program_snapshot: dict[str, Any] = Field(default_factory=dict)


class DeviceRunEvent(StrictModel):
    sequence: int = Field(ge=0, le=9_223_372_036_854_775_807)
    grant_id: str = Field(min_length=1, max_length=200)
    run: LocalRun


class DeviceEvents(StrictModel):
    binding_id: str = Field(min_length=1, max_length=200)
    events: list[DeviceRunEvent] = Field(min_length=1, max_length=100)


class AcceptedEvent(StrictModel):
    run_id: str
    sequence: int


class DeviceEventsResponse(StrictModel):
    accepted: list[AcceptedEvent]


class CentralRun(LocalRun):
    binding_id: str
    device_id: str
    host_name: str
    actor_user_id: str
    synced_at: datetime

    _normalize_synced_at = field_validator("synced_at")(_utc)
