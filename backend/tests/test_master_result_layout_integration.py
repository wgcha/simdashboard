from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from app.database_connection import connect
from app.main import app
from app.services.master_result_refresh import MasterResultRefreshService


PROJECT_ID = "project-tv-001"
REQUEST_ID = "request-drop-001"
LOAD_CASE_ID = "loadcase-drop-bottom-001"


def test_canonical_master_result_example_imports_and_exposes_bindings_without_materialization(
    tmp_path: Path,
):
    """The seeded legacy request has no editable page to materialize.

    Its request-result-layout endpoint is still the dashboard consumer path:
    verify that it exposes the imported run and all contracts represented by
    the canonical scalar, curve, SVG, and glTF example files.
    """
    repository_example = Path(__file__).parents[2] / "examples" / "master-results"
    import_root = tmp_path / "master-results"
    shutil.copytree(repository_example, import_root)

    imported = MasterResultRefreshService(import_root, readiness_policy="required").refresh()
    assert [(item.status, item.load_case_id) for item in imported] == [("IMPORTED", LOAD_CASE_ID)]
    run_id = imported[0].analysis_run_id
    assert run_id

    with connect() as conn:
        assert conn.execute(
            "SELECT ar.project_id, lc.request_id, lc.status "
            "FROM load_cases lc JOIN analysis_requests ar ON ar.id=lc.request_id "
            "WHERE lc.id=?",
            [LOAD_CASE_ID],
        ).fetchone() == (PROJECT_ID, REQUEST_ID, "COMPLETED")
        assert conn.execute(
            "SELECT status FROM analysis_runs WHERE id=? AND load_case_id=?",
            [run_id, LOAD_CASE_ID],
        ).fetchone() == ("COMPLETED",)

        scalar_rows = conn.execute(
            "SELECT variable_key FROM scalar_results WHERE analysis_run_id=? ORDER BY variable_key",
            [run_id],
        ).fetchall()
        assert [row[0] for row in scalar_rows] == [
            "analysis_judgement",
            "analysis_verdict",
            "mesh_element_count",
            "top_edge_max_stress",
        ]

        curve_row = conn.execute(
            "SELECT variable_key, point_count FROM curve_results WHERE analysis_run_id=?",
            [run_id],
        ).fetchone()
        assert curve_row == ("open_cell_top_edge_stress_curve", 7)
        assert conn.execute(
            "SELECT count(*) FROM curve_points WHERE curve_id IN "
            "(SELECT id FROM curve_results WHERE analysis_run_id=?)",
            [run_id],
        ).fetchone()[0] == 7

        media_rows = conn.execute(
            "SELECT asset_type, original_filename, mime_type, blob_id "
            "FROM media_assets WHERE analysis_run_id=? ORDER BY original_filename",
            [run_id],
        ).fetchall()
        assert [(row[0], row[1], row[2]) for row in media_rows] == [
            ("MODEL_3D", "chassis_sample.gltf", "model/gltf+json"),
            ("IMAGE", "open_cell_stress_contour.svg", "image/svg+xml"),
        ]
        assert all(row[3] for row in media_rows)

    with TestClient(app) as client:
        response = client.get(
            f"/api/workbench/requests/{REQUEST_ID}/result-layout",
            params={"load_case_id": LOAD_CASE_ID},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["request_id"] == REQUEST_ID
    bindings = body["bindings"]
    assert bindings["latest_result_run"]["id"] == run_id
    assert bindings["latest_result_run"]["load_case_id"] == LOAD_CASE_ID
    assert bindings["available_data_contracts"] == [
        "CURVE",
        "LOAD_CASE",
        "MEDIA_ASSET",
        "RESULT_RUN",
        "SCALAR_RESULT",
        "TIME_SERIES",
    ]
    assert {
        scalar["variable_key"] for scalar in bindings["scalars"]
    } == {
        "analysis_judgement",
        "analysis_verdict",
        "mesh_element_count",
        "top_edge_max_stress",
    }
