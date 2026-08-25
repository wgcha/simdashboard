from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import connect, initialize_database
from app.main import app
from app.services import master_result_refresh
from app.services.master_result_refresh import MasterResultRefreshError, MasterResultRefreshService


PROJECT_ID = "project-tv-001"
REQUEST_ID = "request-drop-001"
LOAD_CASE_ID = "loadcase-drop-bottom-001"


def _write_bundle(root: Path, name: str, *, value: float = 42.5, invalid: bool = False) -> Path:
    bundle = root / name
    bundle.mkdir(parents=True)
    (bundle / "scalar-results.json").write_text(json.dumps([
        {
            "variable_key": "top_edge_max_stress",
            "display_name": "상단 최대 응력",
            "data_type": "FLOAT",
            "value": value,
            "unit": "MPa",
            "threshold": 75.0,
            "result_group": "OPEN_CELL",
        }
    ]), encoding="utf-8")
    mapping_path = "../outside.json" if invalid else "scalar-results.json"
    (bundle / "manifest.json").write_text(json.dumps({
        "schema_id": "master-refresh-test",
        "version": 1,
        "solver": "pytest",
        "context": {
            "project_id": PROJECT_ID,
            "request_id": REQUEST_ID,
            "load_case_id": LOAD_CASE_ID,
        },
        "mappings": [{"kind": "typed_scalars", "path": mapping_path}],
    }), encoding="utf-8")
    return bundle


def _clean_master_refresh_records() -> None:
    with connect() as conn:
        run_ids = [row[0] for row in conn.execute(
            "SELECT analysis_run_id FROM analysis_run_metadata WHERE source_type='MASTER_FOLDER_REFRESH'"
        ).fetchall()]
        for run_id in run_ids:
            conn.execute("DELETE FROM media_assets WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM qualitative_notes WHERE analysis_run_id=?", [run_id])
            curve_ids = [row[0] for row in conn.execute("SELECT id FROM curve_results WHERE analysis_run_id=?", [run_id]).fetchall()]
            for curve_id in curve_ids:
                conn.execute("DELETE FROM curve_points WHERE curve_result_id=?", [curve_id])
            conn.execute("DELETE FROM curve_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM time_series_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM scalar_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM analysis_run_metadata WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM analysis_runs WHERE id=?", [run_id])
        conn.execute("DELETE FROM folder_import_jobs WHERE source_folder LIKE 'bundle-%'")


@pytest.fixture(autouse=True)
def _refresh_database_cleanup():
    initialize_database()
    _clean_master_refresh_records()
    yield
    _clean_master_refresh_records()


def test_master_refresh_imports_typed_bundle_and_second_refresh_is_idempotent(tmp_path: Path):
    _write_bundle(tmp_path, "bundle-success")

    first = MasterResultRefreshService(tmp_path).refresh()
    assert [(item.status, item.load_case_id) for item in first] == [("IMPORTED", LOAD_CASE_ID)]
    run_id = first[0].analysis_run_id
    assert run_id
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM scalar_results WHERE analysis_run_id=?", [run_id]).fetchone()[0] == 1
        assert conn.execute("SELECT status FROM load_cases WHERE id=?", [LOAD_CASE_ID]).fetchone()[0] == "COMPLETED"
        assert conn.execute("SELECT status FROM folder_import_jobs WHERE analysis_run_id=?", [run_id]).fetchone()[0] == "COMPLETED"

    second = MasterResultRefreshService(tmp_path).refresh()
    assert [(item.status, item.load_case_id) for item in second] == [("SKIPPED", LOAD_CASE_ID)]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_run_metadata WHERE source_type='MASTER_FOLDER_REFRESH'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM folder_import_jobs WHERE source_folder='bundle-success/manifest.json'").fetchone()[0] == 2
        metadata = conn.execute(
            "SELECT source_checksum, metadata_json FROM analysis_run_metadata WHERE source_type='MASTER_FOLDER_REFRESH'"
        ).fetchone()
        assert metadata[0]
        metadata_json = json.loads(metadata[1])
        assert metadata_json["manifest_checksum"]
        assert metadata_json["bundle_fingerprint"] == metadata[0]
        skip_summary = conn.execute(
            "SELECT summary_json FROM folder_import_jobs WHERE source_folder='bundle-success/manifest.json' AND status='SKIPPED'"
        ).fetchone()[0]
        assert json.loads(skip_summary)["bundle_fingerprint"] == metadata[0]


