"""Application orchestration for canonical typed-result ingestion."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from typing import Any
from uuid import uuid4

from ...domains.results.models import ResultIngestionCommand, ResultIngestionOutcome
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
    parsed = command["parsed"]
    schema_id = str(parsed["schema_id"])
    summary = _summary(parsed, command)
    job_id = id_factory("folder-refresh")
    with unit_of_work_provider() as unit_of_work:
        unit_of_work.validate_target(command)
        unit_of_work.authorize(command)
        unit_of_work.lock_load_case_ingestion(command["load_case_id"])
        source_already_ingested = command["source_checksum"] is not None and (
            unit_of_work.source_completed(command)
            or not unit_of_work.claim_source_identity(command, clock())
        )
        if source_already_ingested:
            unit_of_work.add_skipped_job(
                job_id,
                command,
                {**summary, "reason": "IDENTICAL_COMPLETED"},
                clock(),
            )
            return {
                "status": "SKIPPED",
                "job_id": job_id,
                "load_case_id": command["load_case_id"],
                "analysis_run_id": None,
                "run_no": None,
                "schema_id": schema_id,
                "summary": summary,
            }
        run_id = id_factory("run")
        run_no = unit_of_work.next_run_no(command["load_case_id"])
        created_at = clock()
        unit_of_work.add_running_job(job_id, run_id, command, created_at)
        unit_of_work.ensure_catalog(command)
        unit_of_work.add_run(run_id, run_no, command, created_at)
        unit_of_work.add_metadata(run_id, job_id, command, created_at)
        unit_of_work.add_results(run_id, command, created_at)
        unit_of_work.complete_job(job_id, run_id, summary)
        unit_of_work.sync_status(command, created_at)
    return {
        "status": "IMPORTED",
        "job_id": job_id,
        "load_case_id": command["load_case_id"],
        "analysis_run_id": run_id,
        "run_no": run_no,
        "schema_id": schema_id,
        "summary": summary,
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
