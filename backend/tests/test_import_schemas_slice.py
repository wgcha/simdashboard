from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, NoReturn
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.adapters.http.routers import import_schemas
from app.adapters.persistence import import_schemas as import_schemas_persistence
from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.security import hash_password


def _route_slice() -> list[APIRoute]:
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.endpoint.__module__ == import_schemas.__name__
    ]


@pytest.mark.contract
def test_import_schema_router_owns_exact_four_routes_in_legacy_order() -> None:
    routes = _route_slice()

    assert [(route.path, tuple(sorted(route.methods or ()))) for route in routes] == [
        ("/api/import-schemas", ("GET",)),
        ("/api/import-schemas", ("POST",)),
        ("/api/import-schemas/{schema_id}", ("PUT",)),
        ("/api/import-schemas/{schema_id}", ("DELETE",)),
    ]
    assert [route.endpoint.__name__ for route in routes] == [
        "list_import_schemas",
        "create_import_schema",
        "update_import_schema",
        "delete_import_schema",
    ]
    assert [route.operation_id or route.unique_id for route in routes] == [
        "list_import_schemas_api_import_schemas_get",
        "create_import_schema_api_import_schemas_post",
        "update_import_schema_api_import_schemas__schema_id__put",
        "delete_import_schema_api_import_schemas__schema_id__delete",
    ]
    # Preserve the legacy anonymous response contract: no explicit model is
    # supplied; FastAPI derives these anonymous schemas from return annotations.
    assert [route.status_code for route in routes] == [None, 201, None, None]
    assert [route.response_model for route in routes] == [
        list[dict[str, Any]],
        dict[str, Any],
        dict[str, Any],
        dict[str, str],
    ]
    assert [route.deprecated for route in routes] == [None, None, None, None]
    assert all(
        "deprecated" not in app.openapi()["paths"][path][method]
        for path, method in (
            ("/api/import-schemas", "get"),
            ("/api/import-schemas", "post"),
            ("/api/import-schemas/{schema_id}", "put"),
            ("/api/import-schemas/{schema_id}", "delete"),
        )
    )


