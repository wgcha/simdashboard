import pytest

from app.services.environment_folder_profiles import resolve_role, validate_rules
from app.services.folder_discovery_environment import _expanded_preview_assignments, _interpret, _skip_final_archive
from app.services.folder_discovery_scan import scan

pytestmark = pytest.mark.unit


def test_parent_scoped_rules_preserve_role_ambiguity_and_ignore_physical_depth():
    rules = {"rules": [{"role_kind": "RUN_OPTION", "parent_role": "EXECUTION_RUN", "pattern": "Fatigue*", "match_mode": "glob"}]}
    assert resolve_role("Fatigue Cycle 2", 7, "EXECUTION_RUN", rules, "DISTRIBUTION") == ("RUN_OPTION", True, False)
    assert resolve_role("Fatigue Cycle 2", 12, "EXECUTION_RUN", rules, "DISTRIBUTION") == ("RUN_OPTION", True, False)
    assert resolve_role("Fatigue Cycle 2", 7, "SIMULATION_CASE", rules, "DISTRIBUTION") == (None, False, False)
    rules["rules"].append({"role_kind": "SCENE", "parent_role": "EXECUTION_RUN", "pattern": "Fatigue*"})
    assert resolve_role("Fatigue Cycle 2", 7, "EXECUTION_RUN", rules, "DISTRIBUTION") == (None, True, True)


@pytest.mark.parametrize("rule", [
    {"role_kind": "RUN_OPTION", "pattern": "*"},
    {"role_kind": "SIMULATION_CASE", "depth": -1},
    {"role_kind": "SIMULATION_CASE", "pattern": "bad\x00"},
    {"role_kind": "SIMULATION_CASE", "match_mode": "execute"},
])
def test_invalid_rules_do_not_cross_environment_or_execute_arbitrary_matchers(rule):
    with pytest.raises(ValueError):
        validate_rules("USAGE", {"rules": [rule]})


def test_usage_source_defaults_preserve_exact_segment_arrays():
    result = validate_rules("USAGE", {"rules": [], "usage_sources": {
        "version": 1, "selection": {"json": True, "csv": False},
        "metric_paths": {"Slope_Angle:front:OK/NG": ["nested.key", "OK/NG"]},
    }})

    assert result["usage_sources"] == {
        "version": 1, "selection": {"json": True, "csv": False},
        "metric_paths": {"Slope_Angle:front:OK/NG": ["nested.key", "OK/NG"]},
    }


def test_working_relative_levels_ignore_physical_depth_and_parent_role_and_detect_mixed_roles():
    rules = {"rules": [
        {"role_kind": "SIMULATION_CASE", "pattern": "", "match_mode": "level", "depth": 1},
    ]}
    assert validate_rules("DISTRIBUTION", rules)["rules"] == rules["rules"]
    assert resolve_role("Case Alpha", 12, "LOAD_CASE", rules, "DISTRIBUTION", working_level=1) == (
        "SIMULATION_CASE", True, False,
    )
    assert resolve_role("Case Alpha", 1, "WORKING", rules, "DISTRIBUTION", working_level=2) == (
        None, False, False,
    )
    rules["rules"].append({"role_kind": "LOAD_CASE", "pattern": "", "match_mode": "level", "depth": 1})
    assert resolve_role("Case Alpha", 12, "LOAD_CASE", rules, "DISTRIBUTION", working_level=1) == (
        None, True, True,
    )


def test_name_rule_is_an_individual_exception_to_working_level_default():
    rules = {"rules": [
        {"role_kind": "SIMULATION_CASE", "pattern": "", "match_mode": "level", "depth": 1},
        {"role_kind": "CONTAINER", "pattern": "Calibration", "match_mode": "glob"},
    ]}
    assert resolve_role("Calibration", 8, "WORKING", rules, "DISTRIBUTION", working_level=1) == (
        "CONTAINER", True, False,
    )
    rules["rules"].append({"role_kind": "SCENE", "pattern": "Calibration", "match_mode": "glob"})
    assert resolve_role("Calibration", 8, "WORKING", rules, "DISTRIBUTION", working_level=1) == (
        None, True, True,
    )


def test_interpret_recognizes_request_children_and_keeps_final_descendants_out_of_levels():
    nodes = [
        {"relative_path": "75R9J_PV", "parent_path": None, "name": "75R9J_PV", "depth": 0},
        {"relative_path": "75R9J_PV/[WR-0001]_[유통_환경]", "parent_path": "75R9J_PV",
         "name": "[WR-0001]_[유통_환경]", "depth": 1},
        {"relative_path": "75R9J_PV/[WR-0001]_[유통_환경]/Working", "parent_path": "75R9J_PV/[WR-0001]_[유통_환경]",
         "name": "Working", "depth": 2},
        {"relative_path": "75R9J_PV/[WR-0001]_[유통_환경]/Working/Case 1", "parent_path": "75R9J_PV/[WR-0001]_[유통_환경]/Working",
         "name": "Case 1", "depth": 3},
        {"relative_path": "75R9J_PV/[WR-0001]_[유통_환경]/Final", "parent_path": "75R9J_PV/[WR-0001]_[유통_환경]",
         "name": "Final", "depth": 2},
        {"relative_path": "75R9J_PV/[WR-0001]_[유통_환경]/Final/Case 2", "parent_path": "75R9J_PV/[WR-0001]_[유통_환경]/Final",
         "name": "Case 2", "depth": 3},
    ]
    rules = {"rules": [{"role_kind": "SIMULATION_CASE", "pattern": "", "match_mode": "level", "depth": 1}]}
    interpreted = _interpret(nodes, "root-test", "DISTRIBUTION", None, None, rules)
    assert [item["role_kind"] for item in interpreted] == [
        "PROJECT", "REQUEST", "WORKING", "SIMULATION_CASE", "FINAL", "CONTAINER",
    ]


