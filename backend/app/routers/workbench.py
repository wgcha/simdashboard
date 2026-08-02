from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request

from ..database_connection import connect, rows
from ..repositories.workbench import WorkbenchRepository
from ..schemas.workbench import BatchDispatchCreate, BatchProfileInput, DemoRunCreate, RequestTypeAssignmentInput, RequestTypeVersionCreate, TaskTypeVersionCreate, WorkItemComplete, WorkItemProgress, WorkItemStart
from ..services.demo_runner import DemoRunnerService, WorkbenchValidationError, _topological_nodes
from ..services.request_monitoring import request_monitoring_summary, sync_request_status


router = APIRouter(prefix="/api", tags=["workbench-demo"])


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/workbench/task-types")
def list_task_types(all_versions: bool = Query(default=False)) -> list[dict[str, Any]]:
    with connect() as conn:
        return WorkbenchRepository(conn).list_task_types(all_versions=all_versions)


@router.post("/admin/workbench/task-types", status_code=201)
def create_task_type(payload: TaskTypeVersionCreate) -> dict[str, Any]:
    with connect() as conn:
        return WorkbenchRepository(conn).create_task_type_version(payload.model_dump())


@router.get("/workbench/request-types")
def list_request_types(all_versions: bool = Query(default=False)) -> list[dict[str, Any]]:
    with connect() as conn:
        return WorkbenchRepository(conn).list_request_types(all_versions=all_versions)


@router.post("/admin/workbench/request-types", status_code=201)
def create_request_type(payload: RequestTypeVersionCreate) -> dict[str, Any]:
    try:
        _topological_nodes(payload.default_workflow.nodes)
    except WorkbenchValidationError as exc:
        raise _bad_request(exc) from exc
    allowed = {(item.id, item.version) for item in payload.allowed_task_types}
    for node in payload.default_workflow.nodes:
        if (node.task_type_id, node.task_type_version) not in allowed:
            raise HTTPException(400, f"기본 Workflow에 허용되지 않은 Task가 있습니다: {node.task_type_id}")
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        missing = [f"{item.id} v{item.version}" for item in payload.allowed_task_types if not (task := repository.get_task_type(item.id, item.version)) or not task["is_active"]]
        if missing:
            raise HTTPException(400, f"Task Type 버전을 찾을 수 없습니다: {', '.join(missing)}")
        return repository.create_request_type_version(payload.model_dump())


@router.get("/workbench/batch-profiles")
def list_batch_profiles(include_inactive: bool = Query(default=False)) -> list[dict[str, Any]]:
    with connect() as conn:
        return WorkbenchRepository(conn).list_batch_profiles(include_inactive=include_inactive)


@router.put("/admin/workbench/batch-profiles/{profile_id}")
def save_batch_profile(profile_id: str, payload: BatchProfileInput, request: Request) -> dict[str, Any]:
    if profile_id != payload.id:
        raise HTTPException(422, "경로의 프로필 ID와 본문의 ID가 일치해야 합니다.")
    principal = getattr(request.state, "principal", None)
    if principal:
        payload = payload.model_copy(update={"updated_by": principal.display_name})
    with connect() as conn:
        return WorkbenchRepository(conn).upsert_batch_profile(payload.model_dump())