@pytest.mark.contract
def test_main_relinquishes_import_schema_helpers_and_sql_ownership() -> None:
    source = (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    forbidden = (
        "def list_import_schemas",
        "def create_import_schema",
        "def update_import_schema",
        "def delete_import_schema",
        "SELECT * FROM import_schemas",
        "UPDATE import_schemas",
        "INSERT INTO import_schemas",
        "INSERT INTO import_schema_versions",
        "FROM folder_import_jobs WHERE schema_id=?",
    )
    for token in forbidden:
        assert token not in source
    assert "app.include_router(import_schemas_router)" in source


def _schema_payload(
    *,
    name: str = "  Test schema  ",
    description: str = "  Test description  ",
    definition: dict[str, Any] | None = None,
    updated_by: str | None = "payload actor",
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "definition": definition if definition is not None else {"mappings": []},
        "updated_by": updated_by,
    }


@contextmanager
def _schema_row(
    *,
    schema_id: str | None = None,
    definition: dict[str, Any] | None = None,
    active: bool = True,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
) -> Iterator[tuple[str, datetime, datetime]]:
    schema_id = schema_id or f"import-schema-test-{uuid4().hex[:12]}"
    created_at = created_at or datetime(2026, 1, 2, 3, 4, 5)
    updated_at = updated_at or created_at
    raw_definition = definition or {"schema_id": schema_id, "version": 1, "mappings": []}
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO import_schemas
                (id, name, description, definition_json, is_active, created_at, updated_at, updated_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [schema_id, "Fixture schema", "Fixture description", json.dumps(raw_definition), active, created_at, updated_at, "fixture"],
        )
        connection.execute(
            """
            INSERT INTO import_schema_versions (schema_id, version, definition_json, created_at, updated_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            [schema_id, int(raw_definition.get("version", 1)), json.dumps(raw_definition), created_at, "fixture"],
        )
    try:
        yield schema_id, created_at, updated_at
    finally:
        _cleanup_schema(schema_id)


def _insert_import_job(schema_id: str, status: str) -> str:
    job_id = f"import-job-test-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as connection:
        load_case_row = connection.execute("SELECT id FROM load_cases ORDER BY id LIMIT 1").fetchone()
        assert load_case_row is not None
        connection.execute(
            """
            INSERT INTO folder_import_jobs
                (id, load_case_id, analysis_run_id, schema_id, schema_version, source_folder,
                 status, summary_json, created_at, source_type, source_checksum, source_run_id,
                 conflict_policy, outcome_reason, replaced_analysis_run_id, completed_at)
            VALUES (?, ?, NULL, ?, 1, ?, ?, '{}', ?, 'TEST', ?, NULL, 'ALLOW', NULL, NULL, NULL)
            """,
            [job_id, load_case_row[0], schema_id, f"schema-test/{job_id}", status, now, job_id],
        )
    return job_id


def _delete_import_job(job_id: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM folder_import_jobs WHERE id=?", [job_id])


def _cleanup_schema(schema_id: str) -> None:
    with connect() as connection:
        connection.execute(
            "DELETE FROM audit_events WHERE action IN ('IMPORT_SCHEMA_CREATED', 'IMPORT_SCHEMA_UPDATED') "
            "AND json_extract_string(detail_json, '$.schema_id')=?",
            [schema_id],
        )
        connection.execute("DELETE FROM import_schema_versions WHERE schema_id=?", [schema_id])
        connection.execute("DELETE FROM import_schemas WHERE id=?", [schema_id])


@pytest.mark.duckdb_integration
def test_get_lists_active_only_updated_order_hydrates_json_without_catalog_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    older = datetime(2026, 1, 2, 3, 4, 5)
    newer = datetime(2026, 1, 3, 3, 4, 5)
    with _schema_row(schema_id="schema-list-old", definition={"version": 1, "mappings": [{"a": 1}]}, updated_at=older) as _:
        with _schema_row(schema_id="schema-list-new", definition={"version": 2, "mappings": [{"b": 2}]}, updated_at=newer) as _:
            with _schema_row(schema_id="schema-list-inactive", active=False) as _:
                def denied(*_args: object, **_kwargs: object) -> None:
                    raise AssertionError("GET must not perform an explicit catalog permission check")

                monkeypatch.setattr(import_schemas, "require_permission", denied)
                with TestClient(app) as client:
                    response = client.get("/api/import-schemas")

    assert response.status_code == 200, response.text
    body = response.json()
    ids = [item["id"] for item in body]
    assert ids.index("schema-list-new") < ids.index("schema-list-old")
    assert "schema-list-inactive" not in ids
    assert body[ids.index("schema-list-new")]["definition"] == {"version": 2, "mappings": [{"b": 2}]}


@pytest.mark.duckdb_integration
@pytest.mark.parametrize(
    "definition",
    [{}, {"mappings": None}, {"mappings": "not-an-array"}],
)
def test_mappings_validation_is_exact_422_and_precedes_repository_open(
    monkeypatch: pytest.MonkeyPatch,
    definition: dict[str, Any],
) -> None:
    original_provider = import_schemas.SQLImportSchemaRepositoryProvider

    class GuardedProvider(original_provider):
        @contextmanager
        def __call__(self) -> Iterator[Any]:
            raise AssertionError("repository must not open before mappings validation")

    # Constructing a provider is harmless; entering its context is the
    # observable connection-open boundary that validation must precede.
    monkeypatch.setattr(import_schemas, "SQLImportSchemaRepositoryProvider", GuardedProvider)
    with TestClient(app) as client:
        response = client.post("/api/import-schemas", json=_schema_payload(definition=definition))
    assert response.status_code == 422
    assert response.json() == {"detail": "스키마 정의에는 mappings 배열이 필요합니다."}


@pytest.mark.duckdb_integration
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "x"),
        ("name", "x" * 121),
        ("description", "x" * 501),
        ("updated_by", "x"),
        ("updated_by", "x" * 61),
    ],
)
def test_import_schema_payload_pydantic_limits_are_enforced(field: str, value: str) -> None:
    payload = _schema_payload()
    payload[field] = value
    with TestClient(app) as client:
        response = client.post("/api/import-schemas", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]


@pytest.mark.duckdb_integration
@pytest.mark.parametrize("embedded_schema_id", [None, "embedded-schema-id"])
def test_create_trims_fields_forces_version_one_and_uses_principal_actor(
    embedded_schema_id: str | None,
) -> None:
    definition: dict[str, Any] = {"schema_id": embedded_schema_id, "version": 99, "mappings": []}
    with TestClient(app) as client:
        response = client.post("/api/import-schemas", json=_schema_payload(definition=definition))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Test schema"
    assert body["description"] == "Test description"
    assert body["updated_by"] == "로컬 관리자"
    assert body["definition"]["version"] == 1
    assert body["definition"]["schema_id"] == (embedded_schema_id or body["id"])
    with connect() as connection:
        assert connection.execute("SELECT updated_by FROM import_schemas WHERE id=?", [body["id"]]).fetchone()[0] == "로컬 관리자"
        assert connection.execute("SELECT version, updated_by FROM import_schema_versions WHERE schema_id=?", [body["id"]]).fetchone() == (1, "로컬 관리자")
    _cleanup_schema(body["id"])


@pytest.mark.duckdb_integration
def test_create_mutation_authorizes_on_same_connection_and_orders_transaction_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    transaction_connections: list[Any] = []
    original_permission = import_schemas.require_permission
    original_audit = import_schemas.write_audit_event
    original_provider = import_schemas.SQLImportSchemaRepositoryProvider

    class TrackingConnection:
        def __init__(self, connection: Any) -> None:
            self.connection = connection

        def __enter__(self) -> "TrackingConnection":
            return self

        def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
            self.connection.__exit__(exc_type, exc, traceback)

        @property
        def backend(self) -> str:
            return self.connection.backend

        def execute(self, statement: str, parameters: Any | None = None) -> Any:
            normalized = " ".join(statement.upper().split())
            if normalized.startswith("BEGIN"):
                transaction_connections.append(self)
                events.append("begin")
            elif normalized.startswith("INSERT INTO IMPORT_SCHEMAS"):
                events.append("live")
            elif normalized.startswith("INSERT INTO IMPORT_SCHEMA_VERSIONS"):
                events.append("version")
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

    monkeypatch.setattr(import_schemas, "SQLImportSchemaRepositoryProvider", TrackingProvider)

    def tracking_permission(request: object, permission: object, *args: object, **kwargs: object) -> object:
        events.append("auth")
        assert kwargs.get("conn") is not None
        assert transaction_connections and kwargs["conn"] is transaction_connections[0]
        return original_permission(request, permission, *args, **kwargs)

    def tracking_audit(*args: object, **kwargs: object) -> None:
        events.append("audit")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(import_schemas, "require_permission", tracking_permission)
    monkeypatch.setattr(import_schemas, "write_audit_event", tracking_audit)
    with TestClient(app) as client:
        response = client.post("/api/import-schemas", json=_schema_payload())
    assert response.status_code == 201, response.text
    assert events == ["begin", "auth", "live", "version", "audit", "commit"]

    body = response.json()
    _cleanup_schema(body["id"])


@pytest.mark.duckdb_integration
@pytest.mark.parametrize("failure_point", ["version", "audit"])
def test_create_rolls_back_schema_and_version_when_version_or_audit_write_fails(
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    with connect() as connection:
        schemas_before = connection.execute("SELECT count(*) FROM import_schemas").fetchone()[0]
        versions_before = connection.execute("SELECT count(*) FROM import_schema_versions").fetchone()[0]

    def fail_audit(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("injected audit failure")

    def fail_version(*_args: object, **_kwargs: object) -> NoReturn:
        raise RuntimeError("injected version failure")

    if failure_point == "audit":
        monkeypatch.setattr(import_schemas, "write_audit_event", fail_audit)
    else:
        monkeypatch.setattr(import_schemas_persistence.SQLImportSchemaRepository, "insert_version", fail_version)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/import-schemas", json=_schema_payload())
    assert response.status_code == 500
    # The generated id is not returned on failure; every failed create must
    # leave both tables unchanged, whether version or audit insertion failed.
    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM import_schemas").fetchone()[0] == schemas_before
        assert connection.execute("SELECT count(*) FROM import_schema_versions").fetchone()[0] == versions_before


@pytest.mark.duckdb_integration
def test_update_requires_active_row_preserves_embedded_id_and_created_at_and_appends_version() -> None:
    definition = {"schema_id": "embedded-preserve", "version": 4, "mappings": []}
    with _schema_row(schema_id="schema-update", definition=definition) as (_, created_at, _):
        with TestClient(app) as client:
            missing = client.put("/api/import-schemas/does-not-exist", json=_schema_payload())
            assert missing.status_code == 404
            assert missing.json() == {"detail": "폴더 스키마를 찾을 수 없습니다."}
            updated = client.put(
                "/api/import-schemas/schema-update",
                json=_schema_payload(definition={"schema_id": "payload-must-not-win", "version": 100, "mappings": [{"new": True}]}),
            )
            assert updated.status_code == 200, updated.text
            body = updated.json()
            assert body["definition"]["schema_id"] == "embedded-preserve"
            assert body["definition"]["version"] == 5
            assert body["created_at"].startswith(created_at.isoformat())
            with connect() as connection:
                versions = connection.execute(
                    "SELECT version, definition_json FROM import_schema_versions WHERE schema_id=? ORDER BY version",
                    ["schema-update"],
                ).fetchall()
                assert [row[0] for row in versions] == [4, 5]
                assert json.loads(versions[-1][1])["schema_id"] == "embedded-preserve"


@pytest.mark.duckdb_integration
def test_update_treats_inactive_schema_as_not_found() -> None:
    with _schema_row(schema_id="schema-update-inactive", active=False) as _:
        with TestClient(app) as client:
            response = client.put("/api/import-schemas/schema-update-inactive", json=_schema_payload())
    assert response.status_code == 404
    assert response.json() == {"detail": "폴더 스키마를 찾을 수 없습니다."}


@pytest.mark.duckdb_integration
@pytest.mark.parametrize("failure_point", ["version", "audit"])
def test_update_rolls_back_live_row_and_version_when_version_or_audit_fails(
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    schema_id = f"schema-update-rollback-{failure_point}"
    original_definition = {"schema_id": "rollback-embedded", "version": 1, "mappings": []}
    with _schema_row(schema_id=schema_id, definition=original_definition) as _:
        with connect() as connection:
            before = connection.execute(
                "SELECT name, definition_json, updated_by FROM import_schemas WHERE id=?",
                [schema_id],
            ).fetchone()
            version_count = connection.execute(
                "SELECT count(*) FROM import_schema_versions WHERE schema_id=?", [schema_id]
            ).fetchone()[0]

        def fail_audit(*_args: object, **_kwargs: object) -> NoReturn:
            raise RuntimeError("injected audit failure")

        def fail_version(*_args: object, **_kwargs: object) -> NoReturn:
            raise RuntimeError("injected version failure")

        if failure_point == "audit":
            monkeypatch.setattr(import_schemas, "write_audit_event", fail_audit)
        else:
            monkeypatch.setattr(import_schemas_persistence.SQLImportSchemaRepository, "insert_version", fail_version)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.put(
                f"/api/import-schemas/{schema_id}",
                json=_schema_payload(definition={"mappings": [{"changed": True}]}),
            )
        assert response.status_code == 500
        with connect() as connection:
            assert connection.execute(
                "SELECT name, definition_json, updated_by FROM import_schemas WHERE id=?", [schema_id]
            ).fetchone() == before
            assert connection.execute(
                "SELECT count(*) FROM import_schema_versions WHERE schema_id=?", [schema_id]
            ).fetchone()[0] == version_count


@pytest.mark.duckdb_integration
@pytest.mark.parametrize("status", ["RUNNING", "COMPLETED"])
def test_delete_blocks_running_or_completed_imports_with_exact_409(status: str) -> None:
    with _schema_row(schema_id=f"schema-delete-{status.lower()}") as (schema_id, _, _):
        job_id = _insert_import_job(schema_id, status)
        try:
            with TestClient(app) as client:
                response = client.delete(f"/api/import-schemas/{schema_id}")
            assert response.status_code == 409
            assert response.json() == {"detail": "적재 이력이 있는 스키마는 삭제할 수 없습니다. 비활성화 정책이 필요합니다."}
        finally:
            _delete_import_job(job_id)


@pytest.mark.duckdb_integration
@pytest.mark.parametrize("status", ["FAILED", "SKIPPED"])
def test_delete_allows_failed_or_skipped_and_does_not_mutate_versions_or_write_delete_audit(status: str) -> None:
    schema_id = f"schema-delete-{status.lower()}"
    with _schema_row(schema_id=schema_id) as (schema_id, _, _):
        job_id = _insert_import_job(schema_id, status)
        try:
            with TestClient(app) as client:
                response = client.delete(f"/api/import-schemas/{schema_id}")
            assert response.status_code == 200
            assert response.json() == {"status": "DEACTIVATED", "id": schema_id}
            with connect() as connection:
                assert connection.execute("SELECT is_active FROM import_schemas WHERE id=?", [schema_id]).fetchone()[0] is False
                assert connection.execute("SELECT count(*) FROM import_schema_versions WHERE schema_id=?", [schema_id]).fetchone()[0] == 1
                assert connection.execute(
                    "SELECT count(*) FROM audit_events WHERE action='IMPORT_SCHEMA_DELETED' AND json_extract_string(detail_json, '$.schema_id')=?",
                    [schema_id],
                ).fetchone()[0] == 0
        finally:
            _delete_import_job(job_id)


@pytest.mark.duckdb_integration
def test_delete_checks_authorization_before_opening_schema_repository_and_uses_legacy_separate_permission_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    original_permission = import_schemas.require_permission
    original_provider = import_schemas.SQLImportSchemaRepositoryProvider

    def tracking_permission(*args: object, **kwargs: object) -> object:
        events.append("auth")
        assert kwargs.get("conn") is None
        return original_permission(*args, **kwargs)

    class TrackingProvider(original_provider):
        def __call__(self) -> Any:
            events.append("provider")
            return super().__call__()

    monkeypatch.setattr(import_schemas, "require_permission", tracking_permission)
    monkeypatch.setattr(import_schemas, "SQLImportSchemaRepositoryProvider", TrackingProvider)
    with TestClient(app) as client:
        response = client.delete("/api/import-schemas/missing")
    assert response.status_code == 404
    assert response.json() == {"detail": "폴더 스키마를 찾을 수 없습니다."}
    assert events == ["auth", "provider"]


@pytest.mark.duckdb_integration
def test_delete_does_not_open_schema_repository_when_authorization_is_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    original_provider = import_schemas.SQLImportSchemaRepositoryProvider

    def deny(*_args: object, **_kwargs: object) -> NoReturn:
        events.append("auth")
        raise HTTPException(403, "denied")

    class TrackingProvider(original_provider):
        def __call__(self) -> Any:
            events.append("provider")
            return super().__call__()

    monkeypatch.setattr(import_schemas, "require_permission", deny)
    monkeypatch.setattr(import_schemas, "SQLImportSchemaRepositoryProvider", TrackingProvider)
    with TestClient(app) as client:
        response = client.delete("/api/import-schemas/missing")
    assert response.status_code == 403
    assert events == ["auth"]


def _insert_password_user(*, username: str, display_name: str, global_admin: bool) -> str:
    user_id = f"import-schema-user-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, account_status, is_global_admin)
            VALUES (?, ?, ?, ?, 'viewer', true, ?, ?, 'ACTIVE', ?)
            """,
            [user_id, username, hash_password("correct-horse-battery-staple"), display_name, now, now, global_admin],
        )
    return user_id


@pytest.mark.duckdb_integration
def test_viewer_can_read_but_only_global_admin_can_mutate(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    suffix = uuid4().hex[:8]
    viewer = _insert_password_user(username=f"schema-viewer-{suffix}", display_name="Schema Viewer", global_admin=False)
    admin = _insert_password_user(username=f"schema-admin-{suffix}", display_name="Schema Admin", global_admin=True)
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    password = "correct-horse-battery-staple"
    created_id: str | None = None
    try:
        with TestClient(app) as client:
            viewer_login = client.post("/api/auth/login", json={"username": f"schema-viewer-{suffix}", "password": password})
            admin_login = client.post("/api/auth/login", json={"username": f"schema-admin-{suffix}", "password": password})
            viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}
            admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
            assert client.get("/api/import-schemas", headers=viewer_headers).status_code == 200
            denied = client.post("/api/import-schemas", headers=viewer_headers, json=_schema_payload())
            assert denied.status_code == 403
            created = client.post("/api/import-schemas", headers=admin_headers, json=_schema_payload())
            assert created.status_code == 201, created.text
            created_id = created.json()["id"]
            assert client.delete(f"/api/import-schemas/{created_id}", headers=admin_headers).status_code == 200
    finally:
        with connect() as connection:
            connection.execute("DELETE FROM audit_events WHERE user_id IN (?, ?)", [viewer, admin])
        if created_id is not None:
            _cleanup_schema(created_id)
        with connect() as connection:
            connection.execute("DELETE FROM users WHERE id IN (?, ?)", [viewer, admin])
