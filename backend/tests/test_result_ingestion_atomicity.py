from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import connect, initialize_database
from app.main import app
from app.adapters.persistence.result_ingestion import SQLResultIngestionUnitOfWork
from app.services.master_result_refresh import MasterResultRefreshService


PROJECT_ID = "project-tv-001"
REQUEST_ID = "request-drop-001"
LOAD_CASE_ID = "loadcase-drop-bottom-001"


def _write_bundle(root: Path, name: str = "bundle-atomic") -> None:
    bundle = root / name
    bundle.mkdir()
    (bundle / "scalar-results.json").write_text(
        json.dumps(
            [
                {
                    "variable_key": "atomic_peak",
                    "display_name": "Atomic peak",
                    "data_type": "FLOAT",
                    "value": 12.5,
                    "unit": "MPa",
                    "threshold": 75.0,
                    "result_group": "OPEN_CELL",
                }
            ]
        ),
        encoding="utf-8",
    )
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "schema_id": "atomic-test",
                "version": 1,
                "solver": "pytest",
                "context": {
                    "project_id": PROJECT_ID,
                    "request_id": REQUEST_ID,
                    "load_case_id": LOAD_CASE_ID,
                },
                "mappings": [{"kind": "typed_scalars", "path": "scalar-results.json"}],
            }
        ),
        encoding="utf-8",
    )


def _cleanup() -> None:
    with connect() as conn:
        run_ids = [
            row[0]
            for row in conn.execute(
                "SELECT analysis_run_id FROM analysis_run_metadata WHERE source_type='MASTER_FOLDER_REFRESH'"
            ).fetchall()
        ]
        for run_id in run_ids:
            conn.execute("DELETE FROM media_assets WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM qualitative_notes WHERE analysis_run_id=?", [run_id])
            curve_ids = [row[0] for row in conn.execute("SELECT id FROM curve_results WHERE analysis_run_id=?", [run_id]).fetchall()]
            for curve_id in curve_ids:
                conn.execute("DELETE FROM curve_points WHERE curve_id=?", [curve_id])
            conn.execute("DELETE FROM curve_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM time_series_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM scalar_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM analysis_run_metadata WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM analysis_runs WHERE id=?", [run_id])
        conn.execute("DELETE FROM folder_import_jobs WHERE source_folder LIKE 'bundle-%/manifest.json'")
        conn.execute("DELETE FROM variable_definitions WHERE load_case_id=? AND variable_key='atomic_peak'", [LOAD_CASE_ID])


@pytest.fixture(autouse=True)
def _database_cleanup():
    initialize_database()
    _cleanup()
    yield
    _cleanup()


def test_canonical_ingestion_rolls_back_all_result_rows_when_persistence_fails(monkeypatch, tmp_path: Path):
    """A failure after the first result write cannot leave a partial run behind."""
    _write_bundle(tmp_path)
    original = SQLResultIngestionUnitOfWork.add_results

    def fail_after_persist(unit_of_work, *args, **kwargs):
        original(unit_of_work, *args, **kwargs)
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(SQLResultIngestionUnitOfWork, "add_results", fail_after_persist)

    result = MasterResultRefreshService(tmp_path).refresh()

    assert result[0].status == "FAILED"
    with connect() as conn:
        assert conn.execute(
            "SELECT count(*) FROM analysis_runs ar JOIN analysis_run_metadata arm ON arm.analysis_run_id=ar.id "
            "WHERE ar.load_case_id=? AND arm.source_type='MASTER_FOLDER_REFRESH'",
            [LOAD_CASE_ID],
        ).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM analysis_run_metadata WHERE source_type='MASTER_FOLDER_REFRESH'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM scalar_results WHERE variable_key='atomic_peak'").fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM variable_definitions WHERE load_case_id=? AND variable_key='atomic_peak'",
            [LOAD_CASE_ID],
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM folder_import_jobs WHERE source_folder='bundle-atomic/manifest.json' AND status='FAILED'"
        ).fetchone()[0] == 1


def test_master_refresh_and_typed_example_routes_remain_registered():
    refresh = next(route for route in app.routes if getattr(route, "path", None) == "/api/result-imports/refresh")
    example = next(route for route in app.routes if getattr(route, "path", None) == "/api/load-cases/{load_case_id}/folder-import/example")
    assert "POST" in refresh.methods
    assert "POST" in example.methods


def test_master_refresh_endpoint_uses_configured_root(monkeypatch, tmp_path: Path):
    _write_bundle(tmp_path, "bundle-endpoint")
    monkeypatch.setenv("SIMDASH_IMPORT_ROOT", str(tmp_path))
    with TestClient(app) as client:
        response = client.post("/api/result-imports/refresh")
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["status"] == "IMPORTED"
