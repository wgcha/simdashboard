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


@pytest.mark.parametrize("values,angle,verdict", [
    ({"Slope Angle (deg)": 0, "OK/NG": "NG"}, 0, "NG"),
    ({"Slope Angle (deg)": 15.79}, 15.79, None),
    ({"OK/NG": "OK"}, None, "OK"),
    ({"Slope Angle (deg)": None, "OK/NG": ["OK"]}, None, None),
])
def test_usage_slope_fields_are_independent(values, angle, verdict):
    cap = {"id": "cap", "case_id": "case", "environment": "USAGE", "payload": {
        "context": {}, "evaluations": [{"evaluation": "Slope_Angle", "direction": "front",
            "condition": "same", "status": "READY", "values": values}]}}
    result = queries.usage(cap, "case")
    cell = result["evaluations"][3]["front"]
    assert cell["value"] == angle
    assert cell["verdict"] == verdict
    assert (cell["value_status"] == "READY") == (angle is not None)
    assert (cell["verdict_status"] == "READY") == (verdict is not None)
    compared = queries.usage_reference(result, queries.usage(cap, "case"))["evaluations"][3]["reference"]["front"]
    if angle is not None or verdict is not None:
        assert compared["value"] == angle and compared["verdict"] == verdict


def test_legacy_capture_labels_do_not_mutate_payload():
    import copy
    cap = {"id": "cap", "case_id": "case", "environment": "USAGE", "payload": {
        "context": {}, "evaluations": [], "quality_issues": ["SOURCE_PARSE_ERROR"]}}
    original = copy.deepcopy(cap)
    rows = queries.usage(cap, "case")["evaluations"]
    assert [r["name"] for r in rows] == list(queries.USAGE_KEYS)
    assert rows[0]["common"]["value_key"] == "Set Tilt Angle @ Settle (deg)"
    assert cap == original


