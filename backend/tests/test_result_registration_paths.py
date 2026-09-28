from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.database import initialize_database
from app.database_connection import connect
from app.services import folder_discovery_environment, result_registration_paths as paths, spdm_storage


pytestmark = pytest.mark.duckdb_integration


def _bound_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "SPDM"
    wr_relative = "Project_0009_MODEL_pv1/WR_0009_SimType1"
    wr = root.joinpath(*wr_relative.split("/"))
    wr.mkdir(parents=True)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    initialize_database()
    project_id = f"result-path-project-{uuid4().hex[:10]}"
    request_id = f"result-path-request-{uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
            [project_id, "Result Path Project", "Result Path Project", "synthetic", now],
        )
        conn.execute(
            "INSERT INTO analysis_requests(id,project_id,title,status,owner,requested_at,due_at,overall_note) VALUES(?,?,?,'READY',NULL,?,NULL,'')",
            [request_id, project_id, "Result Path Request", now],
        )
        conn.execute(
            "INSERT INTO spdm_storage_project_parents(project_folder,project_id,created_at,updated_at) VALUES(?,?,?,?)",
            ["Project_0009_MODEL_pv1", project_id, now, now],
        )
        conn.execute(
            "INSERT INTO spdm_storage_request_parents(request_folder,project_folder,project_id,request_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            [wr_relative, "Project_0009_MODEL_pv1", project_id, request_id, now, now],
        )
    return root, wr_relative, project_id, request_id


def test_prepare_preview_is_read_only_and_confirm_creates_only_result_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, wr, project_id, request_id = _bound_request(tmp_path, monkeypatch)
    segments = [
        {"role_kind": "SIMULATION_CASE", "name": "Assy_Result_01"},
        {"role_kind": "EVALUATION", "name": "Settle"},
        {"role_kind": "RESULTS", "name": "results"},
    ]
    with connect() as conn:
        preview = paths.prepare_folders(conn, project_id, request_id, "USAGE", wr, segments, False, "test-user")
        assert preview["status"] == "CONFIRM_REQUIRED"
        assert preview["result_relative_path"].endswith("/Assy_Result_01/Settle/results")
        assert [item["exists"] for item in preview["proposed_paths"]] == [False, False, False]
        assert not root.joinpath(*preview["case_relative_path"].split("/")).exists()
        assert conn.execute("SELECT count(*) FROM result_registration_paths").fetchone()[0] == 0

        prepared = paths.prepare_folders(conn, project_id, request_id, "USAGE", wr, segments, True, "test-user")
        assert prepared["status"] == "PREPARED"
        assert len(prepared["created_paths"]) == 3
        assert root.joinpath(*prepared["result_relative_path"].split("/")).is_dir()
        assert conn.execute("SELECT count(*) FROM result_registration_paths").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM load_cases WHERE request_id=?", [request_id]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM dashboard_cases WHERE request_id=?", [request_id]).fetchone()[0] == 0


def test_existing_arbitrary_run_option_requires_and_accepts_explicit_role_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, wr, project_id, request_id = _bound_request(tmp_path, monkeypatch)
    parent = f"{wr}/Assy_Drop_01/Drop/Run_01"
    result = f"{parent}/CustomOption/Bottom/results"
    root.joinpath(*result.split("/")).mkdir(parents=True)
    with connect() as conn:
        preview = paths.prepare_folders(
            conn, project_id, request_id, "DISTRIBUTION", parent,
            [{"role_kind": "RUN_OPTION", "name": "CustomOption"},
             {"role_kind": "SCENE", "name": "Bottom"},
             {"role_kind": "RESULTS", "name": "results"}], False, "test-user")
        assert preview["status"] == "EXISTS"
        assert conn.execute("SELECT count(*) FROM result_registration_paths").fetchone()[0] == 0
        confirmed = paths.prepare_folders(
            conn, project_id, request_id, "DISTRIBUTION", parent,
            [{"role_kind": "RUN_OPTION", "name": "CustomOption"},
             {"role_kind": "SCENE", "name": "Bottom"},
             {"role_kind": "RESULTS", "name": "results"}], True, "test-user")
        assert confirmed["result_relative_path"] == result
        assert conn.execute(
            "SELECT count(*) FROM result_registration_paths WHERE role_kind IN ('RUN_OPTION','SCENE')"
        ).fetchone()[0] == 2
        folder_view = paths.folders(conn, project_id, request_id, "DISTRIBUTION", parent)
        option = next(item for item in folder_view["nodes"] if item["name"] == "CustomOption")
        assert option["role_kind"] == "RUN_OPTION"
        assert option["selectable"] is True


