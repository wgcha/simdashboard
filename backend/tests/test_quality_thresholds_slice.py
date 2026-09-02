from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.adapters.http.routers import quality_thresholds
from app.adapters.persistence.quality_thresholds import SQLQualityThresholdRepository
from app.application.quality_thresholds import commands, queries
from app.database import initialize_database
from app.database_connection import connect
from app.domains.quality_thresholds.errors import (
    ProjectScopedThresholdUrlRequiredError,
    QualityThresholdNotFoundError,
)
from app.main import app


KEY = "open_cell_stress_mpa"


def _threshold(*, project_id: str = "project", key: str = KEY, value: float = 75.0) -> dict[str, Any]:
    return {
        "project_id": project_id, "criterion_key": key, "analysis_key": "DROP", "label": key,
        "threshold_double": value, "unit": "MPa", "updated_by": "seed", "updated_at": datetime(2026, 1, 1),
    }


class FakeRepository:
    def __init__(self, criteria: list[dict[str, Any]], *, fail_at: str | None = None) -> None:
        self.criteria = criteria
        self.fail_at = fail_at
        self.events: list[str] = []
        self.connection = object()
        self.updated = _threshold()

    def list_thresholds(self, project_id: str) -> list[dict[str, Any]]:
        self.events.append(f"list:{project_id}")
        return [_threshold(project_id=project_id)]

    def find_criteria(self, key: str, project_id: str | None) -> list[dict[str, Any]]:
        self.events.append(f"find:{key}:{project_id}")
        return self.criteria

    def authorize_mutation(self, project_id: str) -> None:
        self.events.append(f"authorize:{project_id}:{self.connection is CONNECTION}")
        self._fail("authorize")

    def begin_transaction(self) -> None:
        self.events.append("begin")
        self._fail("begin")

    def update_threshold(self, *_args: Any) -> None:
        self.events.append("update")
        self.update_args = _args
        self._fail("update")

    def recalculate_chassis_rear(self, *_args: Any) -> None:
        self.events.append("chassis")
        self._fail("recalc")

    def recalculate_open_cell(self, *_args: Any) -> None:
        self.events.append("open-cell")
        self._fail("recalc")

    def add_audit(self, audit: dict[str, Any]) -> None:
        self.events.append("audit")
        self.audit = audit
        self._fail("audit")

    def commit_transaction(self) -> None:
        self.events.append("commit")
        self._fail("commit")

    def updated_threshold(self, *_args: Any) -> dict[str, Any]:
        self.events.append("fetch")
        self._fail("fetch")
        return self.updated

    def rollback_transaction(self) -> None:
        self.events.append("rollback")

    def _fail(self, point: str) -> None:
        if self.fail_at == point:
            raise RuntimeError(point)


CONNECTION = object()


def _provider(repository: FakeRepository):
    repository.connection = CONNECTION

    @contextmanager
    def provide() -> Iterator[FakeRepository]:
        yield repository

    return provide


