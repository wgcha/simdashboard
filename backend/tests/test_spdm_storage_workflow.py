from __future__ import annotations

import ctypes
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.database import initialize_database
from app.database_connection import connect
from app.main import app
from app.modules.access_control import PROJECT_DATA_VIEW, RESULT_IMPORT
from app.routers import spdm_storage as storage_router
from app.security import hash_password
from app.services import spdm_storage


pytestmark = pytest.mark.duckdb_integration

LOAD_CASE_ID = "loadcase-drop-bottom-001"
EXAMPLE_ROOT = Path(__file__).resolve().parents[2] / "examples" / "spdm-storage"
EXAMPLE_CSV_RELATIVE = (
    "Project_0001_85QN80H_pv1/WR_0001_SimType2/CAE/"
    "Assy_SetCase1CushionCase1/Drop/sample_parallel/INDIVIDUAL/"
    "1_Face_Drop_Scene01_Face1_1st/results/summary.csv"
)


def _copy_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "SPDM storage"
    shutil.copytree(EXAMPLE_ROOT, root)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    return root


def _candidate_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    root = tmp_path / "SPDM storage"
    relative = "Project_0002_MODEL_pv1/WR_0002_SimType1/CAE/Assy_Model/CMS"
    (root.joinpath(*relative.split("/")) / "results").mkdir(parents=True)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    return root, relative


def _sibling_candidate_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, str, str]:
    root = tmp_path / "SPDM sibling storage"
    prefix = "Project_0004_MODEL_pv1/WR_0004_SimType1/CAE"
    first = f"{prefix}/Assy_A/CMS"
    second = f"{prefix}/Assy_B/Modal"
    root.joinpath(*first.split("/")).mkdir(parents=True)
    root.joinpath(*second.split("/")).mkdir(parents=True)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    return root, first, second


def _run_count(load_case_id: str) -> int:
    with connect() as conn:
        return int(
            conn.execute(
                "SELECT count(*) FROM analysis_runs WHERE load_case_id=?",
                [load_case_id],
            ).fetchone()[0]
        )


def _next_run_no(load_case_id: str) -> int:
    with connect() as conn:
        return int(
            conn.execute(
                "SELECT COALESCE(max(run_no), 0) + 1 FROM analysis_runs WHERE load_case_id=?",
                [load_case_id],
            ).fetchone()[0]
        )


def test_example_discovery_refresh_is_idempotent_and_changed_bytes_append_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _copy_example(tmp_path, monkeypatch)
    initialize_database()

    with TestClient(app) as client:
        first = client.post("/api/storage/refresh")
        assert first.status_code == 200, first.text
        created = first.json()["created_bindings"]
        assert len(created) == 1
        load_case_id = created[0]["load_case_id"]
        first_result = first.json()["refreshed"][0]["results"]
        assert [(item["status"], item["run_no"]) for item in first_result] == [("IMPORTED", 1)]
        first_run_id = first_result[0]["run_id"]

        repeated = client.post(f"/api/load-cases/{load_case_id}/storage/refresh")
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["results"][0]["status"] == "SKIPPED"
        assert repeated.json()["results"][0]["run_id"] == first_run_id
        assert repeated.json()["results"][0]["run_no"] == 1
        assert _run_count(load_case_id) == 1

        source = root.joinpath(*EXAMPLE_CSV_RELATIVE.split("/"))
        source.write_text(
            "record_type,variable_key,value,unit\nscalar,top_edge_max_stress,43,MPa\n",
            encoding="utf-8",
        )
        changed = client.post(f"/api/load-cases/{load_case_id}/storage/refresh")
        assert changed.status_code == 200, changed.text
        assert [(item["status"], item["run_no"]) for item in changed.json()["results"]] == [("IMPORTED", 2)]
        assert changed.json()["results"][0]["run_id"] != first_run_id

        repeated_changed = client.post(f"/api/load-cases/{load_case_id}/storage/refresh")
        assert repeated_changed.status_code == 200, repeated_changed.text
        assert [(item["status"], item["run_no"]) for item in repeated_changed.json()["results"]] == [("SKIPPED", 2)]
        assert _run_count(load_case_id) == 2


