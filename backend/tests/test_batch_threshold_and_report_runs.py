import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.database import connect, initialize_database
from app.main import app
from app.repositories.workbench import BatchAttemptWorkflowRunLinkConflictError, WorkbenchRepository
from app.schemas.workbench import DemoRunCreate
from app.services.demo_runner import DemoRunnerService, WorkbenchValidationError


def test_quality_thresholds_only_recalculate_matching_unit_and_result_semantics():
    with connect() as conn:
        rows = (
            ("test-chassis-mm", "chassis_rear_top_edge_gap_permanent_deformation", 9.0, "mm", 5.0, "FAIL"),
            ("test-chassis-mpa", "chassis_rear_top_edge_gap_permanent_deformation", 9.0, "MPa", 5.0, "PASS"),
            ("test-open-mpa", "top_edge_max_stress", 90.0, "MPa", 75.0, "FAIL"),
            ("test-open-mm", "top_edge_max_stress", 90.0, "mm", 75.0, "PASS"),
            ("test-custom-chassis", "custom_permanent_deformation", 9.0, "mm", 5.0, "FAIL"),
            ("test-custom-open", "custom_stress_peak", 90.0, "MPa", 75.0, "FAIL"),
        )
        for key, unit in (("custom_permanent_deformation", "mm"), ("custom_stress_peak", "MPa")):
            conn.execute(
                """
                INSERT INTO variable_definitions
                    (id, load_case_id, variable_key, display_name, data_type, unit, description,
                     filterable, source, threshold_double, allowed_widgets_json,
                     allowed_aggregations_json, analysis_type, result_group, is_active,
                     created_at, updated_at, updated_by)
                VALUES (?, 'loadcase-drop-bottom-001', ?, ?, 'NUMBER', ?, 'test custom variable',
                        true, 'scalar_results', NULL, '[]', '[]', 'DROP', 'CUSTOM', true,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'test')
                """,
                [f"variable-test-{key}", key, key, unit],
            )
        for item_id, key, value, unit, threshold, verdict in rows:
            conn.execute(
                "INSERT INTO scalar_results VALUES (?, 'run-drop-001', ?, ?, ?, NULL, NULL, ?, ?, ?)",
                [item_id, key, key, value, unit, threshold, verdict],
            )

    with TestClient(app) as client:
        assert client.put("/api/projects/project-tv-001/quality-thresholds/open_cell_stress_mpa", json={"threshold_double": 85, "updated_by": "테스트 관리자"}).status_code == 200
        assert client.put("/api/projects/project-tv-001/quality-thresholds/chassis_rear_permanent_deformation_mm", json={"threshold_double": 8, "updated_by": "테스트 관리자"}).status_code == 200

    with connect() as conn:
        values = {row[0]: (row[1], row[2]) for row in conn.execute("SELECT id, threshold_double, verdict FROM scalar_results WHERE id LIKE 'test-%'").fetchall()}
    assert values["test-chassis-mm"] == (8.0, "FAIL")
    assert values["test-chassis-mpa"] == (5.0, "PASS")
    assert values["test-open-mpa"] == (85.0, "FAIL")
    assert values["test-open-mm"] == (75.0, "PASS")
    assert values["test-custom-chassis"] == (5.0, "FAIL")
    assert values["test-custom-open"] == (75.0, "FAIL")


def test_quality_thresholds_are_seeded_and_updated_per_project():
    with connect() as conn:
        other_project = conn.execute("SELECT id FROM projects WHERE id <> 'project-tv-001' ORDER BY id LIMIT 1").fetchone()[0]
    with TestClient(app) as client:
        tv_before = client.get("/api/projects/project-tv-001/quality-thresholds").json()
        other_before = client.get(f"/api/projects/{other_project}/quality-thresholds").json()
        assert {item["criterion_key"] for item in other_before} == {
            "chassis_rear_permanent_deformation_mm",
            "open_cell_stress_mpa",
        }
        response = client.put(
            f"/api/projects/{other_project}/quality-thresholds/open_cell_stress_mpa",
            json={"threshold_double": 81, "updated_by": "테스트 관리자"},
        )
        assert response.status_code == 200
        assert response.json()["threshold_double"] == 81
        tv_after = client.get("/api/projects/project-tv-001/quality-thresholds").json()
    before_value = next(item["threshold_double"] for item in tv_before if item["criterion_key"] == "open_cell_stress_mpa")
    after_value = next(item["threshold_double"] for item in tv_after if item["criterion_key"] == "open_cell_stress_mpa")
    assert after_value == before_value


