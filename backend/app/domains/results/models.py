from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict


class AnalysisRun(TypedDict):
    id: str
    load_case_id: str
    template_execution_id: str | None
    run_no: int
    solver: str | None
    status: str
    started_at: datetime | None
    completed_at: datetime | None


class RunEvidence(TypedDict):
    scalar_count: int
    series_count: int
    failed_scalar_verdicts: int
    scalar_verdicts: int
    source_exists: bool
    result_keys: set[str]
    result_units: list[tuple[str, str | None]]
    validation_verdicts: list[str | None]


class RunSummaryReadData(TypedDict):
    runs: list[AnalysisRun]
    catalog_units: dict[str, str | None]
    evidence_by_run: dict[str, RunEvidence]


class AnalysisRunSummary(AnalysisRun):
    overall_verdict: Literal["PASS", "FAIL", "NO_DATA"]
    scalar_count: int
    series_count: int
    trust_status: Literal["TRUSTED", "WARN", "FAIL"]
    is_latest: bool


class ResultIngestionCommand(TypedDict):
    """Database-independent canonical result payload plus its provenance."""

    project_id: str
    request_id: str
    load_case_id: str
    source_type: str
    source_name: str
    source_checksum: str | None
    parser_version: str
    parsed: dict[str, Any]
    actor: str
    metadata: dict[str, Any]


class ResultIngestionOutcome(TypedDict):
    status: Literal["IMPORTED", "SKIPPED"]
    job_id: str
    load_case_id: str
    analysis_run_id: str | None
    run_no: int | None
    schema_id: str
    summary: dict[str, Any]
