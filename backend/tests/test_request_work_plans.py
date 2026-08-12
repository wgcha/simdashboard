from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from app.database import connect
from app.main import app
from app.repositories.workbench import WorkbenchRepository


SCENARIOS = {
    "design-reliability-validation": (
        "설계 신뢰성 검증",
        [
            ("cad-prepare", "CAD 작업"),
            ("analysis-modeling", "해석 모델링"),
            ("hpc-submit", "HPC 수행"),
            ("post-process", "결과 후처리"),
            ("analysis-db-publish", "해석 DB 저장"),
            ("reliability-evaluation", "오픈셀 파손 및 CHR 휨 평가 분석"),
        ],
    ),
    "design-doe-exploration": (
        "설계 DOE 탐색",
        [
            ("cad-prepare", "CAD 작업"),
            ("doe-generate", "DOE 파일 생성"),
            ("hpc-submit", "HPC 수행"),
            ("post-process", "결과 후처리"),
            ("optimization-analysis", "최적화 분석"),
            ("analysis-db-publish", "해석 DB 저장"),
            ("design-performance-ranking", "설계 성능 순위 평가"),
        ],
    ),
}


def _create_request(client: TestClient, request_type_id: str = "design-reliability-validation") -> dict:
    response = client.post(
        "/api/projects/project-tv-001/requests",
        json={
            "title": f"시나리오 의뢰 {uuid4().hex[:8]}",
            "owner_user_id": "local-admin",
            "due_in_days": 7,
            "overall_note": "work plan test",
            "source_type": "EXTERNAL_SYSTEM",
            "source_reference": "PLM Gateway",
            "requested_by": "설계 자동화 시스템",
            "request_type_id": request_type_id,
            "request_type_version": 1,
            "assigned_by": "운영 관리자",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_exact_active_scenarios_and_seed_backfill_progress():
    with TestClient(app) as client:
        request_types = client.get("/api/workbench/request-types").json()
        assert {item["id"] for item in request_types} == set(SCENARIOS)
        for item in request_types:
            expected_name, expected_tasks = SCENARIOS[item["id"]]
            assert item["display_name"] == expected_name
            assert [
                (node["task_type_id"], node["display_name"])
                for node in item["default_workflow"]["nodes"]
            ] == expected_tasks

        drop = client.get("/api/requests/request-drop-001/workflow").json()
        assert drop["work_plan"]["scenario_name"] == "설계 신뢰성 검증"
        assert (drop["completed_count"], drop["total_count"], drop["progress"]) == (2, 6, 33)
        assert drop["current_step"] == "HPC 수행"

        clamp = client.get("/api/requests/request-clamp-001/workflow").json()
        assert clamp["work_plan"]["scenario_name"] == "설계 DOE 탐색"
        assert (clamp["completed_count"], clamp["total_count"], clamp["progress"]) == (1, 7, 14)
        assert clamp["current_step"] == "DOE 파일 생성"


def test_request_creation_is_atomic_and_persists_an_immutable_snapshot(monkeypatch):
    with TestClient(app) as client:
        created = _create_request(client, "design-doe-exploration")
        workflow = client.get(f"/api/requests/{created['id']}/workflow").json()
        assert created["status"] == "READY"
        assert created["scenario_name"] == "설계 DOE 탐색"
        assert workflow["progress"] == 0
        assert workflow["completed_count"] == 0
        assert workflow["total_count"] == 7
        assert [item["status"] for item in workflow["steps"]] == ["READY", *(["WAITING"] * 6)]
        assert [(item["task_type_id"], item["display_name"]) for item in workflow["steps"]] == SCENARIOS["design-doe-exploration"][1]
        assert [
            (node["task_type_id"], node["display_name"])
            for node in workflow["work_plan"]["definition_snapshot"]["nodes"]
        ] == SCENARIOS["design-doe-exploration"][1]
        assert workflow["request_type_assignment"]["source"] == "ADMIN"
        assert workflow["request_type_assignment"]["decided_by"] == "로컬 관리자"
        assert workflow["work_plan"]["source_type"] == "EXTERNAL_SYSTEM"
        assert workflow["work_plan"]["source_reference"] == "PLM Gateway"
        assert workflow["work_plan"]["requested_by"] == "로컬 관리자"
        assert workflow["request"]["owner_user_id"] == "local-admin"
        assert {item["owner_user_id"] for item in workflow["steps"]} == {"local-admin"}
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM request_steps WHERE request_id = ?", [created["id"]]).fetchone()[0] == 0

    original = WorkbenchRepository.create_work_plan

    def fail_after_request_insert(*args, **kwargs):
        raise RuntimeError("forced work plan failure")

    monkeypatch.setattr(WorkbenchRepository, "create_work_plan", fail_after_request_insert)
    failed_title = f"원자성 실패 {uuid4().hex[:8]}"
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/projects/project-tv-001/requests",
            json={
                "title": failed_title,
                "owner_user_id": "local-admin",
                "due_in_days": 3,
                "source_type": "DEPARTMENT_HEAD",
                "source_reference": "구조해석팀",
                "requested_by": "구조해석팀장",
            },
        )
        assert response.status_code == 500
    monkeypatch.setattr(WorkbenchRepository, "create_work_plan", original)
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_requests WHERE title = ?", [failed_title]).fetchone()[0] == 0


def test_work_item_completion_is_sequential_idempotent_and_canonical():
    with TestClient(app) as client:
        created = _create_request(client)
        request_id = created["id"]
        workflow = client.get(f"/api/requests/{request_id}/workflow").json()
        first, second, *rest = workflow["steps"]

        out_of_order = client.post(
            f"/api/workbench/work-items/{second['id']}/complete",
            json={"completed_by": "순서 위반자"},
        )
        assert out_of_order.status_code == 409
        assert out_of_order.json()["detail"]["code"] == "WORK_ITEM_NOT_CURRENT"
        assert out_of_order.json()["detail"]["current_item_id"] == first["id"]

        start_out_of_order = client.post(
            f"/api/workbench/work-items/{second['id']}/start",
            json={"started_by": "순서 위반자"},
        )
        assert start_out_of_order.status_code == 409
        assert start_out_of_order.json()["detail"]["code"] == "WORK_ITEM_NOT_READY"

        assert client.patch(
            f"/api/workflow-steps/{first['id']}",
            json={"name": first["name"], "status": "COMPLETED"},
        ).status_code == 409
        assert client.put(
            f"/api/requests/{request_id}/workflow-steps",
            json={
                "steps": [
                    {
                        "id": first["id"],
                        "name": first["name"],
                        "status": first["status"],
                        "owner_user_id": first["owner_user_id"],
                        "owner": first["owner"],
                        "progress": first["progress"],
                        "is_optional": False,
                        "note": "",
                    }
                ]
            },
        ).status_code == 409

        not_started = client.post(
            f"/api/workbench/work-items/{first['id']}/complete",
            json={"completed_by": "순차 담당자"},
        )
        assert not_started.status_code == 409
        assert not_started.json()["detail"]["code"] == "WORK_ITEM_NOT_STARTED"

        started = client.post(
            f"/api/workbench/work-items/{first['id']}/start",
            json={"started_by": "순차 담당자"},
        )
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "IN_PROGRESS"
        assert started.json()["progress"] == 0
        assert started.json()["steps"][0]["started_by"] == "로컬 관리자"
        first_started_at = started.json()["steps"][0]["started_at"]
        started_again = client.post(
            f"/api/workbench/work-items/{first['id']}/start",
            json={"started_by": "다른 재호출자"},
        )
        assert started_again.status_code == 200
        assert started_again.json()["steps"][0]["started_at"] == first_started_at
        assert started_again.json()["steps"][0]["started_by"] == "로컬 관리자"

        completed = client.post(
            f"/api/workbench/work-items/{first['id']}/complete",
            json={"completed_by": "순차 담당자"},
        )
        assert completed.status_code == 200, completed.text
        summary = completed.json()
        assert (summary["completed_count"], summary["total_count"], summary["progress"]) == (1, 6, 17)
        assert summary["current_step"] == "해석 모델링"
        assert summary["steps"][1]["status"] == "READY"
        first_completed_at = summary["steps"][0]["completed_at"]

        repeated = client.post(
            f"/api/workbench/work-items/{first['id']}/complete",
            json={"completed_by": "다른 재호출자"},
        )
        assert repeated.status_code == 200
        assert repeated.json()["progress"] == 17
        assert repeated.json()["steps"][0]["completed_at"] == first_completed_at
        assert repeated.json()["steps"][0]["completed_by"] == "로컬 관리자"

        expected_progress = [33, 50, 67, 83, 100]
        for item, progress in zip([second, *rest], expected_progress):
            started = client.post(
                f"/api/workbench/work-items/{item['id']}/start",
                json={"started_by": "순차 담당자"},
            )
            assert started.status_code == 200, started.text
            assert next(step for step in started.json()["steps"] if step["id"] == item["id"])["status"] == "IN_PROGRESS"
            response = client.post(
                f"/api/workbench/work-items/{item['id']}/complete",
                json={"completed_by": "순차 담당자"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["progress"] == progress

        final = response.json()
        assert final["status"] == "COMPLETED"
        assert final["current_step"] is None
        assert final["completed_count"] == final["total_count"] == 6

        detail = client.get(f"/api/requests/{request_id}/workflow").json()
        listed = next(item for item in client.get("/api/workflows").json() if item["request"]["id"] == request_id)
        portfolio = next(item for item in client.get("/api/portfolio/overview").json()["records"] if item["request_id"] == request_id)
        assert detail["progress"] == listed["progress"] == portfolio["request_progress"] == 100
        assert detail["request"]["status"] == listed["request"]["status"] == portfolio["request_status"] == "COMPLETED"
        assert portfolio["scenario_name"] == "설계 신뢰성 검증"
        assert portfolio["completed_count"] == portfolio["total_count"] == 6

        missing = client.post(
            "/api/workbench/work-items/work-item-not-found/complete",
            json={"completed_by": "순차 담당자"},
        )
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "WORK_ITEM_NOT_FOUND"


def test_demo_run_must_belong_to_the_current_work_item_request():
    with TestClient(app) as client:
        created = _create_request(client, "design-doe-exploration")
        request_id = created["id"]
        current = client.get(f"/api/requests/{request_id}/workflow").json()["steps"][0]

        unrelated = client.post(
            "/api/workbench/demo-runs",
            json={
                "name": "연결되지 않은 CAD 데모",
                "execution_mode": "DEMO_ONLY",
                "created_by": "데모 담당자",
                "nodes": [{"node_key": "shape", "task_type_id": "cad-prepare", "task_type_version": 1, "depends_on": []}],
            },
        )
        assert unrelated.status_code == 201
        started = client.post(
            f"/api/workbench/work-items/{current['id']}/start",
            json={"started_by": "데모 담당자"},
        )
        assert started.status_code == 200

        invalid = client.post(
            f"/api/workbench/work-items/{current['id']}/complete",
            json={"completed_by": "데모 담당자", "demo_run_id": unrelated.json()["id"]},
        )
        assert invalid.status_code == 409
        assert invalid.json()["detail"]["code"] == "DEMO_RUN_INVALID"

        linked = client.post(
            "/api/workbench/demo-runs",
            json={
                "name": "현재 CAD 작업 데모",
                "request_id": request_id,
                "execution_mode": "DEMO_ONLY",
                "created_by": "데모 담당자",
                "nodes": [{"node_key": "cad-prepare", "task_type_id": "cad-prepare", "task_type_version": 1, "depends_on": []}],
            },
        )
        assert linked.status_code == 201, linked.text
        completed = client.post(
            f"/api/workbench/work-items/{current['id']}/complete",
            json={"completed_by": "데모 담당자", "demo_run_id": linked.json()["id"]},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["steps"][0]["demo_run_id"] == linked.json()["id"]


def test_legacy_request_steps_remain_the_monitoring_fallback():
    request_id = f"request-legacy-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO analysis_requests
                (id, project_id, title, status, owner, requested_at, due_at, overall_note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [request_id, "project-tv-001", "레거시 단계 의뢰", "IN_PROGRESS", "레거시 담당자", now, now + timedelta(days=7), "fallback"],
        )
        for index, (name, status, progress) in enumerate(
            (("레거시 완료", "COMPLETED", 100), ("레거시 진행", "IN_PROGRESS", 50)),
            start=1,
        ):
            conn.execute(
                """
                INSERT INTO request_steps
                    (id, request_id, sequence_no, name, status, owner, planned_start, planned_end,
                     actual_start, actual_end, progress, blocked_reason, note, is_optional)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, false)
                """,
                [f"legacy-step-{index}-{uuid4().hex[:6]}", request_id, index, name, status, "레거시 담당자", now, now + timedelta(days=1), now, now if status == "COMPLETED" else None, progress, None, "fallback"],
            )

    with TestClient(app) as client:
        workflow = client.get(f"/api/requests/{request_id}/workflow").json()
        assert workflow["work_plan"] is None
        assert workflow["completed_count"] is None
        assert workflow["total_count"] is None
        assert workflow["progress"] == 75
        assert workflow["current_step"] == "레거시 진행"
        changed = client.patch(
            f"/api/workflow-steps/{workflow['steps'][1]['id']}",
            json={
                "name": "레거시 진행",
                "status": "IN_PROGRESS",
                "owner": "레거시 담당자",
                "progress": 60,
                "is_optional": False,
                "note": "fallback",
            },
        )
        assert changed.status_code == 200, changed.text
        assert client.get(f"/api/requests/{request_id}/workflow").json()["progress"] == 80
