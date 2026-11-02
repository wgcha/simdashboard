from __future__ import annotations

import pytest

from app.services import dashboard_queries as queries


pytestmark = pytest.mark.unit


def _capture(with_observation: bool = True) -> dict:
    observations = []
    if with_observation:
        observations.append({
            "kind": "SIDE",
            "component_id": "C23",
            "basis": "DETAIL",
            "position": "TOP",
            "line_index": 2,
            "value": 12.5,
            "row": 3,
            "column": "Layer_2",
        })
    return {
        "id": "capture-1",
        "case_id": "case-1",
        "environment": "DISTRIBUTION",
        "payload": {
            "context": {"project_id": "p", "request_id": "r", "simulation_case_id": "case-1", "capture_id": "capture-1"},
            "runs": [{
                "id": "run-1",
                "load_case_id": "load-1",
                "source_name": "run",
                "mode": "INDIVIDUAL",
                "scenes": [{"id": "scene-1", "source_name": "scene", "observations": observations,
                            "media": [{"asset_id": "image-1", "component_id": "C23", "kind": "IMAGE"}]}],
            }],
        },
    }


def test_contour_reuses_selected_location_peak_with_provenance() -> None:
    result = queries.distribution(_capture(), "run-1", "INDIVIDUAL", "C23", "DETAIL", {"TOP"}, {2})

    contour_value = result["contours"][0]["value"]
    assert contour_value["value"] == 12.5
    assert contour_value["basis"] == "DETAIL"
    assert contour_value["scope"] == "EXTRACTED_SELECTED_LINES"
    assert contour_value["status"] == "PARTIAL"
    assert contour_value["source_refs"] == [{"row": 3, "column": "Layer_2"}]
    assert result["location_peaks"][0]["value"] == contour_value["value"]


def test_contour_value_is_null_when_selected_source_has_no_numeric_value() -> None:
    result = queries.distribution(_capture(False), "run-1", "INDIVIDUAL", "C23", "DETAIL", {"TOP"}, {2})

    assert result["location_peaks"][0]["status"] == "MISSING"
    assert result["contours"][0]["value"] is None


def test_contour_value_is_complete_when_every_side_position_has_the_selected_lines() -> None:
    capture = _capture()
    observations = capture["payload"]["runs"][0]["scenes"][0]["observations"]
    for position, value in (("BOT", 3.0), ("LH", 4.0), ("RH", 5.0)):
        observations.append({"kind": "SIDE", "component_id": "C23", "basis": "DETAIL", "position": position,
                             "line_index": 2, "value": value, "row": 3, "column": "Layer_2"})
    result = queries.distribution(capture, "run-1", "INDIVIDUAL", "C23", "DETAIL", {"TOP"}, {2})

    contour = result["contours"][0]
    assert contour["value"]["status"] == "READY" and contour["value"]["completeness"] == "COMPLETE"
    assert contour["status"] == "READY" and contour["reason"] is None


def test_contour_reason_names_what_is_missing() -> None:
    capture = _capture()
    scene = capture["payload"]["runs"][0]["scenes"][0]
    scene["media"] = []
    assert queries.distribution(capture, "run-1", "INDIVIDUAL", "C23", "DETAIL", {"TOP"}, {2})["contours"][0]["reason"] == "컨투어 이미지 없음"
    scene["media"] = [{"asset_id": f"image-{n}", "component_id": "C23", "kind": "IMAGE"} for n in (1, 2)]
    contour = queries.distribution(capture, "run-1", "INDIVIDUAL", "C23", "DETAIL", {"TOP"}, {2})["contours"][0]
    assert contour["status"] == "AMBIGUOUS" and "여러 장" in contour["reason"]
