from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.routers import result_ingestion as result_ingestion_module
from app.adapters.persistence.result_ingestion import (
    SQLResultIngestionQuery,
    SQLResultIngestionUnitOfWork,
)
from app.database import connect, initialize_database
from app.main import app
from app.services.manual_result_ingestion_adapter import (
    ManualResultIngestionAdapterError,
    to_canonical_result_payload,
)
from app.result_import import parse_result_file
from app.services.radioss_result_ingestion_adapter import to_canonical_radioss_result_payload


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
        conn.execute("DELETE FROM folder_import_jobs WHERE source_folder LIKE 'loadcase-drop-bottom-001/%'")
        for run_id in run_ids:
            curve_ids = [row[0] for row in conn.execute("SELECT id FROM curve_results WHERE analysis_run_id=?", [run_id]).fetchall()]
            for curve_id in curve_ids:
                conn.execute("DELETE FROM curve_points WHERE curve_id=?", [curve_id])
            conn.execute("DELETE FROM curve_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM time_series_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM scalar_results WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM result_locations WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM qualitative_notes WHERE analysis_run_id=?", [run_id])
            conn.execute(
                "DELETE FROM canonical_result_ingestion_source_versions "
                "WHERE analysis_run_id=? OR supersedes_analysis_run_id=?",
                [run_id, run_id],
            )
            conn.execute("DELETE FROM analysis_run_metadata WHERE analysis_run_id=?", [run_id])
            conn.execute("DELETE FROM analysis_runs WHERE id=?", [run_id])
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
            assert retry.json()["run_id"] == run_id
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