def test_report_overview_can_select_a_run_but_rejects_another_load_case_run():
    with TestClient(app) as client:
        baseline = client.get("/api/load-cases/loadcase-drop-bottom-001/overview", params={"run_id": "run-drop-baseline-001"})
        assert baseline.status_code == 200
        assert baseline.json()["run"] == "run-drop-baseline-001"
        wrong = client.get("/api/load-cases/loadcase-drop-bottom-001/overview", params={"run_id": "run-clamp-001"})
        assert wrong.status_code == 404


def test_work_item_progress_and_safe_batch_dispatch_are_persistent():
    with TestClient(app) as client:
        workflow = client.get("/api/requests/request-drop-001/workflow").json()
        current = next(item for item in workflow["steps"] if item["status"] == "IN_PROGRESS")
        changed = client.patch(
            f"/api/workbench/work-items/{current['id']}/progress",
            json={"progress": 40, "updated_by": "실행 담당자"},
        )
        assert changed.status_code == 200, changed.text
        assert next(item for item in changed.json()["steps"] if item["id"] == current["id"])["progress"] == 40
        assert changed.json()["progress"] == 40
        rejected = client.patch(
            f"/api/workbench/work-items/{current['id']}/progress",
            json={"progress": 30, "updated_by": "실행 담당자"},
        )
        assert rejected.status_code == 409
        assert rejected.json()["detail"]["code"] == "WORK_ITEM_PROGRESS_NOT_MONOTONIC"

        profiles = client.get("/api/workbench/batch-profiles").json()
        assert profiles and profiles[0]["environment"]
        assert current["task_type_id"] in profiles[0]["task_type_ids"]
        incompatible_profile = {
            "name": "후처리 전용 데모",
            "solver_path": "C:\\Demo\\post.exe",
            "working_directory": "C:\\Demo\\runs\\{request_id}",
            "arguments_template": "--input {input}",
            "environment": {},
            "task_type_id": "post-process",
            "task_type_version": 1,
            "task_type_ids": ["post-process"],
            "is_active": True,
            "updated_by": "테스트 관리자",
        }
        incompatible_created = client.post("/api/admin/workbench/batch-profiles", json=incompatible_profile)
        assert incompatible_created.status_code == 201, incompatible_created.text
        incompatible_profile_id = incompatible_created.json()["id"]
        incompatible_dispatch = client.post(
            f"/api/workbench/work-items/{current['id']}/batch-dispatch",
            json={"batch_profile_id": incompatible_profile_id, "created_by": "실행 담당자", "idempotency_key": "test-incompatible-dispatch"},
        )
        assert incompatible_dispatch.status_code == 409
        assert incompatible_dispatch.json()["detail"]["code"] == "BATCH_PROFILE_TASK_MISMATCH"
        dispatched = client.post(
            f"/api/workbench/work-items/{current['id']}/batch-dispatch",
            json={"batch_profile_id": profiles[0]["id"], "created_by": "실행 담당자", "idempotency_key": "test-successful-dispatch"},
        )
        assert dispatched.status_code == 201, dispatched.text
        payload = dispatched.json()
        assert payload["execution_mode"] == "DEMO_ONLY"
        assert payload["batch_dispatch"]["status"] == "RECORDED_DEMO"
        assert "attempt_id" not in payload["batch_dispatch"]
        assert profiles[0]["solver_path"] in payload["batch_dispatch"]["command_preview"]
        assert payload["batch_attempt"]["status"] == "SUCCEEDED"
        assert payload["batch_attempt"]["workflow_run_id"] == payload["id"]
        assert [event["event_type"] for event in payload["batch_attempt"]["events"]] == ["PREFLIGHT", "QUEUED", "SUCCEEDED"]
        duplicate = client.post(
            f"/api/workbench/work-items/{current['id']}/batch-dispatch",
            json={"batch_profile_id": profiles[0]["id"], "created_by": "실행 담당자", "idempotency_key": "test-successful-dispatch"},
        )
        assert duplicate.status_code == 201
        assert duplicate.json()["id"] == payload["id"]

    with connect() as conn:
        stored = conn.execute("SELECT status, profile_snapshot_json, attempt_id FROM batch_dispatches WHERE workflow_run_id=?", [payload["id"]]).fetchone()
        assert stored and stored[0] == "RECORDED_DEMO"
        assert stored[2] == payload["batch_attempt"]["id"]
        identity = conn.execute("SELECT batch_attempt_id FROM workflow_runs WHERE id=?", [payload["id"]]).fetchone()
        assert identity == (payload["batch_attempt"]["id"],)
        snapshot = json.loads(stored[1]) if isinstance(stored[1], str) else stored[1]
        assert snapshot["solver_path"] == profiles[0]["solver_path"]

        # Simulate a persisted DuckDB file from before 0018, then verify the
        # additive local bootstrap backfills the exact succeeded attempt/run/
        # dispatch triple and is safe to run repeatedly.
        conn.execute("DROP INDEX ux_batch_dispatches_attempt_id")
        conn.execute("DROP INDEX ux_workflow_runs_batch_attempt_id")
        conn.execute("ALTER TABLE batch_dispatches DROP COLUMN attempt_id")
        conn.execute("ALTER TABLE workflow_runs DROP COLUMN batch_attempt_id")
    initialize_database()
    initialize_database()
    with connect() as conn:
        assert conn.execute("SELECT batch_attempt_id FROM workflow_runs WHERE id=?", [payload["id"]]).fetchone() == (payload["batch_attempt"]["id"],)
        assert conn.execute("SELECT attempt_id FROM batch_dispatches WHERE workflow_run_id=?", [payload["id"]]).fetchone() == (payload["batch_attempt"]["id"],)


