from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.adapters.http.routers import dashboard_commands as dashboard_commands_router
from app.database import initialize_database
from app.database_connection import connect
from app.domains.dashboard_commands.policies import normalize_command, preview_command
from app.main import app
from app.schemas.api import NaturalLanguageCommand
from app.security import hash_password


PREVIEW_PATH = "/api/dashboard-commands/preview"
UNRECOGNIZED_MESSAGE = (
    "요청을 안전한 변경 명세로 변환하지 못했습니다. ‘응력-시간 그래프 추가’, "
    "‘상하좌우 최대 응력 막대그래프 추가’, ‘판정 카드 추가’처럼 요청해 주세요."
)


@pytest.mark.contract
def test_dashboard_preview_route_preserves_post_openapi_payload_and_terminal_registration() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    route = next(route for route in routes if route.path == PREVIEW_PATH and route.methods == {"POST"})

    assert route.endpoint.__module__ == dashboard_commands_router.__name__
    assert route.endpoint.__name__ == "preview_dashboard_command"
    assert route.response_model == dict[str, Any]
    assert (route.operation_id or route.unique_id) == "preview_dashboard_command_api_dashboard_commands_preview_post"

    schema = app.openapi()
    operation = schema["paths"][PREVIEW_PATH]["post"]
    assert operation["operationId"] == "preview_dashboard_command_api_dashboard_commands_preview_post"
    assert set(operation["responses"]) == {"200", "422"}
    project_id = next(parameter for parameter in operation["parameters"] if parameter["name"] == "project_id")
    assert project_id["in"] == "query"
    assert project_id["required"] is False
    assert project_id["schema"] == {"anyOf": [{"type": "string"}, {"type": "null"}], "title": "Project Id"}
    body_schema = schema["components"]["schemas"]["NaturalLanguageCommand"]
    assert body_schema["properties"]["command"] == {
        "type": "string",
        "minLength": 2,
        "maxLength": 500,
        "title": "Command",
    }

    restore_route = next(
        candidate
        for candidate in routes
        if candidate.path == "/api/dashboards/{dashboard_id}/restore/{version}" and candidate.methods == {"POST"}
    )
    assert routes.index(restore_route) + 1 == routes.index(route)
    assert routes.index(route) == len(routes) - 1


