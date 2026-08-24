from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.adapters.persistence.result_ingestion import SQLResultIngestionUnitOfWork
from app.database import connect, initialize_database
from app.main import app
from app.services.manual_result_ingestion_adapter import (
    ManualResultIngestionAdapterError,
    to_canonical_result_payload,
)


pytestmark = pytest.mark.duckdb_integration

LOAD_CASE_ID = "loadcase-drop-bottom-001"


def _parsed_summary(*, inconsistent: bool = False) -> dict:
    return {
        "solver": "Manual Solver",
        "note": "manual note",
        "scalars": [
            {
                "variable_key": "top_edge_max_stress",
                "display_name": "상단 엣지 최대 응력",
                "value": 60.0,
                "unit": "MPa",
                "threshold": 75.0,
                "analysis": "OPEN_CELL",
            }
        ],
        "time_series": [
            {
                "variable_key": "custom_manual_series",
                "display_name": "커스텀 이력",
                "time": 0.0,
                "value": 1.0,
                "time_unit": "ms",
                "value_unit": "MPa",
            },
            {
                "variable_key": "custom_manual_series",
                "display_name": "다른 이름" if inconsistent else "커스텀 이력",
                "time": 1.0,
                "value": 2.0,
                "time_unit": "ms",
                "value_unit": "MPa",
            },
        ],
    }


def test_manual_adapter_groups_series_and_preserves_catalog_semantics():
    parsed = to_canonical_result_payload(
        _parsed_summary(),
        source_file="result.json",
        source_checksum="a" * 64,
    )

    assert parsed["schema_id"] == "manual-file-upload"
    assert parsed["schema_version"] == 1
    assert parsed["scalars"][0]["data_type"] == "FLOAT"
    assert parsed["scalars"][0]["source_file"] == "result.json"
    assert parsed["curves"] == [
        {
            "variable_key": "custom_manual_series",
            "display_name": "커스텀 이력",
            "series_key": "default",
            "catalog_data_type": "TIME_SERIES",
            "x_label": "시간",
            "x_unit": "ms",
            "y_label": "커스텀 이력",
            "y_unit": "MPa",
            "result_group": "CUSTOM",
            "points": [{"x": 0.0, "y": 1.0}, {"x": 1.0, "y": 2.0}],
            "source_file": "result.json",
            "source_checksum": "a" * 64,
        }
    ]


def test_manual_adapter_uses_legacy_open_cell_series_group_rule():
    parsed = _parsed_summary()
    parsed["time_series"][0]["variable_key"] = "top_edge_stress_time"
    parsed["time_series"][1]["variable_key"] = "top_edge_stress_time"
    canonical = to_canonical_result_payload(parsed, source_file="result.csv", source_checksum="b" * 64)
    assert canonical["curves"][0]["result_group"] == "OPEN_CELL"


def test_manual_adapter_rejects_inconsistent_curve_metadata():
    with pytest.raises(ManualResultIngestionAdapterError, match="일관되지 않습니다"):
        to_canonical_result_payload(
            _parsed_summary(inconsistent=True),
            source_file="result.json",
            source_checksum="c" * 64,
        )


def _create_time_series_catalog(client: TestClient) -> None:
    response = client.post(
        f"/api/load-cases/{LOAD_CASE_ID}/variables",
        json={
            "variable_key": "custom_manual_series",
            "display_name": "커스텀 이력",
            "data_type": "TIME_SERIES",
            "unit": "MPa",
            "description": "manual upload test",
            "result_group": "CUSTOM",
            "updated_by": "테스트 관리자",
        },
    )
    assert response.status_code == 201, response.text


def _cleanup_manual_rows() -> None:
    with connect() as conn:
        run_ids = [
            row[0]
            for row in conn.execute(
                "SELECT analysis_run_id FROM analysis_run_metadata WHERE source_type='FILE_UPLOAD'"
            ).fetchall()
        ]
        for run_id in run_ids:
            curve_ids = [row[0] for row in conn.execute("SELECT id FROM curve_results WHERE analysis_run_id=?", [run_id]).fetchall()]
            for curve_id in curve_ids:
                conn.execute("DELETE FROM curve_points WHERE curve_id=?", [curve_id])
            conn.execute("DELETE FROM curve_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM time_series_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM scalar_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM qualitative_notes WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM analysis_run_metadata WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM analysis_runs WHERE id=?", [run_id])
        conn.execute("DELETE FROM folder_import_jobs WHERE source_folder LIKE 'loadcase-drop-bottom-001/%'")
        conn.execute("DELETE FROM variable_definitions WHERE load_case_id=? AND variable_key='custom_manual_series'", [LOAD_CASE_ID])


