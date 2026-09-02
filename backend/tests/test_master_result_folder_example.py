from __future__ import annotations

import shutil
from pathlib import Path

from app.database_connection import connect
from app.services.master_result_refresh import MasterResultRefreshService


PROJECT_ID = "project-tv-001"
REQUEST_ID = "request-drop-001"
LOAD_CASE_ID = "loadcase-drop-bottom-001"
RUN_FOLDER = "project-tv-001/request-drop-001/loadcase-drop-bottom-001/run-example-001"


def test_canonical_master_result_example_imports_typed_results_media_blobs_and_is_idempotent(tmp_path: Path):
    repository_example = Path(__file__).parents[2] / "examples" / "master-results"
    import_root = tmp_path / "master-results"
    shutil.copytree(repository_example, import_root)

    first = MasterResultRefreshService(import_root).refresh()
    assert [(item.status, item.load_case_id) for item in first] == [("IMPORTED", LOAD_CASE_ID)]
    run_id = first[0].analysis_run_id
    assert run_id

    with connect() as conn:
        scalar_rows = conn.execute(
            "SELECT variable_key, value_double, value_integer, value_text, verdict "
            "FROM scalar_results WHERE analysis_run_id=? ORDER BY variable_key",
            [run_id],
        ).fetchall()
        assert [row[0] for row in scalar_rows] == [
            "analysis_judgement",
            "analysis_verdict",
            "mesh_element_count",
            "top_edge_max_stress",
        ]
        assert scalar_rows[2][1:4] == (None, 428120, None)
        assert scalar_rows[3][1:4] == (68.0, None, None)
        assert scalar_rows[1][4] == "PASS"

        assert conn.execute(
            "SELECT count(*) FROM curve_results WHERE analysis_run_id=?", [run_id]
        ).fetchone()[0] == 1
        curve_id = conn.execute(
            "SELECT id FROM curve_results WHERE analysis_run_id=?", [run_id]
        ).fetchone()[0]
        assert conn.execute(
            "SELECT count(*) FROM curve_points WHERE curve_id=?", [curve_id]
        ).fetchone()[0] == 7

        media_rows = conn.execute(
            "SELECT asset_type, original_filename, mime_type, blob_id, file_size "
            "FROM media_assets WHERE analysis_run_id=? ORDER BY original_filename",
            [run_id],
        ).fetchall()
        assert [(row[0], row[1], row[2]) for row in media_rows] == [
            ("MODEL_3D", "chassis_sample.gltf", "model/gltf+json"),
            ("IMAGE", "open_cell_stress_contour.svg", "image/svg+xml"),
        ]
        assert all(row[3] and row[4] > 0 for row in media_rows)
        blob_ids = [row[3] for row in media_rows]
        assert conn.execute(
            "SELECT count(*) FROM asset_blobs WHERE id IN (?, ?)", blob_ids
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT count(*) FROM asset_blob_chunks WHERE blob_id IN (?, ?)", blob_ids
        ).fetchone()[0] == 2

        metadata = conn.execute(
            "SELECT source_name, schema_id, parser_version FROM analysis_run_metadata WHERE analysis_run_id=?",
            [run_id],
        ).fetchone()
        assert metadata == (
            f"{RUN_FOLDER}/manifest.json",
            "tv-drop-master-result-v1",
            "master-folder-refresh-v1",
        )
        assert conn.execute(
            "SELECT count(*) FROM folder_import_jobs WHERE analysis_run_id=? AND status='COMPLETED'",
            [run_id],
        ).fetchone()[0] == 1

    second = MasterResultRefreshService(import_root).refresh()
    assert [(item.status, item.load_case_id) for item in second] == [("SKIPPED", LOAD_CASE_ID)]

    with connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM analysis_run_metadata WHERE source_type='MASTER_FOLDER_REFRESH' "
            "AND source_name=?",
            [f"{RUN_FOLDER}/manifest.json"],
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM media_assets WHERE analysis_run_id=?", [run_id]
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT count(*) FROM folder_import_jobs WHERE source_folder=?", [f"{RUN_FOLDER}/manifest.json"]
        ).fetchone()[0] == 2
