from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..adapters.persistence.workbench import (
    SQLWorkbenchCatalogQuery,
    SQLWorkbenchRequestTypeAssignmentCommand,
    SQLWorkbenchRequestWorkPlanQuery,
    SQLWorkbenchRequestTypeResolutionQuery,
    SQLWorkbenchWorkItemCompleteCommand,
    SQLWorkbenchWorkItemProgressCommand,
    SQLWorkbenchWorkItemReassignmentCommand,
    SQLWorkbenchBatchDispatchCommand,
    SQLWorkbenchWorkItemStartCommand,
)
from ..application.workbench.commands import (
    assign_workbench_request_type,
    complete_workbench_work_item,
    reassign_workbench_work_item,
    dispatch_workbench_batch,
    start_workbench_work_item,
    update_workbench_work_item_progress,
)
from ..application.workbench.queries import (
    list_workbench_request_types,
    list_workbench_task_types,
    get_workbench_request_work_plan,
    resolve_workbench_request_type,
)
from ..domains.workbench.models import (
    RequestTypeAssignmentCommand,
    RequestTypeAssignmentLockedError,
    RequestTypeAssignmentTargetNotFoundError,
    RequestWorkPlanImmutableError,
    DemoRunInvalidError,
    WorkItemAssigneeAccountNotActiveError,
    WorkItemAssigneeMembershipRequiredError,
    WorkItemCompleteCommand,
    WorkItemNotFoundError,
    WorkItemNotInProgressError,
    WorkItemNotCurrentError,
    WorkItemProgressCommand,
    WorkItemProgressNotMonotonicError,
    WorkItemNotReadyError,
    WorkItemNotStartedError,
    WorkItemPrerequisiteIncompleteError,
    WorkItemReassignmentCommand,
    WorkItemReassignmentFinalError,
    BatchDispatchCommand,
    BatchDispatchError,
    BatchDemoRunValidationError,
    WorkItemStartCommand,
)
from ..modules.access_control import (
    DASHBOARD_EDIT,
    PROJECT_DATA_VIEW,
    REQUEST_EDIT,
    SYSTEM_CATALOG_MANAGE,
    WORKFLOW_EDIT,
    require_any_project_permission,
    require_assigned_work_item,
    require_permission,
    require_resource_permission,
)
from ..database_connection import connect, rows
from ..repositories.workbench import WorkbenchRepository
from ..schemas.api import DashboardDefinition
from ..schemas.workbench import AnalysisTemplateVersionCreate, BatchDispatchCreate, BatchProfileInput, DemoRunCreate, RequestTypeAssignmentInput, RequestTypeVersionCreate, ResultLayoutMaterializeInput, ResultProfileInput, TaskTypeVersionCreate, WorkItemAssigneeUpdate, WorkItemComplete, WorkItemProgress, WorkItemStart
from ..security import write_audit_event
from ..services.batch_execution import BatchPreflightError, validate_profile_definition
from ..services.demo_runner import DemoRunnerService, WorkbenchValidationError, _topological_nodes
from ..services.request_result_dashboard import materialize_request_result_dashboard


router = APIRouter(prefix="/api", tags=["workbench-demo"])


def _audit_execution_override(request: Request, conn: Any, operation: str) -> None:
    detail = getattr(request.state, "work_execution_override", None)
    if not detail:
        return
    write_audit_event(
        request=request,
        principal=request.state.principal,
        status_code=200,
        action="WORK_EXECUTION_OVERRIDE",
        detail={**detail, "operation": operation},
        connection=conn,
    )