@pytest.mark.contract
def test_threshold_router_owns_three_legacy_routes_openapi_alias_and_global_order() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute) and route.endpoint.__module__ == quality_thresholds.__name__]
    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/projects/{project_id}/quality-thresholds", ("GET",)),
        ("/api/projects/{project_id}/quality-thresholds/{criterion_key}", ("PUT",)),
        ("/api/quality-thresholds/{criterion_key}", ("PUT",)),
    ]
    assert [route.endpoint.__name__ for route in routes] == ["get_quality_thresholds", "update_project_quality_threshold", "update_quality_threshold"]
    assert [(route.operation_id or route.unique_id) for route in routes] == [
        "get_quality_thresholds_api_projects__project_id__quality_thresholds_get",
        "update_project_quality_threshold_api_projects__project_id__quality_thresholds__criterion_key__put",
        "update_quality_threshold_api_quality_thresholds__criterion_key__put",
    ]
    assert [route.status_code for route in routes] == [None, None, None]
    assert [route.response_model for route in routes] == [list[dict[str, Any]], dict[str, Any], dict[str, Any]]
    spec = app.openapi()["paths"]
    for path, method, status, title in (
        ("/api/projects/{project_id}/quality-thresholds", "get", "200", "Response Get Quality Thresholds Api Projects  Project Id  Quality Thresholds Get"),
        ("/api/projects/{project_id}/quality-thresholds/{criterion_key}", "put", "200", "Response Update Project Quality Threshold Api Projects  Project Id  Quality Thresholds  Criterion Key  Put"),
        ("/api/quality-thresholds/{criterion_key}", "put", "200", "Response Update Quality Threshold Api Quality Thresholds  Criterion Key  Put"),
    ):
        operation = spec[path][method]
        assert set(operation["responses"]) == {status, "422"}
        expected = {"type": "array", "items": {"type": "object", "additionalProperties": True}, "title": title} if method == "get" else {"type": "object", "additionalProperties": True, "title": title}
        assert operation["responses"][status]["content"]["application/json"]["schema"] == expected
    assert spec["/api/quality-thresholds/{criterion_key}"]["put"]["deprecated"] is True
    all_routes = [route for route in app.routes if isinstance(route, APIRoute)]
    index = all_routes.index(routes[0])
    assert all_routes[index - 1].endpoint.__name__ == "update_review_item"
    assert all_routes[index + len(routes)].endpoint.__name__ == "get_workflow"


@pytest.mark.contract
def test_main_relinquishes_threshold_sql_and_router_is_sql_free() -> None:
    main = (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    router = Path(quality_thresholds.__file__).read_text(encoding="utf-8")
    for token in ("def get_quality_thresholds", "def _update_quality_threshold", "def update_project_quality_threshold", "def update_quality_threshold", "UPDATE quality_thresholds", "PROJECT_THRESHOLD_CHANGED"):
        assert token not in main
    assert "app.include_router(quality_thresholds_router)" in main
    assert router.count(".execute(") == 0
    assert main.count(".execute(") <= 71


@pytest.mark.unit
def test_list_is_read_only_and_query_provider_is_used() -> None:
    repository = FakeRepository([])
    assert queries.list_quality_thresholds("project", _provider(repository)) == [_threshold(project_id="project")]
    assert repository.events == ["list:project"]


@pytest.mark.unit
def test_command_canonical_and_alias_lookup_errors_happen_before_auth() -> None:
    for criteria, expected_project, error in (
        ([], "project", QualityThresholdNotFoundError),
        ([_threshold(), _threshold(project_id="other")], None, ProjectScopedThresholdUrlRequiredError),
    ):
        repository = FakeRepository(criteria)
        with pytest.raises(error):
            commands.update_quality_threshold(criterion_key=KEY, threshold_double=80, expected_project_id=expected_project, actor_name=lambda: "actor", audit=lambda: {}, repository_provider=_provider(repository))
        assert repository.events == [f"find:{KEY}:{expected_project}"]


@pytest.mark.unit
def test_command_preserves_actor_time_audit_and_exact_success_order() -> None:
    repository = FakeRepository([_threshold()])
    now = datetime(2026, 1, 2, 3, 4, 5)
    result = commands.update_quality_threshold(
        criterion_key=KEY, threshold_double=80, expected_project_id="project", actor_name=lambda: "trusted actor",
        audit=lambda: {"method": "PUT", "path": "/canonical", "request_id": "request", "user_id": "u", "username": "name", "role": "admin", "client_ip": None, "user_agent": "pytest"},
        repository_provider=_provider(repository), clock=lambda: now,
    )
    assert result == _threshold()
    assert repository.events == [f"find:{KEY}:project", "authorize:project:True", "begin", "update", "open-cell", "audit", "commit", "fetch"]
    assert repository.update_args == ("project", KEY, 80, "trusted actor", now)
    assert repository.audit["detail"] == {"project_id": "project", "criterion_key": KEY, "old_value": 75.0, "new_value": 80, "unit": "MPa"}
    assert repository.audit["action"] == "PROJECT_THRESHOLD_CHANGED"


@pytest.mark.unit
@pytest.mark.parametrize("failure,expected", [
    ("authorize", ["find:open_cell_stress_mpa:project", "authorize:project:True"]),
    ("update", ["find:open_cell_stress_mpa:project", "authorize:project:True", "begin", "update", "rollback"]),
    ("recalc", ["find:open_cell_stress_mpa:project", "authorize:project:True", "begin", "update", "open-cell", "rollback"]),
    ("audit", ["find:open_cell_stress_mpa:project", "authorize:project:True", "begin", "update", "open-cell", "audit", "rollback"]),
    ("commit", ["find:open_cell_stress_mpa:project", "authorize:project:True", "begin", "update", "open-cell", "audit", "commit", "rollback"]),
    ("fetch", ["find:open_cell_stress_mpa:project", "authorize:project:True", "begin", "update", "open-cell", "audit", "commit", "fetch", "rollback"]),
])
def test_command_failure_rolls_back_once_and_never_commits_early(failure: str, expected: list[str]) -> None:
    repository = FakeRepository([_threshold()], fail_at=failure)
    with pytest.raises(RuntimeError, match=failure):
        commands.update_quality_threshold(criterion_key=KEY, threshold_double=80, expected_project_id="project", actor_name=lambda: "actor", audit=lambda: {}, repository_provider=_provider(repository))
    assert repository.events == expected
    assert repository.events.count("rollback") == (0 if failure == "authorize" else 1)


@pytest.mark.unit
def test_sql_adapter_fails_closed_without_authorizer_or_audit_writer() -> None:
    adapter = SQLQualityThresholdRepository(object())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="authorization callback"):
        adapter.authorize_mutation("project")
    with pytest.raises(RuntimeError, match="audit callback"):
        adapter.add_audit({})  # type: ignore[arg-type]