def test_scoped_working_scan_seeds_level_and_scoped_final_scan_stays_archival():
    rules = {"rules": [{"role_kind": "SIMULATION_CASE", "pattern": "", "match_mode": "level", "depth": 1}]}
    working_nodes = [
        {"relative_path": "Project/WR-1/Working/A", "parent_path": None, "name": "A", "depth": 0},
        {"relative_path": "Project/WR-1/Working/B", "parent_path": "Project/WR-1/Working", "name": "B", "depth": 1},
    ]
    interpreted = _interpret(working_nodes, "root-test", "DISTRIBUTION", "project", "request", rules,
                            seed_request_path="Project/WR-1")
    assert [item["role_kind"] for item in interpreted] == ["SIMULATION_CASE", "SIMULATION_CASE"]
    expanded = _expanded_preview_assignments(interpreted, [{"node_id": interpreted[0]["id"],
        "role_kind": "SIMULATION_CASE", "propagate_same_level": True}])
    assert {item["node_id"] for item in expanded} == {item["id"] for item in interpreted}

    final_nodes = [{"relative_path": "Project/WR-1/Final/A", "parent_path": None, "name": "A", "depth": 0}]
    final_result = _interpret(final_nodes, "root-test", "DISTRIBUTION", "project", "request", rules,
                              seed_request_path="Project/WR-1")
    assert final_result[0]["role_kind"] == "CONTAINER"
    assert len(_expanded_preview_assignments(final_result, [{"node_id": final_result[0]["id"],
        "role_kind": "SIMULATION_CASE", "propagate_same_level": True}])) == 1


def test_manual_working_level_assignment_propagates_with_direct_folder_exception():
    nodes = [
        {"id": "request", "relative_path": "Project/WR-1", "parent_path": None, "role_kind": "REQUEST"},
        {"id": "working", "relative_path": "Project/WR-1/Working", "parent_path": "Project/WR-1", "role_kind": "WORKING"},
        {"id": "case-a", "relative_path": "Project/WR-1/Working/A", "parent_path": "Project/WR-1/Working", "role_kind": None},
        {"id": "case-b", "relative_path": "Project/WR-1/Working/B", "parent_path": "Project/WR-1/Working", "role_kind": None},
        {"id": "final", "relative_path": "Project/WR-1/Final", "parent_path": "Project/WR-1", "role_kind": "FINAL"},
        {"id": "archive-case", "relative_path": "Project/WR-1/Final/C", "parent_path": "Project/WR-1/Final", "role_kind": None},
    ]
    expanded = _expanded_preview_assignments(nodes, [{"node_id": "case-a", "role_kind": "SIMULATION_CASE", "propagate_same_level": True}])
    assert {item["node_id"] for item in expanded} == {"case-a", "case-b"}
    with_exception = _expanded_preview_assignments(nodes, [
        {"node_id": "case-a", "role_kind": "SIMULATION_CASE", "propagate_same_level": True},
        {"node_id": "case-b", "role_kind": "CONTAINER", "propagate_same_level": False},
    ])
    assert {item["node_id"]: item["role_kind"] for item in with_exception} == {
        "case-b": "CONTAINER", "case-a": "SIMULATION_CASE",
    }
    assert _expanded_preview_assignments(nodes, [{"node_id": "archive-case", "role_kind": "SIMULATION_CASE", "propagate_same_level": True}]) == [
        {"node_id": "archive-case", "role_kind": "SIMULATION_CASE", "propagate_same_level": True},
    ]


def test_scan_prunes_only_request_direct_final_archive(tmp_path):
    request = tmp_path / "Project_1000" / "[WR-0001]_[유통_환경]"
    (tmp_path / "Final" / "RootArchive").mkdir(parents=True)
    (tmp_path / "Project_1000" / "Case_Archive" / "Final" / "Keep").mkdir(parents=True)
    (request / "Final" / "Deep" / "More").mkdir(parents=True)
    (request / "Working" / "Final" / "Kept").mkdir(parents=True)
    result = scan(tmp_path, "", skip_descendants=_skip_final_archive())
    paths = {node["relative_path"]: node for node in result["nodes"]}
    final_path = "Project_1000/[WR-0001]_[유통_환경]/Final"
    assert paths[final_path]["children_skipped"] is True
    assert f"{final_path}/Deep" not in paths
    assert f"{final_path}/Deep/More" not in paths
    assert "Final/RootArchive" in paths
    assert "Project_1000/Case_Archive/Final/Keep" in paths
    assert "Project_1000/[WR-0001]_[유통_환경]/Working/Final/Kept" in paths

    for start in (final_path, f"{final_path}/Deep"):
        started_inside_archive = scan(tmp_path, start, skip_descendants=_skip_final_archive())
        assert len(started_inside_archive["nodes"]) == 1
        assert started_inside_archive["nodes"][0]["relative_path"] == start
        assert started_inside_archive["nodes"][0]["children_skipped"] is True
