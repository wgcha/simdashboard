from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import connect
from app.main import app
from app.repositories.workbench import WorkbenchRepository


def _page(page_id: str, widget_id: str, title: str) -> dict:
    return {
        "id": page_id,
        "name": title,
        "description": "의뢰 생성 시점에 고정되는 12열 결과 페이지",
        "widgets": [
            {
                "id": widget_id,
                "type": "summary",
                "title": f"{title} 요약",
                "x": 0,
                "y": 0,
                "w": 6,
                "h": 3,
                "settings": {},
            },
            {
                "id": f"{widget_id}-kpi",
                "type": "kpi",
                "title": f"{title} KPI",
                "x": 6,
                "y": 0,
                "w": 6,
                "h": 3,
                "settings": {},
            },
        ],
    }


def _template_payload(template_id: str, page: dict, display_name: str) -> dict:
    return {
        "id": template_id,
        "display_name": display_name,
        "description": "불변 결과 레이아웃 snapshot 계약 검증",
        "page_definitions": [page],
        "lifecycle_status": "PUBLISHED",
        "scope_kind": "SYSTEM",
    }


def _request_type_payload(display_name: str, profile: dict | None = None) -> dict:
    payload = {
        "display_name": display_name,
        "description": "결과 레이아웃이 연결된 접수 작업 유형",
        "allowed_task_types": [{"id": "analysis-db-publish", "version": 1}],
        "default_workflow": {
            "nodes": [
                {
                    "node_key": "analysis-db-publish-step",
                    "task_type_id": "analysis-db-publish",
                    "task_type_version": 1,
                    "depends_on": [],
                }
            ]
        },
        "match_rules": {"labels": ["snapshot-contract"]},
        "is_active": True,
    }
    if profile is not None:
        payload["result_profile"] = profile
    return payload


