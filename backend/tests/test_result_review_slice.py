from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, NoReturn
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.adapters.http.routers import result_review
from app.adapters.persistence import result_review as result_review_persistence
from app.adapters.persistence.result_keys import run_result_keys
from app.database import initialize_database
from app.database_connection import connect
from app.main import app


RUN_ID = "run-drop-001"


def _review_routes() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.endpoint.__module__ == result_review.__name__
    ]


def _create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "  Result review title  ",
        "body": "  Result review body  ",
        "variable_key": "bottom_edge_max_stress",
        "time_value": 1.25,
        "entity_type": "ELEMENT",
        "entity_id": "  E-100  ",
        "review_status": "OPEN",
        "created_by": "spoofed client actor",
    }
    payload.update(overrides)
    return payload


def _update_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"review_status": "IN_REVIEW"}
    payload.update(overrides)
    return payload


def _cleanup_review(annotation_id: str | None, bookmark_id: str | None) -> None:
    if not annotation_id or not bookmark_id:
        return
    with connect() as connection:
        connection.execute(
            "DELETE FROM audit_events WHERE action IN ('RESULT_REVIEW_CREATED', 'RESULT_REVIEW_UPDATED') "
            "AND json_extract_string(detail_json, '$.review_item_id')=?",
            [annotation_id],
        )
        connection.execute("DELETE FROM review_annotations WHERE id=?", [annotation_id])
        connection.execute("DELETE FROM result_bookmarks WHERE id=?", [bookmark_id])


@contextmanager
def _unrelated_audit() -> Iterator[str]:
    audit_id = f"audit-review-slice-{uuid4().hex[:12]}"
    occurred_at = datetime(2026, 1, 2, 3, 4, 5)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO audit_events
                (id, occurred_at, user_id, username, role, action, method, path, status_code,
                 request_id, client_ip, user_agent, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [audit_id, occurred_at, "unrelated-user", "unrelated", "viewer", "UNRELATED_AUDIT", "POST", "/unrelated", 200, audit_id, None, "pytest", "{}"],
        )
    try:
        yield audit_id
    finally:
        with connect() as connection:
            connection.execute("DELETE FROM audit_events WHERE id=?", [audit_id])