def test_manual_summary_json_uses_canonical_uow_and_retry_skips():
    initialize_database()
    _cleanup_manual_rows()
    payload = {
        "filename": "manual-uow-retry.json",
        "content": json.dumps(
            {
                "solver": "Manual Solver",
                "note": "manual note",
                "scalar_results": [
                    {"variable_key": "top_edge_max_stress", "value": 60.0, "unit": "MPa", "threshold": 75.0}
                ],
                "time_series": [
                    {"variable_key": "custom_manual_series", "time": 0, "value": 1, "time_unit": "ms", "value_unit": "MPa"},
                    {"variable_key": "custom_manual_series", "time": 1, "value": 2, "time_unit": "ms", "value_unit": "MPa"},
                ],
            },
            ensure_ascii=False,
        ),
        "author": "업로드 화면 작성자",
    }
    try:
        with TestClient(app) as client:
            _create_time_series_catalog(client)
            first = client.post(f"/api/load-cases/{LOAD_CASE_ID}/results/import", json=payload)
            assert first.status_code == 200, first.text
            assert first.json()["status"] == "IMPORTED"
            run_id = first.json()["run_id"]

            with connect() as conn:
                metadata = conn.execute(
                    "SELECT source_type, source_name, source_checksum, schema_id, schema_version, parser_version, metadata_json FROM analysis_run_metadata WHERE analysis_run_id=?",
                    [run_id],
                ).fetchone()
                assert metadata[0:6] == (
                    "FILE_UPLOAD",
                    f"{LOAD_CASE_ID}/manual-uow-retry.json",
                    metadata[2],
                    "manual-file-upload",
                    1,
                    "result-import-v1",
                )
                assert json.loads(metadata[6])["submitted_author"] == "업로드 화면 작성자"
                assert conn.execute("SELECT count(*) FROM curve_results WHERE analysis_run_id=?", [run_id]).fetchone()[0] == 1
                assert conn.execute("SELECT count(*) FROM time_series_results WHERE analysis_run_id=?", [run_id]).fetchone()[0] == 2
                assert conn.execute("SELECT author FROM qualitative_notes WHERE analysis_run_id=?", [run_id]).fetchone()[0] == "로컬 관리자"
                assert conn.execute(
                    "SELECT data_type FROM variable_definitions WHERE load_case_id=? AND variable_key=?",
                    [LOAD_CASE_ID, "custom_manual_series"],
                ).fetchone()[0] == "TIME_SERIES"
                audit_row = conn.execute(
                    "SELECT count(*), min(status_code) FROM audit_events "
                    "WHERE action='RESULT_IMPORTED' AND detail_json LIKE ?",
                    [f"%{run_id}%"],
                ).fetchone()
                assert audit_row == (1, 200)

            retry = client.post(f"/api/load-cases/{LOAD_CASE_ID}/results/import", json=payload)
            assert retry.status_code == 200, retry.text
            assert retry.json()["status"] == "SKIPPED"
            assert retry.json()["run_id"] is None
            with connect() as conn:
                assert conn.execute("SELECT count(*) FROM analysis_runs WHERE id=?", [run_id]).fetchone()[0] == 1
                assert conn.execute("SELECT count(*) FROM analysis_run_metadata WHERE source_type='FILE_UPLOAD'").fetchone()[0] == 1
                assert conn.execute("SELECT count(*) FROM folder_import_jobs WHERE source_folder=?", [f"{LOAD_CASE_ID}/manual-uow-retry.json"]).fetchone()[0] == 2
    finally:
        _cleanup_manual_rows()


