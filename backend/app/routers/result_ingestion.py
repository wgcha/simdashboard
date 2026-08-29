from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from fastapi import APIRouter, HTTPException, Request, Response

from ..adapters.persistence.result_ingestion import (
    SQLResultIngestionQuery,
    SQLResultIngestionUnitOfWorkProvider,
    bound_result_ingestion_transaction,
)
from ..application.results.commands import utc_identifier
from ..application.results.ingestion import (
    AuditRecord,
    ManualResultImportInput,
    ResultImportPreparationError,
    ResultIngestionTarget,
    prepare_typed_example,
    run_manual_import,
    run_typed_example,
)
from ..application.results.queries import (
    get_manual_result_import_context,
    get_result_ingestion_target,
)
from ..database_connection import connect
from ..folder_import import FolderImportError
from ..modules.access_control import RESULT_IMPORT, require_resource_permission
from ..result_import import CSV_TEMPLATE, JSON_TEMPLATE
from ..schemas.api import ResultImportPayload, ResultImportResponse, TypedResultExampleResponse
from ..security import write_audit_event


router = APIRouter(prefix="/api")


@router.get("/result-import/template/{file_format}")
def get_result_import_template(file_format: Literal["csv", "json", "radioss-csv"]) -> Response:
    if file_format == "csv":
        return Response(
            CSV_TEMPLATE,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="analysis-result-template.csv"'},
        )
    if file_format == "radioss-csv":
        sample_path = Path(__file__).resolve().parents[3] / "examples" / "radioss" / "radioss_tv_result_example.csv"
        if not sample_path.exists():
            raise HTTPException(404, "Radioss 예제 파일을 찾을 수 없습니다.")
        return Response(
            sample_path.read_bytes(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="radioss-tv-result-example.csv"'},
        )
    return Response(
        json.dumps(JSON_TEMPLATE, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="analysis-result-template.json"'},
    )


@router.post("/load-cases/{load_case_id}/results/import", response_model=ResultImportResponse)
def import_analysis_results(
    load_case_id: str,
    payload: ResultImportPayload,
    request: Request,
) -> ResultImportResponse:
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
        principal = request.state.principal
        context = get_manual_result_import_context(SQLResultIngestionQuery(conn), load_case_id)
        if context is None:
            raise HTTPException(404, "하중 경우를 찾을 수 없습니다.")
        target = ResultIngestionTarget(context.project_id, context.request_id, load_case_id)
        import_input = ManualResultImportInput(
            target=target,
            filename=payload.filename,
            content=payload.content,
            author=payload.author,
            principal_user_id=principal.user_id,
            actor_name=principal.display_name,
            source_run_id=payload.source_run_id,
            conflict_policy=payload.conflict_policy,
            chassis_threshold=context.chassis_threshold,
            open_cell_threshold=context.open_cell_threshold,
            catalog=context.catalog,
            validate_only=payload.validate_only,
        )
        try:
            execution = run_manual_import(
                import_input,
                transaction=lambda: bound_result_ingestion_transaction(
                    conn,
                    utc_identifier,
                    authorize=lambda command, transaction_connection: require_resource_permission(
                        request,
                        RESULT_IMPORT,
                        "load_case",
                        command["load_case_id"],
                        conn=transaction_connection,
                    ),
                ),
                audit=lambda record: _write_manual_import_audit(
                    request,
                    principal,
                    conn,
                    record,
                ),
                now=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
            )
        except ResultImportPreparationError as exc:
            raise HTTPException(422, str(exc)) from exc
        if payload.validate_only:
            return _valid_result_import_response(payload.filename, execution.parsed)
        outcome = execution.outcome
        assert outcome is not None
    if outcome["status"] == "REJECTED":
        raise HTTPException(
            409,
            {
                "code": "SOURCE_RUN_CONFLICT",
                "existing_run_id": outcome["existing_analysis_run_id"],
                "source_revision": outcome["source_revision"],
            },
        )
    return _persisted_result_import_response(payload.filename, execution.parsed, outcome)


@router.post(
    "/load-cases/{load_case_id}/folder-import/example",
    response_model=TypedResultExampleResponse,
)
def import_typed_result_example(load_case_id: str, request: Request) -> TypedResultExampleResponse:
    """Register the checked-in typed folder example through the same importer used by future uploads."""
    example_root = Path(__file__).resolve().parents[3] / "examples" / "typed-results" / "tv-drop-chassis"
    try:
        parsed = prepare_typed_example(example_root)
    except FolderImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)
        context = get_result_ingestion_target(SQLResultIngestionQuery(conn), load_case_id)
        if context is None:
            raise HTTPException(404, "하중경우를 찾을 수 없습니다.")
    outcome = run_typed_example(
        example_root=example_root,
        target=ResultIngestionTarget(context.project_id, context.request_id, load_case_id),
        parsed=parsed,
        provider=SQLResultIngestionUnitOfWorkProvider(
            utc_identifier,
            authorize=lambda command, connection: require_resource_permission(
                request,
                RESULT_IMPORT,
                "load_case",
                command["load_case_id"],
                conn=connection,
            ),
        ),
        now=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
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


def _write_manual_import_audit(
    request: Request,
    principal: Any,
    connection: Any,
    record: AuditRecord,
) -> None:
    write_audit_event(
        request=request,
        principal=principal,
        status_code=record.status_code,
        action=record.action,
        detail=dict(record.detail),
        connection=connection,
    )


def _valid_result_import_response(filename: str, parsed: Mapping[str, Any]) -> ResultImportResponse:
    return ResultImportResponse(
        status="VALID",
        filename=filename,
        summary=parsed["summary"],
        **parsed["summary"],
        results=parsed["scalars"],
        warnings=parsed["warnings"],
        operation="NOOP",
        reason_code="VALIDATION_ONLY",
    )


def _persisted_result_import_response(
    filename: str,
    parsed: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> ResultImportResponse:
    return ResultImportResponse(
        status=outcome["status"],
        run_id=outcome["analysis_run_id"],
        run_no=outcome["run_no"],
        filename=filename,
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