def _bad_request(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _create_request_type_version_atomically(conn: Any, repository: WorkbenchRepository, data: dict[str, Any]) -> dict[str, Any]:
    repository.begin_transaction()
    try:
        saved = repository.create_request_type_version(data)
        repository.commit_transaction()
        return saved
    except Exception:
        repository.rollback_transaction()
        raise


def _is_admin(request: Request) -> bool:
    return bool(getattr(getattr(request.state, "principal", None), "is_global_admin", False))


def _sanitize_batch_profile(profile: dict[str, Any], request: Request) -> dict[str, Any]:
    if _is_admin(request):
        return profile
    return {**profile, "solver_path": "", "working_directory": "", "environment": {}}


def _sanitize_batch_attempt(attempt: dict[str, Any], request: Request) -> dict[str, Any]:
    if _is_admin(request):
        return attempt
    return {**attempt, "profile_snapshot": {}, "command_preview": "[관리자 전용]"}


def _sanitize_demo_run(run: dict[str, Any], request: Request) -> dict[str, Any]:
    sanitized = dict(run)
    if isinstance(sanitized.get("batch_attempt"), dict):
        sanitized["batch_attempt"] = _sanitize_batch_attempt(sanitized["batch_attempt"], request)
    if isinstance(sanitized.get("batch_dispatch"), dict) and not _is_admin(request):
        sanitized["batch_dispatch"] = {
            **sanitized["batch_dispatch"],
            "profile_snapshot": {},
            "command_preview": "[관리자 전용]",
        }
    return sanitized


@router.get("/workbench/task-types")
def list_task_types(all_versions: bool = Query(default=False)) -> list[dict[str, Any]]:
    with connect() as conn:
        return list_workbench_task_types(SQLWorkbenchCatalogQuery(conn), all_versions=all_versions)


@router.post("/admin/workbench/task-types", status_code=201)
def create_task_type(payload: TaskTypeVersionCreate, request: Request) -> dict[str, Any]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    with connect() as conn:
        data = payload.model_dump()
        # IDs are server-owned.  Ignore the optional legacy field on create.
        data["id"] = None
        try:
            return WorkbenchRepository(conn).create_task_type_version(data)
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise HTTPException(409, "수행 작업 유형 ID 생성이 충돌했습니다. 다시 시도해 주세요.") from exc
            raise


@router.put("/admin/workbench/task-types/{task_type_id}", status_code=201)
def update_task_type(task_type_id: str, payload: TaskTypeVersionCreate, request: Request) -> dict[str, Any]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    data = payload.model_dump()
    data["id"] = task_type_id
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        if not repository.task_type_exists(task_type_id):
            raise HTTPException(404, "수행 작업 유형을 찾을 수 없습니다.")
        return repository.create_task_type_version(data)


@router.delete("/admin/workbench/task-types/{task_type_id}")
def deactivate_task_type(task_type_id: str, request: Request) -> dict[str, str]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    with connect() as conn:
        if not WorkbenchRepository(conn).deactivate_task_type(task_type_id):
            raise HTTPException(404, "수행 작업 유형을 찾을 수 없습니다.")
    return {"id": task_type_id, "status": "INACTIVE"}


@router.get("/workbench/request-types")
def list_request_types(all_versions: bool = Query(default=False)) -> list[dict[str, Any]]:
    with connect() as conn:
        return list_workbench_request_types(SQLWorkbenchCatalogQuery(conn), all_versions=all_versions)


@router.get("/workbench/analysis-templates")
def list_analysis_templates(request: Request, all_versions: bool = Query(default=False), project_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    with connect() as conn:
        if project_id:
            require_permission(
                request,
                DASHBOARD_EDIT if all_versions else PROJECT_DATA_VIEW,
                project_id,
                conn=conn,
            )
        elif all_versions:
            require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        return WorkbenchRepository(conn).list_analysis_templates(
            all_versions=all_versions,
            project_id=project_id,
        )


@router.post("/admin/workbench/analysis-templates", status_code=201)
def create_analysis_template(payload: AnalysisTemplateVersionCreate, request: Request) -> dict[str, Any]:
    if payload.scope_kind == "PROJECT" and not payload.project_id:
        raise HTTPException(422, "프로젝트 분석 템플릿에는 project_id가 필요합니다.")
    with connect() as conn:
        if payload.scope_kind == "PROJECT":
            require_permission(request, DASHBOARD_EDIT, payload.project_id, conn=conn)
        else:
            require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        result = WorkbenchRepository(conn).create_analysis_template_version(payload.model_dump(), request.state.principal.display_name)
        write_audit_event(request=request, principal=request.state.principal, status_code=201, action="ANALYSIS_TEMPLATE_VERSION_CREATED", detail={"template_id": result["template_id"], "version": result["version"]}, connection=conn)
        return result


@router.get("/workbench/request-types/{request_type_id}/{version}/result-profile")
def get_result_profile(request_type_id: str, version: int, request: Request, project_id: str | None = Query(default=None)) -> dict[str, Any]:
    with connect() as conn:
        if project_id:
            require_permission(request, PROJECT_DATA_VIEW, project_id, conn=conn)
        else:
            require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        repository = WorkbenchRepository(conn)
        profile = repository.resolved_result_profile_for(project_id, request_type_id, version) if project_id else repository.result_profile_for(request_type_id, version)
        return profile or {"request_type_id": request_type_id, "request_type_version": version, "status": "UNCONFIGURED"}


@router.put("/projects/{project_id}/admin/workbench/request-types/{request_type_id}/{version}/result-profile", status_code=201)
def save_project_result_profile(project_id: str, request_type_id: str, version: int, payload: ResultProfileInput, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, DASHBOARD_EDIT, project_id, conn=conn)
        try:
            result = WorkbenchRepository(conn).save_project_result_profile(
                project_id, request_type_id, version, payload.model_dump(), bound_by=request.state.principal.display_name
            )
        except PermissionError as exc:
            raise HTTPException(403, detail={"code": str(exc)}) from exc
        except LookupError as exc:
            raise HTTPException(404, detail={"code": str(exc)}) from exc
        except ValueError as exc:
            raise _bad_request(exc) from exc
        write_audit_event(request=request, principal=request.state.principal, status_code=201, action="PROJECT_RESULT_PROFILE_BOUND", detail={"project_id": project_id, "request_type_id": request_type_id, "request_type_version": version, "template_id": result["template_id"], "template_version": result["template_version"]}, connection=conn)
        return result


@router.get("/workbench/requests/{request_id}/result-layout")
def get_request_result_layout(
    request_id: str,
    request: Request,
    load_case_id: str | None = Query(default=None),
) -> dict[str, Any]:
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        context = repository.request_context(request_id)
        if not context:
            raise HTTPException(404, detail={"code": "REQUEST_NOT_FOUND", "request_id": request_id})
        require_permission(request, PROJECT_DATA_VIEW, context["project_id"], conn=conn)
        if load_case_id and not repository.load_case_belongs_to_request(request_id, load_case_id):
            raise HTTPException(404, detail={"code": "LOAD_CASE_NOT_FOUND", "load_case_id": load_case_id})
        snapshot = repository.result_layout_snapshot(request_id)
        if not snapshot:
            return {"request_id": request_id, "status": "UNCONFIGURED", "message": "이 의뢰에는 결과 화면 구성이 지정되지 않았습니다."}
        snapshot["bindings"] = repository.result_layout_bindings(request_id, load_case_id)
        # No load-case/result heuristic is allowed here. Only a deliberately
        # migrated LEGACY_ASSIGNED snapshot may retain its old domain route.
        if snapshot.get("snapshot_reason") == "LEGACY_ASSIGNED":
            snapshot["compatibility"] = {"route_kind": "DOMAIN", "renderer": "LEGACY_DOMAIN"}
        return snapshot


@router.post(
    "/workbench/requests/{request_id}/result-layout/materialize",
    response_model=DashboardDefinition,
    status_code=201,
)
def materialize_request_result_layout(
    request_id: str,
    payload: ResultLayoutMaterializeInput,
    request: Request,
) -> dict[str, Any]:
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        context = repository.request_context(request_id)
        if not context:
            raise HTTPException(404, detail={"code": "REQUEST_NOT_FOUND", "request_id": request_id})
        require_permission(request, DASHBOARD_EDIT, context["project_id"], conn=conn)
        if not repository.load_case_belongs_to_request(request_id, payload.load_case_id):
            raise HTTPException(404, detail={"code": "LOAD_CASE_NOT_FOUND", "load_case_id": payload.load_case_id})
        repository.begin_transaction()
        try:
            dashboard = materialize_request_result_dashboard(
                conn,
                request_id=request_id,
                load_case_id=payload.load_case_id,
                page_id=payload.page_id,
                created_by=request.state.principal.display_name,
            )
            repository.commit_transaction()
            return dashboard
        except LookupError as exc:
            repository.rollback_transaction()
            raise HTTPException(404, detail={"code": str(exc), "request_id": request_id}) from exc
        except ValueError as exc:
            repository.rollback_transaction()
            raise _bad_request(exc) from exc


@router.post("/admin/workbench/request-types", status_code=201)
def create_request_type(payload: RequestTypeVersionCreate, request: Request) -> dict[str, Any]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
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
        data = payload.model_dump()
        # IDs are server-owned.  Ignore the optional legacy field on create.
        data["id"] = None
        try:
            return _create_request_type_version_atomically(conn, repository, data)
        except (LookupError, ValueError) as exc:
            raise _bad_request(exc) from exc
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise HTTPException(409, "작업 유형 ID 생성이 충돌했습니다. 다시 시도해 주세요.") from exc
            raise


@router.put("/admin/workbench/request-types/{request_type_id}", status_code=201)
def update_request_type(request_type_id: str, payload: RequestTypeVersionCreate, request: Request) -> dict[str, Any]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    try:
        _topological_nodes(payload.default_workflow.nodes)
    except WorkbenchValidationError as exc:
        raise _bad_request(exc) from exc
    allowed = {(item.id, item.version) for item in payload.allowed_task_types}
    for node in payload.default_workflow.nodes:
        if (node.task_type_id, node.task_type_version) not in allowed:
            raise HTTPException(400, f"기본 Workflow에 허용되지 않은 Task가 있습니다: {node.task_type_id}")
    data = payload.model_dump()
    data["id"] = request_type_id
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        missing = [f"{item.id} v{item.version}" for item in payload.allowed_task_types if not (task := repository.get_task_type(item.id, item.version)) or not task["is_active"]]
        if missing:
            raise HTTPException(400, f"Task Type 버전을 찾을 수 없습니다: {', '.join(missing)}")
        if not repository.request_type_exists(request_type_id):
            raise HTTPException(404, "작업 유형을 찾을 수 없습니다.")
        try:
            return _create_request_type_version_atomically(conn, repository, data)
        except (LookupError, ValueError) as exc:
            raise _bad_request(exc) from exc


@router.delete("/admin/workbench/request-types/{request_type_id}")
def deactivate_request_type(request_type_id: str, request: Request) -> dict[str, str]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    with connect() as conn:
        if not WorkbenchRepository(conn).deactivate_request_type(request_type_id):
            raise HTTPException(404, "작업 유형을 찾을 수 없습니다.")
    return {"id": request_type_id, "status": "INACTIVE"}


@router.get("/workbench/batch-profiles")
def list_batch_profiles(request: Request, include_inactive: bool = Query(default=False)) -> list[dict[str, Any]]:
    with connect() as conn:
        profiles = WorkbenchRepository(conn).list_batch_profiles(include_inactive=include_inactive if _is_admin(request) else False)
        return [_sanitize_batch_profile(profile, request) for profile in profiles]


@router.get("/admin/workbench/batch-profiles/{profile_id}/versions")
def list_batch_profile_versions(profile_id: str, request: Request) -> list[dict[str, Any]]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    with connect() as conn:
        return WorkbenchRepository(conn).list_batch_profile_versions(profile_id)


def _save_batch_profile(payload: BatchProfileInput, request: Request, profile_id: str | None = None) -> dict[str, Any]:
    principal = getattr(request.state, "principal", None)
    if principal:
        payload = payload.model_copy(update={"updated_by": principal.display_name})
    data = payload.model_dump()
    if profile_id is not None:
        if payload.id is not None and profile_id != payload.id:
            raise HTTPException(422, "경로의 프로필 ID와 본문의 ID가 일치해야 합니다.")
        data["id"] = profile_id
    else:
        # IDs are server-owned.  Ignore the optional legacy field on create.
        data["id"] = None
    try:
        # Validate path/template data before entering the transaction.
        validate_profile_definition(data)
    except BatchPreflightError as exc:
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        if profile_id is not None and not repository.get_batch_profile(profile_id):
            raise HTTPException(404, "배치 실행 정의를 찾을 수 없습니다.")
        repository.begin_transaction()
        try:
            saved = repository.upsert_batch_profile(data)
            repository.commit_transaction()
            return saved
        except ValueError as exc:
            repository.rollback_transaction()
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            repository.rollback_transaction()
            message = str(exc).lower()
            if "unique" in message or "duplicate" in message or "constraint" in message:
                raise HTTPException(409, "동일한 작업 유형 버전에 이미 배치 실행 정의가 있습니다.") from exc
            raise


@router.post("/admin/workbench/batch-profiles", status_code=201)
def create_batch_profile(payload: BatchProfileInput, request: Request) -> dict[str, Any]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    return _save_batch_profile(payload, request)


@router.put("/admin/workbench/batch-profiles/{profile_id}")
def save_batch_profile(profile_id: str, payload: BatchProfileInput, request: Request) -> dict[str, Any]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    return _save_batch_profile(payload, request, profile_id)


@router.delete("/admin/workbench/batch-profiles/{profile_id}")
def deactivate_batch_profile(profile_id: str, request: Request) -> dict[str, str]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    with connect() as conn:
        if not WorkbenchRepository(conn).deactivate_batch_profile(profile_id):
            raise HTTPException(404, "배치 실행 정의를 찾을 수 없습니다.")
    return {"id": profile_id, "status": "INACTIVE"}


@router.get("/workbench/work-items/{item_id}")
def get_work_item_detail(item_id: str, request: Request) -> dict[str, Any]:
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        item = repository.work_item(item_id)
        if not item:
            raise HTTPException(404, detail={"code": "WORK_ITEM_NOT_FOUND", "item_id": item_id})
        task_type = repository.get_task_type(item["task_type_id"], int(item["task_type_version"]))
        profile = repository.get_batch_profile_for_task(item["task_type_id"], int(item["task_type_version"]))
        sanitized_profile = _sanitize_batch_profile(profile, request) if profile else None
        return {
            "work_item": item,
            "task_type": task_type,
            "execution_definition": sanitized_profile,
            "compatible_profiles": [sanitized_profile] if sanitized_profile else [],
            "attempts": [_sanitize_batch_attempt(attempt, request) for attempt in repository.list_batch_attempts(item_id)],
        }


@router.get("/workbench/work-items/{item_id}/batch-attempts")
def list_batch_attempts(item_id: str, request: Request) -> list[dict[str, Any]]:
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM request_work_items WHERE id=?", [item_id]).fetchone():
            raise HTTPException(404, detail={"code": "WORK_ITEM_NOT_FOUND", "item_id": item_id})
        return [_sanitize_batch_attempt(attempt, request) for attempt in WorkbenchRepository(conn).list_batch_attempts(item_id)]


@router.post("/workbench/work-items/{item_id}/batch-dispatch", status_code=201)
def dispatch_batch_work_item(item_id: str, payload: BatchDispatchCreate, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    with connect() as conn:
        command_port = SQLWorkbenchBatchDispatchCommand(conn)
        try:
            run = dispatch_workbench_batch(
                command_port,
                item_id,
                BatchDispatchCommand(
                    batch_profile_id=payload.batch_profile_id,
                    idempotency_key=payload.idempotency_key,
                    created_by=principal.display_name,
                ),
                authorize=lambda: require_assigned_work_item(request, item_id, conn=conn),
                audit=lambda: _audit_execution_override(request, conn, "batch_dispatch"),
            )
        except WorkItemNotFoundError as exc:
            raise HTTPException(404, detail={"code": "WORK_ITEM_NOT_FOUND", "item_id": exc.item_id}) from exc
        except BatchDispatchError as exc:
            status_code = 404 if exc.code == "BATCH_PROFILE_NOT_CONFIGURED" else 409
            detail = {"code": exc.code, **exc.detail}
            if exc.code == "BATCH_ATTEMPT_ALREADY_REJECTED" and isinstance(detail.get("attempt"), dict):
                detail["attempt"] = _sanitize_batch_attempt(detail["attempt"], request)
            raise HTTPException(status_code, detail=detail) from exc
        except BatchDemoRunValidationError as exc:
            raise _bad_request(exc) from exc
        return _sanitize_demo_run(run.run, request)


@router.get("/workbench/requests/{request_id}/request-type")
def resolve_request_type(request_id: str) -> dict[str, Any]:
    with connect() as conn:
        try:
            return resolve_workbench_request_type(SQLWorkbenchRequestTypeResolutionQuery(conn), request_id)
        except LookupError as exc:
            raise HTTPException(404, "해석 의뢰를 찾을 수 없습니다.") from exc


@router.put("/workbench/requests/{request_id}/request-type")
def assign_request_type(request_id: str, payload: RequestTypeAssignmentInput, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    source = "ADMIN" if principal.is_global_admin else "USER"
    with connect() as conn:
        require_resource_permission(request, REQUEST_EDIT, "request", request_id, conn=conn)
        command = RequestTypeAssignmentCommand(
            request_type_id=payload.request_type_id,
            request_type_version=payload.request_type_version,
            source=source,
            decided_by=principal.display_name,
        )
        try:
            return assign_workbench_request_type(
                SQLWorkbenchRequestTypeAssignmentCommand(conn),
                request_id,
                command,
            )
        except RequestWorkPlanImmutableError as exc:
            raise HTTPException(
                409,
                detail={"code": "WORK_PLAN_IMMUTABLE", "request_id": request_id},
            ) from exc
        except RequestTypeAssignmentLockedError as exc:
            raise HTTPException(409, "관리자가 고정한 의뢰 유형은 관리자만 변경할 수 있습니다.") from exc
        except RequestTypeAssignmentTargetNotFoundError as exc:
            raise HTTPException(404, "해석 의뢰 또는 Request Type 버전을 찾을 수 없습니다.") from exc


@router.post("/workbench/demo-runs", status_code=201)
def create_demo_run(payload: DemoRunCreate, request: Request) -> dict[str, Any]:
    principal = getattr(request.state, "principal", None)
    if principal:
        payload = payload.model_copy(update={"created_by": principal.display_name})
    with connect() as conn:
        if payload.request_id:
            require_resource_permission(request, WORKFLOW_EDIT, "request", payload.request_id, conn=conn)
        else:
            # Standalone demo runs do not mutate a project resource. Keep the
            # existing API while requiring workflow-edit capability somewhere.
            require_any_project_permission(request, WORKFLOW_EDIT, conn=conn)
        try:
            return DemoRunnerService(WorkbenchRepository(conn)).create_run(payload)
        except WorkbenchValidationError as exc:
            raise _bad_request(exc) from exc


@router.get("/workbench/demo-runs")
def list_demo_runs(request: Request, request_id: str | None = Query(default=None, min_length=3, max_length=100)) -> list[dict[str, Any]]:
    with connect() as conn:
        return [_sanitize_demo_run(run, request) for run in DemoRunnerService(WorkbenchRepository(conn)).list_runs(request_id)]


@router.get("/workbench/demo-runs/{run_id}")
def get_demo_run(run_id: str, request: Request) -> dict[str, Any]:
    with connect() as conn:
        item = DemoRunnerService(WorkbenchRepository(conn)).get_run(run_id)
    if not item:
        raise HTTPException(404, "데모 실행을 찾을 수 없습니다.")
    return _sanitize_demo_run(item, request)


@router.get("/workbench/requests/{request_id}/work-plan")
def get_request_work_plan(request_id: str) -> dict[str, Any]:
    with connect() as conn:
        result = get_workbench_request_work_plan(SQLWorkbenchRequestWorkPlanQuery(conn), request_id)
        if result["status"] == "REQUEST_NOT_FOUND":
            raise HTTPException(
                404,
                detail={"code": "REQUEST_NOT_FOUND", "request_id": request_id},
            )
        if result["status"] == "WORK_PLAN_NOT_FOUND":
            raise HTTPException(
                404,
                detail={"code": "WORK_PLAN_NOT_FOUND", "request_id": request_id},
            )
        summary = result["summary"]
        assert summary is not None
        return {"request_id": request_id, **summary}


@router.patch("/workbench/work-items/{item_id}/assignee")
def reassign_work_item(item_id: str, payload: WorkItemAssigneeUpdate, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    with connect() as conn:
        command_port = SQLWorkbenchWorkItemReassignmentCommand(conn)
        command_port.begin_transaction()
        try:
            updated = reassign_workbench_work_item(
                command_port,
                item_id,
                WorkItemReassignmentCommand(owner_user_id=payload.owner_user_id),
                authorize=lambda _item: require_resource_permission(
                    request,
                    WORKFLOW_EDIT,
                    "work_item",
                    item_id,
                    conn=conn,
                ),
                audit=lambda item, assignee: write_audit_event(
                    request=request,
                    principal=principal,
                    status_code=200,
                    action="WORK_ITEM_ASSIGNEE_CHANGED",
                    detail={
                        "project_id": item.project_id,
                        "request_id": item.request_id,
                        "work_item_id": item_id,
                        "old_owner_user_id": item.owner_user_id,
                        "new_owner_user_id": assignee.user_id,
                    },
                    connection=conn,
                ),
            )
            command_port.commit_transaction()
            return updated
        except WorkItemNotFoundError as exc:
            command_port.rollback_transaction()
            raise HTTPException(404, detail={"code": "WORK_ITEM_NOT_FOUND", "item_id": exc.item_id}) from exc
        except WorkItemReassignmentFinalError as exc:
            command_port.rollback_transaction()
            raise HTTPException(409, detail={"code": "WORK_ITEM_REASSIGNMENT_FINAL", "item_id": exc.item_id}) from exc
        except WorkItemAssigneeMembershipRequiredError as exc:
            command_port.rollback_transaction()
            raise HTTPException(
                422,
                detail={
                    "code": "ASSIGNEE_PROJECT_MEMBERSHIP_REQUIRED",
                    "project_id": exc.project_id,
                    "owner_user_id": exc.owner_user_id,
                },
            ) from exc
        except WorkItemAssigneeAccountNotActiveError as exc:
            command_port.rollback_transaction()
            raise HTTPException(
                422,
                detail={
                    "code": "ASSIGNEE_ACCOUNT_NOT_ACTIVE",
                    "project_id": exc.project_id,
                    "owner_user_id": exc.owner_user_id,
                },
            ) from exc
        except Exception:
            command_port.rollback_transaction()
            raise


@router.post("/workbench/work-items/{item_id}/start")
def start_work_item(item_id: str, payload: WorkItemStart, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    started_by = principal.display_name
    with connect() as conn:
        command_port = SQLWorkbenchWorkItemStartCommand(conn)
        command_port.begin_transaction()
        try:
            result = start_workbench_work_item(
                command_port,
                item_id,
                WorkItemStartCommand(started_by=started_by),
                authorize=lambda: require_assigned_work_item(request, item_id, conn=conn),
                audit=lambda: _audit_execution_override(request, conn, "start"),
            )
            command_port.commit_transaction()
            return {"request_id": result.request_id, **result.summary}
        except WorkItemNotFoundError as exc:
            command_port.rollback_transaction()
            raise HTTPException(404, detail={"code": "WORK_ITEM_NOT_FOUND", "item_id": exc.item_id}) from exc
        except WorkItemNotReadyError as exc:
            command_port.rollback_transaction()
            raise HTTPException(
                409,
                detail={
                    "code": "WORK_ITEM_NOT_READY",
                    "item_id": exc.item_id,
                    "current_item_id": exc.current_item_id,
                },
            ) from exc
        except WorkItemPrerequisiteIncompleteError as exc:
            command_port.rollback_transaction()
            raise HTTPException(
                409,
                detail={"code": "WORK_ITEM_PREREQUISITE_INCOMPLETE", "item_id": exc.item_id},
            ) from exc
        except HTTPException:
            command_port.rollback_transaction()
            raise
        except Exception:
            command_port.rollback_transaction()
            raise


@router.patch("/workbench/work-items/{item_id}/progress")
def update_work_item_progress(item_id: str, payload: WorkItemProgress, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    updated_by = principal.display_name
    with connect() as conn:
        command_port = SQLWorkbenchWorkItemProgressCommand(conn)
        command_port.begin_transaction()
        try:
            result = update_workbench_work_item_progress(
                command_port,
                item_id,
                WorkItemProgressCommand(
                    progress=payload.progress,
                    updated_by=updated_by,
                ),
                authorize=lambda: require_assigned_work_item(request, item_id, conn=conn),
                audit=lambda: _audit_execution_override(request, conn, "progress"),
            )
            command_port.commit_transaction()
            return {"request_id": result.request_id, **result.summary}
        except WorkItemNotFoundError as exc:
            command_port.rollback_transaction()
            raise HTTPException(404, detail={"code": "WORK_ITEM_NOT_FOUND", "item_id": exc.item_id}) from exc
        except WorkItemNotInProgressError as exc:
            command_port.rollback_transaction()
            raise HTTPException(409, detail={"code": "WORK_ITEM_NOT_IN_PROGRESS", "item_id": exc.item_id}) from exc
        except WorkItemProgressNotMonotonicError as exc:
            command_port.rollback_transaction()
            raise HTTPException(
                409,
                detail={
                    "code": "WORK_ITEM_PROGRESS_NOT_MONOTONIC",
                    "current": exc.current,
                    "requested": exc.requested,
                },
            ) from exc
        except HTTPException:
            command_port.rollback_transaction()
            raise
        except Exception:
            command_port.rollback_transaction()
            raise


@router.post("/workbench/work-items/{item_id}/complete")
def complete_work_item(item_id: str, payload: WorkItemComplete, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    completed_by = principal.display_name
    with connect() as conn:
        command_port = SQLWorkbenchWorkItemCompleteCommand(conn)
        command_port.begin_transaction()
        try:
            result = complete_workbench_work_item(
                command_port,
                item_id,
                WorkItemCompleteCommand(completed_by=completed_by, demo_run_id=payload.demo_run_id),
                authorize=lambda: require_assigned_work_item(request, item_id, conn=conn),
                audit=lambda: _audit_execution_override(request, conn, "complete"),
            )
            command_port.commit_transaction()
            return {"request_id": result.request_id, **result.summary}
        except WorkItemNotFoundError as exc:
            command_port.rollback_transaction()
            raise HTTPException(404, detail={"code": "WORK_ITEM_NOT_FOUND", "item_id": exc.item_id}) from exc
        except WorkItemNotStartedError as exc:
            command_port.rollback_transaction()
            raise HTTPException(
                409,
                detail={"code": "WORK_ITEM_NOT_STARTED", "item_id": exc.item_id, "current_item_id": exc.current_item_id},
            ) from exc
        except WorkItemNotCurrentError as exc:
            command_port.rollback_transaction()
            raise HTTPException(
                409,
                detail={"code": "WORK_ITEM_NOT_CURRENT", "item_id": exc.item_id, "current_item_id": exc.current_item_id},
            ) from exc
        except WorkItemPrerequisiteIncompleteError as exc:
            command_port.rollback_transaction()
            raise HTTPException(409, detail={"code": "WORK_ITEM_PREREQUISITE_INCOMPLETE", "item_id": exc.item_id}) from exc
        except DemoRunInvalidError as exc:
            command_port.rollback_transaction()
            raise HTTPException(
                409,
                detail={"code": "DEMO_RUN_INVALID", "item_id": exc.item_id, "demo_run_id": exc.demo_run_id},
            ) from exc
        except HTTPException:
            command_port.rollback_transaction()
            raise
        except Exception:
            command_port.rollback_transaction()
            raise