@pytest.mark.unit
def test_main_relinquishes_dashboard_preview_handler_and_router_stays_side_effect_free() -> None:
    main_path = Path(main_module.__file__)
    source = main_path.read_text(encoding="utf-8")
    assert "def preview_dashboard_command(" not in source
    assert "app.include_router(dashboard_commands_router)" in source
    assert all(token not in source for token in ("time-series-", "edge-bar-", "기준선을 표시합니다.", "compact = text.replace"))

    actual = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(ast.parse(source))
    )
    baseline = json.loads((main_path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8"))
    assert actual == 59
    assert baseline["execute_call_ceilings"]["app/main.py"] == actual

    router_source = Path(dashboard_commands_router.__file__).read_text(encoding="utf-8")
    assert ".execute(" not in router_source
    assert "write_audit_event" not in router_source
    assert all(token not in router_source for token in ("BEGIN", "COMMIT", "ROLLBACK"))

    policy_source = Path(preview_command.__code__.co_filename).read_text(encoding="utf-8")
    assert all(token not in policy_source for token in ("fastapi", "database", "connect(", ".execute("))


@pytest.mark.unit
def test_preview_policy_preserves_exact_normalization_branch_precedence_and_payloads() -> None:
    # The legacy normalizer removes literal spaces only; this deliberate behavior
    # avoids broadening matching to tabs/newlines while refactoring the policy.
    assert normalize_command("  응력 시간  ") == "응력시간"
    assert normalize_command("A\t B\n C") == "a\tb\nc"

    assert preview_command("  응력-시간 그래프에 기준선을 추가해  ") == {
        "recognized": True,
        "message": "기존 응력-시간 위젯에 기준선을 표시합니다.",
        "proposal": {
            "action": "update_widgets",
            "updates": [{"widget_type": "time_series", "settings": {"showThreshold": True}}],
        },
    }
    # This overlaps the add-card vocabulary, so update must retain precedence.
    assert preview_command("패스/실패 판정 카드를 맨 위 오른쪽으로 이동해") == {
        "recognized": True,
        "message": "패스/실패 판정 카드를 맨 위 오른쪽으로 이동합니다.",
        "proposal": {
            "action": "update_widgets",
            "updates": [{"widget_type": "verdict", "x": 9, "y": 0}],
        },
    }
    assert preview_command("컨투어 이미지와 수행자 의견을 나란히 배치해") == {
        "recognized": True,
        "message": "컨투어 이미지와 수행자 의견을 같은 행에 배치합니다.",
        "proposal": {
            "action": "update_widgets",
            "updates": [
                {"widget_type": "contour", "x": 4, "y": 18, "w": 4},
                {"widget_type": "note", "x": 8, "y": 18, "w": 4},
            ],
        },
    }

    epoch = lambda: 1_735_689_123.75
    assert preview_command("응력-시간 그래프를 추가해", epoch=epoch) == {
        "recognized": True,
        "message": "상하좌우 엣지 응력 시계열과 기준선을 표시하는 위젯을 추가합니다.",
        "proposal": {
            "action": "add_widget",
            "widget": {
                "id": "time-series-1735689123",
                "type": "time_series",
                "title": "Open Cell 엣지 응력-시간",
                "x": 0,
                "y": 20,
                "w": 8,
                "h": 5,
                "settings": {"showThreshold": True},
            },
        },
    }
    assert preview_command("상하좌우 최대 응력 막대그래프 추가", epoch=epoch) == {
        "recognized": True,
        "message": "엣지별 최대 응력과 기준값을 비교하는 막대그래프를 추가합니다.",
        "proposal": {
            "action": "add_widget",
            "widget": {
                "id": "edge-bar-1735689123",
                "type": "edge_bar",
                "title": "상하좌우 엣지 최대 응력",
                "x": 0,
                "y": 20,
                "w": 6,
                "h": 4,
                "settings": {"failColor": "#ff5d73", "showThreshold": True},
            },
        },
    }
    assert preview_command("판정 카드 추가", epoch=epoch) == {
        "recognized": True,
        "message": "기준값에 따른 전체 판정 카드를 추가합니다.",
        "proposal": {
            "action": "add_widget",
            "widget": {
                "id": "verdict-1735689123",
                "type": "verdict",
                "title": "패스/실패 판정",
                "x": 0,
                "y": 20,
                "w": 3,
                "h": 2,
                "settings": {},
            },
        },
    }
    assert preview_command("데이터베이스를 삭제해", epoch=epoch) == {
        "recognized": False,
        "message": UNRECOGNIZED_MESSAGE,
        "proposal": None,
    }


@pytest.mark.unit
def test_preview_router_authorizes_before_policy_and_keeps_generic_403_and_project_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[Any, ...]] = []
    request = SimpleNamespace(state=SimpleNamespace())
    payload = NaturalLanguageCommand(command="응력-시간 그래프를 추가해")
    detail = {"code": "PERMISSION_DENIED", "project_id": "project-locked"}

    monkeypatch.setattr(
        dashboard_commands_router,
        "require_permission",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPException(403, detail=detail)),
    )
    monkeypatch.setattr(
        dashboard_commands_router,
        "preview_dashboard_command_query",
        lambda command: events.append(("policy", command)),
    )
    with pytest.raises(HTTPException) as error:
        dashboard_commands_router.preview_dashboard_command(payload, request, "project-locked")
    assert error.value.status_code == 403
    assert error.value.detail == detail
    assert events == []

    def allow(received_request: object, permission: str, project_id: str | None) -> object:
        events.append(("permission", received_request, permission, project_id))
        return object()

    monkeypatch.setattr(dashboard_commands_router, "require_permission", allow)
    monkeypatch.setattr(
        dashboard_commands_router,
        "preview_dashboard_command_query",
        lambda command: events.append(("policy", command)) or {"recognized": True},
    )
    assert dashboard_commands_router.preview_dashboard_command(payload, request, "project-22") == {"recognized": True}
    assert events == [
        ("permission", request, "dashboard.edit", "project-22"),
        ("policy", "응력-시간 그래프를 추가해"),
    ]


