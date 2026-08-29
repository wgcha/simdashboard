from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, NotRequired, TypedDict


ConflictPolicy = Literal["SKIP", "REJECT", "REPLACE"]
IngestionStatus = Literal["IMPORTED", "SKIPPED", "REJECTED"]
IngestionOperation = Literal["CREATED", "NOOP", "REPLACED", "REJECTED"]


@dataclass(frozen=True)
class ResultIngestionTargetRead:
    project_id: str
    request_id: str
    load_case_id: str


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
    # A producer-owned identity. The internal analysis run id/no are always
    # allocated by this application and are deliberately not accepted here.
    source_run_id: NotRequired[str | None]
    conflict_policy: NotRequired[ConflictPolicy]


class SourceRunRecord(TypedDict):
    """A persisted source version joined with its immutable analysis run."""

    analysis_run_id: str
    run_no: int
    source_revision: int | None


class SourceConflictDecision(TypedDict):
    status: IngestionStatus
    operation: IngestionOperation
    reason_code: str
    existing_analysis_run_id: str | None
    existing_run_no: int | None
    replaced_analysis_run_id: str | None
    source_revision: int | None


class ResultIngestionOutcome(TypedDict):
    status: IngestionStatus
    job_id: str
    load_case_id: str
    analysis_run_id: str | None
    run_no: int | None
    schema_id: str
    summary: dict[str, Any]
    operation: IngestionOperation
    reason_code: str
    existing_analysis_run_id: str | None
    replaced_analysis_run_id: str | None
    source_revision: int | None


def source_key_for(command: ResultIngestionCommand) -> str:
    """Return the version ledger key without exposing server run identity."""
    source_run_id = command.get("source_run_id")
    return f"run:{source_run_id}" if source_run_id else f"name:{command['source_name']}"


def decide_source_conflict(
    command: ResultIngestionCommand,
    exact: SourceRunRecord | None,
    latest: SourceRunRecord | None,
) -> SourceConflictDecision:
    """Pure conflict matrix for one checksum-bearing source import.

    A missing checksum is intentionally outside the ledger: it remains the
    legacy append behaviour because it cannot prove source equality.
    """
    if exact is not None:
        return {
            "status": "SKIPPED",
            "operation": "NOOP",
            "reason_code": "IDENTICAL_COMPLETED",
            "existing_analysis_run_id": exact["analysis_run_id"],
            "existing_run_no": exact["run_no"],
            "replaced_analysis_run_id": None,
            "source_revision": exact["source_revision"],
        }

    source_run_id = command.get("source_run_id")
    if not source_run_id:
        return {
            "status": "IMPORTED",
            "operation": "CREATED",
            "reason_code": "LEGACY_APPEND",
            "existing_analysis_run_id": None,
            "existing_run_no": None,
            "replaced_analysis_run_id": None,
            "source_revision": (latest["source_revision"] or 0) + 1 if latest else 1,
        }

    if latest is None:
        return {
            "status": "IMPORTED",
            "operation": "CREATED",
            "reason_code": "SOURCE_RUN_CREATED",
            "existing_analysis_run_id": None,
            "existing_run_no": None,
            "replaced_analysis_run_id": None,
            "source_revision": 1,
        }

    policy: ConflictPolicy = command.get("conflict_policy", "SKIP")
    if policy == "SKIP":
        return {
            "status": "SKIPPED",
            "operation": "NOOP",
            "reason_code": "SOURCE_RUN_CHANGED_SKIPPED",
            "existing_analysis_run_id": latest["analysis_run_id"],
            "existing_run_no": latest["run_no"],
            "replaced_analysis_run_id": None,
            "source_revision": latest["source_revision"],
        }
    if policy == "REJECT":
        return {
            "status": "REJECTED",
            "operation": "REJECTED",
            "reason_code": "SOURCE_RUN_CHANGED_REJECTED",
            "existing_analysis_run_id": latest["analysis_run_id"],
            "existing_run_no": latest["run_no"],
            "replaced_analysis_run_id": None,
            "source_revision": latest["source_revision"],
        }
    if policy == "REPLACE":
        return {
            "status": "IMPORTED",
            "operation": "REPLACED",
            "reason_code": "SOURCE_RUN_REPLACED",
            "existing_analysis_run_id": latest["analysis_run_id"],
            "existing_run_no": latest["run_no"],
            "replaced_analysis_run_id": latest["analysis_run_id"],
            "source_revision": (latest["source_revision"] or 0) + 1,
        }
    raise ValueError("지원하지 않는 conflict_policy입니다.")
