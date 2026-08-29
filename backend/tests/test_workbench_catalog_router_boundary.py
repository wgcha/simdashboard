from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.domains.workbench.models import (
    DemoRunInvalidError,
    RequestTypeAssignmentLockedError,
    RequestTypeAssignmentTargetNotFoundError,
    WorkItemAssigneeAccountNotActiveError,
    WorkItemAssigneeMembershipRequiredError,
    WorkItemNotFoundError,
    WorkItemNotInProgressError,
    WorkItemNotCurrentError,
    WorkItemNotReadyError,
    WorkItemNotStartedError,
    WorkItemPrerequisiteIncompleteError,
    WorkItemProgressNotMonotonicError,
    WorkItemReassignmentFinalError,
    WorkItemLifecycleResult,
)
from app.routers import workbench
from scripts.check_openapi_contract import check_contract


def test_workbench_catalog_routes_keep_their_contract_order_and_operation_ids() -> None:
    routes = [
        route
        for route in app.routes
        if getattr(route, "path", "")
        in {
            "/api/workbench/task-types",
            "/api/workbench/request-types",
            "/api/workbench/requests/{request_id}/request-type",
            "/api/workbench/requests/{request_id}/work-plan",
        }
    ]

    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/workbench/task-types", ("GET",)),
        ("/api/workbench/request-types", ("GET",)),
        ("/api/workbench/requests/{request_id}/request-type", ("GET",)),
        ("/api/workbench/requests/{request_id}/request-type", ("PUT",)),
        ("/api/workbench/requests/{request_id}/work-plan", ("GET",)),
    ]
    assert all(route.endpoint.__module__ == workbench.__name__ for route in routes)
    paths = app.openapi()["paths"]
    assert paths["/api/workbench/task-types"]["get"]["operationId"] == "list_task_types_api_workbench_task_types_get"
    assert paths["/api/workbench/request-types"]["get"]["operationId"] == "list_request_types_api_workbench_request_types_get"
    assert (
        paths["/api/workbench/requests/{request_id}/request-type"]["get"]["operationId"]
        == "resolve_request_type_api_workbench_requests__request_id__request_type_get"
    )
    assert (
        paths["/api/workbench/requests/{request_id}/request-type"]["put"]["operationId"]
        == "assign_request_type_api_workbench_requests__request_id__request_type_put"
    )
    assert (
        paths["/api/workbench/requests/{request_id}/work-plan"]["get"]["operationId"]
        == "get_request_work_plan_api_workbench_requests__request_id__work_plan_get"
    )


def test_workbench_boundary_handlers_delegate_to_use_cases() -> None:
    for handler in (
        workbench.list_task_types,
        workbench.list_request_types,
        workbench.resolve_request_type,
        workbench.assign_request_type,
        workbench.get_request_work_plan,
    ):
        tree = ast.parse(inspect.getsource(handler))
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "execute"
            for node in ast.walk(tree)
        )
    assert "WorkbenchRepository" not in inspect.getsource(workbench.list_task_types)
    assert "WorkbenchRepository" not in inspect.getsource(workbench.list_request_types)
    assert "WorkbenchRepository" not in inspect.getsource(workbench.resolve_request_type)
    assert "WorkbenchRepository" not in inspect.getsource(workbench.assign_request_type)
    assert "WorkbenchRepository" not in inspect.getsource(workbench.get_request_work_plan)


