from __future__ import annotations

import pytest

from app.domains.dashboard import parser
from app.domains.dashboard.parser import (
    _pick_usage,
    build_distribution_scene,
    parse_distribution_csv,
    parse_scene_name,
    parse_usage_file,
)
from app.services.dashboard_capture import _distribution_payload, _usage_payload


pytestmark = pytest.mark.unit


def test_standard_scene_sequence_and_internal_scenario_remain_distinct() -> None:
    parsed = parse_scene_name("20_Face_Drop_Scene12_Face3_1st")

    assert parsed["scene_sequence_number"] == 20
    assert parsed["scenario_number"] == 12
    assert parsed["sequence_status"] == "CONFIRMED"

    legacy = parse_scene_name("DAMP-2_Face_Drop_Scene02_Face2_1st")
    assert legacy["scene_sequence_number"] is None
    assert legacy["scenario_number"] == 2
    assert legacy["sequence_status"] == "UNCONFIRMED"


def test_usage_json_wins_without_double_counting_equivalent_csv() -> None:
    relative_stem = "Project/WR/Case/Wobble/model_wobble_center_front_result"
    parsed = _pick_usage(
        [
            (f"{relative_stem}.csv", b"Wobble Disp. (mm),999\n"),
            (f"{relative_stem}.json", b'{"Wobble Disp. (mm)":17.997}'),
        ]
    )

    assert len(parsed["evaluations"]) == 1
    assert parsed["evaluations"][0]["source"].endswith(".json")
    assert parsed["evaluations"][0]["values"]["Wobble Disp. (mm)"] == 17.997


def test_malformed_preferred_json_is_not_hidden_by_csv_fallback() -> None:
    relative_stem = "Project/WR/Case/Settle/model_settle_result"
    parsed = _pick_usage(
        [
            (f"{relative_stem}.csv", b"Set Tilt Angle @ Settle (deg),1.18\n"),
            (f"{relative_stem}.json", b'{"Set Tilt Angle @ Settle (deg)":,,1.18}'),
        ]
    )

    assert len(parsed["evaluations"]) == 1
    assert parsed["evaluations"][0]["source"].endswith(".json")
    assert parsed["evaluations"][0]["status"] == "SOURCE_PARSE_ERROR"


def test_side_rows_expand_to_four_lines_and_corner_position_is_preserved() -> None:
    side = parse_distribution_csv(
        b"Node_id,coord_x,coord_y,coord_z,ref_coord,C23_SIDE_TOP_L1,C23_SIDE_TOP_L2,C23_SIDE_TOP_L3,C23_SIDE_TOP_L4\n"
        b"1001,0,0,0,0,10,20,30,40\n"
        b"1002,1,0,0,1,12,22,32,42\n",
        "Scene/LAYER_ALIGN_C23_SIDE_TOP_Max_Stress_P1 (major)_Mid_Scene.h3d.csv",
    )
    corner = parse_distribution_csv(
        b"Node_id,Value\n2001,50\n2002,45\n",
        "Scene/CORNER_DATA_C23_CORNER_BOT_LH_Max_Stress_P1 (major)_Mid_Scene.h3d.csv",
    )

    assert len(side["observations"]) == 8
    assert side["observations"][-1] == {
        "kind": "SIDE",
        "position": "TOP",
        "line_index": 4,
        "value": 42,
        "alignment_node_id": "1002",
        "coord_x": 1,
        "coord_y": 0,
        "coord_z": 0,
        "ref_coord": 1,
        "row": 3,
        "column": "C23_SIDE_TOP_L4",
    }
    assert {item["position"] for item in corner["observations"]} == {"BOT_LH"}
    assert all(item["line_index"] is None for item in corner["observations"])


