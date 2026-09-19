from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters.persistence.dashboard_schema import ensure_dashboard_schema
from app.database_connection import connect
from app.services.dashboard_capture import (
    _distribution_payload,
    _root_id,
    _walk,
    create_capture,
    find_asset,
    get_capture,
)
from app.services.dashboard_queries import distribution


pytestmark = pytest.mark.duckdb_integration


def _request_context(connection) -> tuple[str, str]:
    row = connection.execute("SELECT project_id,id FROM analysis_requests ORDER BY id LIMIT 1").fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def test_walk_preserves_allowed_bytes_and_explains_skipped_entries(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    case = root / "case"
    result = case / "Drop" / "run" / "INDIVIDUAL" / "scene"
    result.mkdir(parents=True)
    expected = result / "unknown.csv"
    expected.write_bytes(b"arbitrary,shape\n1,2\n")
    (result / "not-a-result.txt").write_bytes(b"kept outside capture policy")
    excluded = case / "Report"
    excluded.mkdir()
    (excluded / "report.csv").write_bytes(b"private report")

    issues: list[str] = []
    files = _walk(root, "case", issues=issues)

    assert [(path, data) for path, data, _ in files] == [
        ("case/Drop/run/INDIVIDUAL/scene/unknown.csv", b"arbitrary,shape\n1,2\n")
    ]
    assert "UNPROCESSED_FILE:case/Report:EXCLUDED_DIRECTORY" in issues
    assert "UNPROCESSED_FILE:case/Drop/run/INDIVIDUAL/scene/not-a-result.txt:UNSUPPORTED_EXTENSION" in issues


def test_distribution_payload_reports_unknown_and_deep_paths() -> None:
    paths = [
        "case/Drop/run/INDIVIDUAL/scene/unknown.csv",
        "case/Drop/run/INDIVIDUAL/scene/deeper/unknown.csv",
        "case/Other/run/INDIVIDUAL/scene/unknown.csv",
    ]
    payload = _distribution_payload(
        "case",
        [(path, b"arbitrary,shape\n1,2\n", "text/csv") for path in paths],
        {"simulation_case_id": "case-id"},
        "root-id",
    )

    issues = set(payload["quality_issues"])
    assert "UNPROCESSED_FILE:case/Drop/run/INDIVIDUAL/scene/unknown.csv:UNRECOGNIZED_RESULT_FILE" in issues
    assert "UNPROCESSED_FILE:case/Drop/run/INDIVIDUAL/scene/deeper/unknown.csv:UNEXPECTED_RESULT_PATH_DEPTH" in issues
    assert "UNPROCESSED_FILE:case/Other/run/INDIVIDUAL/scene/unknown.csv:UNKNOWN_LOAD_CASE_DIRECTORY" in issues


def test_capture_keeps_unrecognized_csv_in_manifest_and_asset_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shared"
    scene = root / "distribution-case" / "Drop" / "run" / "INDIVIDUAL" / "1_Face_Drop_Scene01_Face1_1st"
    scene.mkdir(parents=True)
    (scene / "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv").write_text(
        "Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,1,2,3,4\n", encoding="utf-8"
    )
    unknown = scene / "unknown.csv"
    unknown.write_bytes(b"arbitrary,shape\n1,2\n")
    report = scene / "report.csv"
    report.write_bytes(b"report,shape\n1,2\n")
    final = scene / "final.csv"
    final.write_bytes(b"final,shape\n1,2\n")
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))

    with connect() as connection:
        ensure_dashboard_schema(connection)
        project_id, request_id = _request_context(connection)
        payload = {
            "project_id": project_id,
            "request_id": request_id,
            "simulation_case_id": "distribution-case-id",
            "load_case_id": None,
            "execution_run_id": None,
            "run_display_name": None,
            "mode": None,
            "component_id": None,
            "storage_root_id": _root_id(root),
            "root_relative_path": "distribution-case",
            "environment": "DISTRIBUTION",
        }
        result = create_capture(connection, payload, actor="test-actor")
        repeated = create_capture(connection, payload, actor="test-actor")
        assert repeated["id"] == result["id"]

        quality = result["payload"]["quality_issues"]
        assert "UNPROCESSED_FILE:distribution-case/Drop/run/INDIVIDUAL/1_Face_Drop_Scene01_Face1_1st/unknown.csv:UNRECOGNIZED_RESULT_FILE" in quality
        assert "UNPROCESSED_FILE:distribution-case/Drop/run/INDIVIDUAL/1_Face_Drop_Scene01_Face1_1st/report.csv:UNRECOGNIZED_RESULT_FILE" in quality
        assert "UNPROCESSED_FILE:distribution-case/Drop/run/INDIVIDUAL/1_Face_Drop_Scene01_Face1_1st/final.csv:UNRECOGNIZED_RESULT_FILE" in quality
        stored_manifest = connection.execute(
            "SELECT manifest_json FROM dashboard_captures WHERE id=?", [result["id"]]
        ).fetchone()[0]
        manifest_paths = {item["relative_path"] for item in json.loads(stored_manifest)}
        assert all(path in manifest_paths for path in (
            "distribution-case/Drop/run/INDIVIDUAL/1_Face_Drop_Scene01_Face1_1st/unknown.csv",
            "distribution-case/Drop/run/INDIVIDUAL/1_Face_Drop_Scene01_Face1_1st/report.csv",
            "distribution-case/Drop/run/INDIVIDUAL/1_Face_Drop_Scene01_Face1_1st/final.csv",
        ))
        for name, content in (("unknown.csv", b"arbitrary,shape\n1,2\n"), ("report.csv", b"report,shape\n1,2\n"), ("final.csv", b"final,shape\n1,2\n")):
            asset_row = connection.execute(
                "SELECT id FROM dashboard_assets WHERE capture_id=? AND relative_path LIKE ?",
                [result["id"], f"%/{name}"],
            ).fetchone()
            assert asset_row is not None
            asset = find_asset(connection, str(asset_row[0]))
            assert asset is not None and bytes(asset["content"]) == content

        excluded = root / "distribution-case" / "Drop" / "run" / "INDIVIDUAL" / "Report"
        excluded.mkdir()
        (excluded / "excluded.csv").write_bytes(b"excluded")
        with_excluded = create_capture(connection, payload, actor="test-actor")
        assert with_excluded["id"] != result["id"]
        excluded_issue = "UNPROCESSED_FILE:distribution-case/Drop/run/INDIVIDUAL/Report:EXCLUDED_DIRECTORY"
        assert excluded_issue in with_excluded["payload"]["quality_issues"]
        old = get_capture(connection, result["id"])
        assert old is not None and excluded_issue not in old["payload"]["quality_issues"]

        (excluded / "excluded.csv").unlink()
        excluded.rmdir()
        after_delete = create_capture(connection, payload, actor="test-actor")
        assert after_delete["id"] != with_excluded["id"]
        retained = get_capture(connection, with_excluded["id"])
        assert retained is not None and excluded_issue in retained["payload"]["quality_issues"]
        capture = get_capture(connection, result["id"])
        assert capture is not None
        run = capture["payload"]["runs"][0]
        projected = distribution(capture, run["id"], run["mode"], "C23", "REPORTED_SUMMARY", {"TOP"}, {1, 2, 3, 4})
        assert any(issue.endswith(":UNRECOGNIZED_RESULT_FILE") for issue in projected["quality_issues"])
        contour_value = projected["contours"][0]["value"]
        assert contour_value["value"] == projected["location_peaks"][0]["value"] == 4
        assert contour_value["basis"] == "REPORTED_SUMMARY"
        assert contour_value["scope"] == "EXTRACTED_SIDES_AND_CORNERS"
        assert contour_value["unit"] is None
        assert contour_value["source_refs"]
        subset = distribution(capture, run["id"], run["mode"], "C23", "REPORTED_SUMMARY", {"TOP"}, {1})
        assert subset["contours"][0]["value"]["value"] == 1
        assert subset["contours"][0]["value"]["scope"] == "EXTRACTED_SELECTED_LINES"
        missing = distribution(capture, run["id"], run["mode"], "C23", "DETAIL", {"TOP"}, {1})
        assert missing["contours"][0]["value"] is None