def test_catalog_uses_confirmed_schema_for_uncaptured_choices_and_keeps_capture_scope(monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    from app.services import dashboard_capture, folder_discovery, folder_discovery_environment, folder_schema_resolver

    request_path = "75R9J_PV/request [WR-0001]_[유통_환경]/Working"
    case_a = f"{request_path}/Case Alpha"
    case_b = f"{request_path}/Case Beta"
    load_path = f"{case_a}/Drop"
    run_path = f"{load_path}/Run A"
    option_path = f"{run_path}/INDIVIDUAL"
    scene_path = f"{option_path}/2_Face &3_Face"
    hierarchy = {
        "simulation_case": {"target_id": "schema-case-a", "relative_path": case_a},
        "load_case": {"target_id": "schema-load-a", "relative_path": load_path},
        "execution_run": {"target_id": "schema-run-a", "relative_path": run_path},
    }
    nodes = [
        {"role_kind": "SIMULATION_CASE", "status": "CONFIRMED", "target_id": "schema-case-a", "name": "Case Alpha", "relative_path": case_a, "hierarchy": {}},
        {"role_kind": "SIMULATION_CASE", "status": "CONFIRMED", "target_id": "schema-case-b", "name": "Case Beta", "relative_path": case_b, "hierarchy": {}},
        {"role_kind": "LOAD_CASE", "status": "CONFIRMED", "target_id": "schema-load-a", "name": "Drop", "relative_path": load_path, "hierarchy": {"simulation_case": hierarchy["simulation_case"]}},
        {"role_kind": "EXECUTION_RUN", "status": "CONFIRMED", "target_id": "schema-run-a", "name": "Run A", "relative_path": run_path, "hierarchy": {"simulation_case": hierarchy["simulation_case"], "load_case": hierarchy["load_case"]}},
        {"role_kind": "RUN_OPTION", "status": "CONFIRMED", "target_id": "target-option-a", "run_option_id": "semantic-option-a", "option_status": "PRESENT", "name": "INDIVIDUAL", "relative_path": option_path, "hierarchy": hierarchy},
    ]
    locations = SimpleNamespace(schema={"nodes": nodes, "scan": {"id": "scan-1"}}, locations=(
        {
            "role_kind": "SCENE", "scene_id": "schema-scene-a", "id": "schema-scene-a",
            "label": "2_Face &3_Face", "relative_path": scene_path,
            "hierarchy": {**hierarchy, "run_option": {"target_id": "target-option-a", "relative_path": option_path}},
        },
        {
            "role_kind": "SCENE", "scene_id": "schema-scene-no-option", "id": "schema-scene-no-option",
            "label": "Scene without option", "relative_path": f"{run_path}/Scene without option",
            "hierarchy": hierarchy,
        },
    ))
    monkeypatch.setattr(folder_schema_resolver, "resolve_request_locations", lambda *_args: locations)
    monkeypatch.setattr(folder_discovery, "configured_root", lambda _conn: Path("/synthetic-spdm"))
    monkeypatch.setattr(folder_discovery_environment, "root_identity", lambda _root: "root-synthetic")
    monkeypatch.setattr(dashboard_capture, "_root_id", lambda _root: "storage-root-1")

    class Connection:
        def execute(self, statement, _parameters=None):
            if "FROM analysis_requests" in statement:
                return SimpleNamespace(fetchone=lambda: ("project-1",))
            payload = {"runs": [{"id": "run-old", "load_case_id": "load-old", "load_case_name": "Old Drop",
                "source_name": "Old Run", "mode": "INDIVIDUAL", "scenes": [{"id": "scene-old", "source_name": "Old Scene", "observations": [], "media": []}]}]}
            row = ("dashboard-case-a", "Case Alpha", "storage-root-1", case_a, "capture-a", "2026-10-01", payload)
            empty_capture = ("dashboard-case-a", "Case Alpha", "storage-root-1", case_a, "capture-empty", "2026-10-02", {"runs": []})
            return SimpleNamespace(fetchall=lambda: [row, empty_capture])

    result = queries.catalog(Connection(), "request-1", "DISTRIBUTION")
    assert result["folder_schema"] == {"status": "AVAILABLE", "diagnostic": None, "snapshot_id": "scan-1"}
    cases = {item["id"]: item for item in result["cases"]}
    assert set(cases) == {"schema-case-a", "schema-case-b"}
    assert cases["schema-case-a"]["dashboard_case_id"] == "dashboard-case-a"
    assert cases["schema-case-a"]["capture_count"] == 2
    assert cases["schema-case-b"]["match_status"] == "UNCAPTURED"
    assert result["captures"][0]["case_id"] == "schema-case-a"
    assert result["captures"][0]["dashboard_case_id"] == "dashboard-case-a"
    assert any(item["id"] == "capture-empty" for item in result["captures"])
    assert any(item["id"] == "schema-load-a" and item.get("capture_id") is None for item in result["load_cases"])
    assert any(item["id"] == "load-old" and item["capture_id"] == "capture-a" for item in result["load_cases"])
    assert any(item["id"] == "run-old" and item["capture_id"] == "capture-a" for item in result["execution_runs"])
    schema_scene = next(item for item in result["scenes"] if item["label"] == "2_Face &3_Face")
    schema_option = next(item for item in result["run_options"] if item["execution_run_id"] == "schema-run-a")
    assert schema_scene["match_status"] == "UNCAPTURED"
    assert schema_scene["capture_id"] is None
    assert schema_scene["case_id"] == "schema-case-a"
    assert schema_scene["load_case_id"] == "schema-load-a"
    assert schema_scene["execution_run_id"] == "schema-run-a"
    assert schema_option["id"] == "semantic-option-a"
    assert schema_scene["run_option_id"] == schema_option["id"]
    scene_without_option = next(item for item in result["scenes"] if item["id"] == "schema-scene-no-option")
    absent_option = next(item for item in result["run_options"] if item.get("option_status") == "ABSENT")
    assert scene_without_option["run_option_id"] == absent_option["id"]
    assert any(item["id"] == "scene-old" and item["capture_id"] == "capture-a" for item in result["scenes"])


def test_final_branch_detection_uses_request_direct_path_even_for_legacy_roles():
    from app.services.folder_schema_locations import final_branch_paths, is_final_branch

    schema = {"request_relative_path": "Project/WR/Request", "nodes": [
        {"relative_path": "Project/WR/Request/Working", "parent_path": "Project/WR/Request", "name": "Working", "role_kind": None},
        {"relative_path": "Project/WR/Request/Final", "parent_path": "Project/WR/Request", "name": "Final", "role_kind": None},
        {"relative_path": "Project/WR/Request/Final/Case A", "parent_path": "Project/WR/Request/Final", "name": "Case A", "role_kind": "SIMULATION_CASE"},
    ]}
    assert final_branch_paths(schema) == ["Project/WR/Request/Final"]
    assert is_final_branch(schema, "Project/WR/Request/Final/Case A")
    assert not is_final_branch(schema, "Project/WR/Request/Working/Case A")


def test_usage_exact_nested_segments_and_strict_reviewed_values():
    entry = {"status": "READY", "values": {"a.b": {"OK/NG": "NG", "angle (deg)": -1.18}},
        "metric_paths": {"Slope Angle (deg)": ["a.b", "angle (deg)"], "OK/NG": ["a.b", "OK/NG"]}}
    assert queries.usage_metric(entry, "Slope Angle (deg)", "READY")[0] == -1.18
    assert queries.usage_metric(entry, "OK/NG", "READY", verdict=True)[0] == "NG"
    entry["values"]["a.b"]["angle (deg)"] = "1.18"
    assert queries.usage_metric(entry, "Slope Angle (deg)", "READY")[1] == "INVALID_TYPE"