def test_master_refresh_reimports_when_mapping_file_changes_with_same_manifest(tmp_path: Path):
    bundle = _write_bundle(tmp_path, "bundle-content-change")

    first = MasterResultRefreshService(tmp_path).refresh()
    assert [(item.status, item.load_case_id) for item in first] == [("IMPORTED", LOAD_CASE_ID)]

    # Keep manifest bytes unchanged while changing the referenced canonical file.
    (bundle / "scalar-results.json").write_text(json.dumps([
        {
            "variable_key": "top_edge_max_stress",
            "display_name": "상단 최대 응력",
            "data_type": "FLOAT",
            "value": 43.5,
            "unit": "MPa",
            "threshold": 75.0,
            "result_group": "OPEN_CELL",
        }
    ]), encoding="utf-8")

    second = MasterResultRefreshService(tmp_path).refresh()
    assert [(item.status, item.load_case_id) for item in second] == [("IMPORTED", LOAD_CASE_ID)]
    assert second[0].analysis_run_id != first[0].analysis_run_id

    with connect() as conn:
        metadata = conn.execute(
            "SELECT source_checksum, metadata_json FROM analysis_run_metadata "
            "WHERE source_type='MASTER_FOLDER_REFRESH' ORDER BY created_at"
        ).fetchall()
        assert len(metadata) == 2
        first_metadata, second_metadata = [json.loads(row[1]) for row in metadata]
        assert first_metadata["manifest_checksum"] == second_metadata["manifest_checksum"]
        assert first_metadata["bundle_fingerprint"] != second_metadata["bundle_fingerprint"]
        assert [row[0] for row in metadata] == [
            first_metadata["bundle_fingerprint"],
            second_metadata["bundle_fingerprint"],
        ]


def test_master_refresh_isolates_invalid_bundle_from_valid_bundle(tmp_path: Path):
    _write_bundle(tmp_path, "bundle-good")
    _write_bundle(tmp_path, "bundle-bad", invalid=True)

    items = MasterResultRefreshService(tmp_path).refresh()
    by_path = {item.manifest_path: item for item in items}
    assert by_path["bundle-good/manifest.json"].status == "IMPORTED"
    assert by_path["bundle-bad/manifest.json"].status == "FAILED"
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_run_metadata WHERE source_type='MASTER_FOLDER_REFRESH'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM folder_import_jobs WHERE source_folder='bundle-bad/manifest.json' AND status='FAILED'").fetchone()[0] == 1


def test_master_refresh_records_captured_target_failure_without_live_reread(tmp_path: Path):
    bundle = _write_bundle(tmp_path, "bundle-captured-target")
    # The manifest is discovered, but its declared source disappears before
    # capture. The captured manifest still supplies the target IDs for the
    # diagnostic job; no live source reread can manufacture a result.
    (bundle / "scalar-results.json").unlink()

    items = MasterResultRefreshService(tmp_path).refresh()

    assert [(item.status, item.load_case_id) for item in items] == [("FAILED", LOAD_CASE_ID)]
    with connect() as conn:
        row = conn.execute(
            "SELECT status, summary_json FROM folder_import_jobs "
            "WHERE source_folder='bundle-captured-target/manifest.json'"
        ).fetchone()
    assert row[0] == "FAILED"
    assert json.loads(row[1])["error_code"] == "BUNDLE_MAPPING_FILE_UNSAFE"


def test_master_refresh_categorizes_nul_mapping_and_keeps_captured_target(tmp_path: Path):
    bundle = _write_bundle(tmp_path, "bundle-nul-path")
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mappings"][0]["path"] = "summary\x00.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    items = MasterResultRefreshService(tmp_path).refresh()

    assert [(item.status, item.load_case_id) for item in items] == [("FAILED", LOAD_CASE_ID)]
    with connect() as conn:
        row = conn.execute(
            "SELECT status, summary_json FROM folder_import_jobs "
            "WHERE source_folder='bundle-nul-path/manifest.json'"
        ).fetchone()
    assert row[0] == "FAILED"
    assert json.loads(row[1])["error_code"] == "BUNDLE_MAPPING_PATH_INVALID"


