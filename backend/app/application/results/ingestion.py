"""Framework-neutral result-ingestion use cases."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ...folder_import import scan_folder
from ...result_import import ResultFormatError, parse_result_file
from ...services.manual_result_ingestion_adapter import (
    ManualResultIngestionAdapterError,
    to_canonical_result_payload,
)
from ...services.radioss_result_ingestion_adapter import (
    RadiossResultIngestionAdapterError,
    to_canonical_radioss_result_payload,
)
from .commands import ResultIngestionUnitOfWorkProvider, ingest_result_bundle, utc_identifier


class ResultImportPreparationError(Exception):
    """Input parsing or canonical adaptation failed before persistence."""


@dataclass(frozen=True)
class ResultIngestionTarget:
    project_id: str
    request_id: str
    load_case_id: str


@dataclass(frozen=True)
class ManualResultImportInput:
    target: ResultIngestionTarget
    filename: str
    content: str
    author: str | None
    principal_user_id: str
    actor_name: str
    source_run_id: str | None
    conflict_policy: str
    chassis_threshold: float
    open_cell_threshold: float
    catalog: Mapping[str, Any]
    validate_only: bool


@dataclass(frozen=True)
class AuditRecord:
    action: str
    status_code: int
    detail: Mapping[str, Any]


@dataclass(frozen=True)
class ManualResultImportExecution:
    parsed: Mapping[str, Any]
    outcome: Mapping[str, Any] | None


Clock = Callable[[], datetime]
TransactionScope = Callable[[], AbstractContextManager[ResultIngestionUnitOfWorkProvider]]
AuditSink = Callable[[AuditRecord], None]


def prepare_typed_example(example_root: Path) -> Mapping[str, Any]:
    """Read the typed example before authorization, preserving legacy order."""
    return scan_folder(example_root)


def run_manual_import(
    import_input: ManualResultImportInput,
    *,
    transaction: TransactionScope,
    audit: AuditSink,
    now: Clock,
) -> ManualResultImportExecution:
    """Parse, canonicalize, persist, and audit one manual result import."""
    parsed = _parse_manual_result(import_input)
    if import_input.validate_only:
        return ManualResultImportExecution(parsed=parsed, outcome=None)
    command = _manual_command(import_input, parsed)
    with transaction() as provider:
        outcome = ingest_result_bundle(command, provider, now, utc_identifier)
        audit(_audit_record(import_input.target, outcome))
    return ManualResultImportExecution(parsed=parsed, outcome=outcome)


def run_typed_example(
    *,
    example_root: Path,
    target: ResultIngestionTarget,
    parsed: Mapping[str, Any],
    provider: ResultIngestionUnitOfWorkProvider,
    now: Clock,
) -> Mapping[str, Any]:
    """Persist the already-scanned typed example through the injected UoW."""
    return ingest_result_bundle(
        {
            "project_id": target.project_id,
            "request_id": target.request_id,
            "load_case_id": target.load_case_id,
            "source_type": "FOLDER_IMPORT",
            "source_name": str(example_root),
            "source_checksum": None,
            "parser_version": "folder-import-v1",
            "parsed": parsed,
            "actor": "폴더 가져오기",
            "metadata": {},
        },
        provider,
        now,
        utc_identifier,
    )


def _parse_manual_result(import_input: ManualResultImportInput) -> Mapping[str, Any]:
    try:
        parsed = parse_result_file(
            import_input.filename,
            import_input.content,
            import_input.chassis_threshold,
            dict(import_input.catalog),
            import_input.open_cell_threshold,
        )
    except ResultFormatError as exc:
        raise ResultImportPreparationError(str(exc)) from exc
    if import_input.validate_only:
        return parsed
    checksum = hashlib.sha256(import_input.content.encode("utf-8")).hexdigest()
    source_format = parsed["summary"]["source_format"]
    try:
        if source_format == "SUMMARY_RESULT":
            canonical = to_canonical_result_payload(
                parsed,
                source_file=import_input.filename,
                source_checksum=checksum,
            )
        elif source_format == "RADIOSS_MESH_CSV":
            canonical = to_canonical_radioss_result_payload(
                parsed,
                source_file=import_input.filename,
                source_checksum=checksum,
            )
        else:
            raise ResultImportPreparationError(f"지원하지 않는 결과 형식입니다: {source_format}")
    except ResultImportPreparationError:
        raise
    except (ManualResultIngestionAdapterError, RadiossResultIngestionAdapterError, ValueError) as exc:
        raise ResultImportPreparationError(str(exc)) from exc
    return {**parsed, "_canonical": canonical, "_source_checksum": checksum}


def _manual_command(import_input: ManualResultImportInput, parsed: Mapping[str, Any]) -> dict[str, Any]:
    command: dict[str, Any] = {
        "project_id": import_input.target.project_id,
        "request_id": import_input.target.request_id,
        "load_case_id": import_input.target.load_case_id,
        "source_type": "FILE_UPLOAD",
        "source_name": f"{import_input.target.load_case_id}/{import_input.filename}",
        "source_checksum": parsed["_source_checksum"],
        "parser_version": "result-import-v1",
        "parsed": parsed["_canonical"],
        "actor": import_input.actor_name,
        "metadata": {
            "author_user_id": import_input.principal_user_id,
            "submitted_author": import_input.author,
            "original_filename": import_input.filename,
            "source_format": parsed["summary"]["source_format"],
        },
    }
    if import_input.source_run_id is not None:
        command["source_run_id"] = import_input.source_run_id
        command["conflict_policy"] = import_input.conflict_policy
    return command


def _audit_record(target: ResultIngestionTarget, outcome: Mapping[str, Any]) -> AuditRecord:
    action = {
        "IMPORTED": "RESULT_IMPORTED",
        "SKIPPED": "RESULT_IMPORT_SKIPPED",
        "REJECTED": "RESULT_IMPORT_REJECTED",
    }[outcome["status"]]
    if outcome["operation"] == "REPLACED":
        action = "RESULT_IMPORT_REPLACED"
    return AuditRecord(
        action=action,
        status_code=409 if outcome["status"] == "REJECTED" else 200,
        detail={
            "project_id": target.project_id,
            "load_case_id": target.load_case_id,
            "run_id": outcome["analysis_run_id"],
            "status": outcome["status"],
            "operation": outcome["operation"],
            "reason_code": outcome["reason_code"],
            "existing_run_id": outcome["existing_analysis_run_id"],
            "replaced_run_id": outcome["replaced_analysis_run_id"],
            "source_revision": outcome["source_revision"],
        },
    )
