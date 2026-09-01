from __future__ import annotations

import json
import shutil
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from . import config as app_config
from .modules.access_control import (
    DASHBOARD_EDIT,
    PROJECT_DATA_VIEW,
    REPORT_EXPORT,
    REQUEST_CREATE,
    REQUEST_EDIT,
    RESULT_IMPORT,
    WORKFLOW_EDIT,
    has_permission,
    require_permission,
    require_resource_permission,
    resolve_project_assignee,
)
from .database import connect, initialize_database, json_value, rows
from .config import database_settings, security_settings
from .repositories.media_repository import get_blob, get_drop_video, list_drop_videos
from .services.media_http import build_media_response
from .repositories.portfolio import PortfolioRepository
from .repositories.workbench import WorkbenchRepository
from .services.request_monitoring import sync_request_status
from .schemas.api import (
    AnalysisPageCreate,
    AnalysisPageOrderUpdate,
    AnalysisPageSummary,
    AnalysisPageUpdate,
    AnalysisRequestCreate,
    DashboardClone,
    DashboardDefinition,
    DropVideoPageResponse,
    LoadCaseCreate,
    NaturalLanguageCommand,
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
from .adapters.http.routers.requests import router as requests_router
from .adapters.http.routers.request_load_cases import router as request_load_cases_router
from .adapters.http.routers.variable_catalog import router as variable_catalog_router
from .adapters.http.routers.workspace_layouts import router as workspace_layouts_router
from .adapters.http.routers.import_schemas import router as import_schemas_router
from .adapters.persistence.results import SQLAnalysisRunSummaryRepositoryProvider
from .application.results.queries import list_analysis_runs as list_analysis_runs_query
from .adapters.http.routers.load_case_overview import router as load_case_overview_router
from .adapters.http.routers.analysis_insights import router as analysis_insights_router
from .adapters.http.routers.result_review import router as result_review_router
from .adapters.http.routers.quality_thresholds import router as quality_thresholds_router
from .adapters.http.routers.workflow_queries import router as workflow_queries_router
from .adapters.http.routers.feature_examples import router as feature_examples_router
from .services.drop_video_demo import (
    DEMO_DROP_VIDEO_LOAD_CASE_IDS,
    DROP_VIDEO_DEMO_BY_ID,
    DROP_VIDEO_DEMO_SCENES,
    DROP_VIDEO_SOURCE_DIR,
    build_demo_evaluation,
    probe_mp4,
    summarize_demo_evaluations,
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


@app.get("/api/health")
def health() -> dict[str, str]:
    with connect() as conn:
        conn.execute("SELECT 1").fetchone()
    return {"status": "ok", "database_backend": database_settings().backend}


app.include_router(feature_examples_router)
app.include_router(projects_router)
app.include_router(import_schemas_router)


@app.get("/api/portfolio/overview")
def get_portfolio_overview(
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: str | None = None,
    analysis_type: str | None = None,
    status: str | None = None,
    search: str | None = Query(default=None, max_length=120),
) -> dict[str, Any]:
    with connect() as conn:
        return PortfolioRepository(conn).overview(
            date_from=date_from,
            date_to=date_to,
            project_id=project_id,
            analysis_type=analysis_type,
            status=status,
            search=search,
        )


@app.get("/api/portfolio/export.csv")
def export_portfolio_csv(
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: str | None = None,
    analysis_type: str | None = None,
    status: str | None = None,
    search: str | None = Query(default=None, max_length=120),
) -> Response:
    with connect() as conn:
        payload = PortfolioRepository(conn).overview(date_from=date_from, date_to=date_to, project_id=project_id, analysis_type=analysis_type, status=status, search=search)
    return Response(PortfolioRepository.to_csv(payload["records"]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="analysis-portfolio.csv"'})


@app.get("/api/projects/{project_id}/requests")
def get_requests(project_id: str, request: Request) -> list[dict[str, Any]]:
    with connect() as conn:
        context = require_permission(request, PROJECT_DATA_VIEW, project_id, conn=conn)
        if context.project_role is None:
            detail = {
                "code": "PROJECT_MEMBERSHIP_REQUIRED",
                "message": "이 작업을 수행할 권한이 없습니다.",
                "required_permission": PROJECT_DATA_VIEW,
                "project_id": project_id,
            }
            request.state.authorization_detail = detail
            raise HTTPException(403, detail)
        return rows(
            conn.execute(
                "SELECT * FROM analysis_requests WHERE project_id = ? ORDER BY requested_at DESC",
                [project_id],
            )
        )


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


@app.get("/api/load-cases/{load_case_id}/drop-videos", response_model=DropVideoPageResponse)
def get_drop_videos(
    load_case_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=20),
) -> dict[str, Any]:
    with connect() as conn:
        context = conn.execute(
            """
            SELECT lc.id, lc.name, lc.analysis_type, ar.id, ar.title
            FROM load_cases lc
            JOIN analysis_requests ar ON ar.id = lc.request_id
            WHERE lc.id = ?
            """,
            [load_case_id],
        ).fetchone()
        require_resource_permission(request, PROJECT_DATA_VIEW, "load_case", load_case_id, conn=conn)
        stored_videos = list_drop_videos(conn, load_case_id)
    if context is None:
        raise HTTPException(404, "하중 경우를 찾을 수 없습니다.")

    videos: list[dict[str, Any]] = []
    database_only = media_storage_mode() == "database-only"
    storage_source = "DATABASE" if stored_videos or database_only else "EXAMPLE_ADAPTER"
    if stored_videos:
        for item in stored_videos:
            scene = DROP_VIDEO_DEMO_BY_ID.get(item["video_id"])
            metadata = json_value(item.get("metadata_json")) or {}
            videos.append(
                {
                    "video_id": item["video_id"],
                    "scene_id": item["video_id"],
                    "scene_name": item["scene_name"],
                    "video_url": f"/api/drop-videos/{item['video_id']}/content",
                    "download_url": f"/api/drop-videos/{item['video_id']}/download",
                    "thumbnail_url": None,
                    "duration": None,
                    "file_size": int(item["file_size"]),
                    "format": "mp4" if str(item["mime_type"]) == "video/mp4" else "webm",
                    "codec": metadata.get("codec"),
                    "fast_start": metadata.get("fast_start"),
                    "sort_order": int(item["sort_order"]),
                    "drop_direction": metadata.get("drop_direction"),
                    "drop_condition": metadata.get("drop_condition"),
                    "analysis_version": metadata.get("analysis_version"),
                    "evaluation": build_demo_evaluation(scene) if scene else {
                        "overall_verdict": "PASS",
                        "open_cell": {"critical_value": 0, "threshold": 75, "unit": "MPa", "verdict": "PASS", "metrics": {}},
                        "chassis_rear": {"critical_value": 0, "threshold": 5, "unit": "mm", "verdict": "PASS", "metrics": {}},
                    },
                }
            )
    elif not database_only and load_case_id in DEMO_DROP_VIDEO_LOAD_CASE_IDS:
        for scene in DROP_VIDEO_DEMO_SCENES:
            path = DROP_VIDEO_SOURCE_DIR / scene.filename
            if not path.is_file():
                continue
            media = probe_mp4(path)
            videos.append(
                {
                    "video_id": scene.video_id,
                    "scene_id": scene.video_id,
                    "scene_name": scene.scene_name,
                    "video_url": f"/api/drop-videos/{scene.video_id}/content",
                    "download_url": f"/api/drop-videos/{scene.video_id}/download",
                    "thumbnail_url": None,
                    "duration": None,
                    "file_size": path.stat().st_size,
                    "format": "mp4",
                    "codec": media.codec,
                    "fast_start": media.fast_start,
                    "sort_order": scene.sort_order,
                    "drop_direction": None,
                    "drop_condition": None,
                    "analysis_version": None,
                    "evaluation": build_demo_evaluation(scene),
                }
            )
    videos.sort(key=lambda item: (item["sort_order"], item["scene_id"]))
    summary = summarize_demo_evaluations(videos)
    total_items = len(videos)
    total_pages = (total_items + page_size - 1) // page_size if total_items else 0
    start = (page - 1) * page_size
    page_videos = videos[start:start + page_size] if start < total_items else []
    return {
        "load_case": {
            "load_case_id": context[0],
            "load_case_name": context[1],
            "analysis_type": context[2],
            "request_id": context[3],
            "request_name": context[4],
        },
        "source": storage_source,
        "demo_only": True,
        "evaluation_source": "SYNTHETIC_DEMO",
        "contract_version": 1,
        "summary": summary,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_previous": page > 1 and total_pages > 0,
            "has_next": page < total_pages,
        },
        "videos": page_videos,
    }


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


@app.get("/api/load-cases/{load_case_id}/runs")
def list_analysis_runs(load_case_id: str, request: Request) -> list[dict[str, Any]]:
    return list_analysis_runs_query(
        load_case_id,
        lambda: require_permission(request, PROJECT_DATA_VIEW),
        SQLAnalysisRunSummaryRepositoryProvider(),
    )


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


SYSTEM_ANALYSIS_PAGE_IDS = {"dashboard-drop-default", "dashboard-chassis-default", "dashboard-run-comparison-default"}


def _analysis_page_meta(definition: dict[str, Any]) -> dict[str, Any] | None:
    page = definition.get("page")
    if not isinstance(page, dict) or page.get("kind") != "analysis_page":
        return None
    return page


def _analysis_page_summary(item: dict[str, Any], definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "project_id": item["project_id"],
        "request_id": item.get("request_id"),
        "load_case_id": item.get("load_case_id"),
        "name": item["name"],
        "description": item.get("description") or "",
        "version": item["version"],
        "updated_at": item["updated_at"],
        "page": _analysis_page_meta(definition),
    }


def _require_load_case_context(conn: Any, load_case_id: str) -> tuple[str, str]:
    context = conn.execute(
        """
        SELECT ar.project_id, lc.request_id
        FROM load_cases lc
        JOIN analysis_requests ar ON ar.id = lc.request_id
        WHERE lc.id = ?
        """,
        [load_case_id],
    ).fetchone()
    if not context:
        raise HTTPException(404, "하중 경우를 찾을 수 없습니다.")
    return context[0], context[1]


def _page_name_exists(conn: Any, load_case_id: str, name: str, exclude_id: str | None = None) -> bool:
    candidates = rows(
        conn.execute(
            """
            SELECT id, load_case_id, definition_json
            FROM dashboards
            WHERE load_case_id = ? OR id IN ('dashboard-drop-default', 'dashboard-chassis-default', 'dashboard-run-comparison-default')
            """,
            [load_case_id],
        )
    )
    normalized_name = name.strip().casefold()
    for item in candidates:
        if item["id"] == exclude_id:
            continue
        definition = json_value(item["definition_json"]) or {}
        page = _analysis_page_meta(definition)
        if not page or page.get("status") == "archived":
            continue
        if str(definition.get("name", "")).strip().casefold() == normalized_name:
            return True
    return False


def _write_dashboard_definition(
    conn: Any,
    dashboard_id: str,
    definition: dict[str, Any],
    current_version: int,
    created_by: str,
) -> tuple[int, datetime]:
    del current_version
    next_version = int(
        conn.execute(
            "SELECT COALESCE(max(version), 0) + 1 FROM dashboard_versions WHERE dashboard_id = ?",
            [dashboard_id],
        ).fetchone()[0]
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    encoded = json.dumps(definition, ensure_ascii=False)
    conn.execute(
        """
        UPDATE dashboards
        SET name = ?, description = ?, version = ?, definition_json = ?, updated_at = ?
        WHERE id = ?
        """,
        [definition["name"], definition.get("description", ""), next_version, encoded, now, dashboard_id],
    )
    conn.execute(
        "INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)",
        [dashboard_id, next_version, encoded, created_by, now, True],
    )
    return next_version, now


def _delete_analysis_page_records(conn: Any, dashboard_id: str) -> None:
    conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [dashboard_id])
    conn.execute("DELETE FROM dashboards WHERE id = ?", [dashboard_id])


def _list_analysis_pages(conn: Any, load_case_id: str, *, include_private: bool, include_archived: bool) -> list[dict[str, Any]]:
    _require_load_case_context(conn, load_case_id)
    stored = rows(
        conn.execute(
            """
            SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at
            FROM dashboards
            WHERE load_case_id = ? OR id IN ('dashboard-drop-default', 'dashboard-chassis-default', 'dashboard-run-comparison-default')
            """,
            [load_case_id],
        )
    )
    result: list[dict[str, Any]] = []
    for item in stored:
        definition = json_value(item.pop("definition_json")) or {}
        page = _analysis_page_meta(definition)
        if not page:
            continue
        is_system = item["id"] in SYSTEM_ANALYSIS_PAGE_IDS and page.get("is_system") is True
        is_current_custom = item.get("load_case_id") == load_case_id and page.get("analysis_key") == "custom" and page.get("is_system") is False
        if not (is_system or is_current_custom):
            continue
        status = page.get("status")
        if not include_private and not is_system and status != "published":
            continue
        if not include_archived and status == "archived":
            continue
        result.append(_analysis_page_summary(item, definition))
    return sorted(result, key=lambda item: (item["page"]["display_order"], item["name"].casefold(), item["id"]))


@app.get("/api/dashboard-pages", response_model=list[AnalysisPageSummary])
def list_public_dashboard_pages(load_case_id: str = Query(min_length=1, max_length=120)) -> list[dict[str, Any]]:
    with connect() as conn:
        return _list_analysis_pages(conn, load_case_id, include_private=False, include_archived=False)


@app.get("/api/admin/dashboard-pages", response_model=list[AnalysisPageSummary])
def list_admin_dashboard_pages(
    request: Request,
    load_case_id: str = Query(min_length=1, max_length=120),
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    with connect() as conn:
        project_id, _ = _require_load_case_context(conn, load_case_id)
        require_permission(request, DASHBOARD_EDIT, project_id, conn=conn)
        return _list_analysis_pages(conn, load_case_id, include_private=True, include_archived=include_archived)


@app.post("/api/admin/dashboard-pages", response_model=DashboardDefinition, status_code=201)
def create_dashboard_page(payload: AnalysisPageCreate, request: Request) -> dict[str, Any]:
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(422, "분석 페이지 이름은 두 글자 이상이어야 합니다.")
    dashboard_id = f"dashboard-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        project_id, request_id = _require_load_case_context(conn, payload.load_case_id)
        require_permission(request, DASHBOARD_EDIT, project_id, conn=conn)
        if _page_name_exists(conn, payload.load_case_id, name):
            raise HTTPException(409, "같은 하중 경우에 동일한 분석 페이지 이름이 이미 있습니다.")
        existing = rows(conn.execute("SELECT definition_json FROM dashboards WHERE load_case_id = ?", [payload.load_case_id]))
        custom_orders = []
        for item in existing:
            page = _analysis_page_meta(json_value(item["definition_json"]) or {})
            if page and page.get("analysis_key") == "custom" and page.get("is_system") is False:
                custom_orders.append(int(page.get("display_order", 99)))
        display_order = max([99, *custom_orders]) + 1
        definition = {
            "id": dashboard_id,
            "name": name,
            "description": payload.description.strip(),
            "widgets": [],
            "page": {
                "kind": "analysis_page",
                "analysis_key": "custom",
                "status": "draft",
                "display_order": display_order,
                "is_system": False,
            },
        }
        definition = DashboardDefinition.model_validate(definition).model_dump()
        encoded = json.dumps(definition, ensure_ascii=False)
        conn.execute(
            "INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [dashboard_id, project_id, request_id, payload.load_case_id, name, definition["description"], 1, encoded, now],
        )
        conn.execute("INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)", [dashboard_id, 1, encoded, "관리자", now, True])
    return definition


@app.patch("/api/admin/dashboard-pages/{dashboard_id}", response_model=DashboardDefinition)
def update_dashboard_page(dashboard_id: str, payload: AnalysisPageUpdate, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        stored_rows = rows(
            conn.execute(
                "SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at FROM dashboards WHERE id = ?",
                [dashboard_id],
            )
        )
        if not stored_rows:
            raise HTTPException(404, "분석 페이지를 찾을 수 없습니다.")
        item = stored_rows[0]
        definition = json_value(item.pop("definition_json")) or {}
        page = _analysis_page_meta(definition)
        if not page:
            raise HTTPException(404, "관리 가능한 분석 페이지가 아닙니다.")
        if dashboard_id in SYSTEM_ANALYSIS_PAGE_IDS or page.get("is_system") is True:
            raise HTTPException(409, "시스템 기본 분석 페이지의 생명주기는 변경할 수 없습니다.")

        name = payload.name.strip() if payload.name is not None else definition["name"]
        if len(name) < 2:
            raise HTTPException(422, "분석 페이지 이름은 두 글자 이상이어야 합니다.")
        target_status = payload.status or page["status"]
        if target_status == "published" and not definition.get("widgets"):
            raise HTTPException(422, "위젯이 없는 분석 페이지는 게시할 수 없습니다.")
        if target_status != "archived" and _page_name_exists(conn, item["load_case_id"], name, dashboard_id):
            raise HTTPException(409, "같은 하중 경우에 동일한 분석 페이지 이름이 이미 있습니다.")

        definition["name"] = name
        if payload.description is not None:
            definition["description"] = payload.description.strip()
        page["status"] = target_status
        definition["page"] = page
        definition = DashboardDefinition.model_validate(definition).model_dump()
        _write_dashboard_definition(conn, dashboard_id, definition, item["version"], "관리자")
    return definition


@app.delete("/api/admin/dashboard-pages/{dashboard_id}")
def delete_dashboard_page(
    dashboard_id: str,
    request: Request,
    load_case_id: str = Query(min_length=1, max_length=120),
) -> dict[str, str]:
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        stored = conn.execute("SELECT load_case_id, definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not stored:
            raise HTTPException(404, "분석 페이지를 찾을 수 없습니다.")
        definition = json_value(stored[1]) or {}
        page = _analysis_page_meta(definition)
        if (
            dashboard_id in SYSTEM_ANALYSIS_PAGE_IDS
            or not page
            or page.get("is_system") is not False
            or page.get("analysis_key") != "custom"
        ):
            raise HTTPException(409, "사용자 정의 분석 페이지만 영구 삭제할 수 있습니다.")
        if stored[0] != load_case_id:
            raise HTTPException(409, "분석 페이지가 요청한 하중 경우에 속하지 않습니다.")
        _require_load_case_context(conn, load_case_id)
        conn.execute("BEGIN TRANSACTION")
        try:
            _delete_analysis_page_records(conn, dashboard_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"status": "deleted", "id": dashboard_id, "load_case_id": load_case_id}


@app.put("/api/admin/dashboard-pages/order", response_model=list[AnalysisPageSummary])
def reorder_dashboard_pages(payload: AnalysisPageOrderUpdate, request: Request) -> list[dict[str, Any]]:
    if len(payload.page_ids) != len(set(payload.page_ids)):
        raise HTTPException(422, "분석 페이지 순서에 중복 ID가 있습니다.")
    with connect() as conn:
        project_id, _ = _require_load_case_context(conn, payload.load_case_id)
        require_permission(request, DASHBOARD_EDIT, project_id, conn=conn)
        stored = rows(
            conn.execute(
                "SELECT id, version, definition_json FROM dashboards WHERE load_case_id = ?",
                [payload.load_case_id],
            )
        )
        custom_pages: dict[str, dict[str, Any]] = {}
        for item in stored:
            definition = json_value(item.pop("definition_json")) or {}
            page = _analysis_page_meta(definition)
            if page and page.get("analysis_key") == "custom" and page.get("is_system") is False and page.get("status") != "archived":
                custom_pages[item["id"]] = {**item, "definition": definition}
        if set(payload.page_ids) != set(custom_pages):
            raise HTTPException(422, "현재 하중 경우의 보관되지 않은 사용자 분석 페이지를 모두 한 번씩 지정해야 합니다.")
        for offset, dashboard_id in enumerate(payload.page_ids):
            item = custom_pages[dashboard_id]
            item["definition"]["page"]["display_order"] = 100 + offset
            definition = DashboardDefinition.model_validate(item["definition"]).model_dump()
            _write_dashboard_definition(conn, dashboard_id, definition, item["version"], "관리자 순서 변경")
        return _list_analysis_pages(conn, payload.load_case_id, include_private=True, include_archived=False)


@app.get("/api/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: str, request: Request) -> dict[str, Any]:
    with connect() as conn:
        result = rows(conn.execute("SELECT * FROM dashboards WHERE id = ?", [dashboard_id]))
        if not result:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        item = result[0]
        definition = json_value(item.pop("definition_json"))
        page = _analysis_page_meta(definition)
        if page and page.get("status") in {"draft", "archived"}:
            require_permission(request, DASHBOARD_EDIT, item["project_id"], conn=conn)
        definition["version"] = item["version"]
        definition["updated_at"] = item["updated_at"]
        return definition


@app.get("/api/dashboards")
def list_dashboards(request: Request, project_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if project_id:
            stored = rows(conn.execute("SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at FROM dashboards WHERE project_id = ? ORDER BY updated_at DESC", [project_id]))
        else:
            stored = rows(conn.execute("SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at FROM dashboards ORDER BY updated_at DESC"))
        result = []
        for item in stored:
            definition = json_value(item.pop("definition_json")) or {}
            page = _analysis_page_meta(definition)
            if (
                page
                and page.get("status") in {"draft", "archived"}
                and not has_permission(request, DASHBOARD_EDIT, item["project_id"], conn=conn)
            ):
                continue
            result.append(item)
        return result


@app.put("/api/dashboards/{dashboard_id}")
def save_dashboard(dashboard_id: str, definition: DashboardDefinition, request: Request) -> dict[str, Any]:
    if dashboard_id != definition.id:
        raise HTTPException(400, "대시보드 ID가 일치하지 않습니다.")
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        existing = conn.execute("SELECT version, definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not existing:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        stored_definition = json_value(existing[1]) or {}
        stored_page = _analysis_page_meta(stored_definition)
        incoming = definition.model_dump()
        if stored_page:
            if (
                incoming.get("name") != stored_definition.get("name")
                or incoming.get("description", "") != stored_definition.get("description", "")
                or incoming.get("page") != stored_page
            ):
                raise HTTPException(409, "분석 페이지 이름·설명·상태·순서는 관리자 페이지 API에서 변경해야 합니다.")
        version, now = _write_dashboard_definition(conn, dashboard_id, incoming, existing[0], "대시보드 사용자")
    return {"status": "saved", "version": version, "updated_at": now}


@app.get("/api/dashboards/{dashboard_id}/versions")
def get_dashboard_versions(
    dashboard_id: str,
    request: Request,
    include_invalid: bool = False,
) -> list[dict[str, Any]]:
    with connect() as conn:
        current = conn.execute("SELECT definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not current:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        page = _analysis_page_meta(json_value(current[0]) or {})
        if page and page.get("status") in {"draft", "archived"}:
            require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        valid_filter = "" if include_invalid else " AND is_valid = true"
        return rows(
            conn.execute(
                f"SELECT dashboard_id, version, created_by, created_at, is_valid FROM dashboard_versions WHERE dashboard_id = ?{valid_filter} ORDER BY version DESC",
                [dashboard_id],
            )
        )


@app.get("/api/dashboards/{dashboard_id}/versions/{version}")
def get_dashboard_version(
    dashboard_id: str,
    version: int,
    request: Request,
    include_invalid: bool = False,
) -> dict[str, Any]:
    with connect() as conn:
        current = conn.execute("SELECT definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not current:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        page = _analysis_page_meta(json_value(current[0]) or {})
        if page and page.get("status") in {"draft", "archived"}:
            require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        valid_filter = "" if include_invalid else " AND is_valid = true"
        stored = conn.execute(
            f"SELECT definition_json, created_by, created_at, is_valid FROM dashboard_versions WHERE dashboard_id = ? AND version = ?{valid_filter}",
            [dashboard_id, version],
        ).fetchone()
        if not stored:
            raise HTTPException(404, "대시보드 버전을 찾을 수 없습니다.")
    return {
        "dashboard_id": dashboard_id,
        "version": version,
        "definition": json_value(stored[0]) or {},
        "created_by": stored[1],
        "created_at": stored[2],
        "is_valid": stored[3],
    }


@app.delete("/api/dashboards/{dashboard_id}/versions/{version}")
def delete_dashboard_version(dashboard_id: str, version: int, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        current = conn.execute("SELECT version, definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not current:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        current_version = int(current[0])
        definition = json_value(current[1]) or {}
        page = _analysis_page_meta(definition)
        if version == 1 and (dashboard_id in SYSTEM_ANALYSIS_PAGE_IDS or (page and page.get("is_system") is True)):
            raise HTTPException(409, "시스템 대시보드의 최초 기준 버전은 삭제할 수 없습니다.")

        stored = conn.execute(
            "SELECT is_valid FROM dashboard_versions WHERE dashboard_id = ? AND version = ?",
            [dashboard_id, version],
        ).fetchone()
        if not stored or stored[0] is not True:
            raise HTTPException(404, "삭제할 수 있는 유효한 대시보드 버전을 찾을 수 없습니다.")
        if version == current_version:
            raise HTTPException(409, "현재 사용 중인 live 버전은 삭제할 수 없습니다.")

        valid_history_count = int(
            conn.execute(
                "SELECT count(*) FROM dashboard_versions WHERE dashboard_id = ? AND is_valid = true AND version <> ?",
                [dashboard_id, current_version],
            ).fetchone()[0]
        )
        if valid_history_count <= 1:
            raise HTTPException(409, "현재 버전 외에 최소 1개의 유효한 과거 버전을 유지해야 합니다.")
        conn.execute(
            "UPDATE dashboard_versions SET is_valid = false WHERE dashboard_id = ? AND version = ? AND is_valid = true",
            [dashboard_id, version],
        )
    return {"status": "invalidated", "dashboard_id": dashboard_id, "version": version}


@app.post("/api/dashboards/{dashboard_id}/clone", status_code=201)
def clone_dashboard(dashboard_id: str, payload: DashboardClone, request: Request) -> dict[str, Any]:
    clone_id = f"dashboard-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    principal = request.state.principal
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        source = conn.execute("SELECT project_id, request_id, load_case_id, definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not source:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        definition = json_value(source[3])
        definition.pop("page", None)
        definition.update({"id": clone_id, "name": payload.name.strip(), "description": payload.description.strip()})
        encoded = json.dumps(definition, ensure_ascii=False)
        conn.execute("INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [clone_id, source[0], source[1], source[2], definition["name"], definition["description"], 1, encoded, now])
        conn.execute("INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)", [clone_id, 1, encoded, principal.user_id, now, True])
    return {"id": clone_id, "version": 1, "status": "cloned"}


@app.post("/api/dashboards/{dashboard_id}/restore/{version}")
def restore_dashboard(dashboard_id: str, version: int, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        stored = conn.execute("SELECT definition_json FROM dashboard_versions WHERE dashboard_id = ? AND version = ? AND is_valid = true", [dashboard_id, version]).fetchone()
        current = conn.execute("SELECT version, definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not stored or not current:
            raise HTTPException(404, "복구 가능한 정상 버전을 찾을 수 없습니다.")
        definition = json_value(stored[0])
        current_definition = json_value(current[1]) or {}
        current_page = _analysis_page_meta(current_definition)
        if current_page:
            if current_page.get("status") == "published" and not definition.get("widgets"):
                raise HTTPException(422, "게시된 분석 페이지를 빈 위젯 버전으로 복구할 수 없습니다.")
            definition.update(
                {
                    "id": current_definition["id"],
                    "name": current_definition["name"],
                    "description": current_definition.get("description", ""),
                    "page": current_page,
                }
            )
        definition = DashboardDefinition.model_validate(definition).model_dump()
        next_version, _ = _write_dashboard_definition(conn, dashboard_id, definition, current[0], "복구 작업")
    return {"status": "restored", "version": next_version, "restored_from": version}


@app.post("/api/dashboard-commands/preview")
def preview_dashboard_command(
    payload: NaturalLanguageCommand,
    request: Request,
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    require_permission(request, DASHBOARD_EDIT, project_id)
    text = payload.command.strip()
    compact = text.replace(" ", "").lower()
    widget: dict[str, Any] | None = None
    message = ""

    if "응력" in compact and ("시간" in compact or "시계열" in compact) and "기준선" in compact and ("추가" in compact or "넣" in compact):
        return {"recognized": True, "message": "기존 응력-시간 위젯에 기준선을 표시합니다.", "proposal": {"action": "update_widgets", "updates": [{"widget_type": "time_series", "settings": {"showThreshold": True}}]}}
    if ("판정" in compact or "패스" in compact or "실패" in compact) and ("오른쪽" in compact or "우측" in compact) and ("이동" in compact or "옮" in compact):
        return {"recognized": True, "message": "패스/실패 판정 카드를 맨 위 오른쪽으로 이동합니다.", "proposal": {"action": "update_widgets", "updates": [{"widget_type": "verdict", "x": 9, "y": 0}]}}
    if "컨투어" in compact and "의견" in compact and ("나란히" in compact or "옆" in compact):
        return {"recognized": True, "message": "컨투어 이미지와 수행자 의견을 같은 행에 배치합니다.", "proposal": {"action": "update_widgets", "updates": [{"widget_type": "contour", "x": 4, "y": 18, "w": 4}, {"widget_type": "note", "x": 8, "y": 18, "w": 4}]}}
    if "응력" in compact and ("시간" in compact or "시계열" in compact) and ("추가" in compact or "만들" in compact):
        widget = {
            "id": f"time-series-{int(datetime.now().timestamp())}",
            "type": "time_series",
            "title": "Open Cell 엣지 응력-시간",
            "x": 0,
            "y": 20,
            "w": 8,
            "h": 5,
            "settings": {"showThreshold": True},
        }
        message = "상하좌우 엣지 응력 시계열과 기준선을 표시하는 위젯을 추가합니다."
    elif ("최대응력" in compact or "상하좌우" in compact) and ("추가" in compact or "막대" in compact):
        widget = {
            "id": f"edge-bar-{int(datetime.now().timestamp())}",
            "type": "edge_bar",
            "title": "상하좌우 엣지 최대 응력",
            "x": 0,
            "y": 20,
            "w": 6,
            "h": 4,
            "settings": {"failColor": "#ff5d73", "showThreshold": True},
        }
        message = "엣지별 최대 응력과 기준값을 비교하는 막대그래프를 추가합니다."
    elif "판정" in compact and ("추가" in compact or "카드" in compact):
        widget = {
            "id": f"verdict-{int(datetime.now().timestamp())}",
            "type": "verdict",
            "title": "패스/실패 판정",
            "x": 0,
            "y": 20,
            "w": 3,
            "h": 2,
            "settings": {},
        }
        message = "기준값에 따른 전체 판정 카드를 추가합니다."
    else:
        return {
            "recognized": False,
            "message": "요청을 안전한 변경 명세로 변환하지 못했습니다. ‘응력-시간 그래프 추가’, ‘상하좌우 최대 응력 막대그래프 추가’, ‘판정 카드 추가’처럼 요청해 주세요.",
            "proposal": None,
        }

    return {"recognized": True, "message": message, "proposal": {"action": "add_widget", "widget": widget}}