def test_raw_h3d_is_downloadable_and_never_creates_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, relative = _candidate_root(tmp_path, monkeypatch)
    initialize_database()
    payload = b"H3D original bytes\x00\x01"

    with TestClient(app) as client:
        bound = client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": relative},
        )
        assert bound.status_code == 200, bound.text
        before = _run_count(LOAD_CASE_ID)
        uploaded = client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage/files",
            params={"filename": "solver-output.h3d", "kind": "solver"},
            content=payload,
        )
        assert uploaded.status_code == 200, uploaded.text

        refreshed = client.post(f"/api/load-cases/{LOAD_CASE_ID}/storage/refresh")
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["results"] == []
        assert _run_count(LOAD_CASE_ID) == before

        storage = client.get(f"/api/load-cases/{LOAD_CASE_ID}/storage")
        assert storage.status_code == 200, storage.text
        raw = next(item for item in storage.json()["files"] if item["name"] == "solver-output.h3d")
        downloaded = client.get(f"/api/storage/files/{raw['id']}/download")
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == payload


def test_structured_upload_persists_original_and_returns_result_import_contract_without_duplicate_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, relative = _candidate_root(tmp_path, monkeypatch)
    initialize_database()
    content = (
        "record_type,variable_key,value,unit\n"
        "scalar,top_edge_max_stress,41,MPa\n"
    )

    with TestClient(app) as client:
        assert client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": relative},
        ).status_code == 200
        before = _run_count(LOAD_CASE_ID)
        expected_run_no = _next_run_no(LOAD_CASE_ID)
        uploaded = client.post(
            f"/api/load-cases/{LOAD_CASE_ID}/storage/upload",
            json={"filename": "uploaded.csv", "content": content, "author": "테스트 담당자"},
        )
        assert uploaded.status_code == 200, uploaded.text
        body = uploaded.json()
        stored = body["stored_file"]
        assert stored["relative_path"] == "results/uploaded.csv"
        assert stored["run_id"] == body["run_id"]
        assert body["status"] == "IMPORTED"
        assert body["filename"] == "uploaded.csv"
        assert body["run_no"] == expected_run_no
        assert {"summary", "operation", "reason_code"} <= set(body)
        assert (root.joinpath(*relative.split("/")) / "results" / "uploaded.csv").read_text(
            encoding="utf-8"
        ) == content

        repeated = client.post(f"/api/load-cases/{LOAD_CASE_ID}/storage/refresh")
        assert repeated.status_code == 200, repeated.text
        selected = next(item for item in repeated.json()["results"] if item["relative_path"] == "results/uploaded.csv")
        assert selected["status"] == "SKIPPED"
        assert selected["run_id"] == body["run_id"]
        assert _run_count(LOAD_CASE_ID) == before + 1


def test_changed_raw_upload_gets_stable_version_name_and_retry_reuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, relative = _candidate_root(tmp_path, monkeypatch)
    initialize_database()
    with TestClient(app) as client:
        assert client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": relative},
        ).status_code == 200

        first = client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage/files",
            params={"filename": "solver.h3d", "kind": "solver"},
            content=b"first",
        )
        changed = client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage/files",
            params={"filename": "solver.h3d", "kind": "solver"},
            content=b"second",
        )
        retry = client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage/files",
            params={"filename": "solver.h3d", "kind": "solver"},
            content=b"second",
        )

    assert first.status_code == changed.status_code == retry.status_code == 200
    changed_file = changed.json()["stored_file"]
    retried_file = retry.json()["stored_file"]
    assert changed_file["relative_path"].startswith("solver/solver--")
    assert retried_file["relative_path"] == changed_file["relative_path"]
    assert retried_file["checksum"] == changed_file["checksum"]
    assert retried_file["reused"] is True