def test_prepare_preserves_confirmed_folder_roles_and_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, wr, project_id, request_id = _bound_request(tmp_path, monkeypatch)
    run_path = f"{wr}/Assy_Drop_01/Drop/Run_01"
    existing_scene = f"{run_path}/Scene_01"
    custom_option = f"{run_path}/CustomOption"
    confirmed_missing_scene = f"{run_path}/Scene_Missing"
    root.joinpath(*existing_scene.split("/")).mkdir(parents=True)
    root.joinpath(*custom_option.split("/")).mkdir(parents=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    root_key = folder_discovery_environment.root_identity(root)
    registration_id = f"confirmed-hierarchy-{uuid4().hex}"
    with connect() as conn:
        scan_id = f"confirmed-scan-{uuid4().hex}"
        preview_id = "confirmed-preview"
        conn.execute(
            "INSERT INTO folder_environment_scans(id,root_key,relative_path,environment,profile_id,profile_revision,project_id,request_id,status,tree_json,issues_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [scan_id, root_key, wr, "DISTRIBUTION", "environment-profile-distribution-default", 1,
             project_id, request_id, "COMPLETE", "[]", "[]", "test-user", now],
        )
        conn.execute(
            "INSERT INTO folder_environment_previews(id,scan_id,rows_json,can_apply,created_by,created_at) VALUES(?,?,?,?,?,?)",
            [preview_id, scan_id, "[]", True, "test-user", now],
        )
        conn.execute(
            "INSERT INTO folder_environment_registrations(id,preview_id,idempotency_key,environment,project_id,request_id,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            [registration_id, preview_id, registration_id, "DISTRIBUTION", project_id, request_id, "REGISTERED", "test-user", now],
        )
        for relative, role, target_id, option_status in (
            (existing_scene, "SCENE", "confirmed-scene-id", None),
            (confirmed_missing_scene, "SCENE", "confirmed-missing-scene-id", "PRESENT"),
            (custom_option, "RUN_OPTION", "confirmed-option-id", "UNRESOLVED"),
        ):
            conn.execute(
                "INSERT INTO folder_environment_registry(id,registration_id,root_key,relative_path,role_kind,parent_context_id,target_id,raw_name,option_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                [f"confirmed-registry-{uuid4().hex}", registration_id, root_key, relative, role, None,
                 target_id, Path(relative).name, option_status, now],
            )

        with pytest.raises(paths.ResultRegistrationError) as captured:
            paths.prepare_folders(
                conn, project_id, request_id, "DISTRIBUTION", run_path,
                [{"role_kind": "RUN_OPTION", "name": "Scene_01"},
                 {"role_kind": "SCENE", "name": "Other"},
                 {"role_kind": "RESULTS", "name": "results"}], False, "test-user")
        assert captured.value.code == "RESULT_PATH_ROLE_CONFLICT"
        assert not (root / run_path / "Other").exists()
        assert conn.execute("SELECT count(*) FROM result_registration_paths").fetchone()[0] == 0

        scene_result = paths.prepare_folders(
            conn, project_id, request_id, "DISTRIBUTION", run_path,
            [{"role_kind": "SCENE", "name": "Scene_01"},
             {"role_kind": "RESULTS", "name": "results"}], True, "test-user")
        assert scene_result["context"]["scene"]["id"] == "confirmed-scene-id"

        option_result = paths.prepare_folders(
            conn, project_id, request_id, "DISTRIBUTION", run_path,
            [{"role_kind": "RUN_OPTION", "name": "CustomOption"},
             {"role_kind": "SCENE", "name": "Bottom"},
             {"role_kind": "RESULTS", "name": "results"}], True, "test-user")
        assert option_result["context"]["run_option"]["id"] == "confirmed-option-id"
        assert option_result["context"]["run_option"]["status"] == "UNRESOLVED"
        scene = conn.execute(
            "SELECT target_id FROM result_registration_paths WHERE root_key=? AND path_key=?",
            [root_key, existing_scene.casefold()],
        ).fetchone()
        option = conn.execute(
            "SELECT target_id,option_status FROM result_registration_paths WHERE root_key=? AND path_key=?",
            [root_key, custom_option.casefold()],
        ).fetchone()
        assert scene == ("confirmed-scene-id",)
        assert option == ("confirmed-option-id", "UNRESOLVED")

        repaired_scene = paths.prepare_folders(
            conn, project_id, request_id, "DISTRIBUTION", run_path,
            [{"role_kind": "SCENE", "name": "Scene_Missing"},
             {"role_kind": "RESULTS", "name": "results"}], True, "test-user")
        assert repaired_scene["context"]["scene"]["id"] == "confirmed-missing-scene-id"
        assert repaired_scene["context"]["scene"]["relative_path"] == confirmed_missing_scene
        repaired_row = conn.execute(
            "SELECT target_id,option_status FROM result_registration_paths WHERE root_key=? AND path_key=?",
            [root_key, confirmed_missing_scene.casefold()],
        ).fetchone()
        assert repaired_row == ("confirmed-missing-scene-id", "PRESENT")


