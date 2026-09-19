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