@pytest.mark.duckdb_integration
def test_threshold_http_sort_unknown_alias_parity_actor_audit_and_duplicate_alias_block() -> None:
    initialize_database()
    unique_key = f"slice-only-{uuid4().hex[:10]}"
    now = datetime(2026, 1, 2, 3, 4, 5)
    with connect() as connection:
        connection.execute(
            "INSERT INTO quality_thresholds VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [unique_key, "project-tv-001", "Z", "Slice only", 1.0, "N", "seed", now],
        )
    try:
        with TestClient(app) as client:
            listed = client.get("/api/projects/project-tv-001/quality-thresholds")
            unknown = client.get("/api/projects/no-such-project/quality-thresholds")
            canonical = client.put(f"/api/projects/project-tv-001/quality-thresholds/{unique_key}", json={"threshold_double": 2.0, "updated_by": "spoofed"})
            alias = client.put(f"/api/quality-thresholds/{unique_key}", json={"threshold_double": 3.0, "updated_by": "spoofed again"})
            duplicate = client.put(f"/api/quality-thresholds/{KEY}", json={"threshold_double": 80, "updated_by": "spoofed"})
            wrong_project = client.put(f"/api/projects/no-such-project/quality-thresholds/{unique_key}", json={"threshold_double": 4.0, "updated_by": "spoofed"})
            missing = client.put("/api/projects/project-tv-001/quality-thresholds/no-such-criterion", json={"threshold_double": 4.0, "updated_by": "spoofed"})
        assert listed.status_code == 200
        assert [(row["analysis_key"], row["criterion_key"]) for row in listed.json()] == sorted((row["analysis_key"], row["criterion_key"]) for row in listed.json())
        assert unknown.status_code == 200 and unknown.json() == []
        assert canonical.status_code == alias.status_code == 200
        assert canonical.json()["threshold_double"] == 2.0 and alias.json()["threshold_double"] == 3.0
        assert alias.json()["updated_by"] == "로컬 관리자"
        assert duplicate.status_code == 409 and duplicate.json() == {"detail": "프로젝트 범위 품질 기준 URL을 사용해야 합니다."}
        assert wrong_project.status_code == missing.status_code == 404
        assert wrong_project.json() == missing.json() == {"detail": "품질 판정 기준을 찾을 수 없습니다."}
        with connect() as connection:
            audit = connection.execute("SELECT user_id, username, action, detail_json FROM audit_events WHERE action='PROJECT_THRESHOLD_CHANGED' ORDER BY occurred_at DESC LIMIT 1").fetchone()
        assert audit is not None and audit[:3] == ("local-admin", "local", "PROJECT_THRESHOLD_CHANGED")
        detail = json.loads(audit[3]) if isinstance(audit[3], str) else audit[3]
        assert detail == {"project_id": "project-tv-001", "criterion_key": unique_key, "old_value": 2.0, "new_value": 3.0, "unit": "N"}
    finally:
        with connect() as connection:
            connection.execute("DELETE FROM audit_events WHERE action='PROJECT_THRESHOLD_CHANGED' AND detail_json LIKE ?", [f"%{unique_key}%"])
            connection.execute("DELETE FROM quality_thresholds WHERE project_id=? AND criterion_key=?", ["project-tv-001", unique_key])