def test_existing_evaluation_target_id_survives_result_prepare_and_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, wr, project_id, request_id = _bound_request(tmp_path, monkeypatch)
    case_path = f"{wr}/Assy_Usage_01"
    evaluation_path = f"{case_path}/Settle"
    root.joinpath(*evaluation_path.split("/")).mkdir(parents=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    root_key = folder_discovery_environment.root_identity(root)
    registration_id = f"confirmed-usage-{uuid4().hex}"
    evaluation_id = "confirmed-evaluation-id"
    with connect() as conn:
        scan_id = f"confirmed-usage-scan-{uuid4().hex}"
        preview_id = f"confirmed-usage-preview-{uuid4().hex}"
        conn.execute(
            "INSERT INTO folder_environment_scans(id,root_key,relative_path,environment,profile_id,profile_revision,project_id,request_id,status,tree_json,issues_json,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [scan_id, root_key, wr, "USAGE", "environment-profile-usage-default", 1,
             project_id, request_id, "COMPLETE", "[]", "[]", "test-user", now],
        )
        conn.execute(
            "INSERT INTO folder_environment_previews(id,scan_id,rows_json,can_apply,created_by,created_at) VALUES(?,?,?,?,?,?)",
            [preview_id, scan_id, "[]", True, "test-user", now],
        )
        conn.execute(
            "INSERT INTO folder_environment_registrations(id,preview_id,idempotency_key,environment,project_id,request_id,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            [registration_id, preview_id, registration_id, "USAGE", project_id, request_id, "REGISTERED", "test-user", now],
        )
        conn.execute(
            "INSERT INTO folder_environment_registry(id,registration_id,root_key,relative_path,role_kind,parent_context_id,target_id,raw_name,option_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [f"confirmed-evaluation-{uuid4().hex}", registration_id, root_key, evaluation_path,
             "EVALUATION", None, evaluation_id, "Settle", None, now],
        )

        prepared = paths.prepare_folders(
            conn, project_id, request_id, "USAGE", case_path,
            [{"role_kind": "EVALUATION", "name": "Settle"},
             {"role_kind": "RESULTS", "name": "results"}], True, "test-user")
        assert prepared["context"]["evaluation"]["relative_path"] == evaluation_path
        row = conn.execute(
            "SELECT target_id FROM result_registration_paths WHERE root_key=? AND path_key=?",
            [root_key, evaluation_path.casefold()],
        ).fetchone()
        assert row == (evaluation_id,)

        folder_view = paths.folders(conn, project_id, request_id, "USAGE", case_path)
        evaluation = next(item for item in folder_view["nodes"] if item["name"] == "Settle")
        assert evaluation["role_kind"] == "EVALUATION"
        assert evaluation["suggested_relative_path"] == f"{evaluation_path}/results"