def _create_request(client: TestClient, request_type_id: str, version: int, title: str) -> dict:
    response = client.post(
        "/api/projects/project-tv-001/requests",
        json={
            "title": title,
            "owner_user_id": "local-admin",
            "due_in_days": 14,
            "overall_note": "result layout snapshot contract",
            "source_type": "EXTERNAL_SYSTEM",
            "source_reference": "MVP03 test gateway",
            "requested_by": "MVP03 test",
            "request_type_id": request_type_id,
            "request_type_version": version,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_result_profiles_create_immutable_request_layout_snapshots_and_preserve_legacy_requests():
    suffix = uuid4().hex[:8]
    first_template_id = f"result-template-{suffix}"
    first_widget_id = f"result-widget-{suffix}"
    second_template_id = f"result-template-next-{suffix}"
    second_widget_id = f"result-widget-next-{suffix}"

    with TestClient(app) as client:
        created_template = client.post(
            "/api/admin/workbench/analysis-templates",
            json=_template_payload(
                first_template_id,
                _page(f"result-page-{suffix}", first_widget_id, "초기 충돌 결과"),
                "초기 충돌 결과 템플릿",
            ),
        )
        assert created_template.status_code == 201, created_template.text
        template = created_template.json()
        assert template["template_id"] == first_template_id
        assert template["version"] == 1

        published_templates = client.get("/api/workbench/analysis-templates")
        assert published_templates.status_code == 200
        assert any(item["template_id"] == first_template_id for item in published_templates.json())

        profile_payload = {
            "template_id": first_template_id,
            "template_version": 1,
            "included_widget_ids": [first_widget_id],
            "overrides": {"presentation": "compact"},
            "required_data_contracts": ["LOAD_CASE", "RESULT_RUN"],
        }
        created_type = client.post(
            "/api/admin/workbench/request-types",
            json=_request_type_payload(f"Snapshot profile {suffix}", profile_payload),
        )
        assert created_type.status_code == 201, created_type.text
        request_type = created_type.json()

        profile = client.get(
            f"/api/workbench/request-types/{request_type['id']}/{request_type['version']}/result-profile"
        )
        assert profile.status_code == 200, profile.text
        assert profile.json()["template_id"] == first_template_id
        assert profile.json()["included_widget_ids"] == [first_widget_id]
        assert profile.json()["required_data_contracts"] == ["LOAD_CASE", "RESULT_RUN"]

        rejected_empty_profile = client.post(
            "/api/admin/workbench/request-types",
            json=_request_type_payload(
                f"Empty result profile {suffix}",
                {
                    "template_id": first_template_id,
                    "template_version": 1,
                    "included_widget_ids": [],
                    "overrides": {},
                    "required_data_contracts": ["RESULT_RUN"],
                },
            ),
        )
        assert rejected_empty_profile.status_code == 400
        assert "RESULT_PROFILE_EMPTY_LAYOUT" in rejected_empty_profile.text

        all_widgets_type = client.post(
            "/api/admin/workbench/request-types",
            json=_request_type_payload(
                f"All widgets profile {suffix}",
                {
                    "template_id": first_template_id,
                    "template_version": 1,
                    "included_widget_ids": None,
                    "overrides": {},
                    "required_data_contracts": ["RESULT_RUN"],
                },
            ),
        )
        assert all_widgets_type.status_code == 201, all_widgets_type.text
        all_widgets_request = _create_request(client, all_widgets_type.json()["id"], 1, f"All widgets request {suffix}")
        all_widgets_layout = client.get(f"/api/workbench/requests/{all_widgets_request['id']}/result-layout")
        assert {widget["id"] for widget in all_widgets_layout.json()["snapshot"]["pages"][0]["widgets"]} == {first_widget_id, f"{first_widget_id}-kpi"}

        created_request = _create_request(
            client,
            request_type["id"],
            request_type["version"],
            f"Snapshot request {suffix}",
        )
        initial_layout = client.get(
            f"/api/workbench/requests/{created_request['id']}/result-layout"
        )
        assert initial_layout.status_code == 200, initial_layout.text
        initial = initial_layout.json()
        assert initial["snapshot_reason"] == "REQUEST_CREATED"
        assert initial["source_request_type_id"] == request_type["id"]
        assert initial["source_request_type_version"] == 1
        assert initial["source_template_id"] == first_template_id
        assert initial["snapshot"]["template_id"] == first_template_id
        assert initial["snapshot"]["required_data_contracts"] == ["LOAD_CASE", "RESULT_RUN"]
        assert [widget["id"] for widget in initial["snapshot"]["pages"][0]["widgets"]] == [
            first_widget_id
        ]
        assert initial["bindings"]["available_data_contracts"] == []

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        load_case_id = f"result-layout-load-{suffix}"
        run_id = f"result-layout-run-{suffix}"
        with connect() as conn:
            conn.execute(
                "INSERT INTO load_cases VALUES (?, ?, ?, 'GENERIC', 'COMPLETED', ?, ?)",
                [load_case_id, created_request["id"], "Result layout binding", "{}", now],
            )
        waiting_layout = client.get(f"/api/workbench/requests/{created_request['id']}/result-layout").json()
        assert waiting_layout["bindings"]["available_data_contracts"] == ["LOAD_CASE"]

        with connect() as conn:
            conn.execute(
                "INSERT INTO analysis_runs VALUES (?, ?, NULL, 1, 'UnitSolver', 'COMPLETED', ?, ?)",
                [run_id, load_case_id, now, now],
            )
            conn.execute(
                "INSERT INTO scalar_results VALUES (?, ?, 'max_stress', '최대 응력', 72.5, NULL, NULL, 'MPa', 75.0, 'PASS')",
                [f"result-layout-scalar-{suffix}", run_id],
            )
        ready_layout = client.get(f"/api/workbench/requests/{created_request['id']}/result-layout").json()
        assert ready_layout["bindings"]["available_data_contracts"] == ["LOAD_CASE", "RESULT_RUN", "SCALAR_RESULT"]
        assert ready_layout["bindings"]["latest_result_run"]["id"] == run_id
        assert ready_layout["bindings"]["scalars"] == [{"variable_key": "max_stress", "display_name": "최대 응력", "value": 72.5, "unit": "MPa", "threshold": 75.0, "verdict": "PASS"}]

        with connect() as conn:
            conn.execute("UPDATE analysis_runs SET status='FAILED' WHERE id=?", [run_id])
        failed_layout = client.get(f"/api/workbench/requests/{created_request['id']}/result-layout").json()
        assert failed_layout["bindings"]["error"] == "최근 결과 실행이 실패했습니다."

        created_next_template = client.post(
            "/api/admin/workbench/analysis-templates",
            json=_template_payload(
                second_template_id,
                _page(f"result-page-next-{suffix}", second_widget_id, "변경 충돌 결과"),
                "변경 충돌 결과 템플릿",
            ),
        )
        assert created_next_template.status_code == 201, created_next_template.text

        version_two = client.put(
            f"/api/admin/workbench/request-types/{request_type['id']}",
            json=_request_type_payload(
                f"Snapshot profile {suffix} v2",
                {
                    "template_id": second_template_id,
                    "template_version": 1,
                    "included_widget_ids": [second_widget_id],
                    "overrides": {},
                    "required_data_contracts": ["RESULT_RUN"],
                },
            ),
        )
        assert version_two.status_code == 201, version_two.text
        assert version_two.json()["version"] == 2

        original_after_profile_change = client.get(
            f"/api/workbench/requests/{created_request['id']}/result-layout"
        )
        assert original_after_profile_change.status_code == 200
        preserved = original_after_profile_change.json()
        assert preserved["source_request_type_version"] == 1
        assert preserved["snapshot"]["template_id"] == first_template_id
        assert [widget["id"] for widget in preserved["snapshot"]["pages"][0]["widgets"]] == [
            first_widget_id
        ]

        legacy_type = client.post(
            "/api/admin/workbench/request-types",
            json=_request_type_payload(f"Legacy no-profile {suffix}"),
        )
        assert legacy_type.status_code == 201, legacy_type.text
        legacy_request = _create_request(
            client,
            legacy_type.json()["id"],
            legacy_type.json()["version"],
            f"Legacy request {suffix}",
        )
        legacy_layout = client.get(
            f"/api/workbench/requests/{legacy_request['id']}/result-layout"
        )
        assert legacy_layout.status_code == 200, legacy_layout.text
        assert "compatibility" not in legacy_layout.json()
        assert legacy_layout.json() == {
            "request_id": legacy_request["id"],
            "status": "UNCONFIGURED",
            "message": "이 의뢰에는 결과 화면 구성이 지정되지 않았습니다.",
        }


def test_project_template_listing_is_scoped_and_hides_drafts():
    suffix = uuid4().hex[:8]
    project_id = "project-tv-001"
    other_project_id = f"result-layout-other-{suffix}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with connect() as conn:
        conn.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
            [other_project_id, "다른 프로젝트", "Test product", "result layout scope test", now],
        )
        repository = WorkbenchRepository(conn)

        def create(template_id: str, scope_kind: str, template_project_id: str | None, lifecycle_status: str) -> None:
            repository.create_analysis_template_version(
                {
                    "id": template_id,
                    "scope_kind": scope_kind,
                    "project_id": template_project_id,
                    "display_name": template_id,
                    "description": "project result template scope test",
                    "lifecycle_status": lifecycle_status,
                    "page_definitions": [_page(f"page-{template_id}", f"widget-{template_id}", template_id)],
                },
                "test",
            )

        project_published = f"result-layout-project-published-{suffix}"
        project_draft = f"result-layout-project-draft-{suffix}"
        other_published = f"result-layout-other-published-{suffix}"
        system_published = f"result-layout-system-published-{suffix}"
        system_draft = f"result-layout-system-draft-{suffix}"
        create(project_published, "PROJECT", project_id, "PUBLISHED")
        create(project_draft, "PROJECT", project_id, "DRAFT")
        create(other_published, "PROJECT", other_project_id, "PUBLISHED")
        create(system_published, "SYSTEM", None, "PUBLISHED")
        create(system_draft, "SYSTEM", None, "DRAFT")

        expected = {project_published, project_draft, other_published, system_published, system_draft}
        public_ids = {item["template_id"] for item in repository.list_analysis_templates() if item["template_id"] in expected}
        project_ids = {item["template_id"] for item in repository.list_analysis_templates(project_id=project_id) if item["template_id"] in expected}
        project_all_ids = {item["template_id"] for item in repository.list_analysis_templates(project_id=project_id, all_versions=True) if item["template_id"] in expected}

    assert public_ids == {system_published}
    assert project_ids == {project_published, system_published}
    assert project_all_ids == {project_published, project_draft, system_published}


def test_seeded_drop_request_uses_only_an_explicit_legacy_domain_assignment():
    with TestClient(app) as client:
        assigned = client.get("/api/workbench/requests/request-drop-001/result-layout")
        assert assigned.status_code == 200, assigned.text
        payload = assigned.json()
        assert payload["snapshot_reason"] == "LEGACY_ASSIGNED"
        assert payload["compatibility"] == {"route_kind": "DOMAIN", "renderer": "LEGACY_DOMAIN"}
        assert payload["source_request_type_id"] == "design-reliability-validation"
        assert payload["snapshot"]["legacy_renderer"] == "LEGACY_DOMAIN"


def test_result_profile_contracts_are_canonicalized_and_template_widgets_are_global() -> None:
    suffix = uuid4().hex[:8]
    template_id = "lowercase-contract-template-" + suffix
    widget_id = "lowercase-contract-widget-" + suffix
    page = _page("lowercase-contract-page-" + suffix, widget_id, "소문자 계약")
    page["widgets"][0]["settings"] = {"data_contracts": ["result_run"]}
    with TestClient(app) as client:
        template = client.post("/api/admin/workbench/analysis-templates", json=_template_payload(template_id, page, "소문자 계약 템플릿"))
        assert template.status_code == 201, template.text
        assert template.json()["page_definitions"][0]["widgets"][0]["settings"]["data_contracts"] == ["RESULT_RUN"]
        created_type = client.post("/api/admin/workbench/request-types", json=_request_type_payload("소문자 계약 작업 " + suffix, {
            "template_id": template_id, "template_version": 1, "included_widget_ids": [widget_id],
            "overrides": {}, "required_data_contracts": ["result_run"],
        }))
        assert created_type.status_code == 201, created_type.text
        request_type = created_type.json()
        profile = client.get("/api/workbench/request-types/" + request_type["id"] + "/1/result-profile")
        assert profile.status_code == 200, profile.text
        assert profile.json()["required_data_contracts"] == ["RESULT_RUN"]
        request = _create_request(client, request_type["id"], 1, "소문자 계약 의뢰 " + suffix)
        snapshot = client.get("/api/workbench/requests/" + request["id"] + "/result-layout").json()["snapshot"]
        assert snapshot["required_data_contracts"] == ["RESULT_RUN"]
        assert snapshot["pages"][0]["widgets"][0]["settings"]["data_contracts"] == ["RESULT_RUN"]

        duplicate_pages = [_page("duplicate-page-a-" + suffix, "duplicate-widget-" + suffix, "첫번째"), _page("duplicate-page-b-" + suffix, "duplicate-widget-" + suffix, "두번째")]
        duplicate = client.post("/api/admin/workbench/analysis-templates", json={
            "id": "duplicate-widget-template-" + suffix, "display_name": "중복 위젯 템플릿",
            "description": "template-global widget id contract", "page_definitions": duplicate_pages,
            "lifecycle_status": "PUBLISHED", "scope_kind": "SYSTEM",
        })
        assert duplicate.status_code == 422
        assert "위젯 ID는 모든 페이지에서 고유" in duplicate.text


def test_system_request_type_profile_cannot_promote_project_override_or_leave_orphan_version() -> None:
    suffix = uuid4().hex[:8]
    project_id = "project-tv-001"
    system_template_id = "system-editor-template-" + suffix
    project_template_id = "project-editor-template-" + suffix
    system_widget_id = "system-editor-widget-" + suffix
    project_widget_id = "project-editor-widget-" + suffix
    with TestClient(app) as client:
        system_template = client.post(
            "/api/admin/workbench/analysis-templates",
            json=_template_payload(
                system_template_id,
                _page("system-editor-page-" + suffix, system_widget_id, "시스템 결과"),
                "시스템 편집 결과",
            ),
        )
        assert system_template.status_code == 201, system_template.text
        project_template = client.post(
            "/api/admin/workbench/analysis-templates",
            json={
                **_template_payload(
                    project_template_id,
                    _page("project-editor-page-" + suffix, project_widget_id, "프로젝트 결과"),
                    "프로젝트 편집 결과",
                ),
                "scope_kind": "PROJECT",
                "project_id": project_id,
            },
        )
        assert project_template.status_code == 201, project_template.text
        system_profile = {
            "template_id": system_template_id,
            "template_version": 1,
            "included_widget_ids": [system_widget_id],
            "overrides": {},
            "required_data_contracts": [],
        }
        created = client.post(
            "/api/admin/workbench/request-types",
            json=_request_type_payload("시스템 편집 작업 " + suffix, system_profile),
        )
        assert created.status_code == 201, created.text
        request_type_id = created.json()["id"]
        bound = client.put(
            f"/api/projects/{project_id}/admin/workbench/request-types/{request_type_id}/1/result-profile",
            json={
                "template_id": project_template_id,
                "template_version": 1,
                "included_widget_ids": [project_widget_id],
                "overrides": {},
                "required_data_contracts": [],
            },
        )
        assert bound.status_code == 201, bound.text
        system_read = client.get(f"/api/workbench/request-types/{request_type_id}/1/result-profile")
        project_read = client.get(f"/api/workbench/request-types/{request_type_id}/1/result-profile?project_id={project_id}")
        assert system_read.status_code == 200, system_read.text
        assert project_read.status_code == 200, project_read.text
        assert system_read.json()["template_id"] == system_template_id
        assert project_read.json()["template_id"] == project_template_id

        rejected = client.put(
            f"/api/admin/workbench/request-types/{request_type_id}",
            json=_request_type_payload(
                "시스템 편집 작업 변경 " + suffix,
                {
                    "template_id": project_template_id,
                    "template_version": 1,
                    "included_widget_ids": [project_widget_id],
                    "overrides": {},
                    "required_data_contracts": [],
                },
            ),
        )
        assert rejected.status_code == 400
        assert rejected.json()["detail"] == "SYSTEM_RESULT_PROFILE_REQUIRES_SYSTEM_TEMPLATE"

    with connect() as conn:
        versions = conn.execute("SELECT count(*) FROM request_type_versions WHERE id=?", [request_type_id]).fetchone()[0]
        profiles = conn.execute("SELECT count(*) FROM request_type_result_profiles WHERE request_type_id=?", [request_type_id]).fetchone()[0]
        stored_profile = WorkbenchRepository(conn).result_profile_for(request_type_id, 1)
    assert versions == 1
    assert profiles == 1
    assert stored_profile and stored_profile["template_id"] == system_template_id


def test_result_layout_bindings_are_scoped_to_the_selected_load_case() -> None:
    suffix = uuid4().hex[:8]
    selected_load_case = f"result-layout-selected-{suffix}"
    selected_run = f"result-layout-selected-run-{suffix}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            "INSERT INTO load_cases VALUES (?, 'request-drop-001', 'LC-B', 'DROP', 'COMPLETED', ?, ?)",
            [selected_load_case, "{}", now],
        )
        conn.execute(
            "INSERT INTO analysis_runs VALUES (?, ?, NULL, 99, 'UnitSolver', 'COMPLETED', ?, ?)",
            [selected_run, selected_load_case, now, now],
        )
        conn.execute(
            "INSERT INTO scalar_results VALUES (?, ?, 'case_metric', 'Case metric', 20.0, NULL, NULL, 'MPa', 100.0, 'PASS')",
            [f"result-layout-selected-scalar-{suffix}", selected_run],
        )
        conn.execute(
            "INSERT INTO time_series_results VALUES (?, 'case_time', 'Case time', 0.0, 20.0, 's', 'MPa')",
            [selected_run],
        )
        conn.execute(
            "INSERT INTO curve_results VALUES (?, ?, 'case_curve', 'Case curve', 'default', 'Time', 's', 'Stress', 'MPa', 2, NULL, NULL, ?)",
            [f"result-layout-selected-curve-{suffix}", selected_run, now],
        )
        conn.execute(
            "INSERT INTO media_assets (id, analysis_run_id, asset_type, title, file_path, mime_type) VALUES (?, ?, 'IMAGE', 'Case image', '/assets/demo-workbench.svg', 'image/svg+xml')",
            [f"result-layout-selected-media-{suffix}", selected_run],
        )

    with TestClient(app) as client:
        request_level = client.get("/api/workbench/requests/request-drop-001/result-layout")
        assert request_level.status_code == 200, request_level.text
        assert {item["id"] for item in request_level.json()["bindings"]["load_cases"]} >= {
            "loadcase-drop-bottom-001",
            selected_load_case,
        }
        assert request_level.json()["bindings"]["latest_result_run"]["id"] == selected_run

        selected_a = client.get(
            "/api/workbench/requests/request-drop-001/result-layout",
            params={"load_case_id": "loadcase-drop-bottom-001"},
        )
        assert selected_a.status_code == 200, selected_a.text
        assert [item["id"] for item in selected_a.json()["bindings"]["load_cases"]] == ["loadcase-drop-bottom-001"]
        assert selected_a.json()["bindings"]["latest_result_run"]["load_case_id"] == "loadcase-drop-bottom-001"

        selected_b = client.get(
            "/api/workbench/requests/request-drop-001/result-layout",
            params={"load_case_id": selected_load_case},
        )
        assert selected_b.status_code == 200, selected_b.text
        assert [item["id"] for item in selected_b.json()["bindings"]["load_cases"]] == [selected_load_case]
        assert selected_b.json()["bindings"]["latest_result_run"]["id"] == selected_run
        assert selected_b.json()["bindings"]["scalars"][0]["value"] == 20.0
        assert {"TIME_SERIES", "CURVE", "MEDIA_ASSET"} <= set(selected_b.json()["bindings"]["available_data_contracts"])

        rejected = client.get(
            "/api/workbench/requests/request-drop-001/result-layout",
            params={"load_case_id": "loadcase-clamp-left-001"},
        )
        assert rejected.status_code == 404
        assert rejected.json()["detail"]["code"] == "LOAD_CASE_NOT_FOUND"