def test_unicode_download_name_uses_rfc5987_header_and_preserves_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, relative = _candidate_root(tmp_path, monkeypatch)
    initialize_database()
    with TestClient(app) as client:
        assert client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": relative},
        ).status_code == 200
        uploaded = client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage/files",
            params={"filename": "해석 원본.h3d", "kind": "solver"},
            content=b"unicode-name",
        )
        assert uploaded.status_code == 200, uploaded.text
        storage = client.get(f"/api/load-cases/{LOAD_CASE_ID}/storage").json()
        item = next(entry for entry in storage["files"] if entry["name"] == "해석 원본.h3d")
        downloaded = client.get(f"/api/storage/files/{item['id']}/download")

    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == b"unicode-name"
    disposition = downloaded.headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition


def test_reports_for_same_scene_name_in_distinct_drop_branches_never_share_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "SPDM storage"
    base = root / "Project_0003_MODEL_pv1" / "WR_0003_SimType2" / "CAE" / "Assy" / "Drop"
    branches = (
        base / "parallel" / "INDIVIDUAL" / "Scene01",
        base / "serial" / "CUMULATIVE" / "Scene01",
    )
    for branch in branches:
        branch.mkdir(parents=True)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    initialize_database()

    with TestClient(app) as client:
        discovered = client.post("/api/storage/refresh")
        assert discovered.status_code == 200, discovered.text
        bindings = discovered.json()["created_bindings"]
        assert len(bindings) == 2
        by_branch = {item["relative_path"]: item["load_case_id"] for item in bindings}
        first_id = by_branch[next(path for path in by_branch if "/parallel/" in path)]
        second_id = by_branch[next(path for path in by_branch if "/serial/" in path)]

        first = client.put(
            f"/api/load-cases/{first_id}/storage/files",
            params={"filename": "검토.pdf", "kind": "reports"},
            content=b"parallel-report",
        )
        second = client.put(
            f"/api/load-cases/{second_id}/storage/files",
            params={"filename": "검토.pdf", "kind": "reports"},
            content=b"serial-report",
        )
        assert first.status_code == second.status_code == 200

        first_state = client.get(f"/api/load-cases/{first_id}/storage").json()
        second_state = client.get(f"/api/load-cases/{second_id}/storage").json()
        first_file = next(item for item in first_state["files"] if item["kind"] == "reports")
        second_file = next(item for item in second_state["files"] if item["kind"] == "reports")
        assert first_file["id"] != second_file["id"]
        first_download = client.get(f"/api/storage/files/{first_file['id']}/download")
        second_download = client.get(f"/api/storage/files/{second_file['id']}/download")

    assert first_download.content == b"parallel-report"
    assert second_download.content == b"serial-report"
    assert spdm_storage._report_directory_relative(
        next(item for item in bindings if item["load_case_id"] == first_id)
    ) != spdm_storage._report_directory_relative(
        next(item for item in bindings if item["load_case_id"] == second_id)
    )


def test_scoped_read_and_binding_use_distinct_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, relative = _candidate_root(tmp_path, monkeypatch)
    initialize_database()
    calls: list[str] = []

    def record_permission(_request, permission, _resource_type, _resource_id, *, conn=None):
        del conn
        calls.append(permission)

    monkeypatch.setattr(storage_router, "require_resource_permission", record_permission)
    with TestClient(app) as client:
        response = client.get(f"/api/load-cases/{LOAD_CASE_ID}/storage")
        assert response.status_code == 200, response.text
        assert calls == [PROJECT_DATA_VIEW]
        calls.clear()

        response = client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": relative},
        )
        assert response.status_code == 200, response.text
        assert calls == [RESULT_IMPORT]


