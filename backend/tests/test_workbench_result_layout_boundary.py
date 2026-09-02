from __future__ import annotations

import ast
import inspect

import pytest
from fastapi.testclient import TestClient

from app.adapters.persistence import workbench as workbench_persistence
from app.adapters.persistence.workbench import (
    SQLWorkbenchRequestResultLayoutQuery,
    SQLWorkbenchResultLayoutMaterializeCommand,
)
from app.application.workbench.commands import materialize_workbench_request_result_layout
from app.application.workbench.queries import get_workbench_request_result_layout
from app.domains.workbench.models import (
    RequestResultLayoutContext,
    RequestResultLayoutNotFoundError,
    ResultLayoutLoadCaseNotFoundError,
    ResultLayoutMaterializeCommand,
)
from app.main import app
from app.routers import workbench


class _ResultLayoutQueryPort:
    def __init__(self, *, context: RequestResultLayoutContext | None, snapshot: dict[str, object] | None) -> None:
        self.context = context
        self.snapshot = snapshot
        self.events: list[str] = []

    def request_result_layout_context(self, request_id: str) -> RequestResultLayoutContext | None:
        assert request_id == "request-1"
        self.events.append("context")
        return self.context

    def load_case_belongs_to_request(self, request_id: str, load_case_id: str) -> bool:
        assert (request_id, load_case_id) == ("request-1", "case-1")
        self.events.append("ownership")
        return True

    def result_layout_snapshot(self, request_id: str) -> dict[str, object] | None:
        assert request_id == "request-1"
        self.events.append("snapshot")
        return self.snapshot

    def result_layout_bindings(self, request_id: str, load_case_id: str | None) -> dict[str, object]:
        assert (request_id, load_case_id) == ("request-1", "case-1")
        self.events.append("bindings")
        return {"load_cases": [{"id": "case-1"}]}


def test_result_layout_query_preserves_context_authorize_ownership_snapshot_bindings_and_legacy_order() -> None:
    port = _ResultLayoutQueryPort(
        context=RequestResultLayoutContext("request-1", "project-1"),
        snapshot={"request_id": "request-1", "snapshot": {}, "snapshot_reason": "LEGACY_ASSIGNED"},
    )

    result = get_workbench_request_result_layout(
        port,
        "request-1",
        load_case_id="case-1",
        authorize=lambda project_id: (assert_project(project_id, port)),
    )

    assert result["bindings"] == {"load_cases": [{"id": "case-1"}]}
    assert result["compatibility"] == {"route_kind": "DOMAIN", "renderer": "LEGACY_DOMAIN"}
    assert port.events == ["context", "authorize", "ownership", "snapshot", "bindings"]


def assert_project(project_id: str, port: _ResultLayoutQueryPort) -> None:
    assert project_id == "project-1"
    port.events.append("authorize")


def test_result_layout_query_unconfigured_short_circuits_before_bindings() -> None:
    port = _ResultLayoutQueryPort(
        context=RequestResultLayoutContext("request-1", "project-1"),
        snapshot=None,
    )

    result = get_workbench_request_result_layout(
        port,
        "request-1",
        load_case_id="case-1",
        authorize=lambda _project_id: port.events.append("authorize"),
    )

    assert result == {
        "request_id": "request-1",
        "status": "UNCONFIGURED",
        "message": "이 의뢰에는 결과 화면 구성이 지정되지 않았습니다.",
    }
    assert port.events == ["context", "authorize", "ownership", "snapshot"]


def test_result_layout_query_keeps_the_existing_empty_load_case_query_compatibility() -> None:
    port = _ResultLayoutQueryPort(
        context=RequestResultLayoutContext("request-1", "project-1"),
        snapshot=None,
    )

    result = get_workbench_request_result_layout(
        port,
        "request-1",
        load_case_id="",
        authorize=lambda _project_id: port.events.append("authorize"),
    )

    assert result["status"] == "UNCONFIGURED"
    assert port.events == ["context", "authorize", "snapshot"]