@router.post("/workbench/work-items/{item_id}/batch-dispatch", status_code=201)
def dispatch_batch_work_item(item_id: str, payload: BatchDispatchCreate, request: Request) -> dict[str, Any]:
    principal = getattr(request.state, "principal", None)
    created_by = principal.display_name if principal else payload.created_by.strip()
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        work_items = rows(conn.execute("SELECT * FROM request_work_items WHERE id=?", [item_id]))
        if not work_items:
            raise HTTPException(404, detail={"code": "WORK_ITEM_NOT_FOUND", "item_id": item_id})
        work_item = work_items[0]
        if work_item["status"] != "IN_PROGRESS":
            raise HTTPException(409, detail={"code": "WORK_ITEM_NOT_IN_PROGRESS", "item_id": item_id})
        profile = repository.get_batch_profile(payload.batch_profile_id)
        if not profile or not profile["is_active"]:
            raise HTTPException(404, detail={"code": "BATCH_PROFILE_NOT_FOUND", "batch_profile_id": payload.batch_profile_id})
        if work_item["task_type_id"] not in profile["task_type_ids"]:
            raise HTTPException(409, detail={"code": "BATCH_PROFILE_TASK_MISMATCH", "batch_profile_id": profile["id"], "task_type_id": work_item["task_type_id"]})
        node = {
            "node_key": work_item["node_key"],
            "task_type_id": work_item["task_type_id"],
            "task_type_version": int(work_item["task_type_version"]),
            "depends_on": [],
        }
        demo_payload = DemoRunCreate(
            name=f"{work_item['display_name']} 배치 기록",
            request_id=work_item["request_id"],
            execution_mode="DEMO_ONLY",
            nodes=[node],
            created_by=created_by,
        )
        try:
            run = DemoRunnerService(repository).create_run(demo_payload)
        except WorkbenchValidationError as exc:
            raise _bad_request(exc) from exc
        command_preview = f'"{profile["solver_path"]}" {profile["arguments_template"]}'.strip()
        dispatch = {
            "id": f"dispatch-{uuid4().hex[:12]}",
            "work_item_id": item_id,
            "workflow_run_id": run["id"],
            "batch_profile_id": profile["id"],
            "profile_snapshot_json": json.dumps(profile, ensure_ascii=False, default=str),
            "command_preview": command_preview,
            "status": "RECORDED_DEMO",
            "created_by": created_by,
            "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        repository.insert_batch_dispatch(dispatch)
        return DemoRunnerService(repository).get_run(run["id"])  # type: ignore[return-value]


@router.get("/workbench/requests/{request_id}/request-type")
def resolve_request_type(request_id: str) -> dict[str, Any]:
    with connect() as conn:
        try:
            return WorkbenchRepository(conn).request_type_resolution(request_id)
        except LookupError as exc:
            raise HTTPException(404, "해석 의뢰를 찾을 수 없습니다.") from exc


@router.put("/workbench/requests/{request_id}/request-type")
def assign_request_type(request_id: str, payload: RequestTypeAssignmentInput, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    source = "ADMIN" if principal.role == "admin" else "USER"
    with connect() as conn:
        if WorkbenchRepository(conn).work_plan(request_id):
            raise HTTPException(
                409,
                detail={"code": "WORK_PLAN_IMMUTABLE", "request_id": request_id},
            )
        try:
            return WorkbenchRepository(conn).assign_request_type(request_id, payload.request_type_id, payload.request_type_version, source, principal.display_name)
        except PermissionError as exc:
            raise HTTPException(409, "관리자가 고정한 의뢰 유형은 관리자만 변경할 수 있습니다.") from exc
        except LookupError as exc:
            raise HTTPException(404, "해석 의뢰 또는 Request Type 버전을 찾을 수 없습니다.") from exc


@router.post("/workbench/demo-runs", status_code=201)
def create_demo_run(payload: DemoRunCreate, request: Request) -> dict[str, Any]:
    principal = getattr(request.state, "principal", None)
    if principal:
        payload = payload.model_copy(update={"created_by": principal.display_name})
    with connect() as conn:
        try:
            return DemoRunnerService(WorkbenchRepository(conn)).create_run(payload)
        except WorkbenchValidationError as exc:
            raise _bad_request(exc) from exc


@router.get("/workbench/demo-runs")
def list_demo_runs(request_id: str | None = Query(default=None, min_length=3, max_length=100)) -> list[dict[str, Any]]:
    with connect() as conn:
        return DemoRunnerService(WorkbenchRepository(conn)).list_runs(request_id)


@router.get("/workbench/demo-runs/{run_id}")
def get_demo_run(run_id: str) -> dict[str, Any]:
    with connect() as conn:
        item = DemoRunnerService(WorkbenchRepository(conn)).get_run(run_id)
    if not item:
        raise HTTPException(404, "데모 실행을 찾을 수 없습니다.")
    return item


@router.get("/workbench/requests/{request_id}/work-plan")
def get_request_work_plan(request_id: str) -> dict[str, Any]:
    with connect() as conn:
        if not WorkbenchRepository(conn).analysis_request_exists(request_id):
            raise HTTPException(
                404,
                detail={"code": "REQUEST_NOT_FOUND", "request_id": request_id},
            )
        summary = request_monitoring_summary(conn, request_id)
        if not summary["work_plan"]:
            raise HTTPException(
                404,
                detail={"code": "WORK_PLAN_NOT_FOUND", "request_id": request_id},
            )
        return {"request_id": request_id, **summary}


@router.post("/workbench/work-items/{item_id}/start")
def start_work_item(item_id: str, payload: WorkItemStart) -> dict[str, Any]:
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            items = rows(conn.execute("SELECT * FROM request_work_items WHERE id = ?", [item_id]))
            if not items:
                raise HTTPException(
                    404,
                    detail={"code": "WORK_ITEM_NOT_FOUND", "item_id": item_id},
                )
            item = items[0]
            request_id = item["request_id"]
            if item["status"] in {"IN_PROGRESS", "COMPLETED"}:
                summary = request_monitoring_summary(conn, request_id)
                conn.execute("COMMIT")
                return {"request_id": request_id, **summary}

            current_row = conn.execute(
                """
                SELECT id FROM request_work_items
                WHERE request_id = ? AND status IN ('IN_PROGRESS', 'READY')
                ORDER BY CASE WHEN status = 'IN_PROGRESS' THEN 0 ELSE 1 END, sequence_no
                LIMIT 1
                """,
                [request_id],
            ).fetchone()
            current_item_id = current_row[0] if current_row else None
            if item["status"] != "READY" or current_item_id != item_id:
                raise HTTPException(
                    409,
                    detail={
                        "code": "WORK_ITEM_NOT_READY",
                        "item_id": item_id,
                        "current_item_id": current_item_id,
                    },
                )
            incomplete_prior = conn.execute(
                """
                SELECT count(*) FROM request_work_items
                WHERE request_id = ? AND sequence_no < ? AND status <> 'COMPLETED'
                """,
                [request_id, item["sequence_no"]],
            ).fetchone()[0]
            if incomplete_prior:
                raise HTTPException(
                    409,
                    detail={"code": "WORK_ITEM_PREREQUISITE_INCOMPLETE", "item_id": item_id},
                )

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            conn.execute(
                """
                UPDATE request_work_items
                SET status = 'IN_PROGRESS', progress = 1, progress_updated_by = ?, progress_updated_at = ?, started_by = ?, started_at = ?
                WHERE id = ? AND status = 'READY'
                """,
                [payload.started_by.strip(), now, payload.started_by.strip(), now, item_id],
            )
            summary = sync_request_status(conn, request_id)
            conn.execute("COMMIT")
            return {"request_id": request_id, **summary}
        except HTTPException:
            conn.execute("ROLLBACK")
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise


@router.patch("/workbench/work-items/{item_id}/progress")
def update_work_item_progress(item_id: str, payload: WorkItemProgress, request: Request) -> dict[str, Any]:
    principal = getattr(request.state, "principal", None)
    updated_by = principal.display_name if principal else payload.updated_by.strip()
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            items = rows(conn.execute("SELECT request_id, status, progress FROM request_work_items WHERE id=?", [item_id]))
            if not items:
                raise HTTPException(404, detail={"code": "WORK_ITEM_NOT_FOUND", "item_id": item_id})
            item = items[0]
            if item["status"] != "IN_PROGRESS":
                raise HTTPException(409, detail={"code": "WORK_ITEM_NOT_IN_PROGRESS", "item_id": item_id})
            current = int(item.get("progress") or 0)
            if payload.progress <= current:
                raise HTTPException(409, detail={"code": "WORK_ITEM_PROGRESS_NOT_MONOTONIC", "current": current, "requested": payload.progress})
            conn.execute(
                "UPDATE request_work_items SET progress=?, progress_updated_by=?, progress_updated_at=? WHERE id=?",
                [payload.progress, updated_by, datetime.now(timezone.utc).replace(tzinfo=None), item_id],
            )
            summary = sync_request_status(conn, item["request_id"])
            conn.execute("COMMIT")
            return {"request_id": item["request_id"], **summary}
        except HTTPException:
            conn.execute("ROLLBACK")
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise


@router.post("/workbench/work-items/{item_id}/complete")
def complete_work_item(item_id: str, payload: WorkItemComplete) -> dict[str, Any]:
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            items = rows(
                conn.execute(
                    "SELECT * FROM request_work_items WHERE id = ?",
                    [item_id],
                )
            )
            if not items:
                raise HTTPException(
                    404,
                    detail={"code": "WORK_ITEM_NOT_FOUND", "item_id": item_id},
                )
            item = items[0]
            request_id = item["request_id"]

            if item["status"] == "COMPLETED":
                summary = request_monitoring_summary(conn, request_id)
                conn.execute("COMMIT")
                return {"request_id": request_id, **summary}

            current_row = conn.execute(
                """
                SELECT id FROM request_work_items
                WHERE request_id = ? AND status IN ('IN_PROGRESS', 'READY')
                ORDER BY CASE WHEN status = 'IN_PROGRESS' THEN 0 ELSE 1 END, sequence_no
                LIMIT 1
                """,
                [request_id],
            ).fetchone()
            current_item_id = current_row[0] if current_row else None
            if item["status"] == "READY" and current_item_id == item_id:
                raise HTTPException(
                    409,
                    detail={
                        "code": "WORK_ITEM_NOT_STARTED",
                        "item_id": item_id,
                        "current_item_id": current_item_id,
                    },
                )
            if current_item_id != item_id:
                raise HTTPException(
                    409,
                    detail={
                        "code": "WORK_ITEM_NOT_CURRENT",
                        "item_id": item_id,
                        "current_item_id": current_item_id,
                    },
                )

            incomplete_prior = conn.execute(
                """
                SELECT count(*) FROM request_work_items
                WHERE request_id = ? AND sequence_no < ? AND status <> 'COMPLETED'
                """,
                [request_id, item["sequence_no"]],
            ).fetchone()[0]
            if incomplete_prior:
                raise HTTPException(
                    409,
                    detail={
                        "code": "WORK_ITEM_PREREQUISITE_INCOMPLETE",
                        "item_id": item_id,
                    },
                )

            if payload.demo_run_id:
                demo_run = conn.execute(
                    "SELECT request_id, status FROM workflow_runs WHERE id = ?",
                    [payload.demo_run_id],
                ).fetchone()
                if not demo_run or demo_run[0] != request_id or demo_run[1] != "SUCCEEDED":
                    raise HTTPException(
                        409,
                        detail={
                            "code": "DEMO_RUN_INVALID",
                            "item_id": item_id,
                            "demo_run_id": payload.demo_run_id,
                        },
                    )

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            conn.execute(
                """
                UPDATE request_work_items
                SET status = 'COMPLETED', progress = 100, progress_updated_by = ?, progress_updated_at = ?, completed_by = ?, completed_at = ?, demo_run_id = ?
                WHERE id = ? AND status = 'IN_PROGRESS'
                """,
                [payload.completed_by.strip(), now, payload.completed_by.strip(), now, payload.demo_run_id, item_id],
            )
            next_row = conn.execute(
                """
                SELECT id FROM request_work_items
                WHERE request_id = ? AND sequence_no > ? AND status = 'WAITING'
                ORDER BY sequence_no LIMIT 1
                """,
                [request_id, item["sequence_no"]],
            ).fetchone()
            if next_row:
                conn.execute(
                    "UPDATE request_work_items SET status = 'READY' WHERE id = ? AND status = 'WAITING'",
                    [next_row[0]],
                )
            summary = sync_request_status(conn, request_id)
            conn.execute("COMMIT")
            return {"request_id": request_id, **summary}
        except HTTPException:
            conn.execute("ROLLBACK")
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise
