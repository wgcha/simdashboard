import json
import base64
import io
import os
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.routers import result_ingestion as result_ingestion_module
from app.adapters.persistence.result_ingestion import SQLResultIngestionQuery
from app.config import database_settings
from app.database import connect, initialize_database
from app.main import app
from app.media_policy import validate_media_metadata
from app.security import hash_password


@pytest.mark.contract
@pytest.mark.duckdb_integration
def test_workspace_layout_versions_are_persisted():
    initialize_database()
    with TestClient(app) as client:
        path = "/api/projects/project-tv-001/workspace-layouts/portfolio"
        initial = client.get(path)
        assert initial.status_code == 200
        original = initial.json()
        changed_definition = {**original["definition"], "fontSize": 11 if original["definition"]["fontSize"] != 11 else 12}
        try:
            saved = client.put(path, json={"definition": changed_definition, "updated_by": "위조 편집자"})
            assert saved.status_code == 200, saved.text
            assert saved.json()["version"] == original["version"] + 1
            assert saved.json()["updated_by"] == "로컬 관리자"
            assert client.get(path).json()["definition"] == changed_definition
            versions = client.get(f"{path}/versions").json()
            assert versions[0]["version"] == saved.json()["version"]
        finally:
            restored = client.put(path, json={"definition": original["definition"], "updated_by": "위조 복원"})
            assert restored.status_code == 200


def test_portfolio_metrics_filters_and_csv_reconcile():
    initialize_database()
    with TestClient(app) as client:
        payload = client.get("/api/portfolio/overview").json()
        assert payload["grain"] == "LOAD_CASE_LATEST_RUN"
        assert payload["kpis"]["load_cases"] == len(payload["records"])
        assert sum(item["value"] for item in payload["type_distribution"]) == len(payload["records"])
        filtered = client.get("/api/portfolio/overview", params={"analysis_type": "DROP"}).json()
        assert filtered["records"]
        assert all(item["analysis_type"] == "DROP" for item in filtered["records"])
        searched = client.get("/api/portfolio/overview", params={"search": "존재하지않는검색어"}).json()
        assert searched["kpis"]["load_cases"] == 0
        csv_response = client.get("/api/portfolio/export.csv", params={"analysis_type": "DROP"})
        assert csv_response.status_code == 200
        assert "project_name" in csv_response.text
        assert "DROP" in csv_response.text


def test_widget_catalog_and_dashboard_version_flows():
    initialize_database()
    with TestClient(app) as client:
        catalog = client.get("/api/widget-catalog").json()
        assert {item["type"] for item in catalog} >= {"kpi", "gauge", "time_series", "video", "video_grid", "model3d", "workflow"}
        source = client.get("/api/dashboards/dashboard-drop-default").json()
        assert any(item["type"] == "video_grid" for item in source["widgets"])
        clone = client.post("/api/dashboards/dashboard-drop-default/clone", json={"name": "테스트 복제본", "description": "회귀 검증"})
        assert clone.status_code == 201
        clone_id = clone.json()["id"]
        assert client.get(f"/api/dashboards/{clone_id}").json()["name"] == "테스트 복제본"
        saved = client.put(f"/api/dashboards/{clone_id}", json={**client.get(f"/api/dashboards/{clone_id}").json(), "description": "변경됨"})
        assert saved.status_code == 200
        restored = client.post(f"/api/dashboards/{clone_id}/restore/1")
        assert restored.status_code == 200
        assert client.get(f"/api/dashboards/{clone_id}").json()["description"] == "회귀 검증"
    with connect() as conn:
        conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [clone_id])
        conn.execute("DELETE FROM dashboards WHERE id = ?", [clone_id])


def test_custom_analysis_page_lifecycle_order_clone_restore_and_validation():
    initialize_database()
    load_case_id = "loadcase-drop-bottom-001"
    created_ids: list[str] = []
    clone_id: str | None = None
    with TestClient(app) as client:
        public_pages = client.get("/api/dashboard-pages", params={"load_case_id": load_case_id})
        assert public_pages.status_code == 200, public_pages.text
        assert [item["page"]["analysis_key"] for item in public_pages.json()[:2]] == ["open_cell", "chassis_rear"]
        comparison_page = next(item for item in public_pages.json() if item["page"]["analysis_key"] == "run_comparison")
        comparison_definition = client.get(f"/api/dashboards/{comparison_page['id']}").json()
        assert comparison_definition["widgets"][0]["type"] == "run_comparison"

        first = client.post(
            "/api/admin/dashboard-pages",
            json={"load_case_id": load_case_id, "name": "사용자 분석 A", "description": "첫 분석"},
        )
        assert first.status_code == 201, first.text
        first_id = first.json()["id"]
        created_ids.append(first_id)
        assert first.json()["page"] == {
            "kind": "analysis_page",
            "analysis_key": "custom",
            "status": "draft",
            "display_order": 100,
            "is_system": False,
        }
        assert first_id not in {item["id"] for item in client.get("/api/dashboard-pages", params={"load_case_id": load_case_id}).json()}
        assert first_id in {item["id"] for item in client.get("/api/admin/dashboard-pages", params={"load_case_id": load_case_id}).json()}
        duplicate = client.post(
            "/api/admin/dashboard-pages",
            json={"load_case_id": load_case_id, "name": " 사용자 분석 A ", "description": "중복"},
        )
        assert duplicate.status_code == 409
        assert client.patch(f"/api/admin/dashboard-pages/{first_id}", json={"status": "published"}).status_code == 422

        dashboard = client.get(f"/api/dashboards/{first_id}").json()
        dashboard["widgets"] = [
            {"id": "kpi-one", "type": "kpi", "title": "최대 응력", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {"variableId": "top_edge_max_stress"}}
        ]
        saved = client.put(f"/api/dashboards/{first_id}", json=dashboard)
        assert saved.status_code == 200, saved.text
        widget_version = saved.json()["version"]

        published = client.patch(
            f"/api/admin/dashboard-pages/{first_id}",
            json={"name": "사용자 분석 A 게시", "description": "게시 설명", "status": "published"},
        )
        assert published.status_code == 200, published.text
        assert published.json()["page"]["status"] == "published"
        assert first_id in {item["id"] for item in client.get("/api/dashboard-pages", params={"load_case_id": load_case_id}).json()}

        immutable = client.get(f"/api/dashboards/{first_id}").json()
        immutable["name"] = "일반 저장 이름 변경"
        assert client.put(f"/api/dashboards/{first_id}", json=immutable).status_code == 409

        changed = client.get(f"/api/dashboards/{first_id}").json()
        changed["widgets"].append(
            {"id": "note-one", "type": "note", "title": "의견", "x": 3, "y": 0, "w": 4, "h": 3, "settings": {}}
        )
        assert client.put(f"/api/dashboards/{first_id}", json=changed).status_code == 200
        restored = client.post(f"/api/dashboards/{first_id}/restore/{widget_version}")
        assert restored.status_code == 200, restored.text
        restored_dashboard = client.get(f"/api/dashboards/{first_id}").json()
        assert restored_dashboard["name"] == "사용자 분석 A 게시"
        assert restored_dashboard["description"] == "게시 설명"
        assert restored_dashboard["page"]["status"] == "published"
        assert [item["id"] for item in restored_dashboard["widgets"]] == ["kpi-one"]

        clone = client.post(f"/api/dashboards/{first_id}/clone", json={"name": "페이지 아닌 복제본", "description": "복제"})
        assert clone.status_code == 201
        clone_id = clone.json()["id"]
        assert client.get(f"/api/dashboards/{clone_id}").json().get("page") is None
        assert clone_id not in {item["id"] for item in client.get("/api/admin/dashboard-pages", params={"load_case_id": load_case_id}).json()}

        second = client.post(
            "/api/admin/dashboard-pages",
            json={"load_case_id": load_case_id, "name": "사용자 분석 B", "description": "둘째"},
        )
        assert second.status_code == 201
        second_id = second.json()["id"]
        created_ids.append(second_id)
        second_dashboard = client.get(f"/api/dashboards/{second_id}").json()
        second_dashboard["widgets"] = [{"id": "table-one", "type": "result_table", "title": "결과", "x": 0, "y": 0, "w": 12, "h": 4, "settings": {}}]
        assert client.put(f"/api/dashboards/{second_id}", json=second_dashboard).status_code == 200
        assert client.patch(f"/api/admin/dashboard-pages/{second_id}", json={"status": "published"}).status_code == 200

        active_custom_ids = [
            item["id"]
            for item in client.get("/api/admin/dashboard-pages", params={"load_case_id": load_case_id}).json()
            if item["page"]["analysis_key"] == "custom"
        ]
        desired_order = list(reversed(active_custom_ids))
        reordered = client.put("/api/admin/dashboard-pages/order", json={"load_case_id": load_case_id, "page_ids": desired_order})
        assert reordered.status_code == 200, reordered.text
        ordered = [
            item["id"]
            for item in client.get("/api/admin/dashboard-pages", params={"load_case_id": load_case_id}).json()
            if item["page"]["analysis_key"] == "custom"
        ]
        assert ordered == desired_order

        invalid = client.get(f"/api/dashboards/{second_id}").json()
        invalid["widgets"] = [{"id": "bad", "type": "unknown-widget", "title": "오류", "x": 0, "y": 0, "w": 3, "h": 2}]
        assert client.put(f"/api/dashboards/{second_id}", json=invalid).status_code == 422
        invalid["widgets"] = [
            {"id": "same", "type": "kpi", "title": "A", "x": 0, "y": 0, "w": 3, "h": 2},
            {"id": "same", "type": "note", "title": "B", "x": 3, "y": 0, "w": 3, "h": 2},
        ]
        assert client.put(f"/api/dashboards/{second_id}", json=invalid).status_code == 422
        invalid["widgets"] = [{"id": "overflow", "type": "kpi", "title": "오류", "x": 10, "y": 0, "w": 3, "h": 2}]
        assert client.put(f"/api/dashboards/{second_id}", json=invalid).status_code == 422
        invalid["widgets"] = [{"id": "workflow", "type": "workflow", "title": "업무 흐름", "x": 0, "y": 0, "w": 12, "h": 5}]
        assert client.put(f"/api/dashboards/{second_id}", json=invalid).status_code == 422

        assert client.patch("/api/admin/dashboard-pages/dashboard-drop-default", json={"status": "archived"}).status_code == 409
        assert client.delete("/api/admin/dashboard-pages/dashboard-run-comparison-default", params={"load_case_id": load_case_id}).status_code == 409
        assert client.delete(f"/api/admin/dashboard-pages/{clone_id}", params={"load_case_id": load_case_id}).status_code == 409
        assert client.delete(f"/api/admin/dashboard-pages/{second_id}", params={"load_case_id": "loadcase-clamp-left-001"}).status_code == 409
        assert client.patch(f"/api/admin/dashboard-pages/{first_id}", json={"status": "archived"}).status_code == 200
        assert first_id not in {item["id"] for item in client.get("/api/dashboard-pages", params={"load_case_id": load_case_id}).json()}
        archived = client.get("/api/admin/dashboard-pages", params={"load_case_id": load_case_id, "include_archived": True}).json()
        assert next(item for item in archived if item["id"] == first_id)["page"]["status"] == "archived"
        deleted = client.delete(f"/api/admin/dashboard-pages/{second_id}", params={"load_case_id": load_case_id})
        assert deleted.status_code == 200
        assert deleted.json() == {"status": "deleted", "id": second_id, "load_case_id": load_case_id}
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM dashboard_versions WHERE dashboard_id = ?", [second_id]).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM dashboards WHERE id = ?", [second_id]).fetchone()[0] == 0
        reused = client.post(
            "/api/admin/dashboard-pages",
            json={"load_case_id": load_case_id, "name": "사용자 분석 B", "description": "삭제 후 이름 재사용"},
        )
        assert reused.status_code == 201
        created_ids.append(reused.json()["id"])

    with connect() as conn:
        if clone_id:
            conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [clone_id])
            conn.execute("DELETE FROM dashboards WHERE id = ?", [clone_id])
        for dashboard_id in created_ids:
            conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [dashboard_id])
            conn.execute("DELETE FROM dashboards WHERE id = ?", [dashboard_id])


def test_analysis_page_role_access_and_editor_published_widget_edit(monkeypatch):
    initialize_database()
    suffix = uuid4().hex[:8]
    password = "correct-horse-battery-staple"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    users = {
        role: (f"user-{role}-{suffix}", f"{role}-{suffix}")
        for role in ("viewer", "editor", "admin")
    }
    with connect() as conn:
        for role, (user_id, username) in users.items():
            conn.execute(
                """
                INSERT INTO users
                    (id, username, password_hash, display_name, legacy_role, is_active,
                     created_at, updated_at, account_status, is_global_admin)
                VALUES (?, ?, ?, ?, ?, true, ?, ?, 'ACTIVE', ?)
                """,
                [user_id, username, hash_password(password), username, role, now, now, role == "admin"],
            )
    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters")
    page_id: str | None = None
    try:
        with TestClient(app) as client:
            headers = {}
            for role, (_, username) in users.items():
                login = client.post("/api/auth/login", json={"username": username, "password": password})
                assert login.status_code == 200
                headers[role] = {"Authorization": f"Bearer {login.json()['access_token']}"}

            created = client.post(
                "/api/admin/dashboard-pages",
                headers=headers["admin"],
                json={"load_case_id": "loadcase-drop-bottom-001", "name": f"권한 분석 {suffix}", "description": "권한 검증"},
            )
            assert created.status_code == 201
            page_id = created.json()["id"]
            assert client.get(f"/api/dashboards/{page_id}", headers=headers["viewer"]).status_code == 403
            assert client.get(f"/api/dashboards/{page_id}/versions", headers=headers["viewer"]).status_code == 403
            assert client.get(f"/api/dashboards/{page_id}/versions/1", headers=headers["editor"]).status_code == 403
            assert client.get(f"/api/dashboards/{page_id}", headers=headers["admin"]).status_code == 200
            assert client.get(f"/api/dashboards/{page_id}/versions/1", headers=headers["admin"]).status_code == 200

            draft = client.get(f"/api/dashboards/{page_id}", headers=headers["admin"]).json()
            draft["widgets"] = [{"id": "kpi", "type": "kpi", "title": "KPI", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {}}]
            assert client.put(f"/api/dashboards/{page_id}", headers=headers["editor"], json=draft).status_code == 403
            assert client.put(f"/api/dashboards/{page_id}", headers=headers["admin"], json=draft).status_code == 200
            assert client.patch(f"/api/admin/dashboard-pages/{page_id}", headers=headers["admin"], json={"status": "published"}).status_code == 200

            published = client.get(f"/api/dashboards/{page_id}", headers=headers["editor"]).json()
            assert client.get(f"/api/dashboards/{page_id}/versions", headers=headers["viewer"]).status_code == 200
            published["widgets"][0]["title"] = "편집자 수정"
            # Legacy editor maps to the power role; dashboard editing is now
            # reserved for a project admin or global admin.
            assert client.put(f"/api/dashboards/{page_id}", headers=headers["editor"], json=published).status_code == 403
            assert client.put(f"/api/dashboards/{page_id}", headers=headers["viewer"], json=published).status_code == 403
            assert client.patch(f"/api/admin/dashboard-pages/{page_id}", headers=headers["editor"], json={"name": "금지"}).status_code == 403
            assert client.delete(f"/api/admin/dashboard-pages/{page_id}", headers=headers["editor"], params={"load_case_id": "loadcase-drop-bottom-001"}).status_code == 403
            assert client.delete(f"/api/dashboards/{page_id}/versions/1", headers=headers["editor"]).status_code == 403
    finally:
        with connect() as conn:
            if page_id:
                conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [page_id])
                conn.execute("DELETE FROM dashboards WHERE id = ?", [page_id])
            if database_settings().backend == "duckdb":
                conn.execute("DELETE FROM audit_events WHERE username LIKE ?", [f"%-{suffix}"])
            conn.execute("DELETE FROM project_memberships WHERE user_id IN (?, ?, ?)", [*(user_id for user_id, _ in users.values())])
            conn.execute("DELETE FROM users WHERE username LIKE ?", [f"%-{suffix}"])


def test_analysis_page_delete_rolls_back_versions_when_body_delete_fails(monkeypatch):
    initialize_database()
    load_case_id = "loadcase-drop-bottom-001"
    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post(
            "/api/admin/dashboard-pages",
            json={"load_case_id": load_case_id, "name": "삭제 롤백 검증", "description": "트랜잭션"},
        )
        assert created.status_code == 201
        dashboard_id = created.json()["id"]

        def fail_after_version_delete(conn, target_id: str):
            conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [target_id])
            raise RuntimeError("forced delete failure")

        monkeypatch.setattr(main_module, "_delete_analysis_page_records", fail_after_version_delete)
        response = client.delete(f"/api/admin/dashboard-pages/{dashboard_id}", params={"load_case_id": load_case_id})
        assert response.status_code == 500
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM dashboard_versions WHERE dashboard_id = ?", [dashboard_id]).fetchone()[0] == 1


def test_dashboard_version_detail_logical_delete_protections_and_monotonic_numbering():
    initialize_database()
    clone_id: str | None = None
    try:
        with TestClient(app) as client:
            cloned = client.post(
                "/api/dashboards/dashboard-drop-default/clone",
                json={"name": "버전 삭제 검증", "description": "논리 삭제와 단조 증가"},
            )
            assert cloned.status_code == 201, cloned.text
            clone_id = cloned.json()["id"]

            initial_versions = client.get(f"/api/dashboards/{clone_id}/versions")
            assert initial_versions.status_code == 200
            assert [item["version"] for item in initial_versions.json()] == [1]
            first_detail = client.get(f"/api/dashboards/{clone_id}/versions/1")
            assert first_detail.status_code == 200
            assert first_detail.json()["definition"]["name"] == "버전 삭제 검증"
            assert first_detail.json()["is_valid"] is True

            for index in range(2, 5):
                definition = client.get(f"/api/dashboards/{clone_id}").json()
                definition["description"] = f"저장 버전 {index}"
                saved = client.put(f"/api/dashboards/{clone_id}", json=definition)
                assert saved.status_code == 200, saved.text
                assert saved.json()["version"] == index

            assert client.delete(f"/api/dashboards/{clone_id}/versions/4").status_code == 409
            deleted = client.delete(f"/api/dashboards/{clone_id}/versions/2")
            assert deleted.status_code == 200, deleted.text
            assert deleted.json() == {"status": "invalidated", "dashboard_id": clone_id, "version": 2}

            valid_versions = client.get(f"/api/dashboards/{clone_id}/versions").json()
            assert [item["version"] for item in valid_versions] == [4, 3, 1]
            all_versions = client.get(
                f"/api/dashboards/{clone_id}/versions",
                params={"include_invalid": True},
            ).json()
            assert [item["version"] for item in all_versions] == [4, 3, 2, 1]
            assert next(item for item in all_versions if item["version"] == 2)["is_valid"] is False
            assert client.get(f"/api/dashboards/{clone_id}/versions/2").status_code == 404
            invalid_detail = client.get(
                f"/api/dashboards/{clone_id}/versions/2",
                params={"include_invalid": True},
            )
            assert invalid_detail.status_code == 200
            assert invalid_detail.json()["is_valid"] is False
            assert client.delete(f"/api/dashboards/{clone_id}/versions/2").status_code == 404

            assert client.delete(f"/api/dashboards/{clone_id}/versions/3").status_code == 200
            assert client.delete(f"/api/dashboards/{clone_id}/versions/1").status_code == 409
            assert client.delete(f"/api/dashboards/{clone_id}/versions/999").status_code == 404
            assert client.delete("/api/dashboards/dashboard-drop-default/versions/1").status_code == 409

            current = client.get(f"/api/dashboards/{clone_id}").json()
            with connect() as conn:
                conn.execute(
                    "INSERT INTO dashboard_versions VALUES (?, 9, ?, '삭제 이력', current_timestamp, false)",
                    [clone_id, json.dumps(current, ensure_ascii=False)],
                )
            current["description"] = "삭제 번호를 건너뛴 저장"
            monotonic = client.put(f"/api/dashboards/{clone_id}", json=current)
            assert monotonic.status_code == 200, monotonic.text
            assert monotonic.json()["version"] == 10
            assert client.get(f"/api/dashboards/{clone_id}").json()["version"] == 10
    finally:
        if clone_id:
            with connect() as conn:
                conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [clone_id])
                conn.execute("DELETE FROM dashboards WHERE id = ?", [clone_id])


def test_drop_video_example_adapter_is_scoped_paginated_and_safe():
    initialize_database()
    with TestClient(app) as client:
        first_page = client.get(
            "/api/load-cases/loadcase-drop-bottom-001/drop-videos",
            params={"page": 1, "page_size": 7},
        )
        assert first_page.status_code == 200, first_page.text
        payload = first_page.json()
        assert payload["source"] == "DATABASE"
        assert payload["demo_only"] is True
        assert payload["evaluation_source"] == "SYNTHETIC_DEMO"
        assert payload["contract_version"] == 1
        assert payload["summary"] == {
            "total_scenes": 20,
            "pass_count": 9,
            "fail_count": 11,
            "open_cell": {"pass_count": 13, "fail_count": 7, "threshold": 75.0, "unit": "MPa"},
            "chassis_rear": {"pass_count": 12, "fail_count": 8, "threshold": 5.0, "unit": "mm"},
        }
        assert payload["pagination"] == {
            "page": 1,
            "page_size": 7,
            "total_items": 20,
            "total_pages": 3,
            "has_previous": False,
            "has_next": True,
        }
        assert len(payload["videos"]) == 7
        assert payload["videos"][0]["scene_name"] == "기준 낙하 해석"
        assert payload["videos"][1]["scene_name"] == "낙하 비교 Scene 01"
        assert all(item["codec"] == "h264" and item["fast_start"] is True for item in payload["videos"])
        assert payload["videos"][0]["drop_direction"] is None
        assert payload["videos"][0]["drop_condition"] is None
        assert payload["videos"][0]["evaluation"]["overall_verdict"] == "PASS"
        for video in payload["videos"]:
            evaluation = video["evaluation"]
            assert evaluation["overall_verdict"] == (
                "PASS" if evaluation["open_cell"]["verdict"] == evaluation["chassis_rear"]["verdict"] == "PASS" else "FAIL"
            )
            for subsystem in ("open_cell", "chassis_rear"):
                metric = evaluation[subsystem]
                assert max(metric["metrics"].values()) == metric["critical_value"]

        last_page = client.get(
            "/api/load-cases/loadcase-drop-bottom-001/drop-videos",
            params={"page": 3, "page_size": 7},
        ).json()
        assert len(last_page["videos"]) == 6
        assert last_page["summary"] == payload["summary"]
        assert last_page["pagination"]["has_previous"] is True
        assert last_page["pagination"]["has_next"] is False

        boundary = next(item for item in first_page.json()["videos"] if item["sort_order"] == 7)
        assert boundary["evaluation"]["open_cell"] | {"metrics": {}} == {
            "critical_value": 75.0,
            "threshold": 75.0,
            "unit": "MPa",
            "verdict": "PASS",
            "metrics": {},
        }
        assert boundary["evaluation"]["chassis_rear"]["critical_value"] == 5.0
        assert boundary["evaluation"]["chassis_rear"]["verdict"] == "FAIL"
        assert boundary["evaluation"]["overall_verdict"] == "FAIL"

        unrelated = client.get("/api/load-cases/loadcase-clamp-left-001/drop-videos").json()
        assert unrelated["pagination"]["total_items"] == 0
        assert unrelated["summary"]["total_scenes"] == 0
        assert unrelated["videos"] == []
        assert client.get("/api/load-cases/missing/drop-videos").status_code == 404
        assert client.get("/api/drop-videos/not-allowlisted/content").status_code == 404

        content = client.get(payload["videos"][0]["video_url"], headers={"Range": "bytes=0-31"})
        assert content.status_code == 206
        assert content.headers["content-type"].startswith("video/mp4")
        assert content.headers["content-range"].startswith("bytes 0-31/")
        assert len(content.content) == 32


def test_workflow_step_full_edit_and_validation():
    initialize_database()
    with TestClient(app) as client:
        workflow = next(item for item in client.get("/api/workflows").json() if item["work_plan"] is None and item["steps"])
        original = workflow["steps"][0]
        payload = {
            "name": f'{original["name"]} 편집',
            "status": "IN_PROGRESS",
            "owner": "워크플로 편집자",
            "progress": 55,
            "is_optional": True,
            "note": "편집 기능 회귀 검증",
        }
        try:
            updated = client.patch(f'/api/workflow-steps/{original["id"]}', json=payload)
            assert updated.status_code == 200, updated.text
            expected = {key: value for key, value in payload.items() if key != "owner"}
            assert all(updated.json()[key] == value for key, value in expected.items())
            assert updated.json()["owner"] == original["owner"]
            refreshed = client.get("/api/workflows").json()
            saved = next(step for item in refreshed for step in item["steps"] if step["id"] == original["id"])
            assert all(saved[key] == value for key, value in expected.items())
            assert saved["owner"] == original["owner"]
            invalid = client.patch(f'/api/workflow-steps/{original["id"]}', json={**payload, "progress": 101})
            assert invalid.status_code == 422
        finally:
            client.patch(f'/api/workflow-steps/{original["id"]}', json={
                "name": original["name"],
                "status": original["status"],
                "owner": original["owner"],
                "progress": original["progress"],
                "is_optional": original["is_optional"],
                "note": original.get("note") or "",
            })


def test_workflow_steps_replace_supports_add_delete_and_reorder():
    initialize_database()
    request_id = f"request-legacy-replace-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO analysis_requests
                (id, project_id, title, status, owner, requested_at, due_at, overall_note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [request_id, "project-tv-001", "레거시 단계 편집 API 검증", "IN_PROGRESS", "워크플로 편집자", now, now + timedelta(days=7), "테스트 후 삭제"],
        )
        for sequence_no, name in enumerate(("레거시 첫 단계", "레거시 두 번째 단계"), start=1):
            conn.execute(
                """
                INSERT INTO request_steps
                    (id, request_id, sequence_no, name, status, owner, planned_start, planned_end,
                     actual_start, actual_end, progress, blocked_reason, note, is_optional)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, ?, false)
                """,
                [f"step-{uuid4().hex[:12]}", request_id, sequence_no, name, "IN_PROGRESS" if sequence_no == 1 else "WAITING", "워크플로 편집자", now, now + timedelta(days=1), 10 if sequence_no == 1 else 0, "legacy"],
            )
    with TestClient(app) as client:
        workflow = next(item for item in client.get("/api/workflows").json() if item["request"]["id"] == request_id)
        first, second = workflow["steps"][:2]
        with connect() as conn:
            assignee = conn.execute(
                """
                SELECT users.id FROM users
                JOIN project_memberships memberships ON memberships.user_id=users.id
                WHERE memberships.project_id='project-tv-001' AND users.account_status='ACTIVE'
                ORDER BY users.id LIMIT 1
                """
            ).fetchone()
        assert assignee
        payload = {"steps": [
            {"id": second["id"], "name": "순서가 바뀐 두 번째 단계", "status": "IN_PROGRESS", "owner_user_id": assignee[0], "progress": 45, "is_optional": False, "note": "앞으로 이동"},
            {"id": first["id"], "name": first["name"], "status": first["status"], "owner_user_id": assignee[0], "progress": first["progress"], "is_optional": first["is_optional"], "note": first.get("note") or ""},
            {"id": None, "name": "새 승인 단계", "status": "WAITING", "owner_user_id": assignee[0], "progress": 0, "is_optional": True, "note": "신규 추가"},
        ]}
        try:
            replaced = client.put(f"/api/requests/{request_id}/workflow-steps", json=payload)
            assert replaced.status_code == 200, replaced.text
            steps = replaced.json()
            assert len(steps) == 3
            assert [step["sequence_no"] for step in steps] == [1, 2, 3]
            assert steps[0]["id"] == second["id"]
            assert steps[2]["name"] == "새 승인 단계"
            assert steps[2]["id"] not in {first["id"], second["id"]}
            assert client.put(f"/api/requests/{request_id}/workflow-steps", json={"steps": []}).status_code == 422
        finally:
            with connect() as conn:
                conn.execute("DELETE FROM request_steps WHERE request_id = ?", [request_id])
                conn.execute("DELETE FROM analysis_requests WHERE id = ?", [request_id])


def test_report_layout_crud_and_version_history():
    initialize_database()
    with TestClient(app) as client:
        layouts = client.get("/api/report-layouts")
        assert layouts.status_code == 200
        assert len(layouts.json()) >= 3
        assert {item["definition"]["coverVariant"] for item in layouts.json()} >= {"balanced", "executive", "evidence"}
        payload = {
            "name": "테스트 보고서",
            "description": "변수 배치 회귀 검증",
            "definition": {
                "id": "client-placeholder",
                "name": "테스트 보고서",
                "description": "변수 배치 회귀 검증",
                "version": 1,
                "coverVariant": "balanced",
                "accentColor": "1898D5",
                "sectionOrder": ["scalar", "series", "media"],
                "variablePlacements": [{"variableKey": "top_edge_max_stress", "presentation": "table", "order": 0}],
                "includeMedia": False,
                "slideMaster": {"backgroundColor": "F4F8FB", "design": "header-band", "accentColor": "1898D5"},
                "canvas": {"columns": 32, "rows": 18, "widthInches": 13.333, "heightInches": 7.5},
                "slides": [{"id": "cover", "name": "표지", "kind": "cover", "repeat": "none", "style": {"useMaster": False, "backgroundColor": "FFFFFF", "design": "split", "accentColor": "FF9948"}, "elements": [{"id": "title", "type": "title", "label": "제목", "text": "직접 입력한 제목", "x": 1, "y": 1, "w": 20, "h": 2, "z": 1, "binding": {"source": "static"}}]}],
                "templateSource": "native",
                "templateBindings": {},
            },
            "updated_by": "테스트 편집자",
        }
        created = client.post("/api/report-layouts", json=payload)
        assert created.status_code == 201, created.text
        layout_id = created.json()["id"]
        payload["definition"]["accentColor"] = "FF9948"
        saved = client.put(f"/api/report-layouts/{layout_id}", json=payload)
        assert saved.status_code == 200
        assert saved.json()["version"] == 2
        assert saved.json()["definition"]["slideMaster"]["design"] == "header-band"
        assert saved.json()["definition"]["slides"][0]["elements"][0]["text"] == "직접 입력한 제목"
        versions = client.get(f"/api/report-layouts/{layout_id}/versions").json()
        assert [item["version"] for item in versions] == [2, 1]
        historical = client.get(f"/api/report-layouts/{layout_id}/versions/1")
        assert historical.status_code == 200
        assert historical.json()["definition"]["accentColor"] == "1898D5"
        assert client.delete(f"/api/report-layouts/{layout_id}").status_code == 200
        assert client.delete("/api/report-layouts/report-layout-standard").status_code == 409


def test_report_layout_accepts_more_than_32_content_slides():
    initialize_database()
    slides = [
        {
            "id": f"content-slide-{index}",
            "name": f"콘텐츠 {index}",
            "kind": "custom",
            "repeat": "none",
            "elements": [{
                "id": f"content-element-{index}", "type": "text", "label": f"콘텐츠 {index}",
                "x": 1, "y": 1, "w": 30, "h": 16, "z": 1,
                "binding": {"source": "content", "contentId": f"widget:{index}", "key": "body"},
            }],
        }
        for index in range(40)
    ]
    payload = {
        "name": "40장 콘텐츠 보고서",
        "description": "분석 페이지 위젯 수에 따른 슬라이드 제한 제거 검증",
        "definition": {
            "id": "client-placeholder", "name": "40장 콘텐츠 보고서", "description": "슬라이드 제한 검증", "version": 1,
            "coverVariant": "balanced", "accentColor": "1898D5", "sectionOrder": ["series", "scalar", "media"],
            "variablePlacements": [], "includeMedia": False,
            "canvas": {"columns": 32, "rows": 18, "widthInches": 13.333, "heightInches": 7.5},
            "slides": slides, "templateSource": "native", "templateBindings": {},
            "sourceScope": {"kind": "analysis_page", "dashboardId": "dashboard-drop-default", "loadCaseId": "loadcase-drop-bottom-001"},
            "contentMode": "one-per-slide",
        },
        "updated_by": "테스트 편집자",
    }
    layout_id = None
    try:
        with TestClient(app) as client:
            created = client.post("/api/report-layouts", json=payload)
            assert created.status_code == 201, created.text
            layout_id = created.json()["id"]
            assert len(created.json()["definition"]["slides"]) == 40
            assert client.delete(f"/api/report-layouts/{layout_id}").status_code == 200
            layout_id = None
    finally:
        if layout_id:
            with connect() as conn:
                conn.execute("DELETE FROM report_layout_versions WHERE layout_id = ?", [layout_id])
                conn.execute("DELETE FROM report_layouts WHERE id = ?", [layout_id])


def _minimal_tagged_pptx() -> bytes:
    buffer = io.BytesIO()
    presentation = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'''
    slide = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="2" name="VAR:top_edge_max_stress"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="914400" y="914400"/><a:ext cx="3657600" cy="914400"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{{variable:top_edge_max_stress}}</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'''
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/slides/slide1.xml", slide)
    return buffer.getvalue()


def test_pptx_template_upload_placeholder_inspection_and_render():
    initialize_database()
    template_id = None
    with TestClient(app) as client:
        response = client.post("/api/report-templates", json={"name": "태그 템플릿", "filename": "tagged.pptx", "content_base64": base64.b64encode(_minimal_tagged_pptx()).decode("ascii"), "updated_by": "테스트 편집자"})
        assert response.status_code == 201, response.text
        template = response.json()
        template_id = template["id"]
        assert template["slide_count"] == 1
        assert template["definition"]["placeholders"][0]["token"] == "variable:top_edge_max_stress"
        rendered = client.post(f"/api/report-templates/{template_id}/render", json={"replacements": {"variable:top_edge_max_stress": "72.50 MPa (PASS)"}, "filename": "해석 결과.pptx"})
        assert rendered.status_code == 200
        with zipfile.ZipFile(io.BytesIO(rendered.content)) as archive:
            assert "72.50 MPa (PASS)" in archive.read("ppt/slides/slide1.xml").decode("utf-8")
        assert client.delete(f"/api/report-templates/{template_id}").status_code == 200
    if template_id:
        with connect() as conn:
            conn.execute("DELETE FROM report_template_assets WHERE id=?", [template_id])


def test_media_metadata_policy_rejects_unsafe_paths_and_formats():
    validate_media_metadata("MODEL_3D", "results/tv/lightweight.glb", 1024)
    for args in [
        ("MODEL_3D", "../original.fem", 100),
        ("MODEL_3D", "results/heavy.obj", 100),
        ("VIDEO", "https://example.com/result.mp4", 100),
        ("CONTOUR_IMAGE", "results/plot.png", 30 * 1024 * 1024),
    ]:
        try:
            validate_media_metadata(*args)
            assert False, f"unsafe metadata accepted: {args}"
        except ValueError:
            pass


def test_health_and_seeded_overview():
    initialize_database()
    with TestClient(app) as client:
        expected_backend = os.getenv("ANALYSIS_DB_BACKEND", "duckdb").strip().lower()
        assert client.get("/api/health").json() == {"status": "ok", "database_backend": expected_backend}
        asset = client.get("/api/assets/media-contour-001")
        assert asset.status_code == 200
        assert asset.headers["content-type"].startswith("image/svg+xml")
        response = client.get("/api/load-cases/loadcase-drop-bottom-001/overview")
        assert response.status_code == 200
        payload = response.json()
        assert payload["load_case"]["analysis_type"] == "DROP"
        assert payload["overall_verdict"] == "FAIL"
        open_cell_results = [item for item in payload["scalar_results"] if not item["variable_key"].startswith("chassis_rear_")]
        assert len(open_cell_results) == 4
        assert len(payload["time_series"]) == 404
        product_values = {item["category"]: item["value_text"] for item in payload["product_information"]}
        assert product_values["SPEC"] == "65 inch"
        assert product_values["MODEL"] == "ORION-65-OLED-C"
        assert payload["analysis_verdicts"]["open_cell"] == "FAIL"
        assert payload["analysis_verdicts"]["chassis_rear"] == "FAIL"
        chassis_results = [item for item in payload["scalar_results"] if item["variable_key"].startswith("chassis_rear_")]
        assert len(chassis_results) == 6


def test_run_comparison_trust_and_review_are_additive():
    initialize_database()
    annotation_id = None
    bookmark_id = None
    with TestClient(app) as client:
        runs = client.get("/api/load-cases/loadcase-drop-bottom-001/runs")
        assert runs.status_code == 200, runs.text
        run_ids = {item["id"] for item in runs.json()}
        assert {"run-drop-baseline-001", "run-drop-001"} <= run_ids

        comparison = client.get(
            "/api/load-cases/loadcase-drop-bottom-001/run-comparison",
            params={"baseline_run_id": "run-drop-baseline-001", "target_run_id": "run-drop-001", "variable_key": "bottom_edge_stress_time"},
        )
        assert comparison.status_code == 200, comparison.text
        payload = comparison.json()
        bottom = next(item for item in payload["scalar_comparison"] if item["variable_key"] == "bottom_edge_max_stress")
        assert bottom["change"] == "REGRESSION"
        assert bottom["baseline_verdict"] == "PASS"
        assert bottom["target_verdict"] == "FAIL"
        assert payload["summary"]["regression"] >= 1
        assert payload["time_series"]["variable_key"] == "bottom_edge_stress_time"
        assert payload["time_series"]["points"]

        trust = client.get("/api/analysis-runs/run-drop-001/trust")
        assert trust.status_code == 200, trust.text
        trust_payload = trust.json()
        assert trust_payload["metadata"]["source_checksum"]
        assert trust_payload["coverage"]["result_variables"] >= 10
        assert {item["code"] for item in trust_payload["checks"]} >= {"run_status", "source_trace", "catalog_mapping", "unit_consistency", "validation"}

        created = client.post(
            "/api/analysis-runs/run-drop-001/review-items",
            json={
                "title": "하단 엣지 회귀 확인",
                "body": "기준 Run 대비 허용 응력을 초과했습니다.",
                "variable_key": "bottom_edge_max_stress",
                "time_value": 14.6,
                "entity_type": None,
                "entity_id": None,
                "review_status": "OPEN",
                "created_by": "테스트 검토자",
            },
        )
        assert created.status_code == 201, created.text
        annotation_id = created.json()["id"]
        bookmark_id = created.json()["bookmark_id"]
        assert created.json()["review_status"] == "OPEN"
        assert any(item["id"] == annotation_id for item in client.get("/api/analysis-runs/run-drop-001/review-items").json())
        resolved = client.patch(f"/api/review-items/{annotation_id}", json={"review_status": "RESOLVED"})
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["review_status"] == "RESOLVED"
    if annotation_id and bookmark_id:
        with connect() as conn:
            conn.execute("DELETE FROM review_annotations WHERE id=?", [annotation_id])
            conn.execute("DELETE FROM result_bookmarks WHERE id=?", [bookmark_id])


def test_hierarchy_and_workflow():
    initialize_database()
    with TestClient(app) as client:
        load_cases = client.get("/api/requests/request-drop-001/load-cases").json()
        assert len(load_cases) == 1
        assert load_cases[0]["id"] == "loadcase-drop-bottom-001"
        workflow = client.get("/api/requests/request-drop-001/workflow").json()
        assert [step["name"] for step in workflow["steps"]] == [
            "CAD 작업",
            "해석 모델링",
            "HPC 수행",
            "결과 후처리",
            "해석 DB 저장",
            "오픈셀 파손 및 CHR 휨 평가 분석",
        ]
        assert workflow["progress"] == 33
        assert workflow["current_step"] == "HPC 수행"
        assert workflow["work_plan"]["scenario_name"] == "설계 신뢰성 검증"
        workflows = client.get("/api/workflows").json()
        assert len(workflows) >= 2
        assert {"DROP", "SIDE_CLAMP"} <= {item["request"]["category"] for item in workflows}
        clamp_overview = client.get("/api/load-cases/loadcase-clamp-left-001/overview").json()
        assert clamp_overview["analysis_verdicts"]["open_cell"] == "FAIL"
        assert clamp_overview["analysis_verdicts"]["chassis_rear"] == "FAIL"
        clamp_open_cell = [item for item in clamp_overview["scalar_results"] if not item["variable_key"].startswith("chassis_rear_")]
        assert len(clamp_open_cell) == 4
        assert len(clamp_overview["time_series"]) == 244


def test_chassis_threshold_is_admin_configurable():
    initialize_database()
    with TestClient(app) as client:
        thresholds = client.get("/api/projects/project-tv-001/quality-thresholds").json()
        assert thresholds[0]["criterion_key"] == "chassis_rear_permanent_deformation_mm"
        response = client.put(
            "/api/projects/project-tv-001/quality-thresholds/chassis_rear_permanent_deformation_mm",
            json={"threshold_double": 5.0, "updated_by": "관리자"},
        )
        assert response.status_code == 200
        assert response.json()["threshold_double"] == 5.0


def test_natural_language_preview_is_allowlisted():
    with TestClient(app) as client:
        response = client.post("/api/dashboard-commands/preview", json={"command": "응력-시간 그래프를 추가해"})
        assert response.json()["recognized"] is True
        improvement = client.post("/api/dashboard-commands/preview", json={"command": "패스/실패 카드를 맨 위 오른쪽으로 이동해"}).json()
        assert improvement["recognized"] is True
        assert improvement["proposal"]["action"] == "update_widgets"
        unsafe = client.post("/api/dashboard-commands/preview", json={"command": "데이터베이스를 삭제해"})
        assert unsafe.json()["recognized"] is False


def test_default_dashboard_has_persisted_widgets():
    initialize_database()
    with TestClient(app) as client:
        response = client.get("/api/dashboards/dashboard-drop-default")
        assert response.status_code == 200
        assert any(item["id"] == "dashboard-drop-default" for item in client.get("/api/dashboards").json())
        widget_types = {widget["type"] for widget in response.json()["widgets"]}
        assert {"open_cell_map", "verdict", "edge_bar", "time_series", "result_table"} <= widget_types
        chassis = client.get("/api/dashboards/dashboard-chassis-default")
        assert chassis.status_code == 200
        assert {"chassis_summary", "chassis_diagram", "chassis_bar", "chassis_table"} <= {widget["type"] for widget in chassis.json()["widgets"]}
        variables = client.get("/api/load-cases/loadcase-drop-bottom-001/variables").json()
        assert variables and all("allowed_aggregations" in item and "description" in item for item in variables)
        templates = client.get("/api/automation-templates").json()
        assert {item["analysis_type"] for item in templates} >= {"DROP", "SIDE_CLAMP"}


def test_variable_catalog_crud_persists_and_protects_dashboard_bindings():
    initialize_database()
    load_case_id = "loadcase-drop-bottom-001"
    variable_key = "custom_frame_energy"
    dashboard_id = "dashboard-variable-reference-test"
    create_payload = {
        "variable_key": variable_key,
        "display_name": "프레임 흡수 에너지",
        "data_type": "NUMBER",
        "unit": "J",
        "description": "사용자 정의 에너지 결과",
        "threshold": 120.0,
        "allowed_widgets": ["kpi", "edge_bar", "result_table"],
        "allowed_aggregations": ["MAX", "AVG", "LATEST"],
        "result_group": "CUSTOM",
        "updated_by": "테스트 관리자",
    }
    with TestClient(app) as client:
        created = client.post(f"/api/load-cases/{load_case_id}/variables", json=create_payload)
        assert created.status_code == 201, created.text
        assert created.json()["id"] == variable_key
        assert created.json()["has_data"] is False

        preview = client.post(
            f"/api/load-cases/{load_case_id}/results/import",
            json={
                "filename": "custom-variable.csv",
                "content": "record_type,variable_key,display_name,value,unit,threshold,time,time_unit,value_unit\nscalar,custom_frame_energy,프레임 흡수 에너지,96.4,J,120,,,,\n",
                "author": "테스트 관리자",
                "validate_only": True,
            },
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["results"][0]["variable_key"] == variable_key

        updated = client.put(
            f"/api/load-cases/{load_case_id}/variables/{variable_key}",
            json={**{key: value for key, value in create_payload.items() if key not in {"variable_key", "data_type"}}, "display_name": "프레임 에너지", "threshold": 130.0},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["display_name"] == "프레임 에너지"
        assert updated.json()["threshold"] == 130.0
        assert any(item["id"] == variable_key for item in client.get(f"/api/load-cases/{load_case_id}/variables").json())

        with connect() as conn:
            stored = conn.execute(
                "SELECT display_name, threshold_double, is_active FROM variable_definitions WHERE load_case_id=? AND variable_key=?",
                [load_case_id, variable_key],
            ).fetchone()
            assert stored == ("프레임 에너지", 130.0, True)
            definition = json.dumps({"id": dashboard_id, "name": "test", "description": "", "widgets": [{"id": "test-widget", "type": "kpi", "title": "test", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {"variableId": variable_key}}]})
            conn.execute("INSERT INTO dashboards VALUES (?, 'project-tv-001', 'request-drop-001', ?, 'test', '', 1, ?, now())", [dashboard_id, load_case_id, definition])

        blocked = client.delete(f"/api/load-cases/{load_case_id}/variables/{variable_key}")
        assert blocked.status_code == 409

        with connect() as conn:
            conn.execute("DELETE FROM dashboards WHERE id = ?", [dashboard_id])
        deleted = client.delete(f"/api/load-cases/{load_case_id}/variables/{variable_key}")
        assert deleted.status_code == 200
        assert all(item["id"] != variable_key for item in client.get(f"/api/load-cases/{load_case_id}/variables").json())

    with connect() as conn:
        conn.execute("DELETE FROM variable_definitions WHERE load_case_id=? AND variable_key=?", [load_case_id, variable_key])


def test_create_project_request_and_load_case():
    created_project = None
    created_request = None
    created_load_case = None
    created_run = None
    with TestClient(app) as client:
        project_response = client.post(
            "/api/projects",
            json={"name": "등록 API 검증 프로젝트", "product_name": "Test TV", "manufacturer": "Test Display", "display_size_inch": 55, "description": "test"},
        )
        assert project_response.status_code == 201
        created_project = project_response.json()["id"]
        thresholds = client.get(f"/api/projects/{created_project}/quality-thresholds").json()
        assert {item["criterion_key"] for item in thresholds} == {
            "chassis_rear_permanent_deformation_mm",
            "open_cell_stress_mpa",
        }
        with connect() as conn:
            metadata = {row[0]: row[1] for row in conn.execute("SELECT category, value_text FROM product_information WHERE project_id = ?", [created_project]).fetchall()}
            assignee = conn.execute(
                """
                SELECT users.id FROM users
                JOIN project_memberships memberships ON memberships.user_id=users.id
                WHERE memberships.project_id=? AND users.account_status='ACTIVE'
                ORDER BY users.id LIMIT 1
                """,
                [created_project],
            ).fetchone()
        assert metadata == {"MODEL": "Test TV", "MANUFACTURER": "Test Display", "SPEC": "55 inch"}
        assert assignee
        request_response = client.post(
            f"/api/projects/{created_project}/requests",
            json={
                "title": "Side Clamp 검증 의뢰",
                "owner_user_id": assignee[0],
                "due_in_days": 5,
                "source_type": "DEPARTMENT_HEAD",
                "source_reference": "시험해석팀",
                "requested_by": "시험해석팀장",
            },
        )
        assert request_response.status_code == 201
        created_request = request_response.json()["id"]
        load_case_response = client.post(
            f"/api/requests/{created_request}/load-cases",
            json={"name": "0.40 MPa Side Clamp", "analysis_type": "SIDE_CLAMP", "parameters": {"pressure_mpa": 0.4}},
        )
        assert load_case_response.status_code == 201
        created_load_case = load_case_response.json()["id"]
        created_workflow = client.get(f"/api/requests/{created_request}/workflow").json()
        assert len(created_workflow["steps"]) == 6
        assert created_workflow["current_step"] == "CAD 작업"
        assert created_workflow["request"]["status"] == "READY"
        result_content = json.dumps({
            "solver": "Test Solver",
            "note": "업로드 검증 의견",
            "scalar_results": [
                {"variable_key": "top_edge_max_stress", "value": 70.0, "unit": "MPa", "threshold": 75.0},
                {"variable_key": "bottom_edge_max_stress", "value": 80.0, "unit": "MPa", "threshold": 75.0},
                {"variable_key": "chassis_rear_top_edge_gap_permanent_deformation", "value": 5.0, "unit": "mm"},
            ],
            "time_series": [
                {"variable_key": "top_edge_stress_time", "time": 0, "value": 0, "time_unit": "ms", "value_unit": "MPa"},
                {"variable_key": "top_edge_stress_time", "time": 10, "value": 70, "time_unit": "ms", "value_unit": "MPa"},
            ],
        }, ensure_ascii=False)
        invalid_response = client.post(
            f"/api/load-cases/{created_load_case}/results/import",
            json={"filename": "invalid.json", "content": json.dumps({"scalar_results": [{"variable_key": "unknown_result", "value": 1}]}), "author": "테스트", "validate_only": True},
        )
        assert invalid_response.status_code == 422
        radioss_sample = client.get("/api/result-import/template/radioss-csv")
        assert radioss_sample.status_code == 200
        radioss_preview = client.post(
            f"/api/load-cases/{created_load_case}/results/import",
            json={"filename": "radioss-example.csv", "content": radioss_sample.text, "author": "테스트", "validate_only": True},
        )
        assert radioss_preview.status_code == 200
        assert radioss_preview.json()["source_format"] == "RADIOSS_MESH_CSV"
        assert radioss_preview.json()["node_count"] == 111
        assert radioss_preview.json()["element_count"] == 64
        assert radioss_preview.json()["scalar_count"] == 10
        derived_values = {item["variable_key"]: item["value"] for item in radioss_preview.json()["results"]}
        assert derived_values["bottom_edge_max_stress"] == 84.0
        assert 6.39 < derived_values["chassis_rear_top_edge_gap_permanent_deformation"] < 6.41
        assert 4.29 < derived_values["chassis_rear_bottom_edge_gap_permanent_deformation"] < 4.31
        preview_response = client.post(
            f"/api/load-cases/{created_load_case}/results/import",
            json={"filename": "result.json", "content": result_content, "author": "테스트", "validate_only": True},
        )
        assert preview_response.status_code == 200
        assert preview_response.json()["status"] == "VALID"
        assert preview_response.json()["fail_count"] == 2
        import_response = client.post(
            f"/api/load-cases/{created_load_case}/results/import",
            json={"filename": "radioss-example.csv", "content": radioss_sample.text, "author": "테스트"},
        )
        assert import_response.status_code == 200
        assert import_response.json()["status"] == "IMPORTED"
        created_run = import_response.json()["run_id"]
        overview = client.get(f"/api/load-cases/{created_load_case}/overview").json()
        assert overview["analysis_verdicts"] == {"open_cell": "FAIL", "chassis_rear": "FAIL"}
        assert len(overview["time_series"]) == 12
        assert len(overview["result_locations"]) == 10
        locations = {item["variable_key"]: item for item in overview["result_locations"]}
        assert locations["bottom_edge_max_stress"]["entity_id"] == "5001"
        assert locations["chassis_rear_top_edge_gap_permanent_deformation"]["entity_id"] == "2014"
        workflow = client.get(f"/api/requests/{created_request}/workflow").json()["steps"]
        assert workflow[0]["name"] == "CAD 작업"
        assert workflow[0]["status"] == "READY"
        assert all(step["status"] == "WAITING" for step in workflow[1:])
    with connect() as conn:
        if created_run:
            conn.execute("DELETE FROM qualitative_notes WHERE analysis_run_id = ?", [created_run])
            conn.execute("DELETE FROM result_locations WHERE analysis_run_id = ?", [created_run])
            conn.execute("DELETE FROM time_series_results WHERE analysis_run_id = ?", [created_run])
            conn.execute("DELETE FROM scalar_results WHERE analysis_run_id = ?", [created_run])
            conn.execute(
                "DELETE FROM canonical_result_ingestion_source_versions "
                "WHERE analysis_run_id=? OR supersedes_analysis_run_id=?",
                [created_run, created_run],
            )
            conn.execute("DELETE FROM folder_import_jobs WHERE analysis_run_id = ?", [created_run])
            conn.execute("DELETE FROM analysis_run_metadata WHERE analysis_run_id = ?", [created_run])
            conn.execute("DELETE FROM analysis_runs WHERE id = ?", [created_run])
        if created_request:
            conn.execute("DELETE FROM request_steps WHERE request_id = ?", [created_request])
            conn.execute("DELETE FROM load_cases WHERE request_id = ?", [created_request])
            conn.execute("DELETE FROM request_work_items WHERE request_id = ?", [created_request])
            conn.execute("DELETE FROM request_work_plans WHERE request_id = ?", [created_request])
            conn.execute("DELETE FROM analysis_request_type_assignments WHERE request_id = ?", [created_request])
            conn.execute("DELETE FROM analysis_requests WHERE id = ?", [created_request])
        if created_project:
            conn.execute("DELETE FROM product_information WHERE project_id = ?", [created_project])
            conn.execute("DELETE FROM projects WHERE id = ?", [created_project])


def test_typed_folder_example_registers_scalars_curves_media_and_catalog(monkeypatch):
    initialize_database()
    load_case_id = "loadcase-clamp-left-001"
    authorization_connections = []
    query_connections = []
    events = []
    original_authorize = result_ingestion_module.require_resource_permission
    original_target_query = SQLResultIngestionQuery.get_result_ingestion_target

    def track_result_import_authorization(
        request,
        permission,
        resource_type,
        resource_id,
        *,
        conn=None,
    ):
        if permission == result_ingestion_module.RESULT_IMPORT and resource_type == "load_case":
            events.append("authorize")
            authorization_connections.append(conn)
        return original_authorize(
            request,
            permission,
            resource_type,
            resource_id,
            conn=conn,
        )

    def track_target_query(query, *args, **kwargs):
        events.append("query")
        query_connections.append(query._repository.conn)
        return original_target_query(query, *args, **kwargs)

    monkeypatch.setattr(
        result_ingestion_module,
        "require_resource_permission",
        track_result_import_authorization,
    )
    monkeypatch.setattr(SQLResultIngestionQuery, "get_result_ingestion_target", track_target_query)
    with TestClient(app) as client:
        response = client.post(f"/api/load-cases/{load_case_id}/folder-import/example")
        assert response.status_code == 200, response.text
        assert len(authorization_connections) == 2
        assert all(connection is not None for connection in authorization_connections)
        assert authorization_connections[0] is not authorization_connections[1]
        assert events[:3] == ["authorize", "query", "authorize"]
        assert len(query_connections) == 1
        assert query_connections[0] is authorization_connections[0]
        result = response.json()
        assert result["summary"] == {"scalar_count": 13, "curve_count": 2, "media_count": 1}
        overview = client.get(f"/api/load-cases/{load_case_id}/overview").json()
        assert overview["run"] == result["run_id"]
        assert len(overview["curves"]) == 2
        assert any(item["value_integer"] == 428120 for item in overview["scalar_results"])
        assert any(item["value_text"] for item in overview["scalar_results"])
        numeric = [item for item in overview["scalar_results"] if item["value_double"] is not None]
        assert len([item for item in numeric if not item["variable_key"].startswith("chassis_rear_")]) == 4
        assert len([item for item in numeric if item["variable_key"].startswith("chassis_rear_")]) == 6
        assert overview["media"][0]["asset_url"].startswith("/api/assets/")
        assert client.get(overview["media"][0]["asset_url"]).status_code == 200
        catalog = client.get(f"/api/load-cases/{load_case_id}/variables").json()
        assert {"NUMBER", "INTEGER", "TEXT", "CURVE", "IMAGE"} <= {item["data_type"] for item in catalog}

    with connect() as conn:
        asset_paths = [row[0] for row in conn.execute("SELECT file_path FROM media_assets WHERE analysis_run_id=?", [result["run_id"]]).fetchall()]
        curve_ids = [row[0] for row in conn.execute("SELECT id FROM curve_results WHERE analysis_run_id=?", [result["run_id"]]).fetchall()]
        for curve_id in curve_ids:
            conn.execute("DELETE FROM curve_points WHERE curve_id=?", [curve_id])
        conn.execute("DELETE FROM curve_results WHERE analysis_run_id=?", [result["run_id"]])
        conn.execute("DELETE FROM media_assets WHERE analysis_run_id=?", [result["run_id"]])
        conn.execute("DELETE FROM qualitative_notes WHERE analysis_run_id=?", [result["run_id"]])
        conn.execute("DELETE FROM time_series_results WHERE analysis_run_id=?", [result["run_id"]])
        conn.execute("DELETE FROM scalar_results WHERE analysis_run_id=?", [result["run_id"]])
        conn.execute("DELETE FROM folder_import_jobs WHERE id=?", [result["job_id"]])
        conn.execute(
            "DELETE FROM canonical_result_ingestion_source_versions "
            "WHERE analysis_run_id=? OR supersedes_analysis_run_id=?",
            [result["run_id"], result["run_id"]],
        )
        conn.execute("DELETE FROM analysis_run_metadata WHERE analysis_run_id=?", [result["run_id"]])
        conn.execute("DELETE FROM analysis_runs WHERE id=?", [result["run_id"]])
        for key in ("mesh_element_count", "analysis_judgement", "chassis_rear_verdict", "open_cell_top_edge_stress_curve", "chassis_rear_top_edge_deformation_curve", "open_cell_stress_contour"):
            conn.execute("DELETE FROM variable_definitions WHERE load_case_id=? AND variable_key=?", [load_case_id, key])
    for path in asset_paths:
        asset_folder = Path(__file__).resolve().parents[1] / "assets" / Path(path).parent
        if asset_folder.exists():
            shutil.rmtree(asset_folder)
    with connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM analysis_run_metadata WHERE source_type='FOLDER_IMPORT' AND source_name LIKE '%typed-results%tv-drop-chassis'"
        ).fetchone()[0] == 0


def test_import_schema_crud_and_hierarchy_mapping():
    payload = {
        "name": "TV 폴더 규칙",
        "description": "제품/의뢰/하중경우 계층",
        "definition": {"context_mapping": {"mode": "folder_levels", "project_level": 0, "request_level": 1, "load_case_level": 2}, "mappings": []},
        "updated_by": "테스트 관리자",
    }
    with TestClient(app) as client:
        created_response = client.post("/api/import-schemas", json=payload)
        assert created_response.status_code == 201, created_response.text
        created = created_response.json()
        assert created["definition"]["context_mapping"]["load_case_level"] == 2
        updated_response = client.put(f"/api/import-schemas/{created['id']}", json={**payload, "name": "TV 폴더 규칙 수정"})
        assert updated_response.status_code == 200, updated_response.text
        assert updated_response.json()["definition"]["version"] == 2
        assert any(item["name"] == "TV 폴더 규칙 수정" for item in client.get("/api/import-schemas").json())
        assert client.delete(f"/api/import-schemas/{created['id']}").status_code == 200
        assert all(item["id"] != created["id"] for item in client.get("/api/import-schemas").json())
    with connect() as conn:
        conn.execute("DELETE FROM import_schema_versions WHERE schema_id=?", [created["id"]])
        conn.execute("DELETE FROM import_schemas WHERE id=?", [created["id"]])


def test_feature_example_gallery_covers_major_states():
    initialize_database()
    with TestClient(app) as client:
        response = client.get("/api/feature-examples")
        assert response.status_code == 200
        examples = {item["id"]: item for item in response.json()}
        assert len(examples) >= 12
        assert {"run-comparison", "trust-ready", "trust-warning", "review-flow", "multi-type", "data-waiting", "workflow-states", "ppt-layout"} <= set(examples)
        assert examples["run-comparison"]["data_profile"]["runs"] == 3
        assert examples["review-flow"]["data_profile"]["reviews"] == 3
        assert examples["multi-type"]["data_profile"]["curves"] == 1
        assert examples["multi-type"]["data_profile"]["media"] == 1

        comparison = client.get(
            "/api/load-cases/loadcase-showcase-compare/run-comparison",
            params={"baseline_run_id": "run-showcase-compare-2", "target_run_id": "run-showcase-compare-3"},
        )
        assert comparison.status_code == 200
        assert comparison.json()["summary"] == {"regression": 1, "improved": 1, "unchanged": 2, "comparable": 4}

        assert client.get("/api/analysis-runs/run-showcase-trust-2/trust").json()["trust_status"] == "TRUSTED"
        warning = client.get("/api/analysis-runs/run-showcase-warning-2/trust").json()
        assert warning["trust_status"] == "WARN"
        assert "unmapped_hotspot" in warning["coverage"]["unmapped"]

        reviews = client.get("/api/analysis-runs/run-showcase-review-2/review-items").json()
        assert {item["review_status"] for item in reviews} == {"OPEN", "IN_REVIEW", "RESOLVED"}

        waiting = client.get("/api/load-cases/loadcase-showcase-waiting/variables").json()
        assert len(waiting) == 5
        assert {item["data_type"] for item in waiting} == {"NUMBER", "TIME_SERIES", "IMAGE", "VIDEO", "MODEL_3D"}
        assert all(item["has_data"] is False for item in waiting)

        workflow = client.get("/api/requests/request-showcase-workflow/workflow").json()
        assert {step["status"] for step in workflow["steps"]} >= {"COMPLETED", "IN_PROGRESS", "BLOCKED", "WAITING"}


def test_feature_example_seed_is_idempotent():
    initialize_database()
    initialize_database()
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM projects WHERE id='project-feature-showcase'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM analysis_runs WHERE id='run-showcase-compare-3'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM result_locations WHERE analysis_run_id='run-showcase-warning-2' AND variable_key='unmapped_hotspot'").fetchone()[0] == 1