@contextmanager
def _review_fixture(
    *,
    run_id: str = RUN_ID,
    body: str = "Original body",
    status: str = "OPEN",
    updated_at: datetime | None = None,
) -> Iterator[tuple[str, str]]:
    bookmark_id = f"bookmark-review-slice-{uuid4().hex[:12]}"
    annotation_id = f"annotation-review-slice-{uuid4().hex[:12]}"
    created_at = datetime(2026, 1, 2, 3, 4, 5)
    updated_at = updated_at or created_at
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO result_bookmarks
                (id, analysis_run_id, variable_key, time_value, entity_type, entity_id, title, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [bookmark_id, run_id, "bottom_edge_max_stress", 1.0, "ELEMENT", "E-1", "Fixture title", "fixture", created_at],
        )
        connection.execute(
            """
            INSERT INTO review_annotations
                (id, bookmark_id, analysis_run_id, variable_key, body, review_status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [annotation_id, bookmark_id, run_id, "bottom_edge_max_stress", body, status, "fixture", created_at, updated_at],
        )
    try:
        yield annotation_id, bookmark_id
    finally:
        _cleanup_review(annotation_id, bookmark_id)


@pytest.mark.contract
def test_result_review_router_owns_exact_three_legacy_routes_in_global_order() -> None:
    routes = _review_routes()

    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/analysis-runs/{run_id}/review-items", ("GET",)),
        ("/api/analysis-runs/{run_id}/review-items", ("POST",)),
        ("/api/review-items/{annotation_id}", ("PATCH",)),
    ]
    assert [route.endpoint.__name__ for route in routes] == [
        "list_review_items",
        "create_review_item",
        "update_review_item",
    ]
    assert [route.operation_id or route.unique_id for route in routes] == [
        "list_review_items_api_analysis_runs__run_id__review_items_get",
        "create_review_item_api_analysis_runs__run_id__review_items_post",
        "update_review_item_api_review_items__annotation_id__patch",
    ]
    assert [route.status_code for route in routes] == [None, 201, None]
    assert [route.response_model for route in routes] == [
        list[dict[str, Any]],
        dict[str, Any],
        dict[str, Any],
    ]
    paths = app.openapi()["paths"]
    assert set(paths["/api/analysis-runs/{run_id}/review-items"]) == {"get", "post"}
    assert set(paths["/api/review-items/{annotation_id}"]) == {"patch"}
    expected_anonymous_schemas = {
        ("/api/analysis-runs/{run_id}/review-items", "get", "200"): {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
            "title": "Response List Review Items Api Analysis Runs  Run Id  Review Items Get",
        },
        ("/api/analysis-runs/{run_id}/review-items", "post", "201"): {
            "type": "object",
            "additionalProperties": True,
            "title": "Response Create Review Item Api Analysis Runs  Run Id  Review Items Post",
        },
        ("/api/review-items/{annotation_id}", "patch", "200"): {
            "type": "object",
            "additionalProperties": True,
            "title": "Response Update Review Item Api Review Items  Annotation Id  Patch",
        },
    }
    for (path, method, status), expected_schema in expected_anonymous_schemas.items():
        operation = paths[path][method]
        assert set(operation["responses"]) == {status, "422"}
        assert operation["responses"][status]["content"]["application/json"]["schema"] == expected_schema
    all_routes = [route for route in app.routes if isinstance(route, APIRoute)]
    review_index = all_routes.index(routes[0])
    assert all_routes[review_index - 1].endpoint.__name__ == "get_analysis_run_trust"
    assert all_routes[review_index + len(routes)].endpoint.__name__ == "get_quality_thresholds"


@pytest.mark.contract
def test_main_relinquishes_result_review_sql_and_result_key_helper_ownership() -> None:
    source = (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    for token in (
        "def _run_result_keys",
        "def _review_items",
        "def list_review_items",
        "def create_review_item",
        "def update_review_item",
        "INSERT INTO result_bookmarks",
        "INSERT INTO review_annotations",
        "UPDATE review_annotations",
        "FROM review_annotations a JOIN result_bookmarks",
    ):
        assert token not in source
    assert "app.include_router(result_review_router)" in source
    assert "run_result_keys(conn, run_id)" in source


@pytest.mark.duckdb_integration
def test_get_has_no_explicit_permission_check_missing_404_and_updated_order(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    older = datetime(2026, 1, 2, 3, 4, 5)
    newer = datetime(2026, 1, 3, 3, 4, 5)
    with _review_fixture(updated_at=older) as (older_annotation, _), _review_fixture(updated_at=newer) as (newer_annotation, _):
        def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
            raise AssertionError("GET must not make an explicit resource permission call")

        monkeypatch.setattr(result_review, "require_resource_permission", forbidden)
        with TestClient(app) as client:
            missing = client.get("/api/analysis-runs/not-a-run/review-items")
            response = client.get(f"/api/analysis-runs/{RUN_ID}/review-items")

    assert missing.status_code == 404
    assert missing.json() == {"detail": "해석 Run을 찾을 수 없습니다."}
    assert response.status_code == 200, response.text
    ids = [item["id"] for item in response.json()]
    assert ids.index(newer_annotation) < ids.index(older_annotation)


@pytest.mark.duckdb_integration
def test_create_uses_same_connection_and_writes_bookmark_annotation_audit_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    original_permission = result_review.require_resource_permission
    original_audit = result_review.write_audit_event
    original_provider = result_review.SQLResultReviewRepositoryProvider

    class TrackingConnection:
        def __init__(self, connection: Any) -> None:
            self.connection = connection

        def __enter__(self) -> "TrackingConnection":
            return self

        def __exit__(self, *args: object) -> None:
            self.connection.__exit__(*args)

        @property
        def backend(self) -> str:
            return self.connection.backend

        def execute(self, statement: str, parameters: Any | None = None) -> Any:
            normalized = " ".join(statement.upper().split())
            if normalized.startswith("BEGIN"):
                events.append("begin")
            elif normalized.startswith("INSERT INTO RESULT_BOOKMARKS"):
                events.append("bookmark")
            elif normalized.startswith("INSERT INTO REVIEW_ANNOTATIONS"):
                events.append("annotation")
            elif normalized.startswith("COMMIT"):
                events.append("commit")
            elif normalized.startswith("ROLLBACK"):
                events.append("rollback")
            elif "FROM REVIEW_ANNOTATIONS A JOIN RESULT_BOOKMARKS" in normalized:
                events.append("fetch")
            return self.connection.execute(statement, parameters)

    @contextmanager
    def tracking_connect() -> Iterator[TrackingConnection]:
        with connect() as connection:
            yield TrackingConnection(connection)

    class TrackingProvider(original_provider):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(tracking_connect, *args, **kwargs)

    def tracking_permission(*args: object, **kwargs: object) -> object:
        events.append("auth")
        assert kwargs.get("conn") is not None
        return original_permission(*args, **kwargs)

    def tracking_audit(*args: object, **kwargs: object) -> None:
        events.append("audit")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(result_review, "SQLResultReviewRepositoryProvider", TrackingProvider)
    monkeypatch.setattr(result_review, "require_resource_permission", tracking_permission)
    monkeypatch.setattr(result_review, "write_audit_event", tracking_audit)
    annotation_id = bookmark_id = None
    try:
        with TestClient(app) as client:
            response = client.post(f"/api/analysis-runs/{RUN_ID}/review-items", json=_create_payload())
        assert response.status_code == 201, response.text
        annotation_id, bookmark_id = response.json()["id"], response.json()["bookmark_id"]
        assert events == ["begin", "auth", "bookmark", "annotation", "audit", "commit", "fetch"]
        assert response.json()["title"] == "Result review title"
        assert response.json()["body"] == "Result review body"
        assert response.json()["created_by"] == "로컬 관리자"
    finally:
        _cleanup_review(annotation_id, bookmark_id)


@pytest.mark.duckdb_integration
@pytest.mark.parametrize(
    ("run_id", "variable_key"),
    [
        ("run-showcase-trust-2", "bottom_edge_max_stress"),
        ("run-showcase-trust-2", "top_edge_stress_time"),
        ("run-showcase-trust-2", "load_displacement_curve"),
        ("run-showcase-trust-2", "top_edge_max_stress"),
        ("run-showcase-trust-2", "stress_contour_image"),
    ],
)
def test_create_accepts_result_keys_from_all_sql_result_sources(run_id: str, variable_key: str) -> None:
    annotation_id = bookmark_id = None
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/analysis-runs/{run_id}/review-items",
                json=_create_payload(variable_key=variable_key, entity_type=None, entity_id=None),
            )
        assert response.status_code == 201, response.text
        annotation_id, bookmark_id = response.json()["id"], response.json()["bookmark_id"]
    finally:
        _cleanup_review(annotation_id, bookmark_id)


@pytest.mark.unit
def test_run_result_keys_includes_media_metadata_and_ignores_malformed_metadata() -> None:
    class Cursor:
        def __init__(self, values: list[tuple[object, ...]]) -> None:
            self.values = values

        def fetchall(self) -> list[tuple[object, ...]]:
            return self.values

    class Connection:
        def execute(self, statement: str, _parameters: object = None) -> Cursor:
            if "FROM media_assets" in statement:
                return Cursor([('{"variable_key":"media_result"}',), ("not-json",), ("{}",)])
            return Cursor([("scalar_result",), ("time_result",), ("curve_result",), ("location_result",)])

    assert run_result_keys(Connection(), "run") == {
        "scalar_result",
        "time_result",
        "curve_result",
        "location_result",
        "media_result",
    }


@pytest.mark.unit
def test_sql_review_mutation_seams_fail_closed_without_authorizer_or_audit_writer() -> None:
    class Connection:
        def execute(self, *_args: object, **_kwargs: object) -> NoReturn:
            raise AssertionError("a missing seam must fail before SQL is attempted")

    repository = result_review_persistence.SQLResultReviewRepository(Connection())
    with pytest.raises(RuntimeError, match="authorization"):
        repository.authorize_resource("run", "run-id")
    with pytest.raises(RuntimeError, match="audit"):
        repository.add_audit({})  # type: ignore[arg-type]


@pytest.mark.contract
def test_trust_payload_uses_shared_result_key_query_not_a_divergent_local_copy() -> None:
    source = (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    trust_source = source[source.index("def _run_trust_payload") : source.index("def get_analysis_run_trust")]
    assert "run_result_keys(conn, run_id)" in trust_source


@pytest.mark.duckdb_integration
def test_create_missing_run_is_exact_404_and_leaves_no_rows() -> None:
    with connect() as connection:
        bookmarks_before = connection.execute("SELECT count(*) FROM result_bookmarks").fetchone()[0]
        annotations_before = connection.execute("SELECT count(*) FROM review_annotations").fetchone()[0]
    with TestClient(app) as client:
        response = client.post("/api/analysis-runs/not-a-run/review-items", json=_create_payload())
    assert response.status_code == 404
    assert response.json() == {"detail": "요청한 리소스를 찾을 수 없습니다."}
    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM result_bookmarks").fetchone()[0] == bookmarks_before
        assert connection.execute("SELECT count(*) FROM review_annotations").fetchone()[0] == annotations_before


@pytest.mark.duckdb_integration
def test_create_keeps_legacy_entity_id_without_entity_type_asymmetry() -> None:
    annotation_id = bookmark_id = None
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/analysis-runs/{RUN_ID}/review-items",
                json=_create_payload(entity_type=None, entity_id="orphan-entity-id"),
            )
        assert response.status_code == 201, response.text
        annotation_id, bookmark_id = response.json()["id"], response.json()["bookmark_id"]
        assert response.json()["entity_type"] is None
        assert response.json()["entity_id"] == "orphan-entity-id"
    finally:
        _cleanup_review(annotation_id, bookmark_id)


@pytest.mark.duckdb_integration
@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        (_create_payload(variable_key="not-a-result", entity_type=None, entity_id=None), "선택한 변수는 이 Run의 결과에 없습니다."),
        (_create_payload(variable_key=None, entity_type="ELEMENT", entity_id=None), "엔티티 유형을 지정하면 엔티티 ID도 필요합니다."),
    ],
)
def test_create_rejects_invalid_target_without_writing(payload: dict[str, Any], detail: str) -> None:
    with connect() as connection:
        bookmarks_before = connection.execute("SELECT count(*) FROM result_bookmarks").fetchone()[0]
        annotations_before = connection.execute("SELECT count(*) FROM review_annotations").fetchone()[0]
    with TestClient(app) as client:
        response = client.post(f"/api/analysis-runs/{RUN_ID}/review-items", json=payload)
    assert response.status_code == 422
    assert response.json() == {"detail": detail}
    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM result_bookmarks").fetchone()[0] == bookmarks_before
        assert connection.execute("SELECT count(*) FROM review_annotations").fetchone()[0] == annotations_before


@pytest.mark.duckdb_integration
@pytest.mark.parametrize("failure_point", ["annotation", "audit"])
def test_create_rolls_back_bookmark_annotation_and_audit_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    events: list[str] = []
    original_provider = result_review.SQLResultReviewRepositoryProvider

    class TrackingConnection:
        def __init__(self, connection: Any) -> None:
            self.connection = connection

        def __enter__(self) -> "TrackingConnection":
            return self

        def __exit__(self, *args: object) -> None:
            self.connection.__exit__(*args)

        @property
        def backend(self) -> str:
            return self.connection.backend

        def execute(self, statement: str, parameters: Any | None = None) -> Any:
            normalized = " ".join(statement.upper().split())
            if normalized.startswith("BEGIN"):
                events.append("begin")
            elif normalized.startswith("COMMIT"):
                events.append("commit")
            elif normalized.startswith("ROLLBACK"):
                events.append("rollback")
            return self.connection.execute(statement, parameters)

    @contextmanager
    def tracking_connect() -> Iterator[TrackingConnection]:
        with connect() as connection:
            yield TrackingConnection(connection)

    class TrackingProvider(original_provider):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(tracking_connect, *args, **kwargs)

    monkeypatch.setattr(result_review, "SQLResultReviewRepositoryProvider", TrackingProvider)
    with connect() as connection:
        bookmarks_before = connection.execute("SELECT count(*) FROM result_bookmarks").fetchone()[0]
        annotations_before = connection.execute("SELECT count(*) FROM review_annotations").fetchone()[0]
        review_audits_before = connection.execute(
            "SELECT count(*) FROM audit_events WHERE action IN ('RESULT_REVIEW_CREATED', 'RESULT_REVIEW_UPDATED')"
        ).fetchone()[0]

    def fail(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("injected failure")

    if failure_point == "annotation":
        monkeypatch.setattr(result_review_persistence.SQLResultReviewRepository, "insert_annotation", fail)
    else:
        monkeypatch.setattr(result_review, "write_audit_event", fail)
    with _review_fixture() as (unrelated_annotation, _), _unrelated_audit() as unrelated_audit_id:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post(f"/api/analysis-runs/{RUN_ID}/review-items", json=_create_payload())
        assert response.status_code == 500
        with connect() as connection:
            assert connection.execute("SELECT count(*) FROM result_bookmarks").fetchone()[0] == bookmarks_before + 1
            assert connection.execute("SELECT count(*) FROM review_annotations").fetchone()[0] == annotations_before + 1
            assert connection.execute(
                "SELECT count(*) FROM audit_events WHERE action IN ('RESULT_REVIEW_CREATED', 'RESULT_REVIEW_UPDATED')"
            ).fetchone()[0] == review_audits_before
            assert connection.execute("SELECT count(*) FROM review_annotations WHERE id=?", [unrelated_annotation]).fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM audit_events WHERE id=?", [unrelated_audit_id]).fetchone()[0] == 1
    assert events.count("begin") == 1
    assert events.count("rollback") == 1
    assert events.count("commit") == 0


@pytest.mark.duckdb_integration
def test_update_authorizes_before_lookup_preserves_or_trims_body_and_audits_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    original_permission = result_review.require_resource_permission
    original_audit = result_review.write_audit_event
    original_provider = result_review.SQLResultReviewRepositoryProvider

    class TrackingConnection:
        def __init__(self, connection: Any) -> None:
            self.connection = connection

        def __enter__(self) -> "TrackingConnection":
            return self

        def __exit__(self, *args: object) -> None:
            self.connection.__exit__(*args)

        @property
        def backend(self) -> str:
            return self.connection.backend

        def execute(self, statement: str, parameters: Any | None = None) -> Any:
            normalized = " ".join(statement.upper().split())
            if normalized.startswith("BEGIN"):
                events.append("begin")
            elif normalized.startswith("SELECT ANALYSIS_RUN_ID, BODY, REVIEW_STATUS FROM REVIEW_ANNOTATIONS"):
                events.append("lookup")
            elif normalized.startswith("UPDATE REVIEW_ANNOTATIONS"):
                events.append("update")
            elif "FROM REVIEW_ANNOTATIONS A JOIN RESULT_BOOKMARKS" in normalized:
                events.append("fetch")
            elif normalized.startswith("COMMIT"):
                events.append("commit")
            return self.connection.execute(statement, parameters)

    @contextmanager
    def tracking_connect() -> Iterator[TrackingConnection]:
        with connect() as connection:
            yield TrackingConnection(connection)

    class TrackingProvider(original_provider):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(tracking_connect, *args, **kwargs)

    def tracking_permission(*args: object, **kwargs: object) -> object:
        events.append("auth")
        assert kwargs.get("conn") is not None
        return original_permission(*args, **kwargs)

    def tracking_audit(*args: object, **kwargs: object) -> None:
        events.append("audit")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(result_review, "SQLResultReviewRepositoryProvider", TrackingProvider)
    monkeypatch.setattr(result_review, "require_resource_permission", tracking_permission)
    monkeypatch.setattr(result_review, "write_audit_event", tracking_audit)
    with _review_fixture(body="Keep this", status="OPEN") as (annotation_id, bookmark_id):
        with TestClient(app) as client:
            preserved = client.patch(f"/api/review-items/{annotation_id}", json=_update_payload(review_status="IN_REVIEW"))
            trimmed = client.patch(
                f"/api/review-items/{annotation_id}",
                json=_update_payload(body="  Changed body  ", review_status="RESOLVED"),
            )
        assert preserved.status_code == 200, preserved.text
        assert preserved.json()["body"] == "Keep this"
        assert trimmed.status_code == 200, trimmed.text
        assert trimmed.json()["body"] == "Changed body"
        assert trimmed.json()["review_status"] == "RESOLVED"
        assert events == ["begin", "auth", "lookup", "update", "audit", "fetch", "commit"] * 2
        with connect() as connection:
            audit = connection.execute(
                "SELECT detail_json FROM audit_events WHERE action='RESULT_REVIEW_UPDATED' "
                "AND json_extract_string(detail_json, '$.review_item_id')=? ORDER BY occurred_at DESC LIMIT 1",
                [annotation_id],
            ).fetchone()
            assert audit is not None
            detail = json.loads(audit[0])
            assert detail["old_status"] == "IN_REVIEW"
            assert detail["new_status"] == "RESOLVED"


@pytest.mark.duckdb_integration
@pytest.mark.parametrize("failure_point", ["update", "audit"])
def test_update_rolls_back_before_commit_when_update_or_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    events: list[str] = []
    original_provider = result_review.SQLResultReviewRepositoryProvider

    class TrackingConnection:
        def __init__(self, connection: Any) -> None:
            self.connection = connection

        def __enter__(self) -> "TrackingConnection":
            return self

        def __exit__(self, *args: object) -> None:
            self.connection.__exit__(*args)

        @property
        def backend(self) -> str:
            return self.connection.backend

        def execute(self, statement: str, parameters: Any | None = None) -> Any:
            normalized = " ".join(statement.upper().split())
            if normalized.startswith("BEGIN"):
                events.append("begin")
            elif normalized.startswith("COMMIT"):
                events.append("commit")
            elif normalized.startswith("ROLLBACK"):
                events.append("rollback")
            return self.connection.execute(statement, parameters)

    @contextmanager
    def tracking_connect() -> Iterator[TrackingConnection]:
        with connect() as connection:
            yield TrackingConnection(connection)

    class TrackingProvider(original_provider):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(tracking_connect, *args, **kwargs)

    monkeypatch.setattr(result_review, "SQLResultReviewRepositoryProvider", TrackingProvider)
    with _review_fixture(body="Original", status="OPEN") as (annotation_id, _), _unrelated_audit() as unrelated_audit_id:
        with connect() as connection:
            before = connection.execute(
                "SELECT body, review_status, updated_at FROM review_annotations WHERE id=?", [annotation_id]
            ).fetchone()
            review_audits_before = connection.execute(
                "SELECT count(*) FROM audit_events WHERE action IN ('RESULT_REVIEW_CREATED', 'RESULT_REVIEW_UPDATED')"
            ).fetchone()[0]

        def fail(*_args: object, **_kwargs: object) -> NoReturn:
            raise RuntimeError("injected failure")

        if failure_point == "update":
            monkeypatch.setattr(result_review_persistence.SQLResultReviewRepository, "update_annotation", fail)
        else:
            monkeypatch.setattr(result_review, "write_audit_event", fail)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.patch(
                f"/api/review-items/{annotation_id}",
                json=_update_payload(body="Changed", review_status="RESOLVED"),
            )
        assert response.status_code == 500
        with connect() as connection:
            assert connection.execute(
                "SELECT body, review_status, updated_at FROM review_annotations WHERE id=?", [annotation_id]
            ).fetchone() == before
            assert connection.execute(
                "SELECT count(*) FROM audit_events WHERE action IN ('RESULT_REVIEW_CREATED', 'RESULT_REVIEW_UPDATED')"
            ).fetchone()[0] == review_audits_before
            assert connection.execute("SELECT count(*) FROM audit_events WHERE id=?", [unrelated_audit_id]).fetchone()[0] == 1
    assert events.count("begin") == 1
    assert events.count("rollback") == 1
    assert events.count("commit") == 0


@pytest.mark.duckdb_integration
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (f"/api/analysis-runs/{RUN_ID}/review-items", {"title": "x", "body": "valid body"}),
        (f"/api/analysis-runs/{RUN_ID}/review-items", {"title": "valid title", "body": "x"}),
        (f"/api/analysis-runs/{RUN_ID}/review-items", {"title": "valid title", "body": "valid body", "entity_type": "BAD"}),
        ("/api/review-items/missing", {"review_status": "BAD"}),
    ],
)
def test_review_payload_pydantic_contract(path: str, payload: dict[str, Any]) -> None:
    method = "patch" if path.startswith("/api/review-items/") else "post"
    with TestClient(app) as client:
        response = getattr(client, method)(path, json=payload)
    assert response.status_code == 422


@pytest.mark.duckdb_integration
def test_update_missing_review_item_maps_to_404() -> None:
    with TestClient(app) as client:
        response = client.patch("/api/review-items/not-a-review-item", json=_update_payload())
    # Resource authorization performs the missing-resource check before the
    # command lookup, which is the legacy externally visible 404 outcome.
    assert response.status_code == 404