def test_radioss_adapter_preserves_parser_scalars_series_and_locations():
    sample_path = Path(__file__).resolve().parents[2] / "examples" / "radioss" / "radioss_tv_result_example.csv"
    content = sample_path.read_text(encoding="utf-8")
    parsed = parse_result_file("radioss-example.csv", content)
    checksum = "d" * 64
    canonical = to_canonical_radioss_result_payload(parsed, source_file=sample_path.name, source_checksum=checksum)

    expected_scalars = [
        ("top_edge_max_stress", "상단 엣지 최대 응력", 74.4, "MPa", 75.0, "OPEN_CELL"),
        ("bottom_edge_max_stress", "하단 엣지 최대 응력", 84.0, "MPa", 75.0, "OPEN_CELL"),
        ("left_edge_max_stress", "좌측 엣지 최대 응력", 84.0, "MPa", 75.0, "OPEN_CELL"),
        ("right_edge_max_stress", "우측 엣지 최대 응력", 82.95, "MPa", 75.0, "OPEN_CELL"),
        ("chassis_rear_top_edge_gap_permanent_deformation", "상단 엣지 최대 이격", 6.401757571167466, "mm", 5.0, "CHASSIS_REAR"),
        ("chassis_rear_bottom_edge_gap_permanent_deformation", "하단 엣지 최대 이격", 4.302615483633183, "mm", 5.0, "CHASSIS_REAR"),
        ("chassis_rear_corner_top_left_permanent_deformation", "좌상단 모서리 영구변형", 5.2, "mm", 5.0, "CHASSIS_REAR"),
        ("chassis_rear_corner_top_right_permanent_deformation", "우상단 모서리 영구변형", 3.1, "mm", 5.0, "CHASSIS_REAR"),
        ("chassis_rear_corner_bottom_left_permanent_deformation", "좌하단 모서리 영구변형", 2.4, "mm", 5.0, "CHASSIS_REAR"),
        ("chassis_rear_corner_bottom_right_permanent_deformation", "우하단 모서리 영구변형", 5.4, "mm", 5.0, "CHASSIS_REAR"),
    ]
    assert len(canonical["scalars"]) == len(expected_scalars)
    for actual, (key, display_name, value, unit, threshold, result_group) in zip(canonical["scalars"], expected_scalars):
        assert actual["variable_key"] == key
        assert actual["display_name"] == display_name
        assert actual["data_type"] == "FLOAT"
        assert actual["value"] == pytest.approx(value)
        assert actual["unit"] == unit
        assert actual["threshold"] == threshold
        assert actual["result_group"] == result_group
        assert actual["source_file"] == sample_path.name
        assert actual["source_checksum"] == checksum

    expected_curves = [
        {
            "variable_key": "top_edge_stress_time",
            "display_name": "상단 엣지",
            "series_key": "default",
            "catalog_data_type": "TIME_SERIES",
            "x_label": "시간",
            "x_unit": "ms",
            "y_label": "상단 엣지",
            "y_unit": "MPa",
            "result_group": "OPEN_CELL",
            "points": [{"x": 0.0, "y": 5.952}, {"x": 10.0, "y": 74.4}, {"x": 30.0, "y": 8.928}],
            "source_file": sample_path.name,
            "source_checksum": checksum,
        },
        {
            "variable_key": "bottom_edge_stress_time",
            "display_name": "하단 엣지",
            "series_key": "default",
            "catalog_data_type": "TIME_SERIES",
            "x_label": "시간",
            "x_unit": "ms",
            "y_label": "하단 엣지",
            "y_unit": "MPa",
            "result_group": "OPEN_CELL",
            "points": [{"x": 0.0, "y": 6.72}, {"x": 10.0, "y": 84.0}, {"x": 30.0, "y": 10.08}],
            "source_file": sample_path.name,
            "source_checksum": checksum,
        },
        {
            "variable_key": "left_edge_stress_time",
            "display_name": "좌측 엣지",
            "series_key": "default",
            "catalog_data_type": "TIME_SERIES",
            "x_label": "시간",
            "x_unit": "ms",
            "y_label": "좌측 엣지",
            "y_unit": "MPa",
            "result_group": "OPEN_CELL",
            "points": [{"x": 0.0, "y": 6.72}, {"x": 10.0, "y": 84.0}, {"x": 30.0, "y": 10.08}],
            "source_file": sample_path.name,
            "source_checksum": checksum,
        },
        {
            "variable_key": "right_edge_stress_time",
            "display_name": "우측 엣지",
            "series_key": "default",
            "catalog_data_type": "TIME_SERIES",
            "x_label": "시간",
            "x_unit": "ms",
            "y_label": "우측 엣지",
            "y_unit": "MPa",
            "result_group": "OPEN_CELL",
            "points": [{"x": 0.0, "y": 6.636}, {"x": 10.0, "y": 82.95}, {"x": 30.0, "y": 9.954}],
            "source_file": sample_path.name,
            "source_checksum": checksum,
        },
    ]
    assert canonical["curves"] == expected_curves

    expected_locations = [
        {"variable_key": "top_edge_max_stress", "entity_type": "ELEMENT", "entity_id": "5016", "x": 1400.0, "y": 787.5, "z": 0.0, "time": 10.0, "time_unit": "ms", "method": "MAX_PRINCIPAL / 18% EDGE BAND"},
        {"variable_key": "bottom_edge_max_stress", "entity_type": "ELEMENT", "entity_id": "5001", "x": 200.0, "y": 112.5, "z": 0.0, "time": 10.0, "time_unit": "ms", "method": "MAX_PRINCIPAL / 18% EDGE BAND"},
        {"variable_key": "left_edge_max_stress", "entity_type": "ELEMENT", "entity_id": "5001", "x": 200.0, "y": 112.5, "z": 0.0, "time": 10.0, "time_unit": "ms", "method": "MAX_PRINCIPAL / 18% EDGE BAND"},
        {"variable_key": "right_edge_max_stress", "entity_type": "ELEMENT", "entity_id": "5004", "x": 1400.0, "y": 112.5, "z": 0.0, "time": 10.0, "time_unit": "ms", "method": "MAX_PRINCIPAL / 18% EDGE BAND"},
        {"variable_key": "chassis_rear_top_edge_gap_permanent_deformation", "entity_type": "NODE", "entity_id": "2014", "x": 800.0, "y": 960.15, "z": -28.6, "time": 30.0, "time_unit": "ms", "method": "MAX 3D DISTANCE TO DEFORMED ENDPOINT CHORD"},
        {"variable_key": "chassis_rear_bottom_edge_gap_permanent_deformation", "entity_type": "NODE", "entity_id": "2005", "x": 800.0, "y": -59.85, "z": -30.7, "time": 30.0, "time_unit": "ms", "method": "MAX 3D DISTANCE TO DEFORMED ENDPOINT CHORD"},
        {"variable_key": "chassis_rear_corner_top_left_permanent_deformation", "entity_type": "NODE", "entity_id": "2010", "x": -5.2, "y": 960.0, "z": -35.0, "time": 30.0, "time_unit": "ms", "method": "FINAL DISPLACEMENT MAGNITUDE"},
        {"variable_key": "chassis_rear_corner_top_right_permanent_deformation", "entity_type": "NODE", "entity_id": "2018", "x": 1603.1, "y": 960.0, "z": -35.0, "time": 30.0, "time_unit": "ms", "method": "FINAL DISPLACEMENT MAGNITUDE"},
        {"variable_key": "chassis_rear_corner_bottom_left_permanent_deformation", "entity_type": "NODE", "entity_id": "2001", "x": -2.4, "y": -60.0, "z": -35.0, "time": 30.0, "time_unit": "ms", "method": "FINAL DISPLACEMENT MAGNITUDE"},
        {"variable_key": "chassis_rear_corner_bottom_right_permanent_deformation", "entity_type": "NODE", "entity_id": "2009", "x": 1605.4, "y": -60.0, "z": -35.0, "time": 30.0, "time_unit": "ms", "method": "FINAL DISPLACEMENT MAGNITUDE"},
    ]
    assert canonical["locations"] == expected_locations


