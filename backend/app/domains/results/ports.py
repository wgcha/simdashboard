from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .models import ResultIngestionCommand, RunSummaryReadData


class AnalysisRunSummaryRepository(Protocol):
    def read_for_load_case(self, load_case_id: str) -> RunSummaryReadData: ...


class ResultIngestionUnitOfWork(Protocol):
    """Atomic persistence port for a canonical result bundle."""

    def lock_load_case_ingestion(self, load_case_id: str) -> None: ...
    def source_completed(self, command: ResultIngestionCommand) -> bool: ...
    def claim_source_identity(self, command: ResultIngestionCommand, claimed_at: datetime) -> bool: ...
    def validate_target(self, command: ResultIngestionCommand) -> None: ...
    def authorize(self, command: ResultIngestionCommand) -> None: ...
    def next_run_no(self, load_case_id: str) -> int: ...
    def add_skipped_job(
        self,
        job_id: str,
        command: ResultIngestionCommand,
        summary: dict[str, Any],
        created_at: datetime,
    ) -> None: ...
    def add_running_job(
        self,
        job_id: str,
        run_id: str,
        command: ResultIngestionCommand,
        created_at: datetime,
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
    def add_results(
        self,
        run_id: str,
        command: ResultIngestionCommand,
        created_at: datetime,
    ) -> None: ...
    def complete_job(self, job_id: str, run_id: str, summary: dict[str, Any]) -> None: ...
    def sync_status(self, command: ResultIngestionCommand, completed_at: datetime) -> None: ...