def test_work_item_progress_boundary_delegates_without_direct_sql_or_repository() -> None:
    tree = ast.parse(inspect.getsource(workbench.update_work_item_progress))
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "execute"
        for node in ast.walk(tree)
    )
    assert "WorkbenchRepository" not in inspect.getsource(workbench.update_work_item_progress)
    assert (
        app.openapi()["paths"]["/api/workbench/work-items/{item_id}/progress"]["patch"]["operationId"]
        == "update_work_item_progress_api_workbench_work_items__item_id__progress_patch"
    )


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (WorkItemNotFoundError("item-1"), 404, {"code": "WORK_ITEM_NOT_FOUND", "item_id": "item-1"}),
        (WorkItemNotInProgressError("item-1"), 409, {"code": "WORK_ITEM_NOT_IN_PROGRESS", "item_id": "item-1"}),
        (
            WorkItemProgressNotMonotonicError(current=40, requested=30),
            409,
            {"code": "WORK_ITEM_PROGRESS_NOT_MONOTONIC", "current": 40, "requested": 30},
        ),
    ],
)
def test_work_item_progress_router_preserves_error_mapping_and_rollback(
    monkeypatch,
    error: Exception,
    expected_status: int,
    expected_detail: object,
) -> None:
    events: list[str] = []

    class _Adapter:
        def __init__(self, _connection: object) -> None:
            pass

        def begin_transaction(self) -> None:
            events.append("begin")

        def commit_transaction(self) -> None:
            events.append("commit")

        def rollback_transaction(self) -> None:
            events.append("rollback")

    def _raise(*_args: object, **_kwargs: object) -> WorkItemLifecycleResult:
        raise error

    monkeypatch.setattr(workbench, "SQLWorkbenchWorkItemProgressCommand", _Adapter)
    monkeypatch.setattr(workbench, "update_workbench_work_item_progress", _raise)

    with TestClient(app) as client:
        response = client.patch(
            "/api/workbench/work-items/item-1/progress",
            json={"progress": 30, "updated_by": "payload actor"},
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert events == ["begin", "rollback"]


def test_work_item_progress_router_uses_the_principal_actor_not_the_payload_actor(monkeypatch) -> None:
    events: list[str] = []

    class _Adapter:
        def __init__(self, _connection: object) -> None:
            pass

        def begin_transaction(self) -> None:
            events.append("begin")

        def commit_transaction(self) -> None:
            events.append("commit")

        def rollback_transaction(self) -> None:
            events.append("rollback")

    def _success(_port: object, item_id: str, command: object, **_kwargs: object) -> WorkItemLifecycleResult:
        assert item_id == "item-1"
        assert getattr(command, "updated_by") == "로컬 관리자"
        assert getattr(command, "updated_by") != "payload actor"
        return WorkItemLifecycleResult(
            request_id="request-1",
            summary={"status": "IN_PROGRESS", "progress": 42},  # type: ignore[typeddict-item]
            changed=True,
        )

    monkeypatch.setattr(workbench, "SQLWorkbenchWorkItemProgressCommand", _Adapter)
    monkeypatch.setattr(workbench, "update_workbench_work_item_progress", _success)

    with TestClient(app) as client:
        response = client.patch(
            "/api/workbench/work-items/item-1/progress",
            json={"progress": 42, "updated_by": "payload actor"},
        )

    assert response.status_code == 200
    assert response.json() == {"request_id": "request-1", "status": "IN_PROGRESS", "progress": 42}
    assert events == ["begin", "commit"]


def test_work_item_start_boundary_delegates_without_direct_sql_or_repository() -> None:
    tree = ast.parse(inspect.getsource(workbench.start_work_item))
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "execute"
        for node in ast.walk(tree)
    )
    assert "WorkbenchRepository" not in inspect.getsource(workbench.start_work_item)
    assert (
        app.openapi()["paths"]["/api/workbench/work-items/{item_id}/start"]["post"]["operationId"]
        == "start_work_item_api_workbench_work_items__item_id__start_post"
    )