def test_batch_attempt_identity_makes_runner_commit_idempotent_and_discoverable_before_finalization():
    """A committed runner record remains attributable while its attempt is QUEUED."""
    now = datetime(2026, 8, 30, 12, 0, 0)
    attempt_id = "attempt-recovery-identity"
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        work_item = next(item for item in repository.work_items("request-drop-001") if item["status"] == "IN_PROGRESS")
        profile = repository.get_batch_profile_for_task(work_item["task_type_id"], int(work_item["task_type_version"]))
        assert profile is not None
        repository.insert_batch_attempt({
            "id": attempt_id,
            "work_item_id": work_item["id"],
            "workflow_run_id": None,
            "batch_profile_id": profile["id"],
            "batch_profile_version": int(profile["version"]),
            "profile_snapshot_json": "{}",
            "command_preview": "demo command",
            "idempotency_key": "recovery-identity-key",
            "execution_mode": "DEMO_ONLY",
            "status": "QUEUED",
            "progress": 10,
            "last_message": "runner commit 전 중단을 재현합니다.",
            "created_by": "실행 담당자",
            "created_at": now,
            "started_at": now,
            "completed_at": None,
        })
        payload = DemoRunCreate(
            name=f"{work_item['display_name']} 배치 기록",
            request_id=work_item["request_id"],
            execution_mode="DEMO_ONLY",
            nodes=[{
                "node_key": work_item["node_key"],
                "task_type_id": work_item["task_type_id"],
                "task_type_version": int(work_item["task_type_version"]),
                "depends_on": [],
            }],
            created_by="실행 담당자",
        )
        runner = DemoRunnerService(repository)
        first = runner.create_run(payload, run_id=f"demo-{attempt_id}", batch_attempt_id=attempt_id)
        # The work item may advance after runner commit.  Replay must verify
        # immutable attempt/run ownership instead of re-running lifecycle
        # eligibility checks before it can discover the committed run.
        conn.execute("UPDATE request_work_items SET status='COMPLETED' WHERE id=?", [work_item["id"]])
        repeated = runner.create_run(payload, run_id=f"demo-{attempt_id}", batch_attempt_id=attempt_id)

        assert first["id"] == repeated["id"] == f"demo-{attempt_id}"
        assert "batch_attempt_id" not in first
        assert repository.workflow_run_by_batch_attempt(attempt_id)["id"] == first["id"]  # type: ignore[index]
        assert conn.execute("SELECT count(*) FROM workflow_runs WHERE batch_attempt_id=?", [attempt_id]).fetchone() == (1,)
        queued = repository.batch_attempt_by_key(work_item["id"], "recovery-identity-key")
        assert queued and queued["status"] == "QUEUED" and queued["workflow_run_id"] == first["id"]

        repository.insert_workflow_run({
            "id": "demo-attempt-conflict",
            "name": "unrelated deterministic ID",
            "request_id": work_item["request_id"],
            "request_type_id": None,
            "request_type_version": None,
            "definition_json": '{"nodes": []}',
            "execution_mode": "DEMO_ONLY",
            "status": "SUCCEEDED",
            "progress": 100,
            "created_by": "다른 담당자",
            "created_at": now,
            "started_at": now,
            "completed_at": now,
            "batch_attempt_id": None,
        })
        repository.insert_batch_attempt({
            "id": "attempt-conflict",
            "work_item_id": work_item["id"],
            "workflow_run_id": None,
            "batch_profile_id": profile["id"],
            "batch_profile_version": int(profile["version"]),
            "profile_snapshot_json": "{}",
            "command_preview": "demo command",
            "idempotency_key": "conflicting-run-id-key",
            "execution_mode": "DEMO_ONLY",
            "status": "QUEUED",
            "progress": 10,
            "last_message": "conflicting deterministic ID를 재현합니다.",
            "created_by": "실행 담당자",
            "created_at": now,
            "started_at": now,
            "completed_at": None,
        })
        with pytest.raises(WorkbenchValidationError, match="소유권"):
            runner.create_run(payload, run_id="demo-attempt-conflict", batch_attempt_id="attempt-conflict")

        repository.insert_batch_attempt({
            "id": "attempt-owned-collision",
            "work_item_id": work_item["id"],
            "workflow_run_id": None,
            "batch_profile_id": profile["id"],
            "batch_profile_version": int(profile["version"]),
            "profile_snapshot_json": "{}",
            "command_preview": "demo command",
            "idempotency_key": "owned-identity-collision-key",
            "execution_mode": "DEMO_ONLY",
            "status": "QUEUED",
            "progress": 10,
            "last_message": "attempt ID collision을 재현합니다.",
            "created_by": "실행 담당자",
            "created_at": now,
            "started_at": now,
            "completed_at": None,
        })
        repository.insert_workflow_run({
            "id": "demo-another-attempt",
            "name": payload.name,
            "request_id": work_item["request_id"],
            "request_type_id": None,
            "request_type_version": None,
            "definition_json": json.dumps({"nodes": [node.model_dump() for node in payload.nodes]}),
            "execution_mode": "DEMO_ONLY",
            "status": "SUCCEEDED",
            "progress": 100,
            "created_by": "실행 담당자",
            "created_at": now,
            "started_at": now,
            "completed_at": now,
            "batch_attempt_id": "attempt-owned-collision",
        })
        with pytest.raises(WorkbenchValidationError, match="deterministic run"):
            runner.create_run(payload, run_id="demo-attempt-owned-collision", batch_attempt_id="attempt-owned-collision")