def test_component_and_basis_are_independent_dimensions() -> None:
    scene = build_distribution_scene(
        "1_Face_Drop_Scene01_Face1_1st",
        [
            (
                "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv",
                b"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,10,20,30,40\n",
            ),
            (
                "LAYER_ALIGN_C23_SIDE_TOP_Max_Stress_P1 (major)_Mid_scene.h3d.csv",
                b"Node_id,coord_x,coord_y,coord_z,ref_coord,C23_SIDE_TOP_L1,C23_SIDE_TOP_L2,C23_SIDE_TOP_L3,C23_SIDE_TOP_L4\n1001,0,0,0,0,11,21,31,41\n",
            ),
            (
                "MAX_RESULT_Max_Stress_P1 (major)_Mid_C24_scene.h3d.csv",
                b"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,100,200,300,400\n",
            ),
        ],
    )

    components = {item["component_id"]: item for item in scene["component_results"]}
    assert components["C23"]["peaks_by_basis"]["REPORTED_SUMMARY"]["value"] == 40
    assert components["C23"]["peaks_by_basis"]["DETAIL"]["value"] == 41
    assert components["C24"]["peaks_by_basis"]["REPORTED_SUMMARY"]["value"] == 400
    assert {item["basis"] for item in components["C23"]["edge_peaks"]} == {
        "DETAIL",
        "REPORTED_SUMMARY",
    }


def test_usage_direction_values_match_the_public_query_contract() -> None:
    parsed = _usage_payload(
        {
            "all": [
                (
                    "Project/WR/Case/Wobble/model_wobble_center_front_result.json",
                    b'{"Wobble Disp. (mm)":17.997}',
                ),
                (
                    "Project/WR/Case/Wobble/model_wobble_center_back_result.json",
                    b'{"Wobble Disp. (mm)":-21.424}',
                ),
            ]
        }
    )

    wobble = [item for item in parsed["evaluations"] if item["evaluation"] == "Wobble"]
    assert {item["direction"] for item in wobble} == {"front", "back"}


def test_one_folder_run_keeps_modes_and_scenes_isolated() -> None:
    header = b"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,10,20,30,40\n"
    parsed = _distribution_payload(
        "Drop/run-a",
        [
            (
                "Drop/run-a/INDIVIDUAL/1_Face_Drop_Scene01_Face1_1st/"
                "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv",
                header,
                "text/csv",
            ),
            (
                "Drop/run-a/CUMULATIVE/2_Face_Drop_Scene02_Face2_1st/"
                "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv",
                header,
                "text/csv",
            ),
        ],
    )

    assert len(parsed["runs"]) == 2
    assert {run["mode"] for run in parsed["runs"]} == {"INDIVIDUAL", "CUMULATIVE"}
    assert {
        (run["mode"], tuple(scene["source_name"] for scene in run["scenes"]))
        for run in parsed["runs"]
    } == {
        ("INDIVIDUAL", ("1_Face_Drop_Scene01_Face1_1st",)),
        ("CUMULATIVE", ("2_Face_Drop_Scene02_Face2_1st",)),
    }


def test_direct_and_named_unknown_options_with_same_scene_never_merge() -> None:
    header = b"Position,Layer_1\nTOP,10\n"
    scene = "1_Face_Drop_Scene01_Face1_1st"
    filename = "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv"
    parsed = _distribution_payload(
        "Drop/run-a",
        [
            (f"Drop/run-a/{scene}/{filename}", header, "text/csv"),
            (f"Drop/run-a/UNKNOWN/{scene}/{filename}", header, "text/csv"),
            (f"Drop/run-a/CustomRaw/{scene}/{filename}", header, "text/csv"),
        ],
        {"run_option_labels": ["CustomRaw"]},
    )

    assert len(parsed["runs"]) == 3
    assert {run["option_status"] for run in parsed["runs"]} == {"ABSENT", "PRESENT", "UNRESOLVED"}
    assert {run["option_label"] for run in parsed["runs"]} == {None, "UNKNOWN", "CustomRaw"}
    assert len({run["run_option_id"] for run in parsed["runs"]}) == 3
    assert len({run["scenes"][0]["id"] for run in parsed["runs"]}) == 3