def test_work_item_reassignment_boundary_delegates_without_direct_sql_or_repository() -> None:
    tree = ast.parse(inspect.getsource(workbench.reassign_work_item))
    assert not any(isinstance(node, ast.Attribute) and node.attr == "execute" for node in ast.walk(tree))
    assert "WorkbenchRepository" not in inspect.getsource(workbench.reassign_work_item)
    assert (
        app.openapi()["paths"]["/api/workbench/work-items/{item_id}/assignee"]["patch"]["operationId"]
        == "reassign_work_item_api_workbench_work_items__item_id__assignee_patch"
    )


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (WorkItemNotFoundError("item-1"), 404, {"code": "WORK_ITEM_NOT_FOUND", "item_id": "item-1"}),
        (WorkItemReassignmentFinalError("item-1"), 409, {"code": "WORK_ITEM_REASSIGNMENT_FINAL", "item_id": "item-1"}),
        (
            WorkItemAssigneeMembershipRequiredError(project_id="project-1", owner_user_id="owner-new"),
            422,
            {"code": "ASSIGNEE_PROJECT_MEMBERSHIP_REQUIRED", "project_id": "project-1", "owner_user_id": "owner-new"},
        ),
        (
            WorkItemAssigneeAccountNotActiveError(project_id="project-1", owner_user_id="owner-new"),
            422,
            {"code": "ASSIGNEE_ACCOUNT_NOT_ACTIVE", "project_id": "project-1", "owner_user_id": "owner-new"},
        ),
    ],
)
def test_work_item_reassignment_router_preserves_error_mapping_and_single_rollback(
    monkeypatch,
    error: Exception,
    expected_status: int,
    expected_detail: object,
) -> None:
    events: list[str] = []

    class _Adapter:
        def __init__(self, _connection: object) -> None:
            pass

        def begin_transaction(self) -> None:
            events.append("begin")

        def commit_transaction(self) -> None:
            events.append("commit")

        def rollback_transaction(self) -> None:
            events.append("rollback")

    def _raise(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise error

    monkeypatch.setattr(workbench, "SQLWorkbenchWorkItemReassignmentCommand", _Adapter)
    monkeypatch.setattr(workbench, "reassign_workbench_work_item", _raise)

    with TestClient(app) as client:
        response = client.patch(
            "/api/workbench/work-items/item-1/assignee",
            json={"owner_user_id": "owner-new"},
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert events == ["begin", "rollback"]


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (WorkItemNotFoundError("item-1"), 404, {"code": "WORK_ITEM_NOT_FOUND", "item_id": "item-1"}),
        (
            WorkItemNotReadyError(item_id="item-1", current_item_id="current-item"),
            409,
            {"code": "WORK_ITEM_NOT_READY", "item_id": "item-1", "current_item_id": "current-item"},
        ),
        (
            WorkItemPrerequisiteIncompleteError("item-1"),
            409,
            {"code": "WORK_ITEM_PREREQUISITE_INCOMPLETE", "item_id": "item-1"},
        ),
    ],
)
def test_work_item_start_router_preserves_error_mapping_and_rollback(
    monkeypatch,
    error: Exception,
    expected_status: int,
    expected_detail: object,
) -> None:
    events: list[str] = []

    class _Adapter:
        def __init__(self, _connection: object) -> None:
            pass

        def begin_transaction(self) -> None:
            events.append("begin")

        def commit_transaction(self) -> None:
            events.append("commit")

        def rollback_transaction(self) -> None:
            events.append("rollback")

    def _raise(*_args: object, **_kwargs: object) -> WorkItemLifecycleResult:
        raise error

    monkeypatch.setattr(workbench, "SQLWorkbenchWorkItemStartCommand", _Adapter)
    monkeypatch.setattr(workbench, "start_workbench_work_item", _raise)

    with TestClient(app) as client:
        response = client.post(
            "/api/workbench/work-items/item-1/start",
            json={"started_by": "payload actor"},
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert events == ["begin", "rollback"]


def test_work_item_start_router_uses_the_principal_actor_not_the_payload_actor(monkeypatch) -> None:
    events: list[str] = []

    class _Adapter:
        def __init__(self, _connection: object) -> None:
            pass

        def begin_transaction(self) -> None:
            events.append("begin")

        def commit_transaction(self) -> None:
            events.append("commit")

        def rollback_transaction(self) -> None:
            events.append("rollback")

    def _success(_port: object, item_id: str, command: object, **_kwargs: object) -> WorkItemLifecycleResult:
        assert item_id == "item-1"
        assert getattr(command, "started_by") == "로컬 관리자"
        assert getattr(command, "started_by") != "payload actor"
        return WorkItemLifecycleResult(
            request_id="request-1",
            summary={"status": "IN_PROGRESS", "progress": 42},  # type: ignore[typeddict-item]
            changed=True,
        )

    monkeypatch.setattr(workbench, "SQLWorkbenchWorkItemStartCommand", _Adapter)
    monkeypatch.setattr(workbench, "start_workbench_work_item", _success)

    with TestClient(app) as client:
        response = client.post(
            "/api/workbench/work-items/item-1/start",
            json={"started_by": "payload actor"},
        )

    assert response.status_code == 200
    assert response.json() == {"request_id": "request-1", "status": "IN_PROGRESS", "progress": 42}
    assert events == ["begin", "commit"]


def test_work_item_complete_boundary_delegates_without_direct_sql_or_repository() -> None:
    tree = ast.parse(inspect.getsource(workbench.complete_work_item))
    assert not any(isinstance(node, ast.Attribute) and node.attr == "execute" for node in ast.walk(tree))
    assert "WorkbenchRepository" not in inspect.getsource(workbench.complete_work_item)
    assert (
        app.openapi()["paths"]["/api/workbench/work-items/{item_id}/complete"]["post"]["operationId"]
        == "complete_work_item_api_workbench_work_items__item_id__complete_post"
    )


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (WorkItemNotFoundError("item-1"), 404, {"code": "WORK_ITEM_NOT_FOUND", "item_id": "item-1"}),
        (
            WorkItemNotStartedError(item_id="item-1", current_item_id="item-1"),
            409,
            {"code": "WORK_ITEM_NOT_STARTED", "item_id": "item-1", "current_item_id": "item-1"},
        ),
        (
            WorkItemNotCurrentError(item_id="item-1", current_item_id="current-item"),
            409,
            {"code": "WORK_ITEM_NOT_CURRENT", "item_id": "item-1", "current_item_id": "current-item"},
        ),
        (
            WorkItemPrerequisiteIncompleteError("item-1"),
            409,
            {"code": "WORK_ITEM_PREREQUISITE_INCOMPLETE", "item_id": "item-1"},
        ),
        (
            DemoRunInvalidError(item_id="item-1", demo_run_id="run-1"),
            409,
            {"code": "DEMO_RUN_INVALID", "item_id": "item-1", "demo_run_id": "run-1"},
        ),
    ],
)
def test_work_item_complete_router_preserves_error_mapping_and_rollback(
    monkeypatch,
    error: Exception,
    expected_status: int,
    expected_detail: object,
) -> None:
    events: list[str] = []

    class _Adapter:
        def __init__(self, _connection: object) -> None:
            pass

        def begin_transaction(self) -> None:
            events.append("begin")

        def commit_transaction(self) -> None:
            events.append("commit")

        def rollback_transaction(self) -> None:
            events.append("rollback")

    def _raise(*_args: object, **_kwargs: object) -> WorkItemLifecycleResult:
        raise error

    monkeypatch.setattr(workbench, "SQLWorkbenchWorkItemCompleteCommand", _Adapter)
    monkeypatch.setattr(workbench, "complete_workbench_work_item", _raise)

    with TestClient(app) as client:
        response = client.post(
            "/api/workbench/work-items/item-1/complete",
            json={"completed_by": "payload actor", "demo_run_id": "run-1"},
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert events == ["begin", "rollback"]


def test_work_item_complete_router_uses_the_principal_actor_not_the_payload_actor(monkeypatch) -> None:
    events: list[str] = []

    class _Adapter:
        def __init__(self, _connection: object) -> None:
            pass

        def begin_transaction(self) -> None:
            events.append("begin")

        def commit_transaction(self) -> None:
            events.append("commit")

        def rollback_transaction(self) -> None:
            events.append("rollback")

    def _success(_port: object, item_id: str, command: object, **_kwargs: object) -> WorkItemLifecycleResult:
        assert item_id == "item-1"
        assert getattr(command, "completed_by") == "로컬 관리자"
        assert getattr(command, "completed_by") != "payload actor"
        assert getattr(command, "demo_run_id") == "run-1"
        return WorkItemLifecycleResult(
            request_id="request-1",
            summary={"status": "COMPLETED", "progress": 100},  # type: ignore[typeddict-item]
            changed=True,
        )

    monkeypatch.setattr(workbench, "SQLWorkbenchWorkItemCompleteCommand", _Adapter)
    monkeypatch.setattr(workbench, "complete_workbench_work_item", _success)

    with TestClient(app) as client:
        response = client.post(
            "/api/workbench/work-items/item-1/complete",
            json={"completed_by": "payload actor", "demo_run_id": "run-1"},
        )

    assert response.status_code == 200
    assert response.json() == {"request_id": "request-1", "status": "COMPLETED", "progress": 100}
    assert events == ["begin", "commit"]


def test_request_type_resolution_keeps_the_existing_missing_request_response_without_authentication() -> None:
    with TestClient(app) as client:
        response = client.get("/api/workbench/requests/request-type-missing-test/request-type")

    assert response.status_code == 404
    assert response.json() == {"detail": "해석 의뢰를 찾을 수 없습니다."}


@pytest.mark.parametrize(
    ("work_plan", "error", "expected_status", "expected_detail"),
    [
        (
            {"id": "immutable-plan"},
            None,
            409,
            {"code": "WORK_PLAN_IMMUTABLE", "request_id": "request-showcase-waiting"},
        ),
        (None, RequestTypeAssignmentLockedError(), 409, "관리자가 고정한 의뢰 유형은 관리자만 변경할 수 있습니다."),
        (None, RequestTypeAssignmentTargetNotFoundError(), 404, "해석 의뢰 또는 Request Type 버전을 찾을 수 없습니다."),
    ],
)
def test_request_type_assignment_keeps_existing_http_error_mappings(
    monkeypatch,
    work_plan: object | None,
    error: Exception | None,
    expected_status: int,
    expected_detail: object,
) -> None:
    calls: list[str] = []

    class _Adapter:
        def has_work_plan(self, request_id: str) -> bool:
            assert request_id == "request-showcase-waiting"
            calls.append("has_work_plan")
            return work_plan is not None

        def assign_request_type(self, request_id: str, _command: object) -> object:
            assert request_id == "request-showcase-waiting"
            calls.append("assign")
            if error is not None:
                raise error
            raise AssertionError("the immutable-plan case must not assign")

    monkeypatch.setattr(workbench, "SQLWorkbenchRequestTypeAssignmentCommand", lambda _connection: _Adapter())
    with TestClient(app) as client:
        response = client.put(
            "/api/workbench/requests/request-showcase-waiting/request-type",
            json={"request_type_id": "design-reliability-validation", "request_type_version": 1},
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert calls == (["has_work_plan"] if work_plan is not None else ["has_work_plan", "assign"])


def test_request_work_plan_keeps_existing_success_and_not_found_responses_without_authentication() -> None:
    request_id = f"request-work-plan-unconfigured-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestClient(app) as client:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_requests
                    (id, project_id, title, status, owner, requested_at, due_at, overall_note)
                VALUES (?, 'project-tv-001', '계획 없는 의뢰', 'READY', '테스트 담당자', ?, ?, '')
                """,
                [request_id, now, now],
            )
        try:
            missing_request = client.get("/api/workbench/requests/request-work-plan-missing-test/work-plan")
            assert missing_request.status_code == 404
            assert missing_request.json() == {
                "detail": {"code": "REQUEST_NOT_FOUND", "request_id": "request-work-plan-missing-test"}
            }

            missing_plan = client.get(f"/api/workbench/requests/{request_id}/work-plan")
            assert missing_plan.status_code == 404
            assert missing_plan.json() == {
                "detail": {"code": "WORK_PLAN_NOT_FOUND", "request_id": request_id}
            }

            success = client.get("/api/workbench/requests/request-drop-001/work-plan")
            assert success.status_code == 200
            assert success.json()["request_id"] == "request-drop-001"
            assert success.json()["work_plan"] is not None
        finally:
            with connect() as conn:
                conn.execute("DELETE FROM analysis_requests WHERE id=?", [request_id])


def test_workbench_catalog_extraction_preserves_the_checked_in_openapi_contract() -> None:
    snapshot = Path(__file__).parents[2] / "frontend" / "openapi.json"
    assert check_contract(snapshot, app.openapi()) == []
