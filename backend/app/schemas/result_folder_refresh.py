"""Public contracts for the server-owned master result-folder refresh API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


RefreshItemStatus = Literal["IMPORTED", "SKIPPED", "FAILED"]


class MasterResultRefreshItem(BaseModel):
    """The outcome for one manifest discovered below the configured root."""

    manifest_path: str = Field(description="Path relative to SIMDASH_IMPORT_ROOT")
    status: RefreshItemStatus
    load_case_id: str | None = None
    analysis_run_id: str | None = None
    message: str | None = None


class MasterResultRefreshResponse(BaseModel):
    """A best-effort refresh summary; one bad bundle never stops its siblings."""

    scanned_count: int
    imported_count: int
    skipped_count: int
    failed_count: int
    items: list[MasterResultRefreshItem]