def test_confirmed_environment_registry_resolves_nested_project_and_multiple_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "Nested SPDM"
    project_path = "Organization/Projects/Project_001"
    request_paths = [f"{project_path}/Requests/WR_001", f"{project_path}/Requests/WR_002"]
    for relative in request_paths:
        root.joinpath(*relative.split("/")).mkdir(parents=True)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    initialize_database()
    project_id = f"nested-project-{uuid4().hex[:10]}"
    request_ids = [f"nested-request-{uuid4().hex[:10]}" for _ in request_paths]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    root_key = folder_discovery_environment.root_identity(root)
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
            [project_id, "Nested Project", "Nested Project", "synthetic", now],
        )
        for request_id in request_ids:
            conn.execute(
                "INSERT INTO analysis_requests(id,project_id,title,status,owner,requested_at,due_at,overall_note) VALUES(?,?,?,'READY',NULL,?,NULL,'')",
                [request_id, project_id, request_id, now],
            )
        for request_id, request_path in zip(request_ids, request_paths, strict=True):
            registration_id = f"nested-registration-{uuid4().hex}"
            conn.execute(
                "INSERT INTO folder_environment_registrations(id,preview_id,idempotency_key,environment,project_id,request_id,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                [registration_id, f"preview-{request_id}", registration_id, "USAGE", project_id, request_id, "REGISTERED", "test-user", now],
            )
            for role, relative in (("PROJECT", project_path), ("REQUEST", request_path)):
                conn.execute(
                    "INSERT INTO folder_environment_registry(id,registration_id,root_key,relative_path,role_kind,parent_context_id,target_id,raw_name,option_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    [f"environment-registry-{uuid4().hex}", registration_id, root_key, relative, role, None,
                     f"target-{role}-{request_id}", Path(relative).name, None, now],
                )

        # The old leaf binding's first two components are not the confirmed
        # Project/WR boundaries for this nested folder schema.
        spdm_storage._lock_root_identity(conn, root)
        conn.execute(
            "INSERT INTO spdm_storage_bindings(load_case_id,project_id,request_id,relative_path,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            [f"legacy-load-{request_ids[1]}", project_id, request_ids[1],
             f"{request_paths[1]}/Assy_Drop_02/Drop/CMS", now, now],
        )

        first_scope = paths._scope(conn, project_id, request_ids[0], "USAGE")
        second_scope = paths._scope(conn, project_id, request_ids[1], "DISTRIBUTION")
        assert first_scope["spdm_project_folder"] == project_path
        assert first_scope["spdm_request_folder"] == request_paths[0]
        assert second_scope["spdm_project_folder"] == project_path
        assert second_scope["spdm_request_folder"] == request_paths[1]
        assert paths._registry_role(conn, root_key, request_paths[1], project_id, request_ids[1], "DISTRIBUTION")[0] == "REQUEST"

        prepared = paths.prepare_folders(
            conn, project_id, request_ids[1], "DISTRIBUTION", request_paths[1],
            [{"role_kind": "SIMULATION_CASE", "name": "Assy_Drop_02"},
             {"role_kind": "LOAD_CASE", "name": "Drop"},
             {"role_kind": "EXECUTION_RUN", "name": "Run_02"},
             {"role_kind": "SCENE", "name": "Bottom"},
             {"role_kind": "RESULTS", "name": "results"}], True, "test-user")
        assert prepared["result_relative_path"].startswith(request_paths[1] + "/")


def test_legacy_leaf_binding_without_confirmed_parent_does_not_guess_first_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "Legacy SPDM"
    relative = "Organization/Projects/Project_002/Requests/WR_003/CAE/Assy_Drop/Drop/CMS"
    root.joinpath(*relative.split("/")).mkdir(parents=True)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    initialize_database()
    project_id = f"legacy-project-{uuid4().hex[:10]}"
    request_id = f"legacy-request-{uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects(id,name,product_name,description,created_at) VALUES(?,?,?,?,?)",
            [project_id, "Legacy Project", "Legacy Project", "synthetic", now],
        )
        conn.execute(
            "INSERT INTO analysis_requests(id,project_id,title,status,owner,requested_at,due_at,overall_note) VALUES(?,?,?,'READY',NULL,?,NULL,'')",
            [request_id, project_id, "Legacy Request", now],
        )
        spdm_storage._lock_root_identity(conn, root)
        conn.execute(
            "INSERT INTO spdm_storage_bindings(load_case_id,project_id,request_id,relative_path,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            [f"legacy-load-{request_id}", project_id, request_id, relative, now, now],
        )
        with pytest.raises(paths.ResultRegistrationError) as captured:
            paths._scope(conn, project_id, request_id, "USAGE")
        assert captured.value.code == "SPDM_REQUEST_BINDING_REQUIRED"


