from __future__ import annotations

from datetime import datetime

from app.adapters.persistence import workbench as workbench_persistence
from app.adapters.persistence.workbench import (
    SQLWorkbenchCatalogQuery,
    SQLWorkbenchRequestWorkPlanQuery,
    SQLWorkbenchRequestTypeResolutionQuery,
)
from app.application.workbench.queries import (
    get_workbench_request_work_plan,
    list_workbench_request_types,
    list_workbench_task_types,
    resolve_workbench_request_type,
)
from app.domains.workbench.models import (
    RequestTypeResolution,
    RequestTypeResolutionRead,
    RequestTypeVersionRead,
    TaskTypeVersionRead,
    WorkPlanMonitoringSummaryRead,
)


class _CatalogQuery:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def list_task_types(self, *, all_versions: bool) -> list[TaskTypeVersionRead]:
        self.calls.append(("task", all_versions))
        return [_task_item()]

    def list_request_types(self, *, all_versions: bool) -> list[RequestTypeVersionRead]:
        self.calls.append(("request", all_versions))
        return [_request_item()]


def _task_item() -> TaskTypeVersionRead:
    return {
        "id": "task-1",
        "version": 1,
        "kind": "CAD_PREPARE",
        "display_name": "CAD 준비",
        "description": "test",
        "supports_standalone": True,
        "input_artifact_types": [],
        "output_artifact_types": [],
        "parameter_schema": {},
        "demo_artifact_url": "/assets/demo.svg",
        "is_active": True,
        "created_at": datetime(2026, 1, 1),
    }


def _request_item() -> RequestTypeVersionRead:
    return {
        "id": "request-1",
        "version": 1,
        "display_name": "의뢰",
        "description": "test",
        "allowed_task_types": [{"id": "task-1", "version": 1}],
        "default_workflow": {"nodes": []},
        "match_rules": {"labels": ["SPDM", "부서"]},
        "is_active": True,
        "created_at": datetime(2026, 1, 1),
    }


def test_workbench_catalog_queries_forward_the_existing_all_versions_filter() -> None:
    query = _CatalogQuery()

    assert list_workbench_task_types(query, all_versions=False) == [_task_item()]
    assert list_workbench_request_types(query, all_versions=True) == [_request_item()]
    assert query.calls == [("task", False), ("request", True)]


def test_sql_workbench_catalog_query_uses_the_supplied_connection_and_preserves_decoded_records(monkeypatch) -> None:
    connection = object()
    calls: list[tuple[str, object, bool]] = []

    class _Repository:
        def __init__(self, actual_connection: object) -> None:
            assert actual_connection is connection

        def list_task_types(self, *, all_versions: bool) -> list[dict[str, object]]:
            calls.append(("task", connection, all_versions))
            return [_task_item()]

        def list_request_types(self, *, all_versions: bool) -> list[dict[str, object]]:
            calls.append(("request", connection, all_versions))
            return [_request_item()]

    monkeypatch.setattr(workbench_persistence, "WorkbenchRepository", _Repository)
    query = SQLWorkbenchCatalogQuery(connection)  # type: ignore[arg-type]

    assert query.list_task_types(all_versions=False) == [_task_item()]
    assert query.list_request_types(all_versions=True) == [_request_item()]
    assert calls == [("task", connection, False), ("request", connection, True)]


class _ResolutionQuery:
    def __init__(self, resolution: RequestTypeResolutionRead) -> None:
        self.resolution = resolution
        self.request_ids: list[str] = []

    def request_type_resolution(self, request_id: str) -> RequestTypeResolutionRead:
        self.request_ids.append(request_id)
        return self.resolution


def _resolution(state: RequestTypeResolution) -> RequestTypeResolutionRead:
    request_type = _request_item()
    return {
        "resolution": state,
        "request_id": "request-1",
        "source": "ADMIN" if state == "ASSIGNED" else "RULE" if state != "USER_SELECTION" else None,
        "reason": "test resolution",
        "request_type": request_type if state in {"ASSIGNED", "RECOMMENDED"} else None,
        "candidates": [request_type] if state in {"RECOMMENDED", "REVIEW_REQUIRED"} else [],
        "decided_by": "관리자" if state == "ASSIGNED" else None,
        "decided_at": datetime(2026, 1, 1) if state == "ASSIGNED" else None,
    }