def test_confirmed_hierarchy_assignments_override_legacy_folder_names() -> None:
    case = "Root/CaseCustom"
    load = f"{case}/LoadCustom"
    run = f"{load}/RunCustom"
    option = f"{run}/Raw Option"
    scene = f"{option}/PoseCustom"
    assignments = [
        {"relative_path": load, "role_kind": "LOAD_CASE", "target_id": "load-custom", "raw_name": "LoadCustom"},
        {"relative_path": run, "role_kind": "EXECUTION_RUN", "target_id": "run-custom", "raw_name": "RunCustom"},
        {"relative_path": option, "role_kind": "RUN_OPTION", "target_id": "option-custom", "raw_name": "Raw Option", "option_status": "PRESENT"},
        {"relative_path": scene, "role_kind": "SCENE", "target_id": "scene-custom", "raw_name": "PoseCustom"},
    ]
    parsed = _distribution_payload(case, [(f"{scene}/MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv", b"Position,Layer_1\nTOP,7\n", "text/csv")], {"hierarchy_assignments": assignments})
    result = parsed["runs"][0]
    assert (result["load_case_id"], result["id"], result["run_option_id"]) == ("load-custom", "run-custom", "option-custom")
    assert result["option_label"] == "Raw Option"
    assert result["scenes"][0]["id"] == "scene-custom"
    assert result["scenes"][0]["source_name"] == "PoseCustom"


def test_confirmed_hierarchy_without_scene_targets_uses_unique_stable_ids() -> None:
    case = "Root/CaseCustom"
    load = f"{case}/LoadCustom"
    run = f"{load}/RunCustom"
    option_a, option_b = f"{run}/Option A", f"{run}/Option B"
    scene_a1, scene_a2, scene_b1 = f"{option_a}/Pose01", f"{option_a}/Pose02", f"{option_b}/Pose01"
    assignments = [
        {"relative_path": load, "role_kind": "LOAD_CASE", "target_id": "load-custom", "raw_name": "LoadCustom"},
        {"relative_path": run, "role_kind": "EXECUTION_RUN", "target_id": "run-custom", "raw_name": "RunCustom"},
        {"relative_path": option_a, "role_kind": "RUN_OPTION", "target_id": None, "raw_name": "Option A", "option_status": "PRESENT"},
        {"relative_path": scene_a1, "role_kind": "SCENE", "target_id": None, "raw_name": "Pose01"},
        {"relative_path": scene_a2, "role_kind": "SCENE", "target_id": None, "raw_name": "Pose02"},
        {"relative_path": option_b, "role_kind": "RUN_OPTION", "target_id": None, "raw_name": "Option B", "option_status": "PRESENT"},
        {"relative_path": scene_b1, "role_kind": "SCENE", "target_id": None, "raw_name": "Pose01"},
    ]
    filename = "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv"
    files = [(f"{scene}/{filename}", b"Position,Layer_1\nTOP,7\n", "text/csv") for scene in (scene_a1, scene_a2, scene_b1)]

    first = _distribution_payload(case, files, {"hierarchy_assignments": assignments})
    repeated = _distribution_payload(case, files, {"hierarchy_assignments": assignments})

    assert len(first["runs"]) == 2
    assert len({run_row["run_option_id"] for run_row in first["runs"]}) == 2
    scene_ids = [scene["id"] for run_row in first["runs"] for scene in run_row["scenes"]]
    assert len(scene_ids) == len(set(scene_ids)) == 3
    assert "None" not in scene_ids
    assert scene_ids == [scene["id"] for run_row in repeated["runs"] for scene in run_row["scenes"]]


def test_contour_media_keeps_component_and_unknown_frame_provenance() -> None:
    scene = build_distribution_scene(
        "1_Face_Drop_Scene01_Face1_1st",
        [("CONTOUR_COMP23_Max_Stress_P1 (major)_Mid.jpg", b"synthetic-image")],
    )

    assert scene["media"] == [
        {
            "asset_id": scene["media"][0]["asset_id"],
            "relative_path": "CONTOUR_COMP23_Max_Stress_P1 (major)_Mid.jpg",
            "kind": "IMAGE",
            "status": "READY",
            "component_id": "C23",
            "subject_role": "UNKNOWN",
            "frame_role": "UNKNOWN",
        }
    ]


def test_corner_summary_detail_mismatch_is_a_quality_issue() -> None:
    scene = build_distribution_scene(
        "1_Face_Drop_Scene01_Face1_1st",
        [
            (
                "CORNER_DATA_C23_CORNER_BOT_LH_Max_Stress_P1 (major)_Mid_scene.h3d.csv",
                b"Node_id,Value\n2001,50\n2002,45\n",
            ),
            (
                "CORNER_MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv",
                b"Position,Max_Value\nBOT_LH,55\n",
            ),
        ],
    )

    assert "CORNER_SUMMARY_DETAIL_MISMATCH" in scene["quality_issues"]