def test_radioss_canonical_uow_preserves_metadata_and_identical_retry_skips():
    initialize_database()
    _cleanup_manual_rows()
    try:
        with TestClient(app) as client:
            sample = client.get("/api/result-import/template/radioss-csv").text
            payload = {"filename": "radioss-uow.csv", "content": sample, "author": "Radioss 작성자"}
            parsed = parse_result_file(payload["filename"], sample)
            first = client.post(f"/api/load-cases/{LOAD_CASE_ID}/results/import", json=payload)
            assert first.status_code == 200, first.text
            assert first.json()["status"] == "IMPORTED"
            assert first.json()["filename"] == payload["filename"]
            assert first.json()["source_format"] == "RADIOSS_MESH_CSV"
            assert first.json()["scalar_count"] == len(parsed["scalars"])
            assert first.json()["time_series_count"] == len(parsed["time_series"])
            run_id = first.json()["run_id"]
            retry = client.post(f"/api/load-cases/{LOAD_CASE_ID}/results/import", json=payload)
            assert retry.status_code == 200, retry.text
            assert retry.json()["status"] == "SKIPPED"
            with connect() as conn:
                metadata = conn.execute(
                    "SELECT source_name, source_checksum, schema_id, schema_version, metadata_json "
                    "FROM analysis_run_metadata WHERE analysis_run_id=?",
                    [run_id],
                ).fetchone()
                assert metadata[0] == f"{LOAD_CASE_ID}/radioss-uow.csv"
                assert metadata[2:4] == ("radioss-mesh-csv", 1)
                assert json.loads(metadata[4])["author_user_id"] == "local-admin"
                assert conn.execute("SELECT count(*) FROM result_locations WHERE analysis_run_id=?", [run_id]).fetchone()[0] == 10
                scalar_rows = conn.execute(
                    "SELECT variable_key, display_name, value_double, value_integer, value_text, unit, threshold_double, verdict "
                    "FROM scalar_results WHERE analysis_run_id=? ORDER BY variable_key",
                    [run_id],
                ).fetchall()
                expected_scalars = sorted(parsed["scalars"], key=lambda item: item["variable_key"])
                assert len(scalar_rows) == len(expected_scalars)
                for row, expected in zip(scalar_rows, expected_scalars):
                    assert row[0] == expected["variable_key"]
                    assert row[1] == expected["display_name"]
                    assert row[2] == pytest.approx(expected["value"])
                    assert row[3] is None
                    assert row[4] is None
                    assert row[5] == expected["unit"]
                    assert row[6] == expected["threshold"]
                    assert row[7] == expected["verdict"]

                time_series_rows = conn.execute(
                    "SELECT variable_key, display_name, time_value, value, time_unit, value_unit "
                    "FROM time_series_results WHERE analysis_run_id=? ORDER BY variable_key, time_value",
                    [run_id],
                ).fetchall()
                expected_series = sorted(parsed["time_series"], key=lambda item: (item["variable_key"], item["time"]))
                assert len(time_series_rows) == len(expected_series)
                for row, expected in zip(time_series_rows, expected_series):
                    assert row[0] == expected["variable_key"]
                    assert row[1] == expected["display_name"]
                    assert row[2] == pytest.approx(expected["time"])
                    assert row[3] == pytest.approx(expected["value"])
                    assert row[4] == expected["time_unit"]
                    assert row[5] == expected["value_unit"]

                location_rows = conn.execute(
                    "SELECT variable_key, entity_type, entity_id, x, y, z, time_value, time_unit, method "
                    "FROM result_locations WHERE analysis_run_id=? ORDER BY variable_key",
                    [run_id],
                ).fetchall()
                expected_locations = sorted(parsed["locations"], key=lambda item: item["variable_key"])
                assert len(location_rows) == len(expected_locations)
                for row, expected in zip(location_rows, expected_locations):
                    assert row[0] == expected["variable_key"]
                    assert row[1] == expected["entity_type"]
                    assert row[2] == expected["entity_id"]
                    assert row[3] == pytest.approx(expected["x"])
                    assert row[4] == pytest.approx(expected["y"])
                    assert row[5] == pytest.approx(expected["z"])
                    assert row[6] == pytest.approx(expected["time"])
                    assert row[7] == expected["time_unit"]
                    assert row[8] == expected["method"]

                assert conn.execute("SELECT count(*) FROM folder_import_jobs WHERE source_folder=?", [metadata[0]]).fetchone()[0] == 2
                assert conn.execute("SELECT count(*) FROM analysis_run_metadata WHERE source_checksum=?", [metadata[1]]).fetchone()[0] == 1
                assert conn.execute("SELECT count(*) FROM audit_events WHERE action='RESULT_IMPORTED' AND detail_json LIKE ?", [f"%{run_id}%"]).fetchone()[0] == 1
    finally:
        _cleanup_manual_rows()


