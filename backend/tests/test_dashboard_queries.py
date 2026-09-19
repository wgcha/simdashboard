import pytest

from app.services import dashboard_queries as queries
from app.services.dashboard_capture import DashboardCaptureError

pytestmark = pytest.mark.unit


def capture():
    observations = []
    for component, factor in (("C23", 1), ("C24", 10)):
        for basis, offset in (("DETAIL", 0), ("REPORTED_SUMMARY", 1)):
            for position, value in (("TOP", 40), ("RH", 40), ("LH", 500)):
                for line in range(1, 5):
                    observations.append({"kind": "SIDE", "component_id": component, "basis": basis,
                        "position": position, "line_index": line, "value": factor * (value + offset),
                        "ref_coord": 2, "node_id": "alignment-1", "row": 2, "column": "L" + str(line)})
            observations.append({"kind": "CORNER", "component_id": component, "basis": basis,
                "position": "BOT_LH", "line_index": None, "value": 9000})
    scene = {"id": "s1", "source_name": "20_Face_Drop_Scene12_Face3", "scene_sequence_number": 20,
        "scenario_number": 12, "order_status": "CONFIRMED", "observations": observations,
        "media": [{"asset_id": "image-c23", "component_id": "C23", "kind": "IMAGE", "subject_role": "UNKNOWN", "frame_role": "UNKNOWN"}]}
    return {"id": "cap1", "case_id": "case1", "environment": "DISTRIBUTION", "payload": {
        "context": {"project_id": "p", "request_id": "r", "simulation_case_id": "case1", "capture_id": "cap1"},
        "runs": [{"id": "run1", "load_case_id": "drop1", "source_name": "Original run", "mode": "INDIVIDUAL", "scenes": [scene]}]}}


def test_selected_edges_exclude_corner_other_edges_and_other_component():
    result = queries.distribution(capture(), "run1", "INDIVIDUAL", "C23", "DETAIL", {"TOP", "RIGHT"}, {1, 2, 3, 4})
    assert result["series"][0]["value"] == 40
    assert result["series"][0]["completeness"] == "COMPLETE"
    assert {loc["edge"] for loc in result["series"][0]["locations"]} == {"TOP", "RIGHT"}
    assert result["scenes"][0]["scene_sequence_number"] == 20
    assert result["scenes"][0]["scenario_number"] == 12
    assert result["contours"][0]["asset"]["asset_id"] == "image-c23"
    assert all(row["asset"] is None for row in result["behaviors"])
    assert queries.distribution(capture(), "run1", "INDIVIDUAL", "C23", "REPORTED_SUMMARY", {"TOP"}, {4})["series"][0]["value"] == 41


def test_empty_and_partial_selection_never_claim_zero_or_complete():
    empty = queries.distribution(capture(), "run1", "INDIVIDUAL", "C23", "DETAIL", set(), {1})["series"][0]
    assert empty["value"] is None and empty["status"] == "NO_SELECTION"
    partial = queries.distribution(capture(), "run1", "INDIVIDUAL", "C23", "DETAIL", {"TOP", "BOTTOM"}, {1})["series"][0]
    assert partial["value"] == 40 and partial["status"] == "PARTIAL"


def test_location_map_excludes_unmapped_corners_for_line_subset():
    complete = queries.distribution(capture(), "run1", "INDIVIDUAL", "C23", "DETAIL", {"TOP"}, {1, 2, 3, 4})
    assert complete["location_peaks"][0]["value"] == 9000
    subset = queries.distribution(capture(), "run1", "INDIVIDUAL", "C23", "DETAIL", {"TOP"}, {1, 4})
    assert subset["location_peaks"][0]["value"] == 500
    assert {loc["line_index"] for loc in subset["location_peaks"][0]["locations"]} == {1, 4}
    assert all(loc["kind"] == "SIDE" for loc in subset["location_peaks"][0]["locations"])


