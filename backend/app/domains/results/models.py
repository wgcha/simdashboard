from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict


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
