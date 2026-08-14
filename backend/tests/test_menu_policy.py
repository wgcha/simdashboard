from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.access_policy import MENU_DEFINITIONS
from app.database import initialize_database
from app.database_connection import connect
from app.main import app


def test_menu_policy_update_validation_versions_and_restore():
    initialize_database()
    with TestClient(app) as client:
        initial = client.get("/api/navigation/menu-policy")
        assert initial.status_code == 200
        assert initial.json()["version"] == 1
        assert len(initial.json()["menus"]) == 14

        mismatch = client.put(
            "/api/admin/menu-policy",
            json={
                "expected_version": 1,
                "change_note": "권한 없는 메뉴 표시 시도",
                "visibility": {"general": {"data": True}},
            },
        )
        assert mismatch.status_code == 422
        assert mismatch.json()["detail"]["code"] == "MENU_PERMISSION_MISMATCH"

        locked = client.put(
            "/api/admin/menu-policy",
            json={
                "expected_version": 1,
                "change_note": "복구 메뉴 잠금 검증",
                "visibility": {"admin": {"menu_policy_admin": True}},
            },
        )
        assert locked.status_code == 409
        assert locked.json()["detail"]["code"] == "MENU_POLICY_LOCKED"

        updated = client.put(
            "/api/admin/menu-policy",
            json={
                "expected_version": 1,
                "change_note": "파워 데이터 메뉴 숨김",
                "visibility": {"power": {"data": False}},
            },
        )
        assert updated.status_code == 200
        assert updated.json()["version"] == 2
        data_menu = next(item for item in updated.json()["menus"] if item["id"] == "data")
        assert data_menu["visibility"]["power"] is False

        stale = client.put(
            "/api/admin/menu-policy",
            json={
                "expected_version": 1,
                "change_note": "오래된 버전 저장",
                "visibility": {"power": {"data": True}},
            },
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "STALE_POLICY_VERSION"

        versions = client.get("/api/admin/menu-policy/versions")
        assert versions.status_code == 200
        assert [item["version"] for item in versions.json()][:2] == [2, 1]
        restored = client.post("/api/admin/menu-policy/versions/1/restore")
        assert restored.status_code == 200
        assert restored.json()["version"] == 3
        restored_data = next(item for item in restored.json()["menus"] if item["id"] == "data")
        assert restored_data["visibility"]["power"] is True

    with connect() as conn:
        actions = {
            row[0]
            for row in conn.execute(
                "SELECT action FROM audit_events WHERE action IN ('MENU_POLICY_UPDATED', 'MENU_POLICY_RESTORED')"
            ).fetchall()
        }
        assert actions == {"MENU_POLICY_UPDATED", "MENU_POLICY_RESTORED"}


def test_frontend_menu_registry_matches_server_definitions_exactly():
    source = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "features"
        / "navigation"
        / "workspaceRouteRegistry.ts"
    ).read_text(encoding="utf-8")
    entries = re.findall(
        r"\{ id: '([^']+)', page: '[^']+', label: '[^']+', breadcrumb: \{[^}]+\}, requiredPermission: '([^']+)', contextKind: '([^']+)', navigationKind: '[^']+' \}",
        source,
    )
    assert entries == [
        (definition.id, definition.required_permission, definition.context_kind)
        for definition in MENU_DEFINITIONS
    ]