def test_targets_keeps_unbound_legacy_request_visible_without_aborting_bound_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _wr, project_id, request_id = _bound_request(tmp_path, monkeypatch)
    unbound_request_id = f"legacy-only-request-{uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            "INSERT INTO analysis_requests(id,project_id,title,status,owner,requested_at,due_at,overall_note) VALUES(?,?,?,'READY',NULL,?,NULL,'')",
            [unbound_request_id, project_id, "Legacy-only request", now],
        )
        spdm_storage._lock_root_identity(conn, root)
        conn.execute(
            "INSERT INTO spdm_storage_bindings(load_case_id,project_id,request_id,relative_path,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            [f"legacy-load-{unbound_request_id}", project_id, unbound_request_id,
             "Organization/Projects/Project_009/Requests/WR_009/CAE/Assy_Drop/Drop/CMS", now, now],
        )
        result = paths.targets(conn, "USAGE", {project_id})

    targets = {item["request_id"]: item for item in result["targets"]}
    assert set(targets) == {request_id, unbound_request_id}
    assert targets[request_id]["status"] == "READY"
    legacy = targets[unbound_request_id]
    assert legacy["status"] == "BINDING_REQUIRED"
    assert legacy["spdm_request_folder"] is None
    assert legacy["request_relative_path"] is None
    assert legacy["reason"]["code"] == "SPDM_REQUEST_BINDING_REQUIRED"
    assert legacy["cases"] == []


def test_foreign_descendant_environment_registry_blocks_path_claim_before_mkdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, wr, project_id, request_id = _bound_request(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    foreign_registration = f"foreign-registration-{uuid4().hex}"
    other_project = f"foreign-project-{uuid4().hex[:10]}"
    other_request = f"foreign-request-{uuid4().hex[:10]}"
    foreign_path = f"{wr}/Assy_Result_01/Settle"
    with connect() as conn:
        conn.execute(
            "INSERT INTO folder_environment_registrations(id,preview_id,idempotency_key,environment,project_id,request_id,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            [foreign_registration, "foreign-preview", foreign_registration, "USAGE", other_project, other_request, "REGISTERED", "other-user", now],
        )
        conn.execute(
            "INSERT INTO folder_environment_registry(id,registration_id,root_key,relative_path,role_kind,parent_context_id,target_id,raw_name,option_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [f"foreign-registry-{uuid4().hex}", foreign_registration, folder_discovery_environment.root_identity(root),
             foreign_path, "EVALUATION", None, "foreign-evaluation", "Settle", None, now],
        )
        with pytest.raises(paths.ResultRegistrationError) as captured:
            paths.prepare_folders(
                conn, project_id, request_id, "USAGE", wr,
                [{"role_kind": "SIMULATION_CASE", "name": "Assy_Result_01"},
                 {"role_kind": "EVALUATION", "name": "Settle"},
                 {"role_kind": "RESULTS", "name": "results"}], True, "test-user")
        assert captured.value.code == "RESULT_PATH_OWNERSHIP_CONFLICT"
        assert not root.joinpath(*f"{wr}/Assy_Result_01".split("/")).exists()


def test_database_failure_compensates_only_new_empty_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, wr, project_id, request_id = _bound_request(tmp_path, monkeypatch)
    sentinel = root.joinpath(*wr.split("/")) / "keep.txt"
    sentinel.write_text("preexisting", encoding="utf-8")
    segments = [
        {"role_kind": "SIMULATION_CASE", "name": "Assy_Compensate_01"},
        {"role_kind": "EVALUATION", "name": "Settle"},
        {"role_kind": "RESULTS", "name": "results"},
    ]

    class FailingConnection:
        backend = "duckdb"

        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=None):
            if "INSERT INTO result_registration_paths" in statement:
                raise RuntimeError("synthetic registry write failure")
            return self.connection.execute(statement, parameters)

    with connect() as raw:
        with pytest.raises(RuntimeError, match="synthetic registry write failure"):
            paths.prepare_folders(FailingConnection(raw), project_id, request_id, "USAGE", wr,
                                  segments, True, "test-user")
        assert sentinel.read_text(encoding="utf-8") == "preexisting"
        assert not root.joinpath(*f"{wr}/Assy_Compensate_01".split("/")).exists()
        assert raw.execute("SELECT count(*) FROM result_registration_paths").fetchone()[0] == 0
