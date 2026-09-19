from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters.persistence.dashboard_schema import ensure_dashboard_schema
from app.database_connection import connect
from app.services.dashboard_capture import (
    DashboardCaptureError,
    _root_id,
    create_capture,
    find_asset,
    get_capture,
)


pytestmark = pytest.mark.duckdb_integration


def _request_context(connection) -> tuple[str, str]:
    row = connection.execute(
        "SELECT project_id,id FROM analysis_requests ORDER BY id LIMIT 1"
    ).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


def _payload(root: Path, connection, **overrides):
    project_id, request_id = _request_context(connection)
    payload = {
        "project_id": project_id,
        "request_id": request_id,
        "simulation_case_id": "simulation-case-a",
        "load_case_id": None,
        "execution_run_id": None,
        "run_display_name": None,
        "mode": None,
        "component_id": None,
        "storage_root_id": _root_id(root),
        "root_relative_path": "usage-case",
        "environment": "USAGE",
        "recipe_version": "dashboard-v1",
    }
    payload.update(overrides)
    return payload


def _write_usage_case(root: Path, value: float) -> None:
    target = root / "usage-case" / "Settle"
    target.mkdir(parents=True, exist_ok=True)
    (target / "model_settle_result.json").write_text(
        json.dumps({"Set Tilt Angle @ Settle (deg)": value}), encoding="utf-8"
    )
    (target / "model_settle.jpg").write_bytes(b"synthetic-image")


def test_repeated_capture_is_idempotent_and_old_capture_is_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    _write_usage_case(root, 1.18)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))

    with connect() as connection:
        ensure_dashboard_schema(connection)
        payload = _payload(root, connection)
        first = create_capture(connection, payload, actor="test-actor")
        repeated = create_capture(connection, payload, actor="test-actor")
        assert repeated["id"] == first["id"]
        assert repeated["status"] == first["status"]
        assert connection.execute(
            "SELECT count(*) FROM dashboard_captures WHERE case_id=?", [first["case_id"]]
        ).fetchone()[0] == 1

        _write_usage_case(root, 2.25)
        second = create_capture(connection, payload, actor="test-actor")
        assert second["id"] != first["id"]
        assert connection.execute(
            "SELECT count(*) FROM dashboard_captures WHERE case_id=?", [first["case_id"]]
        ).fetchone()[0] == 2

        old = get_capture(connection, first["id"])
        new = get_capture(connection, second["id"])
        assert old is not None and new is not None
        assert old["payload"]["evaluations"][0]["values"]["Set Tilt Angle @ Settle (deg)"] == 1.18
        assert new["payload"]["evaluations"][0]["values"]["Set Tilt Angle @ Settle (deg)"] == 2.25

        asset_id = old["payload"]["evaluations"][0]["media"][0]["asset_id"]
        asset = find_asset(connection, asset_id)
        assert asset is not None
        assert bytes(asset["content"]) == b"synthetic-image"
        assert str(root) not in json.dumps(old["payload"], ensure_ascii=False)


def test_same_storage_path_cannot_be_rebound_to_another_business_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shared"
    root.mkdir()
    _write_usage_case(root, 1.18)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))

    with connect() as connection:
        ensure_dashboard_schema(connection)
        payload = _payload(root, connection)
        create_capture(connection, payload, actor="test-actor")

        with pytest.raises(DashboardCaptureError) as captured:
            create_capture(
                connection,
                {**payload, "simulation_case_id": "simulation-case-b"},
                actor="test-actor",
            )

        assert captured.value.code == "DASHBOARD_CONTEXT_CONFLICT"
