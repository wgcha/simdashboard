"""Public contracts for the server-owned master result-folder refresh API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


RefreshItemStatus = Literal["IMPORTED", "SKIPPED", "FAILED"]
RefreshOperation = Literal["CREATED", "NOOP", "REPLACED", "REJECTED"]
ImportJobStatus = Literal["RUNNING", "COMPLETED", "SKIPPED", "REJECTED", "FAILED"]


class MasterResultRefreshItem(BaseModel):
    """The outcome for one manifest discovered below the configured root."""

    manifest_path: str = Field(description="Path relative to SIMDASH_IMPORT_ROOT")
    status: RefreshItemStatus
    load_case_id: str | None = None
    analysis_run_id: str | None = None
    message: str | None = None
    operation: RefreshOperation | None = None
    reason_code: str | None = None
    existing_analysis_run_id: str | None = None
    replaced_analysis_run_id: str | None = None
    source_revision: int | None = None


class MasterResultRefreshResponse(BaseModel):
    """A best-effort refresh summary; one bad bundle never stops its siblings."""

    scanned_count: int
    imported_count: int
    skipped_count: int
    failed_count: int
    items: list[MasterResultRefreshItem]


class ResultImportHistoryItem(BaseModel):
    """One persisted import attempt, including no-op and rejected attempts."""

    id: str
    load_case_id: str
    status: ImportJobStatus
    source_type: str | None = None
    source_folder: str
    source_checksum: str | None = None
    source_run_id: str | None = None
    conflict_policy: str | None = None
    outcome_reason: str | None = None
    operation: RefreshOperation | None = None
    analysis_run_id: str | None = None
    replaced_analysis_run_id: str | None = None
    source_revision: int | None = None
    created_at: datetime
    completed_at: datetime | None = None
    retryable: bool


class ResultImportHistoryResponse(BaseModel):
    items: list[ResultImportHistoryItem]
    total: int
    counts: dict[str, int]
