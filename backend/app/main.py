from __future__ import annotations

import json
import shutil
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from . import config as app_config
from .modules.access_control import (
    PROJECT_DATA_VIEW,
    REPORT_EXPORT,
    REQUEST_CREATE,
    REQUEST_EDIT,
    RESULT_IMPORT,
    WORKFLOW_EDIT,
    require_permission,
    require_resource_permission,
    resolve_project_assignee,
)
from .database import connect, initialize_database, rows
from .config import security_settings
from .repositories.media_repository import get_blob, get_drop_video
from .services.media_http import build_media_response
from .repositories.workbench import WorkbenchRepository
from .services.request_monitoring import sync_request_status
from .schemas.api import (
    AnalysisRequestCreate,
    LoadCaseCreate,
    WorkflowStepUpdate,
    WorkflowStepsReplace,
)
from .security import SecurityMiddleware, write_audit_event
from .routers.security import router as security_router
from .routers.access_control import router as access_control_router
from .routers.workbench import router as workbench_router
from .routers.modeling_catalog import router as modeling_catalog_router
from .routers.result_folder_refresh import router as result_folder_refresh_router
from .routers.result_ingestion import router as result_ingestion_router
from .routers.media import router as media_router
from .adapters.http.routers.projects import router as projects_router
from .adapters.http.routers.reports import router as reports_router
from .adapters.http.routers.report_templates import router as report_templates_router
from .adapters.http.routers.requests import query_router as request_query_router
from .adapters.http.routers.requests import router as requests_router
from .adapters.http.routers.request_load_cases import router as request_load_cases_router
from .adapters.http.routers.variable_catalog import router as variable_catalog_router
from .adapters.http.routers.workspace_layouts import router as workspace_layouts_router
from .adapters.http.routers.import_schemas import router as import_schemas_router
from .adapters.http.routers.load_case_overview import router as load_case_overview_router
from .adapters.http.routers.analysis_runs import router as analysis_runs_router
from .adapters.http.routers.analysis_insights import router as analysis_insights_router
from .adapters.http.routers.result_review import router as result_review_router
from .adapters.http.routers.quality_thresholds import router as quality_thresholds_router
from .adapters.http.routers.workflow_queries import router as workflow_queries_router
from .adapters.http.routers.feature_examples import router as feature_examples_router
from .adapters.http.routers.portfolio import router as portfolio_router
from .adapters.http.routers.dashboard_commands import router as dashboard_commands_router
from .adapters.http.routers.drop_videos import router as drop_videos_router
from .adapters.http.routers.system_health import router as system_health_router
from .adapters.http.routers.dashboard_reads import router as dashboard_reads_router
from .adapters.http.routers.dashboard_reads import version_router as dashboard_versions_router
from .adapters.http.routers.analysis_pages import router as analysis_pages_router
from .adapters.http.routers.dashboard_writes import history_router as dashboard_history_router
from .adapters.http.routers.dashboard_writes import save_router as dashboard_save_router
from .services.drop_video_demo import (
    DROP_VIDEO_DEMO_BY_ID,
    DROP_VIDEO_DEMO_SCENES,
    DROP_VIDEO_SOURCE_DIR,
    probe_mp4,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Analysis Canvas API", version="0.1.0", lifespan=lifespan)
app.add_middleware(SecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(security_settings().cors_allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/assets", StaticFiles(directory=str(__import__("pathlib").Path(__file__).resolve().parents[1] / "public_assets")), name="assets")
app.include_router(security_router)
app.include_router(access_control_router)
app.include_router(workbench_router)
app.include_router(modeling_catalog_router)
app.include_router(result_folder_refresh_router)


def media_storage_mode() -> app_config.MediaStorageMode:
    """Read media cutover policy at request time and keep it patchable."""

    return app_config.media_storage_mode()


app.include_router(system_health_router)
app.include_router(feature_examples_router)
app.include_router(projects_router)
app.include_router(import_schemas_router)
app.include_router(portfolio_router)


app.include_router(request_query_router)


@app.post("/api/projects/{project_id}/requests", status_code=201)
def create_request(project_id: str, payload: AnalysisRequestCreate, request: Request) -> dict[str, Any]:
    request_id = f"request-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    due_at = now + timedelta(days=payload.due_in_days)
    principal = request.state.principal
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            if conn.execute("SELECT id FROM projects WHERE id = ?", [project_id]).fetchone() is None:
                raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
            require_permission(request, REQUEST_CREATE, project_id, conn=conn)
            assignee = resolve_project_assignee(conn, project_id, payload.owner_user_id)
            repository = WorkbenchRepository(conn)
            request_type = repository.get_request_type(payload.request_type_id, payload.request_type_version)
            if not request_type or not request_type["is_active"]:
                raise HTTPException(
                    404,
                    detail={
                        "code": "REQUEST_TYPE_NOT_FOUND",
                        "request_type_id": payload.request_type_id,
                        "request_type_version": payload.request_type_version,
                    },
                )
            assigned_by = principal.display_name
            conn.execute(
                """
                INSERT INTO analysis_requests
                    (id, project_id, title, status, owner, owner_user_id,
                     requested_at, due_at, overall_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [request_id, project_id, payload.title.strip(), "READY", assignee.display_name,
                 assignee.user_id, now, due_at, payload.overall_note.strip()],
            )
            repository.assign_request_type(
                request_id,
                payload.request_type_id,
                payload.request_type_version,
                "ADMIN",
                assigned_by,
            )
            repository.create_work_plan(
                request_id,
                request_type,
                assignee.display_name,
                assigned_by,
                owner_user_id=assignee.user_id,
                source_type=payload.source_type,
                source_reference=payload.source_reference.strip(),
                requested_by=principal.display_name,
            )
            repository.create_result_layout_snapshot(request_id, request_type, principal.display_name)
            write_audit_event(
                request=request,
                principal=principal,
                status_code=201,
                action="ANALYSIS_REQUEST_CREATED",
                detail={
                    "project_id": project_id,
                    "request_id": request_id,
                    "owner_user_id": assignee.user_id,
                },
                connection=conn,
            )
            conn.execute("COMMIT")
        except (FileExistsError, PermissionError) as exc:
            conn.execute("ROLLBACK")
            raise HTTPException(409, detail={"code": str(exc), "request_id": request_id}) from exc
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {
        "id": request_id,
        "project_id": project_id,
        "title": payload.title.strip(),
        "status": "READY",
        "owner": assignee.display_name,
        "owner_user_id": assignee.user_id,
        "requested_at": now,
        "due_at": due_at,
        "overall_note": payload.overall_note.strip(),
        "request_type_id": payload.request_type_id,
        "request_type_version": payload.request_type_version,
        "scenario_name": request_type["display_name"],
        "source_type": payload.source_type,
        "source_reference": payload.source_reference.strip(),
        "requested_by": principal.display_name,
    }


app.include_router(requests_router)
app.include_router(request_load_cases_router)


app.include_router(drop_videos_router)


def _media_audit_callback(request: Request, action: str, bytes_yielded: int, status_code: int) -> None:
    try:
        with connect() as audit_connection:
            write_audit_event(
                request=request,
                principal=getattr(request.state, "principal", None),
                status_code=status_code,
                action=f"MEDIA_STREAM_{action}",
                detail={"bytes_yielded_to_asgi": bytes_yielded},
                connection=audit_connection,
            )
    except Exception:
        # Observability must not turn a successful media response into a 5xx.
        return


@app.get("/api/drop-videos/{video_id}/content", operation_id="get_drop_video_content")
@app.head("/api/drop-videos/{video_id}/content", include_in_schema=False)
def get_drop_video_content(video_id: str, request: Request) -> Response:
    with connect() as conn:
        stored = get_drop_video(conn, video_id)
        if stored:
            require_resource_permission(request, PROJECT_DATA_VIEW, "drop_video", video_id, conn=conn)
            blob = get_blob(conn, str(stored["blob_id"]))
            if blob is None:
                raise HTTPException(404, "예제 영상 blob을 찾을 수 없습니다.")
            return build_media_response(
                request,
                blob=blob,
                mime_type=str(stored["mime_type"]),
                filename=str(stored["original_filename"]),
                audit=lambda action, yielded, status: _media_audit_callback(request, action, yielded, status),
            )
    if media_storage_mode() == "database-only":
        raise HTTPException(404, "예제 영상 blob을 찾을 수 없습니다.")
    scene = DROP_VIDEO_DEMO_BY_ID.get(video_id)
    if scene is None:
        raise HTTPException(404, "허용된 예제 영상을 찾을 수 없습니다.")
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "load_case", "loadcase-drop-bottom-001", conn=conn)
    source_root = DROP_VIDEO_SOURCE_DIR.resolve()
    path = (source_root / scene.filename).resolve()
    if path.parent != source_root or not path.is_file():
        raise HTTPException(404, "예제 영상 파일을 찾을 수 없습니다.")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/drop-videos/{video_id}/download", operation_id="download_drop_video")
@app.head("/api/drop-videos/{video_id}/download", include_in_schema=False)
def download_drop_video(video_id: str, request: Request) -> Response:
    with connect() as conn:
        stored = get_drop_video(conn, video_id)
        if stored:
            require_resource_permission(request, PROJECT_DATA_VIEW, "drop_video", video_id, conn=conn)
            blob = get_blob(conn, str(stored["blob_id"]))
            if blob is None:
                raise HTTPException(404, "예제 영상 blob을 찾을 수 없습니다.")
            return build_media_response(
                request,
                blob=blob,
                mime_type=str(stored["mime_type"]),
                filename=str(stored["original_filename"]),
                download=True,
                audit=lambda action, yielded, status: _media_audit_callback(request, action, yielded, status),
            )
    if media_storage_mode() == "database-only":
        raise HTTPException(404, "예제 영상 blob을 찾을 수 없습니다.")
    scene = DROP_VIDEO_DEMO_BY_ID.get(video_id)
    if scene is None:
        raise HTTPException(404, "허용된 예제 영상을 찾을 수 없습니다.")
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "load_case", "loadcase-drop-bottom-001", conn=conn)
    source_root = DROP_VIDEO_SOURCE_DIR.resolve()
    path = (source_root / scene.filename).resolve()
    if path.parent != source_root or not path.is_file():
        raise HTTPException(404, "예제 영상 파일을 찾을 수 없습니다.")
    return FileResponse(path, media_type="video/mp4", filename=scene.filename)


@app.post("/api/requests/{request_id}/load-cases", status_code=201)
def create_load_case(request_id: str, payload: LoadCaseCreate, request: Request) -> dict[str, Any]:
    load_case_id = f"loadcase-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        require_resource_permission(request, REQUEST_EDIT, "request", request_id, conn=conn)
        if conn.execute("SELECT id FROM analysis_requests WHERE id = ?", [request_id]).fetchone() is None:
            raise HTTPException(404, "해석 의뢰를 찾을 수 없습니다.")
        conn.execute(
            "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            [load_case_id, request_id, payload.name.strip(), payload.analysis_type, "READY", json.dumps(payload.parameters, ensure_ascii=False), now],
        )
    return {"id": load_case_id, "request_id": request_id, "name": payload.name.strip(), "analysis_type": payload.analysis_type, "status": "READY", "parameters": payload.parameters, "created_at": now}


app.include_router(result_ingestion_router)
app.include_router(media_router)
app.include_router(load_case_overview_router)
app.include_router(analysis_runs_router)
app.include_router(analysis_insights_router)


app.include_router(result_review_router)
app.include_router(quality_thresholds_router)
app.include_router(workflow_queries_router)


@app.put("/api/requests/{request_id}/workflow-steps")
def replace_workflow_steps(request_id: str, payload: WorkflowStepsReplace, request: Request) -> list[dict[str, Any]]:
    with connect() as conn:
        require_resource_permission(request, WORKFLOW_EDIT, "request", request_id, conn=conn)
        request_row = conn.execute("SELECT project_id FROM analysis_requests WHERE id = ?", [request_id]).fetchone()
        if not request_row:
            raise HTTPException(404, "해석 의뢰를 찾을 수 없습니다.")
        project_id = str(request_row[0])
        if conn.execute("SELECT 1 FROM request_work_plans WHERE request_id = ?", [request_id]).fetchone():
            raise HTTPException(
                409,
                detail={"code": "WORK_PLAN_IMMUTABLE", "request_id": request_id},
            )
        existing = rows(conn.execute("SELECT * FROM request_steps WHERE request_id = ?", [request_id]))
        existing_by_id = {item["id"]: item for item in existing}
        submitted_ids = [item.id for item in payload.steps if item.id]
        if len(submitted_ids) != len(set(submitted_ids)):
            raise HTTPException(400, "단계 ID가 중복되었습니다.")
        unknown = [step_id for step_id in submitted_ids if step_id not in existing_by_id]
        if unknown:
            raise HTTPException(400, "다른 의뢰의 단계이거나 존재하지 않는 단계가 포함되어 있습니다.")
        now = datetime.now(timezone.utc)
        try:
            conn.execute("BEGIN TRANSACTION")
            if submitted_ids:
                placeholders = ",".join("?" for _ in submitted_ids)
                conn.execute(
                    f"DELETE FROM request_steps WHERE request_id = ? AND id NOT IN ({placeholders})",
                    [request_id, *submitted_ids],
                )
            else:
                conn.execute("DELETE FROM request_steps WHERE request_id = ?", [request_id])
            for sequence_no, step in enumerate(payload.steps, start=1):
                name = step.name.strip()
                assignee = resolve_project_assignee(conn, project_id, step.owner_user_id)
                if len(name) < 2:
                    raise HTTPException(400, "단계 이름과 담당자를 확인해 주세요.")
                if step.id:
                    conn.execute(
                        """
                        UPDATE request_steps
                        SET sequence_no = ?, name = ?, status = ?, owner = ?, owner_user_id = ?,
                            progress = ?, is_optional = ?, note = ?
                        WHERE id = ? AND request_id = ?
                        """,
                        [sequence_no, name, step.status, assignee.display_name, assignee.user_id,
                         step.progress, step.is_optional, step.note.strip(), step.id, request_id],
                    )
                else:
                    step_id = f"step-{uuid4().hex[:12]}"
                    conn.execute(
                        """
                        INSERT INTO request_steps (
                            id, request_id, sequence_no, name, status, owner, owner_user_id, planned_start, planned_end,
                            actual_start, actual_end, progress, blocked_reason, note, is_optional
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, ?, ?)
                        """,
                        [step_id, request_id, sequence_no, name, step.status, assignee.display_name,
                         assignee.user_id, now, now + timedelta(days=7), step.progress,
                         step.note.strip(), step.is_optional],
                    )
            updated_steps = rows(conn.execute("SELECT * FROM request_steps WHERE request_id = ? ORDER BY sequence_no", [request_id]))
            sync_request_status(conn, request_id, updated_steps)
            write_audit_event(
                request=request,
                principal=request.state.principal,
                status_code=200,
                action="WORKFLOW_STEPS_REPLACED",
                detail={"project_id": project_id, "request_id": request_id, "step_count": len(payload.steps)},
                connection=conn,
            )
            conn.execute("COMMIT")
        except HTTPException:
            conn.execute("ROLLBACK")
            raise
        except Exception as exc:
            conn.execute("ROLLBACK")
            raise HTTPException(500, f"진행 단계를 저장하지 못했습니다: {exc}") from exc
        return rows(conn.execute("SELECT * FROM request_steps WHERE request_id = ? ORDER BY sequence_no", [request_id]))


@app.patch("/api/workflow-steps/{step_id}")
def update_workflow_step(step_id: str, payload: WorkflowStepUpdate, request: Request) -> dict[str, Any]:
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(400, "단계 이름은 두 글자 이상이어야 합니다.")
    with connect() as conn:
        existing = rows(conn.execute("SELECT * FROM request_steps WHERE id = ?", [step_id]))
        if not existing:
            planned_item = conn.execute(
                "SELECT request_id FROM request_work_items WHERE id = ?",
                [step_id],
            ).fetchone()
            if planned_item:
                require_resource_permission(request, WORKFLOW_EDIT, "work_item", step_id, conn=conn)
                raise HTTPException(
                    409,
                    detail={"code": "WORK_PLAN_IMMUTABLE", "request_id": planned_item[0]},
                )
            raise HTTPException(404, "작업 단계를 찾을 수 없습니다.")
        require_resource_permission(request, WORKFLOW_EDIT, "workflow_step", step_id, conn=conn)
        current = existing[0]
        if conn.execute("SELECT 1 FROM request_work_plans WHERE request_id = ?", [current["request_id"]]).fetchone():
            raise HTTPException(
                409,
                detail={"code": "WORK_PLAN_IMMUTABLE", "request_id": current["request_id"]},
            )
        request_row = conn.execute(
            "SELECT project_id FROM analysis_requests WHERE id=?",
            [current["request_id"]],
        ).fetchone()
        owner, owner_user_id = current["owner"], current.get("owner_user_id")
        if payload.owner_user_id is not None:
            assignee = resolve_project_assignee(conn, str(request_row[0]), payload.owner_user_id)
            owner, owner_user_id = assignee.display_name, assignee.user_id
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                """
                UPDATE request_steps
                SET name = ?, status = ?, owner = ?, owner_user_id = ?, progress = ?, is_optional = ?, note = ?
                WHERE id = ?
                """,
                [
                    name,
                    payload.status or current["status"],
                    owner,
                    owner_user_id,
                    payload.progress if payload.progress is not None else current["progress"],
                    payload.is_optional if payload.is_optional is not None else current["is_optional"],
                    payload.note.strip() if payload.note is not None else current["note"],
                    step_id,
                ],
            )
            write_audit_event(
                request=request,
                principal=request.state.principal,
                status_code=200,
                action="WORKFLOW_STEP_UPDATED",
                detail={"project_id": str(request_row[0]), "request_id": current["request_id"], "step_id": step_id, "owner_user_id": owner_user_id},
                connection=conn,
            )
            updated = rows(conn.execute("SELECT * FROM request_steps WHERE id = ?", [step_id]))
            sync_request_status(conn, current["request_id"])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    if not updated:
        raise HTTPException(404, "작업 단계를 찾을 수 없습니다.")
    return updated[0]


app.include_router(variable_catalog_router)
app.include_router(workspace_layouts_router)
app.include_router(reports_router)
app.include_router(report_templates_router)
app.include_router(analysis_pages_router)


app.include_router(dashboard_reads_router)
app.include_router(dashboard_save_router)
app.include_router(dashboard_versions_router)
app.include_router(dashboard_history_router)
app.include_router(dashboard_commands_router)