def _insert_password_user(username: str, role: str, password: str) -> str:
    user_id = f"dashboard-command-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, account_status, is_global_admin)
            VALUES (?, ?, ?, ?, ?, true, ?, ?, 'ACTIVE', ?)
            """,
            [user_id, username, hash_password(password), username, role, now, now, role == "admin"],
        )
    return user_id


@pytest.mark.duckdb_integration
def test_preview_http_permission_and_api_mutation_audit_remain_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    initialize_database()
    suffix = uuid4().hex[:10]
    password = "correct-horse-battery-staple"
    viewer_id = _insert_password_user(f"preview-viewer-{suffix}", "viewer", password)
    editor_id = _insert_password_user(f"preview-editor-{suffix}", "editor", password)
    admin_id = _insert_password_user(f"preview-admin-{suffix}", "admin", password)
    request_ids = [f"preview-local-{suffix}", f"preview-viewer-{suffix}", f"preview-editor-{suffix}", f"preview-admin-{suffix}"]

    try:
        with TestClient(app) as client:
            local = client.post(
                PREVIEW_PATH,
                headers={"X-Request-ID": request_ids[0]},
                json={"command": "응력-시간 그래프를 추가해"},
            )
            assert local.status_code == 200, local.text
            assert local.json()["recognized"] is True

        monkeypatch.setenv("AUTH_MODE", "password")
        monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
        with TestClient(app) as client:
            def login_headers(username: str) -> dict[str, str]:
                response = client.post("/api/auth/login", json={"username": username, "password": password})
                assert response.status_code == 200, response.text
                return {"Authorization": f"Bearer {response.json()['access_token']}"}

            viewer = client.post(
                PREVIEW_PATH,
                headers={**login_headers(f"preview-viewer-{suffix}"), "X-Request-ID": request_ids[1]},
                json={"command": "응력-시간 그래프를 추가해"},
            )
            editor = client.post(
                PREVIEW_PATH,
                headers={**login_headers(f"preview-editor-{suffix}"), "X-Request-ID": request_ids[2]},
                json={"command": "응력-시간 그래프를 추가해"},
            )
            admin = client.post(
                PREVIEW_PATH,
                headers={**login_headers(f"preview-admin-{suffix}"), "X-Request-ID": request_ids[3]},
                json={"command": "응력-시간 그래프를 추가해"},
            )
            assert viewer.status_code == editor.status_code == 403
            assert admin.status_code == 200, admin.text

        with connect() as connection:
            events = connection.execute(
                """
                SELECT request_id, action, status_code
                FROM audit_events
                WHERE request_id IN (?, ?, ?, ?)
                ORDER BY request_id, action
                """,
                request_ids,
            ).fetchall()
        mutation_statuses = {
            (request_id, status_code)
            for request_id, action, status_code in events
            if action == "API_MUTATION"
        }
        assert mutation_statuses == {
            (request_ids[0], 200),
            (request_ids[1], 403),
            (request_ids[2], 403),
            (request_ids[3], 200),
        }
        assert {(request_ids[1], "AUTHORIZATION_DENIED", 403), (request_ids[2], "AUTHORIZATION_DENIED", 403)} <= set(events)
    finally:
        with connect() as connection:
            connection.execute("DELETE FROM audit_events WHERE request_id IN (?, ?, ?, ?)", request_ids)
            connection.execute("DELETE FROM users WHERE id IN (?, ?, ?)", [viewer_id, editor_id, admin_id])