def test_queued_runner_commit_survives_repeated_duckdb_bootstrap_without_dispatch():
    now = datetime(2026, 8, 30, 12, 0, 0)
    attempt_id = "attempt-queued-bootstrap"
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        work_item = next(item for item in repository.work_items("request-drop-001") if item["status"] == "IN_PROGRESS")
        profile = repository.get_batch_profile_for_task(work_item["task_type_id"], int(work_item["task_type_version"]))
        assert profile is not None
        repository.insert_batch_attempt({
            "id": attempt_id, "work_item_id": work_item["id"], "workflow_run_id": None,
            "batch_profile_id": profile["id"], "batch_profile_version": int(profile["version"]),
            "profile_snapshot_json": "{}", "command_preview": "demo command", "idempotency_key": "queued-bootstrap-key",
            "execution_mode": "DEMO_ONLY", "status": "QUEUED", "progress": 10,
            "last_message": "runner commit after queue", "created_by": "실행 담당자",
            "created_at": now, "started_at": now, "completed_at": None,
        })
        payload = DemoRunCreate(
            name=f"{work_item['display_name']} 배치 기록", request_id=work_item["request_id"],
            execution_mode="DEMO_ONLY", created_by="실행 담당자", nodes=[{
                "node_key": work_item["node_key"], "task_type_id": work_item["task_type_id"],
                "task_type_version": int(work_item["task_type_version"]), "depends_on": [],
            }],
        )
        run = DemoRunnerService(repository).create_run(
            payload, run_id=f"demo-{attempt_id}", batch_attempt_id=attempt_id
        )
        assert run["id"] == f"demo-{attempt_id}"

    initialize_database()
    initialize_database()
    with connect() as conn:
        assert conn.execute(
            "SELECT status, workflow_run_id, completed_at FROM batch_execution_attempts WHERE id=?", [attempt_id]
        ).fetchone() == ("QUEUED", f"demo-{attempt_id}", None)
        assert conn.execute("SELECT batch_attempt_id FROM workflow_runs WHERE id=?", [f"demo-{attempt_id}"]).fetchone() == (attempt_id,)
        assert conn.execute("SELECT count(*) FROM batch_dispatches WHERE workflow_run_id=?", [f"demo-{attempt_id}"]).fetchone() == (0,)