@pytest.mark.parametrize("run,mode,component", [("run2", "INDIVIDUAL", "C23"), ("run1", "CUMULATIVE", "C23"), ("run1", "INDIVIDUAL", "C99")])
def test_mismatched_selection_rejected(run, mode, component):
    with pytest.raises(DashboardCaptureError):
        queries.distribution(capture(), run, mode, component, "DETAIL", {"TOP"}, {1})


def test_detail_preserves_alignment_identity_without_claiming_actual_line_node():
    result = queries.scene_detail(capture(), "s1", "run1", "INDIVIDUAL", "C23", "DETAIL", {1, 4}, "TOP")
    assert len(result["line_points"]) == 2
    assert all(o["node_id"] is None and o["alignment_node_id"] == "alignment-1" for o in result["line_points"])


def test_usage_five_rows_preserve_zero_negative_and_separate_verdict():
    cap = {"id": "cap", "case_id": "case", "environment": "USAGE", "payload": {"context": {}, "evaluations": [
        {"evaluation": "Settle", "direction": "common", "status": "READY", "values": {"Set Tilt Angle @ Settle (deg)": 0}},
        {"evaluation": "Wobble", "direction": "back", "status": "READY", "values": {"Wobble Disp. (mm)": -21.424}},
        {"evaluation": "Slope_Angle_360", "direction": "front", "status": "READY", "values": {"OK/NG": "OK"}},
    ]}}
    result = queries.usage(cap, "case")
    assert len(result["evaluations"]) == 5
    assert result["evaluations"][0]["common"]["value"] == 0
    assert result["evaluations"][0]["front"] is None
    assert result["evaluations"][1]["rear"]["value"] == -21.424
    assert result["evaluations"][1]["front"]["value"] is None
    assert result["evaluations"][4]["front"]["verdict"] == "OK"
    assert result["evaluations"][4]["front"]["status"] == "READY"


def test_reference_requires_exact_conditions_and_preserves_direction_values():
    def result(condition, value):
        return {"context": {"capture_id": str(value)}, "evaluations": [{"front": {"status": "READY", "condition": condition, "value": value, "unit": "mm"}}]}
    compatible = queries.usage_reference(result("model_force0.1", -20), result("model_force0.1", -21))
    assert compatible["evaluations"][0]["reference"]["front"]["value"] == -21
    incompatible = queries.usage_reference(result("model_force0.1", -20), result("model_force0.2", -21))
    assert incompatible["evaluations"][0]["reference"]["front"] is None
    assert incompatible["evaluations"][0]["reference"]["status"] == "INCOMPARABLE"


def test_comparison_keeps_unknown_profile_scenes_separate_and_missing_cells():
    first = queries.distribution(capture(), "run1", "INDIVIDUAL", "C23", "DETAIL", {"TOP"}, {1})
    second_capture = capture()
    second_capture["case_id"] = "case2"
    second_capture["id"] = "cap2"
    second_capture["payload"]["runs"][0]["scenes"][0]["id"] = "s2"
    second = queries.distribution(second_capture, "run1", "INDIVIDUAL", "C23", "DETAIL", {"TOP"}, {1})
    result = queries.comparison([first, second])
    assert len(result["scenes"]) == 2
    assert len(result["members"]) == 2
    assert len(result["contours"]) == 4
    assert len([c for c in result["contours"] if c["status"] == "UNMATCHED"]) == 2
    assert "CASE_SCENE_ALIGNMENT_UNCONFIRMED" in result["quality_issues"]


def test_legacy_unknown_option_is_unresolved_and_queryable_by_catalog_id():
    legacy = capture()
    legacy["payload"]["runs"][0]["mode"] = "UNKNOWN"
    legacy["payload"]["runs"][0].pop("run_option_id", None)
    option_id, label, status = queries.option_projection(legacy["payload"]["runs"][0], legacy["id"])
    assert status == "UNRESOLVED"
    assert label == "미확인(기존 자료)"
    run, context = queries.select_run(legacy, "run1", "UNKNOWN", "C23", "DETAIL", option_id)
    assert run["id"] == "run1"
    assert context["run_option_id"] == option_id