class _MaterializePort:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.events: list[str] = []

    def request_result_layout_context(self, request_id: str) -> RequestResultLayoutContext:
        assert request_id == "request-1"
        self.events.append("context")
        return RequestResultLayoutContext(request_id, "project-1")

    def load_case_belongs_to_request(self, request_id: str, load_case_id: str) -> bool:
        assert (request_id, load_case_id) == ("request-1", "case-1")
        self.events.append("ownership")
        return True

    def begin_transaction(self) -> None:
        self.events.append("begin")

    def commit_transaction(self) -> None:
        self.events.append("commit")

    def rollback_transaction(self) -> None:
        self.events.append("rollback")

    def materialize_result_layout(self, request_id: str, command: ResultLayoutMaterializeCommand) -> dict[str, object]:
        assert request_id == "request-1"
        assert command.load_case_id == "case-1"
        self.events.append("materialize")
        if self.error:
            raise self.error
        return {"id": "dashboard-1"}


def _materialize(port: _MaterializePort) -> dict[str, object]:
    def authorize(project_id: str) -> None:
        assert project_id == "project-1"
        port.events.append("authorize")

    return materialize_workbench_request_result_layout(
        port,
        "request-1",
        ResultLayoutMaterializeCommand(load_case_id="case-1", page_id=None, created_by="principal actor"),
        authorize=authorize,
    )


def test_result_layout_materialize_command_preserves_scope_transaction_and_commit_order() -> None:
    port = _MaterializePort()

    assert _materialize(port) == {"id": "dashboard-1"}
    assert port.events == ["context", "authorize", "ownership", "begin", "materialize", "commit"]


@pytest.mark.parametrize("error", [LookupError("RESULT_LAYOUT_PAGE_NOT_FOUND"), ValueError("RESULT_LAYOUT_MATERIALIZATION_CONFLICT")])
def test_result_layout_materialize_command_rolls_back_lookup_and_value_errors(error: Exception) -> None:
    port = _MaterializePort(error=error)

    with pytest.raises(type(error)):
        _materialize(port)

    assert port.events == ["context", "authorize", "ownership", "begin", "materialize", "rollback"]


def test_sql_result_layout_adapters_keep_repository_and_service_on_the_supplied_connection(monkeypatch) -> None:
    connection = object()
    events: list[tuple[object, ...]] = []

    class _Repository:
        def __init__(self, actual_connection: object) -> None:
            assert actual_connection is connection

        def request_context(self, request_id: str) -> dict[str, str]:
            events.append(("context", request_id))
            return {"request_id": request_id, "project_id": "project-1"}

        def load_case_belongs_to_request(self, request_id: str, load_case_id: str) -> bool:
            events.append(("ownership", request_id, load_case_id))
            return True

        def result_layout_snapshot(self, request_id: str) -> dict[str, object]:
            events.append(("snapshot", request_id))
            return {"request_id": request_id, "snapshot": {}}

        def result_layout_bindings(self, request_id: str, load_case_id: str | None) -> dict[str, object]:
            events.append(("bindings", request_id, load_case_id))
            return {"load_cases": []}

        def begin_transaction(self) -> None:
            events.append(("begin",))

        def commit_transaction(self) -> None:
            events.append(("commit",))

        def rollback_transaction(self) -> None:
            events.append(("rollback",))

    monkeypatch.setattr(workbench_persistence, "WorkbenchRepository", _Repository)
    monkeypatch.setattr(
        workbench_persistence,
        "materialize_request_result_dashboard",
        lambda actual_connection, **kwargs: events.append(("materialize", actual_connection, kwargs)) or {"id": "dashboard-1"},
    )

    query = SQLWorkbenchRequestResultLayoutQuery(connection)  # type: ignore[arg-type]
    command = SQLWorkbenchResultLayoutMaterializeCommand(connection)  # type: ignore[arg-type]
    assert query.request_result_layout_context("request-1") == RequestResultLayoutContext("request-1", "project-1")
    assert query.load_case_belongs_to_request("request-1", "case-1") is True
    assert query.result_layout_snapshot("request-1") == {"request_id": "request-1", "snapshot": {}}
    assert query.result_layout_bindings("request-1", "case-1") == {"load_cases": []}
    command.begin_transaction()
    assert command.materialize_result_layout("request-1", ResultLayoutMaterializeCommand("case-1", None, "actor")) == {"id": "dashboard-1"}
    command.commit_transaction()
    command.rollback_transaction()

    assert events == [
        ("context", "request-1"),
        ("ownership", "request-1", "case-1"),
        ("snapshot", "request-1"),
        ("bindings", "request-1", "case-1"),
        ("begin",),
        ("materialize", connection, {"request_id": "request-1", "load_case_id": "case-1", "page_id": None, "created_by": "actor"}),
        ("commit",),
        ("rollback",),
    ]


