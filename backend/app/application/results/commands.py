"""Application orchestration for canonical typed-result ingestion."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any
from uuid import uuid4

from ...domains.results.models import (
    ResultIngestionCommand,
    ResultIngestionOutcome,
    SourceConflictDecision,
    SourceRunRecord,
    decide_source_conflict,
)
from ...domains.results.ports import ResultIngestionUnitOfWork


ResultIngestionUnitOfWorkProvider = Callable[[], AbstractContextManager[ResultIngestionUnitOfWork]]
Clock = Callable[[], datetime]
IdFactory = Callable[[str], str]


def utc_identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


def ingest_result_bundle(
    command: ResultIngestionCommand,
    unit_of_work_provider: ResultIngestionUnitOfWorkProvider,
    clock: Clock,
    id_factory: IdFactory = utc_identifier,
) -> ResultIngestionOutcome:
    """Persist one already-validated canonical parser payload atomically.

    ``parsed`` is an in-process parser result and can contain filesystem values
    such as media :class:`~pathlib.Path` objects; it is not a serialized API
    payload. Filesystem readers own path safety and parsing. This application
    boundary owns the authoritative database relationship check and source
    idempotency.
    """
    command = _normalize_command(command)
    parsed = command["parsed"]
    schema_id = str(parsed["schema_id"])
    summary = _summary(parsed, command)
    job_id = id_factory("folder-refresh")
    with unit_of_work_provider() as unit_of_work:
        unit_of_work.validate_target(command)
        unit_of_work.authorize(command)
        unit_of_work.lock_load_case_ingestion(command["load_case_id"])
        created_at = clock()
        decision = _decide_ingestion(unit_of_work, command)
        if decision["status"] == "SKIPPED":
            unit_of_work.add_skipped_job(
                job_id,
                command,
                {**summary, "reason": decision["reason_code"]},
                created_at,
                decision,
            )
            return _terminal_outcome(job_id, command, schema_id, summary, decision)
        if decision["status"] == "REJECTED":
            unit_of_work.add_rejected_job(
                job_id,
                command,
                {**summary, "reason": decision["reason_code"]},
                created_at,
                decision,
            )
            return _terminal_outcome(job_id, command, schema_id, summary, decision)
        run_id = id_factory("run")
        run_no = unit_of_work.next_run_no(command["load_case_id"])
        unit_of_work.add_running_job(job_id, run_id, command, created_at, decision)
        unit_of_work.ensure_catalog(command)
        unit_of_work.add_run(run_id, run_no, command, created_at)
        unit_of_work.add_metadata(run_id, job_id, command, created_at)
        if command["source_checksum"] is not None:
            unit_of_work.record_source_version(run_id, command, decision, created_at)
        unit_of_work.add_results(run_id, command, created_at)
        unit_of_work.complete_job(job_id, run_id, summary, created_at)
        unit_of_work.sync_status(command, created_at)
    return {
        "status": "IMPORTED",
        "job_id": job_id,
        "load_case_id": command["load_case_id"],
        "analysis_run_id": run_id,
        "run_no": run_no,
        "schema_id": schema_id,
        "summary": summary,
        "operation": decision["operation"],
        "reason_code": decision["reason_code"],
        "existing_analysis_run_id": decision["existing_analysis_run_id"],
        "replaced_analysis_run_id": decision["replaced_analysis_run_id"],
        "source_revision": decision["source_revision"],
    }


def _decide_ingestion(
    unit_of_work: ResultIngestionUnitOfWork,
    command: ResultIngestionCommand,
) -> SourceConflictDecision:
    """Lookup and decide after the load-case lock has been acquired."""
    if command["source_checksum"] is None:
        return {
            "status": "IMPORTED",
            "operation": "CREATED",
            "reason_code": "CHECKSUM_UNAVAILABLE_APPEND",
            "existing_analysis_run_id": None,
            "existing_run_no": None,
            "replaced_analysis_run_id": None,
            "source_revision": None,
        }

    exact = _find_exact(unit_of_work, command)
    latest = _find_latest(unit_of_work, command)
    return decide_source_conflict(command, exact, latest)


def _normalize_command(command: ResultIngestionCommand) -> ResultIngestionCommand:
    """Validate producer identity without accepting any server run identity."""
    normalized = dict(command)
    source_run_id = normalized.get("source_run_id")
    if source_run_id is not None:
        if not isinstance(source_run_id, str):
            raise ValueError("source_run_id는 문자열이어야 합니다.")
        source_run_id = source_run_id.strip()
        if not source_run_id:
            normalized.pop("source_run_id", None)
        elif len(source_run_id) > 120 or not source_run_id.isprintable():
            raise ValueError("source_run_id 형식이 올바르지 않습니다.")
        else:
            normalized["source_run_id"] = source_run_id
    policy = normalized.get("conflict_policy")
    if policy is not None and (not isinstance(policy, str) or policy not in {"SKIP", "REJECT", "REPLACE"}):
        raise ValueError("지원하지 않는 conflict_policy입니다.")
    if normalized.get("source_run_id") and policy is None:
        normalized["conflict_policy"] = "SKIP"
    return normalized


def _find_exact(
    unit_of_work: ResultIngestionUnitOfWork,
    command: ResultIngestionCommand,
) -> SourceRunRecord | None:
    return unit_of_work.find_exact_source_run(command)


def _find_latest(
    unit_of_work: ResultIngestionUnitOfWork,
    command: ResultIngestionCommand,
) -> SourceRunRecord | None:
    return unit_of_work.find_latest_source_run(command)


def _terminal_outcome(
    job_id: str,
    command: ResultIngestionCommand,
    schema_id: str,
    summary: dict[str, Any],
    decision: SourceConflictDecision,
) -> ResultIngestionOutcome:
    return {
        "status": decision["status"],
        "job_id": job_id,
        "load_case_id": command["load_case_id"],
        "analysis_run_id": decision["existing_analysis_run_id"],
        "run_no": decision["existing_run_no"],
        "schema_id": schema_id,
        "summary": summary,
        "operation": decision["operation"],
        "reason_code": decision["reason_code"],
        "existing_analysis_run_id": decision["existing_analysis_run_id"],
        "replaced_analysis_run_id": decision["replaced_analysis_run_id"],
        "source_revision": decision["source_revision"],
    }


def _summary(parsed: dict[str, Any], command: ResultIngestionCommand) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "scalar_count": len(parsed["scalars"]),
        "curve_count": len(parsed["curves"]),
        "media_count": len(parsed["media"]),
    }
    if "locations" in parsed:
        summary["location_count"] = len(parsed["locations"])
    for key in ("manifest_checksum", "bundle_fingerprint"):
        value = command["metadata"].get(key)
        if value is not None:
            summary[key] = value
    return summary
