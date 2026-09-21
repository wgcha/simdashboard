from __future__ import annotations

from app.services.usage_source_review import review


def test_review_selects_only_exact_usage_suffix_before_parsing_and_preserves_segments():
    result = review([
        ("Case/Settle/75R9J_Set0907_Stand0907_Force_spg0.1_settle_result.json", b'{"nested":{"Set Tilt Angle @ Settle (deg)":1.18}}'),
        ("Case/Settle/settings.json", b"not json"),
        ("Case/Settle/other_settle.json", b"not json"),
    ], metric_paths={"Settle:common:Set Tilt Angle @ Settle (deg)": ["nested", "Set Tilt Angle @ Settle (deg)"]})

    settle = next(entry for entry in result["entries"] if entry["evaluation"] == "Settle")
    assert settle["status"] == "READY"
    assert settle["source"].endswith("_settle_result.json")
    assert settle["metrics"][0]["path"] == ["nested", "Set Tilt Angle @ Settle (deg)"]
    assert settle["metrics"][0]["value"] == 1.18
    assert result["excluded_count"] == 2


def test_review_keeps_slope_metric_exclusion_without_hiding_other_metric():
    result = review([
        ("Case/Slope_Angle/a_slope_angle_front_result.json", b'{"Slope Angle (deg)":0,"OK/NG":"NG"}'),
    ], excludes={"Slope_Angle:front:OK/NG": "operator chose partial"})

    slope = next(entry for entry in result["entries"] if entry["evaluation"] == "Slope_Angle" and entry["direction"] == "front")
    assert slope["status"] == "READY"
    assert [(metric["key"], metric["value"], metric["status"]) for metric in slope["metrics"]] == [
        ("Slope Angle (deg)", 0, "READY"), ("OK/NG", "NG", "EXCLUDED"),
    ]


def test_review_blocks_invalid_selected_source_and_distinguishes_missing_from_blocking():
    result = review([], selected_sources={"Settle:common": "Case/Settle/not-present_result.json"})

    settle = next(entry for entry in result["entries"] if entry["evaluation"] == "Settle")
    assert settle["status"] == "INVALID_SOURCE_SELECTION"
    assert result["blocking_count"] >= 1
    assert result["missing_count"] > 0


def test_reviewed_media_only_and_other_condition_media_do_not_hide_numeric_value():
    from app.services.dashboard_capture import _usage_payload
    from app.services.dashboard_queries import usage
    files = [
        ("Case/Settle/a_settle_result.json", b'{"Set Tilt Angle @ Settle (deg)":1.18}'),
        ("Case/Settle/b_settle.mp4", b"synthetic video"),
        ("Case/Wobble/a_wobble_center_front.mp4", b"synthetic media only"),
    ]
    contract = {**review(files)["contract"], "acknowledge_partial": True}
    payload = {**_usage_payload({"Case": files}, contract), "context": {}}
    data = usage({"id": "cap", "case_id": "case", "environment": "USAGE", "payload": payload}, "case")
    assert data["evaluations"][0]["common"]["value"] == 1.18
    assert data["evaluations"][0]["common"]["status"] == "READY"
    assert len(data["evaluations"][0]["media"]) == 1
    assert len(data["evaluations"][1]["media"]) == 1
    assert data["evaluations"][1]["front"]["value"] is None


def test_walk_does_not_open_unselected_or_nonresult_files(tmp_path, monkeypatch):
    from app.services import dashboard_capture
    from app.services.usage_source_review import include_path, selection
    folder = tmp_path / "Case" / "Settle"
    folder.mkdir(parents=True)
    for name in ("settings.json", "model_settle_result.csv", "solver.log", "model_settle_result.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    opened = []
    original = dashboard_capture.spdm_storage.read_stable_bytes
    def track(path, **kwargs):
        opened.append(path.name)
        return original(path, **kwargs)
    monkeypatch.setattr(dashboard_capture.spdm_storage, "read_stable_bytes", track)
    files = dashboard_capture._walk(tmp_path, "Case", include_path=lambda path: include_path(path, selection(None)))
    assert opened == ["model_settle_result.json"]
    assert len(files) == 1