def test_manual_summary_csv_uses_canonical_uow():
    initialize_database()
    _cleanup_manual_rows()
    csv_content = (
        "record_type,variable_key,display_name,value,unit,threshold,time,time_unit,value_unit\n"
        "scalar,top_edge_max_stress,상단 엣지 최대 응력,60,MPa,75,,,\n"
        "time_series,top_edge_stress_time,상단 엣지,1,MPa,,0,ms,MPa\n"
        "time_series,top_edge_stress_time,상단 엣지,2,MPa,,1,ms,MPa\n"
    )
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/load-cases/{LOAD_CASE_ID}/results/import",
                json={"filename": "manual-uow.csv", "content": csv_content, "author": "CSV 작성자"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "IMPORTED"
            run_id = response.json()["run_id"]
        with connect() as conn:
            assert conn.execute("SELECT count(*) FROM curve_results WHERE analysis_run_id=?", [run_id]).fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM time_series_results WHERE analysis_run_id=?", [run_id]).fetchone()[0] == 2
            assert conn.execute(
                "SELECT schema_id FROM analysis_run_metadata WHERE analysis_run_id=?", [run_id]
            ).fetchone()[0] == "manual-file-upload"
    finally:
        _cleanup_manual_rows()


def test_manual_validate_only_does_not_mutate_and_rechecks_authorization(monkeypatch):
    initialize_database()
    _cleanup_manual_rows()
    payload = {
        "filename": "manual-validate.json",
        "content": json.dumps({"scalar_results": [{"variable_key": "top_edge_max_stress", "value": 20}]}),
        "author": "검증 작성자",
        "validate_only": True,
    }
    with TestClient(app) as client:
        _create_time_series_catalog(client)
    authorization_connections = []
    original_authorize = main_module.require_resource_permission

    def track_authorization(*args, **kwargs):
        authorization_connections.append(kwargs.get("conn"))
        return original_authorize(*args, **kwargs)

    monkeypatch.setattr(main_module, "require_resource_permission", track_authorization)
    try:
        with TestClient(app) as client:
            with connect() as conn:
                before = {
                    table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in ("analysis_runs", "analysis_run_metadata", "folder_import_jobs")
                }
            response = client.post(f"/api/load-cases/{LOAD_CASE_ID}/results/import", json=payload)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "VALID"
            inconsistent = client.post(
                f"/api/load-cases/{LOAD_CASE_ID}/results/import",
                json={
                    **payload,
                    "filename": "manual-inconsistent.json",
                    "validate_only": False,
                    "content": json.dumps(
                        {
                            "scalar_results": [{"variable_key": "top_edge_max_stress", "value": 20}],
                            "time_series": [
                                {"variable_key": "custom_manual_series", "time": 0, "value": 1, "time_unit": "ms", "value_unit": "MPa"},
                                {"variable_key": "custom_manual_series", "time": 1, "value": 2, "time_unit": "s", "value_unit": "MPa"},
                            ],
                        }
                    ),
                },
            )
            assert inconsistent.status_code == 422, inconsistent.text
            assert len(authorization_connections) == 2
            with connect() as conn:
                after = {
                    table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in before
                }
            assert after == before
    finally:
        _cleanup_manual_rows()


def test_manual_summary_authorization_is_rechecked_in_write_transaction_and_rolls_back(monkeypatch):
    initialize_database()
    _cleanup_manual_rows()
    payload = {
        "filename": "manual-rollback.json",
        "content": json.dumps({"scalar_results": [{"variable_key": "top_edge_max_stress", "value": 20}]}),
    }
    authorization_connections = []
    original_authorize = main_module.require_resource_permission
    original_add_results = SQLResultIngestionUnitOfWork.add_results

    def track_authorization(*args, **kwargs):
        authorization_connections.append(kwargs.get("conn"))
        return original_authorize(*args, **kwargs)

    def fail_after_persist(unit_of_work, *args, **kwargs):
        original_add_results(unit_of_work, *args, **kwargs)
        raise RuntimeError("injected manual persistence failure")

    monkeypatch.setattr(main_module, "require_resource_permission", track_authorization)
    monkeypatch.setattr(SQLResultIngestionUnitOfWork, "add_results", fail_after_persist)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(f"/api/load-cases/{LOAD_CASE_ID}/results/import", json=payload)
    assert response.status_code == 500
    assert len(authorization_connections) == 2
    assert authorization_connections[0] is authorization_connections[1]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_run_metadata WHERE source_type='FILE_UPLOAD'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM folder_import_jobs WHERE source_folder=?", [f"{LOAD_CASE_ID}/manual-rollback.json"]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='RESULT_IMPORTED' AND path LIKE '%results/import'").fetchone()[0] == 0
    _cleanup_manual_rows()
