from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .models import (
    ResultIngestionCommand,
    ResultIngestionTargetRead,
    ResultMediaAssetRead,
    ResultMediaBlobRead,
    RunSummaryReadData,
    SourceConflictDecision,
    SourceRunRecord,
)


class AnalysisRunSummaryRepository(Protocol):
    def read_for_load_case(self, load_case_id: str) -> RunSummaryReadData: ...


class ResultIngestionQueryPort(Protocol):
    def get_result_ingestion_target(self, load_case_id: str) -> ResultIngestionTargetRead | None: ...

    def get_quality_threshold(
        self,
        project_id: Any,
        criterion_key: str,
        default: float,
    ) -> float: ...

    def list_catalog(self, load_case_id: str) -> dict[str, dict[str, Any]]: ...


class ResultMediaQueryPort(Protocol):
    """Read-side port for one result media asset and its optional DB blob."""

    def get_result_media_asset(self, asset_id: str) -> ResultMediaAssetRead | None: ...

    def get_result_media_blob(self, blob_id: str) -> ResultMediaBlobRead | None: ...


class ResultIngestionUnitOfWork(Protocol):
    """Atomic persistence port for a canonical result bundle."""

    def lock_load_case_ingestion(self, load_case_id: str) -> None: ...
    def find_exact_source_run(
        self,
        command: ResultIngestionCommand,
    ) -> SourceRunRecord | None: ...
    def find_latest_source_run(
        self,
        command: ResultIngestionCommand,
    ) -> SourceRunRecord | None: ...
    def validate_target(self, command: ResultIngestionCommand) -> None: ...
    def authorize(self, command: ResultIngestionCommand) -> None: ...
    def next_run_no(self, load_case_id: str) -> int: ...
    def add_skipped_job(
        self,
        job_id: str,
        command: ResultIngestionCommand,
        summary: dict[str, Any],
        created_at: datetime,
        decision: SourceConflictDecision,
    ) -> None: ...
    def add_rejected_job(
        self,
        job_id: str,
        command: ResultIngestionCommand,
        summary: dict[str, Any],
        created_at: datetime,
        decision: SourceConflictDecision,
    ) -> None: ...
    def add_running_job(
        self,
        job_id: str,
        run_id: str,
        command: ResultIngestionCommand,
        created_at: datetime,
        decision: SourceConflictDecision,
    ) -> None: ...
    def ensure_catalog(self, command: ResultIngestionCommand) -> None: ...
    def add_run(
        self,
        run_id: str,
        run_no: int,
        command: ResultIngestionCommand,
        created_at: datetime,
    ) -> None: ...
    def add_metadata(
        self,
        run_id: str,
        job_id: str,
        command: ResultIngestionCommand,
        created_at: datetime,
    ) -> None: ...
    def record_source_version(
        self,
        run_id: str,
        command: ResultIngestionCommand,
        decision: SourceConflictDecision,
        created_at: datetime,
    ) -> None: ...
    def add_results(
        self,
        run_id: str,
        command: ResultIngestionCommand,
        created_at: datetime,
    ) -> None: ...
    def complete_job(
        self,
        job_id: str,
        run_id: str,
        summary: dict[str, Any],
        completed_at: datetime,
    ) -> None: ...
    def sync_status(self, command: ResultIngestionCommand, completed_at: datetime) -> None: ...