def test_radioss_canonical_uow_rolls_back_locations_job_run_and_audit(monkeypatch):
    initialize_database()
    _cleanup_manual_rows()
    sample_path = Path(__file__).resolve().parents[2] / "examples" / "radioss" / "radioss_tv_result_example.csv"
    payload = {"filename": "radioss-rollback.csv", "content": sample_path.read_text(encoding="utf-8")}
    original_add_results = SQLResultIngestionUnitOfWork.add_results

    def fail_after_persist(unit_of_work, *args, **kwargs):
        original_add_results(unit_of_work, *args, **kwargs)
        raise RuntimeError("injected Radioss persistence failure")

    monkeypatch.setattr(SQLResultIngestionUnitOfWork, "add_results", fail_after_persist)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(f"/api/load-cases/{LOAD_CASE_ID}/results/import", json=payload)
    assert response.status_code == 500
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_runs WHERE load_case_id=? AND id IN (SELECT analysis_run_id FROM analysis_run_metadata WHERE source_name=?)", [LOAD_CASE_ID, f"{LOAD_CASE_ID}/radioss-rollback.csv"]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM analysis_run_metadata WHERE source_name=?", [f"{LOAD_CASE_ID}/radioss-rollback.csv"]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM result_locations WHERE analysis_run_id IN (SELECT analysis_run_id FROM analysis_run_metadata WHERE source_name=?)", [f"{LOAD_CASE_ID}/radioss-rollback.csv"]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM folder_import_jobs WHERE source_folder=?", [f"{LOAD_CASE_ID}/radioss-rollback.csv"]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='RESULT_IMPORTED' AND path LIKE '%results/import'").fetchone()[0] == 0
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
    original_authorize = result_ingestion_module.require_resource_permission

    def track_authorization(*args, **kwargs):
        authorization_connections.append(kwargs.get("conn"))
        return original_authorize(*args, **kwargs)

    monkeypatch.setattr(result_ingestion_module, "require_resource_permission", track_authorization)
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
    query_connections = []
    events = []
    original_authorize = result_ingestion_module.require_resource_permission
    original_target_query = SQLResultIngestionQuery.get_result_ingestion_target
    original_add_results = SQLResultIngestionUnitOfWork.add_results

    def track_authorization(*args, **kwargs):
        events.append("authorize")
        authorization_connections.append(kwargs.get("conn"))
        return original_authorize(*args, **kwargs)

    def track_target_query(query, *args, **kwargs):
        events.append("query")
        query_connections.append(query._repository.conn)
        return original_target_query(query, *args, **kwargs)

    def fail_after_persist(unit_of_work, *args, **kwargs):
        original_add_results(unit_of_work, *args, **kwargs)
        raise RuntimeError("injected manual persistence failure")

    monkeypatch.setattr(result_ingestion_module, "require_resource_permission", track_authorization)
    monkeypatch.setattr(SQLResultIngestionQuery, "get_result_ingestion_target", track_target_query)
    monkeypatch.setattr(SQLResultIngestionUnitOfWork, "add_results", fail_after_persist)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(f"/api/load-cases/{LOAD_CASE_ID}/results/import", json=payload)
    assert response.status_code == 500
    assert len(authorization_connections) == 2
    assert authorization_connections[0] is authorization_connections[1]
    assert events[:3] == ["authorize", "query", "authorize"]
    assert len(query_connections) == 1
    assert query_connections[0] is authorization_connections[0]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM analysis_run_metadata WHERE source_type='FILE_UPLOAD'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM folder_import_jobs WHERE source_folder=?", [f"{LOAD_CASE_ID}/manual-rollback.json"]).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM audit_events WHERE action='RESULT_IMPORTED' AND path LIKE '%results/import'").fetchone()[0] == 0
    _cleanup_manual_rows()