def test_result_layout_routes_remain_thin_http_adapters_with_existing_operation_ids() -> None:
    for handler in (workbench.get_request_result_layout, workbench.materialize_request_result_layout):
        tree = ast.parse(inspect.getsource(handler))
        assert not any(isinstance(node, ast.Attribute) and node.attr == "execute" for node in ast.walk(tree))
        assert "WorkbenchRepository" not in inspect.getsource(handler)

    paths = app.openapi()["paths"]
    assert paths["/api/workbench/requests/{request_id}/result-layout"]["get"]["operationId"] == "get_request_result_layout_api_workbench_requests__request_id__result_layout_get"
    assert paths["/api/workbench/requests/{request_id}/result-layout/materialize"]["post"]["operationId"] == "materialize_request_result_layout_api_workbench_requests__request_id__result_layout_materialize_post"


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (RequestResultLayoutNotFoundError("request-1"), 404, {"code": "REQUEST_NOT_FOUND", "request_id": "request-1"}),
        (ResultLayoutLoadCaseNotFoundError("case-1"), 404, {"code": "LOAD_CASE_NOT_FOUND", "load_case_id": "case-1"}),
        (LookupError("RESULT_LAYOUT_PAGE_NOT_FOUND"), 404, {"code": "RESULT_LAYOUT_PAGE_NOT_FOUND", "request_id": "request-1"}),
        (ValueError("RESULT_LAYOUT_MATERIALIZATION_CONFLICT"), 400, "RESULT_LAYOUT_MATERIALIZATION_CONFLICT"),
    ],
)
def test_result_layout_materialize_router_preserves_error_mapping(
    monkeypatch, error: Exception, expected_status: int, expected_detail: object
) -> None:
    monkeypatch.setattr(workbench, "SQLWorkbenchResultLayoutMaterializeCommand", lambda _connection: object())
    monkeypatch.setattr(
        workbench,
        "materialize_workbench_request_result_layout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/workbench/requests/request-1/result-layout/materialize",
            json={"load_case_id": "case-1"},
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}


@pytest.mark.parametrize(
    ("error", "expected_detail"),
    [
        (RequestResultLayoutNotFoundError("request-1"), {"code": "REQUEST_NOT_FOUND", "request_id": "request-1"}),
        (ResultLayoutLoadCaseNotFoundError("case-1"), {"code": "LOAD_CASE_NOT_FOUND", "load_case_id": "case-1"}),
    ],
)
def test_result_layout_get_router_preserves_request_and_load_case_error_mapping(
    monkeypatch, error: Exception, expected_detail: object
) -> None:
    monkeypatch.setattr(workbench, "SQLWorkbenchRequestResultLayoutQuery", lambda _connection: object())
    monkeypatch.setattr(
        workbench,
        "get_workbench_request_result_layout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with TestClient(app) as client:
        response = client.get("/api/workbench/requests/request-1/result-layout", params={"load_case_id": "case-1"})

    assert response.status_code == 404
    assert response.json() == {"detail": expected_detail}