def test_master_refresh_redacts_unexpected_oserror_to_fixed_message(monkeypatch, tmp_path: Path):
    _write_bundle(tmp_path, "bundle-os-error")

    def fail_capture(*_args, **_kwargs):
        raise OSError("/srv/private/simulation-results/secret.csv")

    monkeypatch.setattr(master_result_refresh, "capture_bundle", fail_capture)
    item = MasterResultRefreshService(tmp_path)._refresh_manifest("bundle-os-error/manifest.json")

    assert item.status == "FAILED"
    assert item.message == "결과 bundle 가져오기 중 내부 오류가 발생했습니다. 관리자에게 문의하세요."
    assert "/srv/private" not in (item.message or "")


def test_master_refresh_rejects_process_contention(monkeypatch, tmp_path: Path):
    _write_bundle(tmp_path, "bundle-lock-contention")
    assert master_result_refresh._refresh_lock.acquire(blocking=False)
    try:
        with pytest.raises(MasterResultRefreshError, match="새로고침이 진행 중"):
            MasterResultRefreshService(tmp_path).refresh()
    finally:
        master_result_refresh._refresh_lock.release()


def test_refresh_endpoint_returns_503_for_process_contention(monkeypatch, tmp_path: Path):
    _write_bundle(tmp_path, "bundle-lock-endpoint")
    monkeypatch.setenv("SIMDASH_IMPORT_ROOT", str(tmp_path))
    assert master_result_refresh._refresh_lock.acquire(blocking=False)
    try:
        with TestClient(app) as client:
            response = client.post("/api/result-imports/refresh")
    finally:
        master_result_refresh._refresh_lock.release()

    assert response.status_code == 503
    assert "새로고침이 진행 중" in response.json()["detail"]


def test_refresh_failure_job_rejects_mismatched_target_relationship(tmp_path: Path):
    service = MasterResultRefreshService(tmp_path)
    source_folder = "bundle-mismatched-target/manifest.json"

    service._record_failure(
        ("project-does-not-match", REQUEST_ID, LOAD_CASE_ID),
        source_folder,
        "safe diagnostic",
        "TEST_TARGET_MISMATCH",
    )

    with connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM folder_import_jobs WHERE source_folder=?",
            [source_folder],
        ).fetchone()[0] == 0


def test_master_refresh_rejects_manifest_symlink_outside_configured_root(tmp_path: Path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    _write_bundle(outside, "escaped")
    linked_bundle = tmp_path / "bundle-link"
    linked_bundle.mkdir()
    try:
        (linked_bundle / "manifest.json").symlink_to(outside / "escaped" / "manifest.json")
    except OSError:
        pytest.skip("현재 파일 시스템은 심볼릭 링크 테스트를 지원하지 않습니다.")

    items = MasterResultRefreshService(tmp_path).refresh()
    assert len(items) == 1
    assert items[0].status == "FAILED"
    assert "심볼릭 링크" in (items[0].message or "")
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_run_metadata WHERE source_type='MASTER_FOLDER_REFRESH'").fetchone()[0] == 0


def test_refresh_endpoint_is_registered_without_a_client_path_parameter():
    route = next(route for route in app.routes if getattr(route, "path", None) == "/api/result-imports/refresh")
    assert "POST" in route.methods
    assert not route.dependant.query_params


def test_refresh_endpoint_returns_503_for_invalid_bundle_limit_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("SIMDASH_IMPORT_ROOT", str(tmp_path))
    monkeypatch.setenv("SIMDASH_IMPORT_MAX_MAPPING_COUNT", "0")

    with TestClient(app) as client:
        response = client.post("/api/result-imports/refresh")

    assert response.status_code == 503
    assert "SIMDASH_IMPORT_MAX_MAPPING_COUNT" in response.json()["detail"]