@pytest.mark.parametrize(
    "content",
    [
        b'{"value":1e999}',
        b'{"nested":{"value":1e999}}',
        b'{"value":1,"value":2}',
        b"value,1\nvalue,2\n",
        b"value,1e999\n",
    ],
)
def test_usage_rejects_non_finite_numbers_and_duplicate_keys(content: bytes) -> None:
    suffix = ".json" if content.startswith(b"{") else ".csv"
    parsed = parse_usage_file(content, f"Settle/result{suffix}", kind="Settle")

    assert parsed["status"] == "SOURCE_PARSE_ERROR"
    assert parsed["values"] == {}


@pytest.mark.parametrize(
    ("content", "relative_path"),
    [
        (
            b"Node,coord_x,coord_y,coord_z,ref_coord,C23_SIDE_TOP_L1,C23_SIDE_TOP_L2,C23_SIDE_TOP_L3,C23_SIDE_TOP_L4\n"
            b"1001,0,0,0,0,10,20,30,40\n",
            "LAYER_ALIGN_C23_SIDE_TOP_Max_Stress_P1 (major)_Mid_scene.h3d.csv",
        ),
        (
            b"Node_id,coord_x,coord_y,coord_z,ref_coord,C23_SIDE_TOP_L1,C23_SIDE_TOP_L2,C23_SIDE_TOP_L3,C23_SIDE_TOP_L4\n"
            b"1001,0,0,0,0,10,missing,30,40\n",
            "LAYER_ALIGN_C23_SIDE_TOP_Max_Stress_P1 (major)_Mid_scene.h3d.csv",
        ),
        (
            b"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,10,20,,40\n",
            "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv",
        ),
        (
            b"Node_id,Value\n2001,\n",
            "CORNER_DATA_C23_CORNER_BOT_LH_Max_Stress_P1 (major)_Mid_scene.h3d.csv",
        ),
    ],
)
def test_known_distribution_shapes_fail_closed_on_bad_headers_or_numbers(
    content: bytes, relative_path: str
) -> None:
    parsed = parse_distribution_csv(content, relative_path)

    assert parsed["status"] == "SOURCE_PARSE_ERROR"
    assert parsed["observations"] == []


def test_unknown_distribution_csv_is_preserved_as_ignored() -> None:
    parsed = parse_distribution_csv(b"arbitrary,shape\n1,2\n", "unknown.csv")

    assert parsed == {
        "status": "IGNORED",
        "source": "unknown.csv",
        "observations": [],
    }


def test_csv_row_limit_fails_closed_before_unbounded_normalization(monkeypatch) -> None:
    monkeypatch.setattr(parser, "MAX_CSV_ROWS", 2)

    parsed = parse_distribution_csv(
        b"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,1,2,3,4\nBOT,5,6,7,8\n",
        "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv",
    )

    assert parsed["status"] == "SOURCE_PARSE_ERROR"
    assert parsed["observations"] == []


def test_scene_observation_limit_rejects_whole_source_without_partial_rows(monkeypatch) -> None:
    monkeypatch.setattr(parser, "MAX_SCENE_OBSERVATIONS", 7)

    scene = build_distribution_scene(
        "1_Face_Drop_Scene01_Face1_1st",
        [
            (
                "MAX_RESULT_Max_Stress_P1 (major)_Mid_C23_scene.h3d.csv",
                b"Position,Layer_1,Layer_2,Layer_3,Layer_4\nTOP,1,2,3,4\n",
            ),
            (
                "CORNER_DATA_C23_CORNER_BOT_LH_Max_Stress_P1 (major)_Mid_scene.h3d.csv",
                b"Node_id,Value\n2001,5\n2002,6\n2003,7\n2004,8\n",
            ),
        ],
    )

    assert "SOURCE_PARSE_ERROR" in scene["quality_issues"]
    assert len(scene["observations"]) == 4
    assert all(item["kind"] == "SIDE" for item in scene["observations"])
