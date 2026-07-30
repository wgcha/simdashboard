from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import connect
from app.main import app


def _demo_payload(**overrides):
    payload = {
        "name": "형상 단독 데모",
        "execution_mode": "DEMO_ONLY",
        "created_by": "테스트 담당자",
        "nodes": [
            {
                "node_key": "shape",
                "task_type_id": "cad-prepare",
                "task_type_version": 1,
                "depends_on": [],
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_seeded_catalog_exposes_independent_task_and_request_types():
    with TestClient(app) as client:
        task_types = client.get("/api/workbench/task-types")
        assert task_types.status_code == 200
        items = task_types.json()
        assert len(items) == 16
        assert {item["kind"] for item in items} >= {
            "CAD_PREPARE",
            "HPC_SUBMIT",
            "PHYSICSAI_TRAIN_VALIDATE",
            "DASHBOARD_VISUALIZE",
            "RELIABILITY_EVALUATION",
            "OPTIMIZATION_ANALYSIS",
            "PERFORMANCE_RANKING",
        }
        assert all(item["supports_standalone"] for item in items)
        assert all(item["demo_artifact_url"] == "/assets/demo-workbench.svg" for item in items)
        assert all("parameter_schema_json" not in item for item in items)

        request_types = client.get("/api/workbench/request-types")
        assert request_types.status_code == 200
        assert {item["id"] for item in request_types.json()} == {
            "design-reliability-validation",
            "design-doe-exploration",
        }
        all_request_types = client.get("/api/workbench/request-types", params={"all_versions": True}).json()
        inactive = {item["id"] for item in all_request_types if not item["is_active"]}
        assert {"result-reprocessing", "doe-analysis", "physicsai-training", "physicsai-prediction"} <= inactive


def test_demo_run_is_persisted_with_deterministic_events_and_static_artifact():
    with TestClient(app) as client:
        response = client.post(
            "/api/workbench/demo-runs",
            json=_demo_payload(
                request_id="request-drop-001",
                nodes=[{"node_key": "hpc-submit", "task_type_id": "hpc-submit", "task_type_version": 1, "depends_on": []}],
            ),
        )
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["execution_mode"] == "DEMO_ONLY"
        assert run["status"] == "SUCCEEDED"
        assert run["progress"] == 100
        assert run["request_id"] == "request-drop-001"
        assert len(run["tasks"]) == 1
        task = run["tasks"][0]
        assert task["status"] == "SUCCEEDED"
        assert [event["event_type"] for event in task["events"]] == ["TASK_STARTED", "DEMO_PROGRESS", "TASK_SUCCEEDED"]
        assert [event["progress"] for event in task["events"]] == [0, 50, 100]
        assert "실행" not in task["events"][-1]["message"] or "없이" in task["events"][-1]["message"]
        assert [item["kind"] for item in task["demo_text_artifacts"]] == ["LOG", "VALIDATION", "RESULT"]

        detail = client.get(f"/api/workbench/demo-runs/{run['id']}")
        assert detail.status_code == 200
        assert detail.json()["tasks"] == run["tasks"]
        request_runs = client.get("/api/workbench/demo-runs", params={"request_id": "request-drop-001"}).json()
        assert [item["id"] for item in request_runs] == [run["id"]]

        artifact = client.get(task["demo_artifact_url"])
        assert artifact.status_code == 200
        assert artifact.headers["content-type"].startswith("image/svg+xml")
        assert b"DEMO ONLY" in artifact.content

        text_assets = {item["kind"]: client.get(item["url"]) for item in task["demo_text_artifacts"]}
        assert all(response.status_code == 200 for response in text_assets.values())
        assert b"external_process_started=false" in text_assets["LOG"].content
        assert b"OVERALL: PASS" in text_assets["VALIDATION"].content
        assert b"max_displacement,1.842,mm,PASS" in text_assets["RESULT"].content


def test_request_type_allows_composed_demo_workflow():
    payload = _demo_payload(
        name="결과 재처리 조합 데모",
        nodes=[
            {"node_key": "collect", "task_type_id": "result-collect", "task_type_version": 1, "depends_on": []},
            {"node_key": "post", "task_type_id": "post-process", "task_type_version": 1, "depends_on": ["collect"]},
            {"node_key": "publish", "task_type_id": "analysis-db-publish", "task_type_version": 1, "depends_on": ["post"]},
        ],
    )
    with TestClient(app) as client:
        response = client.post("/api/workbench/demo-runs", json=payload)
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["request_type_id"] is None
        assert run["request_type_version"] is None
        assert [task["node_key"] for task in run["tasks"]] == ["collect", "post", "publish"]
        assert run["tasks"][-1]["depends_on"] == ["post"]


def test_demo_contract_rejects_real_execution_fields_and_invalid_graphs():
    with TestClient(app) as client:
        real_mode = client.post("/api/workbench/demo-runs", json=_demo_payload(execution_mode="REAL"))
        assert real_mode.status_code == 422

        injected_command = client.post("/api/workbench/demo-runs", json={**_demo_payload(), "command": "solver.exe input.fem"})
        assert injected_command.status_code == 422

        cycle = _demo_payload(
            nodes=[
                {"node_key": "one", "task_type_id": "cad-prepare", "task_type_version": 1, "depends_on": ["two"]},
                {"node_key": "two", "task_type_id": "doe-generate", "task_type_version": 1, "depends_on": ["one"]},
            ]
        )
        cycle_response = client.post("/api/workbench/demo-runs", json=cycle)
        assert cycle_response.status_code == 400
        assert "순환" in cycle_response.json()["detail"]

        disallowed = _demo_payload(
            request_type_id="design-reliability-validation",
            nodes=[{"node_key": "predict", "task_type_id": "realtime-predict", "task_type_version": 1, "depends_on": []}],
        )
        disallowed_response = client.post("/api/workbench/demo-runs", json=disallowed)
        assert disallowed_response.status_code == 400
        assert "허용하지 않은" in disallowed_response.json()["detail"]


def test_admin_can_create_immutable_task_and_request_type_versions():
    with TestClient(app) as client:
        task_payload = {
            "id": "custom-review",
            "kind": "POST_PROCESS",
            "display_name": "사용자 후처리 검토",
            "description": "관리자가 정의한 데모 Task",
            "supports_standalone": True,
            "input_artifact_types": ["RESULT_MANIFEST"],
            "output_artifact_types": ["POST_RESULT"],
            "parameter_schema": {"type": "object", "additionalProperties": False},
            "demo_artifact_url": "/assets/demo-workbench.svg",
            "is_active": True,
        }
        first = client.post("/api/admin/workbench/task-types", json=task_payload)
        second = client.post("/api/admin/workbench/task-types", json={**task_payload, "description": "두 번째 불변 버전"})
        inactive = client.post(
            "/api/admin/workbench/task-types",
            json={**task_payload, "description": "비활성 최신 버전", "is_active": False},
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        assert inactive.status_code == 201, inactive.text
        assert [first.json()["version"], second.json()["version"]] == [1, 2]

        visible = [item for item in client.get("/api/workbench/task-types").json() if item["id"] == "custom-review"]
        all_versions = [
            item
            for item in client.get("/api/workbench/task-types", params={"all_versions": True}).json()
            if item["id"] == "custom-review"
        ]
        assert [item["version"] for item in visible] == [2]
        assert [item["version"] for item in all_versions] == [3, 2, 1]

        request_payload = {
            "id": "custom-request",
            "display_name": "사용자 정의 의뢰",
            "description": "관리자가 정의한 조합",
            "allowed_task_types": [{"id": "custom-review", "version": 2}],
            "default_workflow": {
                "nodes": [
                    {"node_key": "review", "task_type_id": "custom-review", "task_type_version": 2, "depends_on": []}
                ]
            },
            "match_rules": {"analysis_purpose": "review"},
            "is_active": True,
        }
        created = client.post("/api/admin/workbench/request-types", json=request_payload)
        assert created.status_code == 201, created.text
        assert created.json()["version"] == 1
        assert created.json()["default_workflow"]["nodes"][0]["task_type_version"] == 2

        inactive_request_type = client.post(
            "/api/admin/workbench/request-types",
            json={**request_payload, "description": "비활성 최신 의뢰 유형", "is_active": False},
        )
        assert inactive_request_type.status_code == 201, inactive_request_type.text
        visible_request_types = [item for item in client.get("/api/workbench/request-types").json() if item["id"] == "custom-request"]
        assert [item["version"] for item in visible_request_types] == [1]

        run = client.post(
            "/api/workbench/demo-runs",
            json=_demo_payload(
                request_type_id="custom-request",
                nodes=[
                    {
                        "node_key": "review",
                        "task_type_id": "custom-review",
                        "task_type_version": 2,
                        "depends_on": [],
                    }
                ],
            ),
        )
        assert run.status_code == 201, run.text
        assert run.json()["request_type_version"] == 1


def test_request_type_is_recommended_from_request_info_then_fixed_to_an_immutable_version():
    request_type_payload = {
        "id": "drop-analysis-rule",
        "display_name": "DROP 해석 규칙 유형",
        "description": "의뢰의 해석 유형으로 추천하는 조합",
        "allowed_task_types": [{"id": "cad-prepare", "version": 1}],
        "default_workflow": {
            "nodes": [
                {
                    "node_key": "shape",
                    "task_type_id": "cad-prepare",
                    "task_type_version": 1,
                    "depends_on": [],
                }
            ]
        },
        "match_rules": {"analysis_type": "DROP"},
        "is_active": True,
    }
    request_id = f"request-rule-{uuid4().hex[:8]}"
    load_case_id = f"loadcase-rule-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            "INSERT INTO analysis_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [request_id, "project-tv-001", "규칙 추천 검증 의뢰", "READY", "규칙 담당자", now, now + timedelta(days=7), "legacy request"],
        )
        conn.execute(
            "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            [load_case_id, request_id, "DROP 규칙 검증", "DROP", "READY", "{}", now],
        )
    try:
        with TestClient(app) as client:
            created = client.post("/api/admin/workbench/request-types", json=request_type_payload)
            assert created.status_code == 201, created.text

            recommended = client.get(f"/api/workbench/requests/{request_id}/request-type")
            assert recommended.status_code == 200, recommended.text
            assert recommended.json()["resolution"] == "RECOMMENDED"
            assert recommended.json()["source"] == "RULE"
            assert recommended.json()["request_type"]["id"] == "drop-analysis-rule"

            assigned = client.put(
                f"/api/workbench/requests/{request_id}/request-type",
                json={"request_type_id": "drop-analysis-rule", "request_type_version": 1},
            )
            assert assigned.status_code == 200, assigned.text
            assert assigned.json()["resolution"] == "ASSIGNED"
            assert assigned.json()["source"] == "ADMIN"
            assert assigned.json()["request_type"]["version"] == 1

            workflow = client.get(f"/api/requests/{request_id}/workflow").json()
            portfolio = next(
                item
                for item in client.get("/api/portfolio/overview").json()["records"]
                if item["request_id"] == request_id
            )
            assert workflow["request_type_assignment"] == portfolio["request_type_assignment"]
            assert workflow["request_type_assignment"]["request_type_id"] == "drop-analysis-rule"
            assert workflow["request_type_assignment"]["request_type_version"] == 1
    finally:
        with connect() as conn:
            conn.execute("DELETE FROM analysis_request_type_assignments WHERE request_id = ?", [request_id])
            conn.execute("DELETE FROM load_cases WHERE id = ?", [load_case_id])
            conn.execute("DELETE FROM analysis_requests WHERE id = ?", [request_id])