def test_request_type_resolution_query_preserves_all_four_repository_states() -> None:
    states: tuple[RequestTypeResolution, ...] = (
        "ASSIGNED",
        "RECOMMENDED",
        "REVIEW_REQUIRED",
        "USER_SELECTION",
    )
    for state in states:
        expected = _resolution(state)
        query = _ResolutionQuery(expected)

        assert resolve_workbench_request_type(query, "request-1") == expected
        assert query.request_ids == ["request-1"]


def test_sql_request_type_resolution_query_uses_the_supplied_connection_and_maps_nested_reads(monkeypatch) -> None:
    connection = object()
    expected = _resolution("RECOMMENDED")

    class _Repository:
        def __init__(self, actual_connection: object) -> None:
            assert actual_connection is connection

        def request_type_resolution(self, request_id: str) -> dict[str, object]:
            assert request_id == "request-1"
            return expected

    monkeypatch.setattr(workbench_persistence, "WorkbenchRepository", _Repository)

    query = SQLWorkbenchRequestTypeResolutionQuery(connection)  # type: ignore[arg-type]
    assert query.request_type_resolution("request-1") == expected


def _monitoring_summary(*, work_plan: dict[str, object] | None) -> WorkPlanMonitoringSummaryRead:
    return {
        "status": "READY",
        "progress": 0,
        "current_step": "CAD 준비",
        "current_step_id": "item-1",
        "completed_count": 0 if work_plan is not None else None,
        "total_count": 1 if work_plan is not None else None,
        "work_plan": work_plan,
        "steps": [],
        "latest_demo_run": None,
        "request_type_assignment": None,
    }


class _WorkPlanQuery:
    def __init__(self, *, exists: bool, summary: WorkPlanMonitoringSummaryRead) -> None:
        self.exists = exists
        self.summary = summary
        self.events: list[str] = []

    def analysis_request_exists(self, request_id: str) -> bool:
        assert request_id == "request-1"
        self.events.append("exists")
        return self.exists

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead:
        assert request_id == "request-1"
        self.events.append("summary")
        return self.summary


def test_request_work_plan_query_preserves_existence_monitoring_and_not_found_order() -> None:
    missing_request = _WorkPlanQuery(exists=False, summary=_monitoring_summary(work_plan={"id": "plan-1"}))
    assert get_workbench_request_work_plan(missing_request, "request-1") == {
        "status": "REQUEST_NOT_FOUND",
        "summary": None,
    }
    assert missing_request.events == ["exists"]

    missing_plan = _WorkPlanQuery(exists=True, summary=_monitoring_summary(work_plan=None))
    assert get_workbench_request_work_plan(missing_plan, "request-1") == {
        "status": "WORK_PLAN_NOT_FOUND",
        "summary": None,
    }
    assert missing_plan.events == ["exists", "summary"]

    summary = _monitoring_summary(work_plan={"id": "plan-1"})
    found = _WorkPlanQuery(exists=True, summary=summary)
    assert get_workbench_request_work_plan(found, "request-1") == {"status": "FOUND", "summary": summary}
    assert found.events == ["exists", "summary"]


def test_sql_request_work_plan_query_uses_one_connection_and_the_monitoring_projection(monkeypatch) -> None:
    connection = object()
    calls: list[tuple[str, object, str]] = []
    summary = _monitoring_summary(work_plan={"id": "plan-1"})

    class _Repository:
        def __init__(self, actual_connection: object) -> None:
            assert actual_connection is connection

        def analysis_request_exists(self, request_id: str) -> bool:
            calls.append(("exists", connection, request_id))
            return True

    def read_summary(actual_connection: object, request_id: str) -> WorkPlanMonitoringSummaryRead:
        calls.append(("summary", actual_connection, request_id))
        return summary

    monkeypatch.setattr(workbench_persistence, "WorkbenchRepository", _Repository)
    monkeypatch.setattr(workbench_persistence, "request_monitoring_summary", read_summary)

    query = SQLWorkbenchRequestWorkPlanQuery(connection)  # type: ignore[arg-type]
    assert query.analysis_request_exists("request-1") is True
    assert query.request_monitoring_summary("request-1") == summary
    assert calls == [("exists", connection, "request-1"), ("summary", connection, "request-1")]
