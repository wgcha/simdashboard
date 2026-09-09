from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_ITEMS = 100
MAX_TEXT = 500
MAX_ARGUMENTS = 32


class RunMode(str, Enum):
    DIRECT = "DIRECT"
    BATCH = "BATCH"


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    AWAITING_COMPLETION = "AWAITING_COMPLETION"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"


class ProgramCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    executable_path: str = Field(min_length=1, max_length=2048)
    arguments: list[str] = Field(default_factory=list, max_length=MAX_ARGUMENTS)

    @field_validator("keywords", "arguments")
    @classmethod
    def nonempty_and_bounded(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > MAX_TEXT for value in values):
            raise ValueError("빈 값 또는 너무 긴 값은 허용되지 않습니다.")
        return values


class Program(ProgramCreate):
    id: str
    host_id: str
    available: bool


class ProgramCandidate(ProgramCreate):
    pass


class PickRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str

    @field_validator("kind")
    @classmethod
    def permitted_kind(cls, value: str) -> str:
        if value not in {"program", "files", "directory"}:
            raise ValueError("지원하지 않는 선택 유형입니다.")
        return value


class PickResult(BaseModel):
    paths: list[str]


class RunItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    input_path: str = Field(default="", max_length=2048)
    working_directory: str = Field(default="", max_length=2048)


class RunContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: str | None = Field(default=None, max_length=200)
    work_item_id: str | None = Field(default=None, max_length=200)
    task_name: str | None = Field(default=None, max_length=MAX_TEXT)
    actor: str | None = Field(default=None, max_length=200)


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_id: str = Field(min_length=1, max_length=100)
    mode: RunMode
    items: list[RunItemInput] = Field(min_length=1, max_length=MAX_ITEMS)
    context: RunContext = Field(default_factory=RunContext)
    idempotency_key: str = Field(min_length=1, max_length=200)


class Run(BaseModel):
    id: str
    source_run_id: str | None = None
    batch_id: str | None
    mode: RunMode
    status: RunStatus
    program_name: str
    program_version: str
    input_path: str
    working_directory: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    exit_code: int | None
    note: str | None
    error: str | None
    context: dict[str, Any]
    program_snapshot: dict[str, Any]


class CompleteRun(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str = Field(default="", max_length=MAX_TEXT)


class RetryRun(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    idempotency_key: str = Field(min_length=1, max_length=200)


class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_TEXT)
    program_id: str = Field(min_length=1, max_length=100)
    items: list[RunItemInput] = Field(min_length=1, max_length=MAX_ITEMS)


class SavedBatch(BatchCreate):
    id: str