def test_viewer_can_read_storage_but_cannot_change_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, relative = _candidate_root(tmp_path, monkeypatch)
    initialize_database()
    with TestClient(app) as client:
        assert client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": relative},
        ).status_code == 200

    suffix = uuid4().hex[:10]
    username = f"storage-viewer-{suffix}"
    user_id = f"storage-viewer-{suffix}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users
                (id, username, password_hash, display_name, legacy_role, is_active,
                 created_at, updated_at, account_status, is_global_admin)
            VALUES (?, ?, ?, ?, 'viewer', true, ?, ?, 'ACTIVE', false)
            """,
            [user_id, username, hash_password("correct-horse-battery-staple"), username, now, now],
        )
        conn.execute(
            """
            INSERT INTO project_memberships
                (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
            VALUES (?, 'project-tv-001', ?, 'general', 'local-admin', ?, 'local-admin', ?)
            """,
            [f"membership-{suffix}", user_id, now, now],
        )

    monkeypatch.setenv("AUTH_MODE", "password")
    monkeypatch.setenv("AUTH_SECRET_KEY", "spdm-storage-test-secret-key-at-least-32")
    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": username, "password": "correct-horse-battery-staple"},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.get(
            f"/api/load-cases/{LOAD_CASE_ID}/storage", headers=headers
        ).status_code == 200
        denied = client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            headers=headers,
            json={"relative_path": relative},
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["required_permission"] == RESULT_IMPORT


def test_bound_environment_root_identity_cannot_silently_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, relative = _candidate_root(tmp_path, monkeypatch)
    initialize_database()
    with TestClient(app) as client:
        assert client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": relative},
        ).status_code == 200

        moved_root = tmp_path / "other SPDM storage"
        moved_root.mkdir()
        monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(moved_root))
        drift = client.get(f"/api/load-cases/{LOAD_CASE_ID}/storage")
        assert drift.status_code == 422, drift.text
        assert drift.json()["detail"]["code"] == "SPDM_ROOT_IDENTITY_DRIFT"

    with connect() as conn:
        assert conn.execute(
            "SELECT relative_path FROM spdm_storage_bindings WHERE load_case_id=?",
            [LOAD_CASE_ID],
        ).fetchone() == (relative,)


def test_explicit_parent_binding_is_reused_for_new_sibling_without_duplicate_project_or_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, first, second = _sibling_candidate_root(tmp_path, monkeypatch)
    initialize_database()
    with connect() as conn:
        before = conn.execute(
            "SELECT (SELECT count(*) FROM projects), (SELECT count(*) FROM analysis_requests)"
        ).fetchone()

    with TestClient(app) as client:
        bound = client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": first},
        )
        assert bound.status_code == 200, bound.text
        discovered = client.post("/api/storage/refresh")
        assert discovered.status_code == 200, discovered.text

    created = discovered.json()["created_bindings"]
    assert len(created) == 1
    assert created[0]["relative_path"] == second
    assert created[0]["project_id"] == "project-tv-001"
    assert created[0]["request_id"] == "request-drop-001"
    with connect() as conn:
        after = conn.execute(
            "SELECT (SELECT count(*) FROM projects), (SELECT count(*) FROM analysis_requests)"
        ).fetchone()
        assert after == before
        bindings = conn.execute(
            "SELECT project_id, request_id, relative_path FROM spdm_storage_bindings "
            "WHERE relative_path IN (?, ?) ORDER BY relative_path",
            [first, second],
        ).fetchall()
        assert bindings == [
            ("project-tv-001", "request-drop-001", first),
            ("project-tv-001", "request-drop-001", second),
        ]


def test_same_spdm_project_request_parent_cannot_bind_to_different_database_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root, first, second = _sibling_candidate_root(tmp_path, monkeypatch)
    initialize_database()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    other_project = f"storage-other-project-{uuid4().hex[:8]}"
    other_request = f"storage-other-request-{uuid4().hex[:8]}"
    other_case = f"storage-other-case-{uuid4().hex[:8]}"
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects(id, name, product_name, description, created_at) VALUES (?, 'Other', 'Other', '', ?)",
            [other_project, now],
        )
        conn.execute(
            "INSERT INTO analysis_requests(id, project_id, title, status, owner, requested_at, due_at, overall_note) "
            "VALUES (?, ?, 'Other request', 'READY', NULL, ?, NULL, '')",
            [other_request, other_project, now],
        )
        conn.execute(
            "INSERT INTO load_cases(id, request_id, name, analysis_type, status, parameters_json, created_at) "
            "VALUES (?, ?, 'Other case', 'DROP', 'READY', '{}', ?)",
            [other_case, other_request, now],
        )

    with TestClient(app) as client:
        assert client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": first},
        ).status_code == 200
        conflict = client.put(
            f"/api/load-cases/{other_case}/storage",
            json={"relative_path": second},
        )

    assert conflict.status_code == 422, conflict.text
    assert conflict.json()["detail"]["code"] == "SPDM_PARENT_BINDING_CONFLICT"
    with connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM spdm_storage_bindings WHERE load_case_id=?",
            [other_case],
        ).fetchone()[0] == 0


def test_global_refresh_marks_deleted_leaf_missing_and_continues_other_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, first, second = _sibling_candidate_root(tmp_path, monkeypatch)
    first_result = root.joinpath(*first.split("/")) / "results" / "first.csv"
    second_result = root.joinpath(*second.split("/")) / "results" / "second.csv"
    first_result.parent.mkdir(parents=True, exist_ok=True)
    second_result.parent.mkdir(parents=True, exist_ok=True)
    first_result.write_text(
        "record_type,variable_key,value,unit\nscalar,top_edge_max_stress,51,MPa\n",
        encoding="utf-8",
    )
    second_result.write_text(
        "record_type,variable_key,value,unit\nscalar,top_edge_max_stress,52,MPa\n",
        encoding="utf-8",
    )
    initialize_database()

    with TestClient(app) as client:
        assert client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage", json={"relative_path": first}
        ).status_code == 200
        initial = client.post("/api/storage/refresh")
        assert initial.status_code == 200, initial.text
        second_binding = next(
            item for item in initial.json()["created_bindings"] if item["relative_path"] == second
        )
        second_case_id = second_binding["load_case_id"]
        first_runs = _run_count(LOAD_CASE_ID)
        second_runs = _run_count(second_case_id)
        assert first_runs >= 1
        assert second_runs >= 1

        shutil.rmtree(root.joinpath(*first.split("/")))
        second_result.write_text(
            "record_type,variable_key,value,unit\nscalar,top_edge_max_stress,53,MPa\n",
            encoding="utf-8",
        )
        refreshed = client.post("/api/storage/refresh")
        assert refreshed.status_code == 200, refreshed.text

        missing = client.get(f"/api/load-cases/{LOAD_CASE_ID}/storage")
        assert missing.status_code == 200, missing.text
        deleted_file = next(item for item in missing.json()["files"] if item["name"] == "first.csv")
        assert deleted_file["status"] == "MISSING"
        assert deleted_file["run_id"] is not None
        assert _run_count(LOAD_CASE_ID) == first_runs
        assert _run_count(second_case_id) == second_runs + 1


def test_paths_fail_closed_and_discovery_id_collision_preserves_existing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _relative = _candidate_root(tmp_path, monkeypatch)
    initialize_database()
    project_name = "Project_0002_MODEL_pv1"
    conflicting_id = spdm_storage._stable_id("project", project_name)
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects(id, name, product_name, description, created_at) "
            "VALUES (?, 'existing-project', 'existing', 'must survive', CURRENT_TIMESTAMP)",
            [conflicting_id],
        )

    with TestClient(app) as client:
        rejected = client.post("/api/storage/refresh")
        assert rejected.status_code == 422, rejected.text
        assert rejected.json()["detail"]["code"] == "SPDM_DISCOVERY_ID_CONFLICT"
        escaped = client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": "../outside"},
        )
        assert escaped.status_code == 422, escaped.text
        assert escaped.json()["detail"]["code"] == "SPDM_PATH_INVALID"

    assert not (root.parent / "outside").exists()
    with connect() as conn:
        assert conn.execute(
            "SELECT name, description FROM projects WHERE id=?", [conflicting_id]
        ).fetchone() == ("existing-project", "must survive")
        assert conn.execute("SELECT count(*) FROM spdm_storage_bindings").fetchone()[0] == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing semantics require native Windows")
def test_windows_exclusive_writer_keeps_result_pending_until_handle_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, relative = _candidate_root(tmp_path, monkeypatch)
    initialize_database()
    result_path = root.joinpath(*relative.split("/")) / "results" / "writer-busy.csv"
    result_path.write_text(
        "record_type,variable_key,value,unit\nscalar,top_edge_max_stress,44,MPa\n",
        encoding="utf-8",
    )
    before = _run_count(LOAD_CASE_ID)
    expected_run_no = _next_run_no(LOAD_CASE_ID)

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    from ctypes import wintypes

    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(str(result_path), 0x40000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        with TestClient(app) as client:
            assert client.put(
                f"/api/load-cases/{LOAD_CASE_ID}/storage",
                json={"relative_path": relative},
            ).status_code == 200
            pending = client.post(f"/api/load-cases/{LOAD_CASE_ID}/storage/refresh")
            assert pending.status_code == 200, pending.text
            busy = next(item for item in pending.json()["files"] if item["name"] == result_path.name)
            assert (busy["status"], busy["message"], busy["run_id"]) == (
                "PENDING",
                "SPDM_FILE_BUSY",
                None,
            )
            assert pending.json()["results"] == []
    finally:
        kernel32.CloseHandle(handle)

    with TestClient(app) as client:
        imported = client.post(f"/api/load-cases/{LOAD_CASE_ID}/storage/refresh")
        assert imported.status_code == 200, imported.text
        assert [(item["status"], item["run_no"]) for item in imported.json()["results"]] == [
            ("IMPORTED", expected_run_no)
        ]


def test_database_failure_keeps_file_and_later_refresh_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, relative = _candidate_root(tmp_path, monkeypatch)
    initialize_database()
    result_path = root.joinpath(*relative.split("/")) / "results" / "recover.csv"
    result_path.write_text(
        "record_type,variable_key,value,unit\nscalar,top_edge_max_stress,45,MPa\n",
        encoding="utf-8",
    )

    from app.application.results import ingestion as result_ingestion

    original = result_ingestion.run_manual_import
    state = {"failed": False}
    before = _run_count(LOAD_CASE_ID)

    def fail_once(*args, **kwargs):
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("injected database failure")
        return original(*args, **kwargs)

    with TestClient(app) as client:
        assert client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": relative},
        ).status_code == 200
        monkeypatch.setattr(result_ingestion, "run_manual_import", fail_once)
        failed = client.post(f"/api/load-cases/{LOAD_CASE_ID}/storage/refresh")
        assert failed.status_code == 200, failed.text
        assert failed.json()["results"][0]["status"] == "FAILED"
        assert result_path.is_file()
        assert _run_count(LOAD_CASE_ID) == before

        recovered = client.post(f"/api/load-cases/{LOAD_CASE_ID}/storage/refresh")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["results"][0]["status"] == "IMPORTED"
        assert result_path.is_file()
        assert _run_count(LOAD_CASE_ID) == before + 1


@pytest.mark.parametrize(
    "filename",
    ["../escape.h3d", "..\\escape.h3d", "CON.h3d", "name:h3d", "trailing.h3d."],
)
def test_raw_upload_rejects_unsafe_windows_filenames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    _root, relative = _candidate_root(tmp_path, monkeypatch)
    initialize_database()
    with TestClient(app) as client:
        assert client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage",
            json={"relative_path": relative},
        ).status_code == 200
        response = client.put(
            f"/api/load-cases/{LOAD_CASE_ID}/storage/files",
            params={"filename": filename, "kind": "solver"},
            content=b"unsafe",
        )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "SPDM_FILENAME_INVALID"
