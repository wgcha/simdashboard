from __future__ import annotations

import json
import hashlib
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
from app.services import usage_source_review


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


def test_approved_capture_uses_only_reviewed_allowlist_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "shared"
    approved_path = "usage-case/Settle/model_settle_result.json"
    approved_bytes = json.dumps({"Set Tilt Angle @ Settle (deg)": 2.75}).encode("utf-8")
    case = root / "usage-case"
    (case / "Settle" / "results").mkdir(parents=True)
    (case / "Settle" / "results" / "model_settle_result.json").write_bytes(approved_bytes)
    # This file is inside the Case tree but was never included in the review.
    (case / "Settle" / "results" / "unreviewed.json").write_text(
        json.dumps({"Set Tilt Angle @ Settle (deg)": 99.0}), encoding="utf-8"
    )
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))

    review = usage_source_review.review(
        [(approved_path, approved_bytes)],
        selected={"json": True, "csv": True, "video": True, "image": True},
    )["contract"]
    review["acknowledge_partial"] = True
    with connect() as connection:
        ensure_dashboard_schema(connection)
        result = create_capture(
            connection,
            _payload(root, connection, usage_source_review=review),
            actor="test-actor",
            approved_files=[(approved_path, approved_bytes, "application/json")],
            approved_manifest=[{
                "relative_path": approved_path,
                "sha256": hashlib.sha256(approved_bytes).hexdigest(),
                "size": len(approved_bytes),
                "media_type": "application/json",
            }],
        )
        capture = get_capture(connection, result["id"])
        assert capture is not None
        assert [item["relative_path"] for item in capture["payload"]["evaluations"][0]["media"]] == []
        settle = next(item for item in capture["payload"]["evaluations"] if item["evaluation"] == "Settle")
        assert settle["values"]["Set Tilt Angle @ Settle (deg)"] == 2.75
        assert all(item.get("condition") != "unreviewed" for item in capture["payload"]["evaluations"])
        assert connection.execute(
            "SELECT count(*) FROM dashboard_assets WHERE capture_id=?", [result["id"]]
        ).fetchone()[0] == 1
        stored = connection.execute(
            "SELECT relative_path,content FROM dashboard_assets WHERE capture_id=?", [result["id"]]
        ).fetchone()
        assert str(stored[0]) == approved_path
        assert bytes(stored[1]) == approved_bytes


def test_approved_video_only_usage_capture_keeps_unmatched_media_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shared"
    relative_path = "usage-case/Wobble/results/clip.mp4"
    content = b"synthetic-video-bytes"
    (root / "usage-case" / "Wobble" / "results").mkdir(parents=True)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    contract = usage_source_review.review(
        [(relative_path, content)],
        selected={"json": True, "csv": True, "video": True, "image": True},
    )["contract"]
    contract["acknowledge_partial"] = True
    with connect() as connection:
        ensure_dashboard_schema(connection)
        result = create_capture(
            connection, _payload(root, connection, usage_source_review=contract), actor="test-actor",
            approved_files=[(relative_path, content, "video/mp4")],
            approved_manifest=[{"relative_path": relative_path, "sha256": hashlib.sha256(content).hexdigest(),
                                "size": len(content), "media_type": "video/mp4"}],
        )
        capture = get_capture(connection, result["id"])
        assert capture is not None
        media_entries = [entry for entry in capture["payload"]["evaluations"] if entry.get("media_only")]
        assert len(media_entries) == 1
        assert media_entries[0]["media"][0]["relative_path"] == relative_path
        assert media_entries[0]["media"][0]["asset_id"]


def test_approved_capture_rejects_stale_hash_and_keeps_legacy_scan_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "shared"
    _write_usage_case(root, 1.18)
    monkeypatch.setenv("SIMDASH_SPDM_ROOT", str(root))
    approved_path = "usage-case/Settle/model_settle_result.json"
    content = json.dumps({"Set Tilt Angle @ Settle (deg)": 1.18}).encode("utf-8")
    with connect() as connection:
        ensure_dashboard_schema(connection)
        partial_review = usage_source_review.review(
            [(approved_path, content)], selected={"json": True})["contract"]
        partial_review["acknowledge_partial"] = True
        payload = _payload(root, connection, usage_source_review=partial_review)
        with pytest.raises(DashboardCaptureError) as captured:
            create_capture(
                connection, payload, actor="test-actor",
                approved_files=[(approved_path, content, "application/json")],
                approved_manifest=[{"relative_path": approved_path, "sha256": "0" * 64,
                                    "size": len(content), "media_type": "application/json"}],
            )
        assert captured.value.code == "DASHBOARD_APPROVED_SOURCE_STALE"

        # Omitting the trusted keyword adapter intentionally retains the
        # existing full-tree scan behavior for older dashboard callers.
        legacy = create_capture(connection, _payload(root, connection), actor="test-actor")
        assert connection.execute(
            "SELECT count(*) FROM dashboard_assets WHERE capture_id=?", [legacy["id"]]
        ).fetchone()[0] == 2


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