@pytest.mark.duckdb_integration
def test_threshold_audit_failure_rolls_back_live_threshold_and_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    with connect() as connection:
        before = connection.execute("SELECT threshold_double FROM quality_thresholds WHERE project_id=? AND criterion_key=?", ["project-tv-001", KEY]).fetchone()
        scalar_before = connection.execute(
            """
            SELECT sr.id, sr.threshold_double, sr.verdict
            FROM scalar_results sr
            JOIN analysis_runs run ON run.id=sr.analysis_run_id
            JOIN load_cases lc ON lc.id=run.load_case_id
            JOIN analysis_requests ar ON ar.id=lc.request_id
            JOIN variable_definitions vd ON vd.load_case_id=lc.id AND vd.variable_key=sr.variable_key
            WHERE ar.project_id=? AND vd.result_group='OPEN_CELL'
            ORDER BY sr.id
            """,
            ["project-tv-001"],
        ).fetchall()
        audit_before = connection.execute("SELECT count(*) FROM audit_events WHERE action='PROJECT_THRESHOLD_CHANGED'").fetchone()
    assert before is not None and audit_before is not None

    def fail_audit(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit failure")

    monkeypatch.setattr(quality_thresholds, "write_audit_event", fail_audit)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put(f"/api/projects/project-tv-001/quality-thresholds/{KEY}", json={"threshold_double": before[0] + 1, "updated_by": "spoofed"})
    assert response.status_code == 500
    with connect() as connection:
        after = connection.execute("SELECT threshold_double FROM quality_thresholds WHERE project_id=? AND criterion_key=?", ["project-tv-001", KEY]).fetchone()
        scalar_after = connection.execute(
            """
            SELECT sr.id, sr.threshold_double, sr.verdict
            FROM scalar_results sr
            JOIN analysis_runs run ON run.id=sr.analysis_run_id
            JOIN load_cases lc ON lc.id=run.load_case_id
            JOIN analysis_requests ar ON ar.id=lc.request_id
            JOIN variable_definitions vd ON vd.load_case_id=lc.id AND vd.variable_key=sr.variable_key
            WHERE ar.project_id=? AND vd.result_group='OPEN_CELL'
            ORDER BY sr.id
            """,
            ["project-tv-001"],
        ).fetchall()
        audit_after = connection.execute("SELECT count(*) FROM audit_events WHERE action='PROJECT_THRESHOLD_CHANGED'").fetchone()
    assert after == before
    assert scalar_after == scalar_before
    assert audit_after == audit_before