def test_batch_runner_deterministic_insert_race_reloads_only_the_exact_identity(monkeypatch: pytest.MonkeyPatch):
    now = datetime(2026, 8, 30, 12, 0, 0)
    attempt_id = "attempt-runner-race"
    with connect() as conn:
        repository = WorkbenchRepository(conn)
        work_item = next(item for item in repository.work_items("request-drop-001") if item["status"] == "IN_PROGRESS")
        profile = repository.get_batch_profile_for_task(work_item["task_type_id"], int(work_item["task_type_version"]))
        assert profile is not None
        repository.insert_batch_attempt({
            "id": attempt_id, "work_item_id": work_item["id"], "workflow_run_id": None,
            "batch_profile_id": profile["id"], "batch_profile_version": int(profile["version"]),
            "profile_snapshot_json": "{}", "command_preview": "demo command", "idempotency_key": "runner-race-key",
            "execution_mode": "DEMO_ONLY", "status": "QUEUED", "progress": 10,
            "last_message": "deterministic insert race", "created_by": "실행 담당자",
            "created_at": now, "started_at": now, "completed_at": None,
        })
        payload = DemoRunCreate(
            name=f"{work_item['display_name']} 배치 기록", request_id=work_item["request_id"],
            execution_mode="DEMO_ONLY", created_by="실행 담당자", nodes=[{
                "node_key": work_item["node_key"], "task_type_id": work_item["task_type_id"],
                "task_type_version": int(work_item["task_type_version"]), "depends_on": [],
            }],
        )
        original_identity_lookup = repository.workflow_run_by_batch_attempt
        original_run_lookup = repository.get_workflow_run
        original_attempt_lookup = repository.batch_attempt
        original_insert = repository.insert_workflow_run
        original_link = repository.link_batch_attempt_workflow_run
        raced: dict[str, object] = {"committed": False, "run": None}

        def competing_insert(item: dict[str, object]) -> None:
            # Model a separate transaction committing the exact record between
            # the absence check and INSERT.  DuckDB's test adapter serializes
            # all connections, so its fresh read is represented below.
            raced["committed"] = True
            raced["run"] = dict(item)
            error = RuntimeError("simulated workflow run identity conflict")
            error.sqlstate = "23505"  # type: ignore[attr-defined]
            error.constraint_name = "workflow_runs_pkey"  # type: ignore[attr-defined]
            raise error

        def refreshed_identity_lookup(identity: str):
            if not raced["committed"]:
                return original_identity_lookup(identity)
            return dict(raced["run"]) if identity == attempt_id else None

        def refreshed_run_lookup(run_id: str):
            if not raced["committed"]:
                return original_run_lookup(run_id)
            return dict(raced["run"]) if run_id == f"demo-{attempt_id}" else None

        def refreshed_attempt_lookup(identity: str):
            value = original_attempt_lookup(identity)
            if identity == attempt_id and raced["committed"] and value:
                value["workflow_run_id"] = f"demo-{attempt_id}"
            return value

        monkeypatch.setattr(repository, "insert_workflow_run", competing_insert)
        monkeypatch.setattr(repository, "workflow_run_by_batch_attempt", refreshed_identity_lookup)
        monkeypatch.setattr(repository, "get_workflow_run", refreshed_run_lookup)
        monkeypatch.setattr(repository, "batch_attempt", refreshed_attempt_lookup)
        run = DemoRunnerService(repository).create_run(
            payload, run_id=f"demo-{attempt_id}", batch_attempt_id=attempt_id
        )
        assert run["id"] == f"demo-{attempt_id}"

        raw_error_attempt = "attempt-runner-raw-error"
        repository.insert_batch_attempt({
            "id": raw_error_attempt, "work_item_id": work_item["id"], "workflow_run_id": None,
            "batch_profile_id": profile["id"], "batch_profile_version": int(profile["version"]),
            "profile_snapshot_json": "{}", "command_preview": "demo command", "idempotency_key": "runner-raw-error-key",
            "execution_mode": "DEMO_ONLY", "status": "QUEUED", "progress": 10,
            "last_message": "unrelated database error", "created_by": "실행 담당자",
            "created_at": now, "started_at": now, "completed_at": None,
        })
        raw_error = RuntimeError("database connection lost")

        def unavailable_insert(_item: dict[str, object]) -> None:
            raise raw_error

        monkeypatch.setattr(repository, "workflow_run_by_batch_attempt", original_identity_lookup)
        monkeypatch.setattr(repository, "get_workflow_run", original_run_lookup)
        monkeypatch.setattr(repository, "batch_attempt", original_attempt_lookup)
        monkeypatch.setattr(repository, "insert_workflow_run", unavailable_insert)
        with pytest.raises(RuntimeError) as error:
            DemoRunnerService(repository).create_run(
                payload, run_id=f"demo-{raw_error_attempt}", batch_attempt_id=raw_error_attempt
            )
        assert error.value is raw_error

        link_conflict_attempt = "attempt-runner-link-conflict"
        repository.insert_batch_attempt({
            "id": link_conflict_attempt, "work_item_id": work_item["id"], "workflow_run_id": None,
            "batch_profile_id": profile["id"], "batch_profile_version": int(profile["version"]),
            "profile_snapshot_json": "{}", "command_preview": "demo command", "idempotency_key": "runner-link-conflict-key",
            "execution_mode": "DEMO_ONLY", "status": "QUEUED", "progress": 10,
            "last_message": "link state changed", "created_by": "실행 담당자",
            "created_at": now, "started_at": now, "completed_at": None,
        })
        monkeypatch.setattr(repository, "insert_workflow_run", original_insert)

        def link_conflict(_attempt_id: str, _run_id: str) -> None:
            raise BatchAttemptWorkflowRunLinkConflictError("state changed")

        monkeypatch.setattr(repository, "link_batch_attempt_workflow_run", link_conflict)
        with pytest.raises(WorkbenchValidationError, match="연결할 수 없습니다"):
            DemoRunnerService(repository).create_run(
                payload, run_id=f"demo-{link_conflict_attempt}", batch_attempt_id=link_conflict_attempt
            )
        assert repository.batch_attempt(link_conflict_attempt)["workflow_run_id"] is None  # type: ignore[index]
        monkeypatch.setattr(repository, "link_batch_attempt_workflow_run", original_link)
