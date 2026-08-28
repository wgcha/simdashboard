from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response

from ..adapters.persistence.result_ingestion import (
    SQLResultIngestionUnitOfWorkProvider,
    bound_result_ingestion_transaction,
)
from ..database_connection import connect
from ..application.results.commands import ingest_result_bundle, utc_identifier
from ..folder_import import FolderImportError, scan_folder
from ..modules.access_control import RESULT_IMPORT, require_resource_permission
from ..repositories.result_ingestion import ResultIngestionRepository
from ..result_import import CSV_TEMPLATE, JSON_TEMPLATE, ResultFormatError, parse_result_file
from ..schemas.api import ResultImportPayload, ResultImportResponse, TypedResultExampleResponse
from ..security import write_audit_event
from ..services.manual_result_ingestion_adapter import (
    ManualResultIngestionAdapterError,
    to_canonical_result_payload,
)
from ..services.radioss_result_ingestion_adapter import (
    RadiossResultIngestionAdapterError,
    to_canonical_radioss_result_payload,
)


router = APIRouter(prefix="/api")


@router.get("/result-import/template/{file_format}")
def get_result_import_template(file_format: Literal["csv", "json", "radioss-csv"]) -> Response:
    if file_format == "csv":
        return Response(CSV_TEMPLATE, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="analysis-result-template.csv"'})
    if file_format == "radioss-csv":
        sample_path = Path(__file__).resolve().parents[3] / "examples" / "radioss" / "radioss_tv_result_example.csv"
        if not sample_path.exists():
            raise HTTPException(404, "Radioss 예제 파일을 찾을 수 없습니다.")
        return Response(sample_path.read_bytes(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="radioss-tv-result-example.csv"'})
    content = json.dumps(JSON_TEMPLATE, ensure_ascii=False, indent=2)
    return Response(content, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="analysis-result-template.json"'})


@router.post("/load-cases/{load_case_id}/results/import", response_model=ResultImportResponse)
def import_analysis_results(load_case_id: str, payload: ResultImportPayload, request: Request) -> ResultImportResponse:
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
        principal = request.state.principal
        actor_name = principal.display_name
        repository = ResultIngestionRepository(conn)
        context = repository.get_load_case_context(load_case_id)
        if context is None:
            raise HTTPException(404, "하중 경우를 찾을 수 없습니다.")
        chassis_threshold = repository.get_quality_threshold(context[2], "chassis_rear_permanent_deformation_mm", 5.0)
        open_cell_threshold = repository.get_quality_threshold(context[2], "open_cell_stress_mpa", 75.0)
        catalog = repository.list_catalog(load_case_id)
        try:
            parsed = parse_result_file(payload.filename, payload.content, chassis_threshold, catalog, open_cell_threshold)
        except ResultFormatError as exc:
            raise HTTPException(422, str(exc)) from exc
        if payload.validate_only:
            return ResultImportResponse(
                status="VALID",
                filename=payload.filename,
                summary=parsed["summary"],
                **parsed["summary"],
                results=parsed["scalars"],
                warnings=parsed["warnings"],
                operation="NOOP",
                reason_code="VALIDATION_ONLY",
            )
        source_checksum = hashlib.sha256(payload.content.encode("utf-8")).hexdigest()
        source_name = f"{load_case_id}/{payload.filename}"
        source_format = parsed["summary"]["source_format"]
        try:
            if source_format == "SUMMARY_RESULT":
                canonical_parsed = to_canonical_result_payload(
                    parsed,
                    source_file=payload.filename,
                    source_checksum=source_checksum,
                )
            elif source_format == "RADIOSS_MESH_CSV":
                canonical_parsed = to_canonical_radioss_result_payload(
                    parsed,
                    source_file=payload.filename,
                    source_checksum=source_checksum,
                )
            else:
                raise ValueError(f"지원하지 않는 결과 형식입니다: {source_format}")
        except (ManualResultIngestionAdapterError, RadiossResultIngestionAdapterError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        with bound_result_ingestion_transaction(
            conn,
            utc_identifier,
            authorize=lambda command, connection: require_resource_permission(
                request,
                RESULT_IMPORT,
                "load_case",
                command["load_case_id"],
                conn=connection,
            ),
        ) as unit_of_work_provider:
            command: dict[str, Any] = {
                "project_id": str(context[2]),
                "request_id": str(context[1]),
                "load_case_id": load_case_id,
                "source_type": "FILE_UPLOAD",
                "source_name": source_name,
                "source_checksum": source_checksum,
                "parser_version": "result-import-v1",
                "parsed": canonical_parsed,
                "actor": actor_name,
                "metadata": {
                    "author_user_id": principal.user_id,
                    "submitted_author": payload.author,
                    "original_filename": payload.filename,
                    "source_format": source_format,
                },
            }
            # Preserve legacy append semantics when no producer identity was
            # supplied.  In particular, do not pass the payload's default
            # SKIP policy without a source_run_id.
            if payload.source_run_id is not None:
                command["source_run_id"] = payload.source_run_id
                command["conflict_policy"] = payload.conflict_policy
            outcome = ingest_result_bundle(
                command,  # type: ignore[arg-type]
                unit_of_work_provider,
                lambda: datetime.now(timezone.utc).replace(tzinfo=None),
                utc_identifier,
            )
            audit_action = {
                "IMPORTED": "RESULT_IMPORTED",
                "SKIPPED": "RESULT_IMPORT_SKIPPED",
                "REJECTED": "RESULT_IMPORT_REJECTED",
            }[outcome["status"]]
            if outcome["operation"] == "REPLACED":
                audit_action = "RESULT_IMPORT_REPLACED"
            write_audit_event(
                request=request,
                principal=principal,
                status_code=409 if outcome["status"] == "REJECTED" else 200,
                action=audit_action,
                detail={
                    "project_id": context[2],
                    "load_case_id": load_case_id,
                    "run_id": outcome["analysis_run_id"],
                    "status": outcome["status"],
                    "operation": outcome["operation"],
                    "reason_code": outcome["reason_code"],
                    "existing_run_id": outcome["existing_analysis_run_id"],
                    "replaced_run_id": outcome["replaced_analysis_run_id"],
                    "source_revision": outcome["source_revision"],
                },
                connection=conn,
            )
        if outcome["status"] == "REJECTED":
            # The UoW and audit have committed.  Raising inside the transaction
            # would roll back the rejected job and violate the conflict audit.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "SOURCE_RUN_CONFLICT",
                    "existing_run_id": outcome["existing_analysis_run_id"],
                    "source_revision": outcome["source_revision"],
                },
            )
        return ResultImportResponse(
            status=outcome["status"],
            run_id=outcome["analysis_run_id"],
            run_no=outcome["run_no"],
            filename=payload.filename,
            summary=parsed["summary"],
            **parsed["summary"],
            results=parsed["scalars"],
            warnings=parsed["warnings"],
            operation=outcome["operation"],
            reason_code=outcome["reason_code"],
            existing_run_id=outcome["existing_analysis_run_id"],
            replaced_run_id=outcome["replaced_analysis_run_id"],
            source_revision=outcome["source_revision"],
        )


@router.post("/load-cases/{load_case_id}/folder-import/example", response_model=TypedResultExampleResponse)
def import_typed_result_example(load_case_id: str, request: Request) -> TypedResultExampleResponse:
    """Register the checked-in typed folder example through the same importer used by future uploads."""
    example_root = Path(__file__).resolve().parents[3] / "examples" / "typed-results" / "tv-drop-chassis"
    try:
        parsed = scan_folder(example_root)
    except FolderImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
        context = ResultIngestionRepository(conn).get_load_case_context(load_case_id)
        if not context:
            raise HTTPException(404, "하중경우를 찾을 수 없습니다.")
    outcome = ingest_result_bundle(
        {
            "project_id": str(context[2]),
            "request_id": str(context[1]),
            "load_case_id": load_case_id,
            "source_type": "FOLDER_IMPORT",
            "source_name": str(example_root),
            "source_checksum": None,
            "parser_version": "folder-import-v1",
            "parsed": parsed,
            "actor": "폴더 가져오기",
            "metadata": {},
        },
        SQLResultIngestionUnitOfWorkProvider(
            utc_identifier,
            authorize=lambda command, connection: require_resource_permission(
                request,
                RESULT_IMPORT,
                "load_case",
                command["load_case_id"],
                conn=connection,
            ),
        ),
        lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        utc_identifier,
    )
    return TypedResultExampleResponse(
        status=outcome["status"],
        job_id=outcome["job_id"],
        run_id=outcome["analysis_run_id"],
        run_no=outcome["run_no"],
        schema_id=outcome["schema_id"],
        summary=outcome["summary"],
        operation=outcome["operation"],
        reason_code=outcome["reason_code"],
        existing_run_id=outcome["existing_analysis_run_id"],
        replaced_run_id=outcome["replaced_analysis_run_id"],
        source_revision=outcome["source_revision"],
    )
