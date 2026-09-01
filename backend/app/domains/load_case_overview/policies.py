from __future__ import annotations

import json
from typing import Any, Literal

from ...domains.products.models import ProductInformation


def json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def overview_payload(
    load_case: dict[str, Any],
    run_id: str | None,
    projection: dict[str, Any] | None,
    product_information: list[ProductInformation],
) -> dict[str, Any]:
    load_case["parameters"] = json_value(load_case.pop("parameters_json"))
    if run_id is None or projection is None:
        return {
            "load_case": load_case,
            "run": None,
            "template_execution": None,
            "overall_verdict": "NO_DATA",
            "analysis_verdicts": {"open_cell": "NO_DATA", "chassis_rear": "NO_DATA"},
            "threshold": None,
            "product_information": product_information,
            "scalar_results": [],
            "time_series": [],
            "curves": [],
            "result_locations": [],
            "notes": [],
            "media": [],
        }

    scalar_results = projection["scalar_results"]
    time_series = projection["time_series"]
    curves = projection["curves"]
    result_locations = projection["result_locations"]
    notes = projection["notes"]
    media = projection["media"]
    template = projection["template"]
    for item in media:
        item["metadata"] = json_value(item.pop("metadata_json"))
    for item in template:
        item["input"] = json_value(item.pop("input_json"))
        item["generated_model"] = json_value(item.pop("generated_model_json"))
    open_cell_results = [item for item in scalar_results if item.get("result_group") == "OPEN_CELL" and str(item.get("unit", "")).casefold() == "mpa" and "stress" in str(item.get("variable_key", "")).casefold()]
    chassis_results = [item for item in scalar_results if item.get("result_group") == "CHASSIS_REAR" and str(item.get("unit", "")).casefold() == "mm" and "permanent_deformation" in str(item.get("variable_key", "")).casefold()]
    threshold = next((item["threshold_double"] for item in open_cell_results if item["threshold_double"] is not None), None)
    if threshold is None:
        threshold = next((item["threshold_double"] for item in chassis_results if item["threshold_double"] is not None), None)
    overall_verdict: Literal["PASS", "FAIL", "NO_DATA"] = "NO_DATA"
    if scalar_results:
        overall_verdict = "FAIL" if any(item["verdict"] == "FAIL" for item in scalar_results) else "PASS"
    return {
        "load_case": load_case,
        "run": run_id,
        "template_execution": template[0] if template else None,
        "overall_verdict": overall_verdict,
        "analysis_verdicts": {
            "open_cell": "FAIL" if any(item["verdict"] == "FAIL" for item in open_cell_results) else "PASS" if open_cell_results else "NO_DATA",
            "chassis_rear": "FAIL" if any(item["verdict"] == "FAIL" for item in chassis_results) else "PASS" if chassis_results else "NO_DATA",
        },
        "threshold": threshold,
        "product_information": product_information,
        "scalar_results": scalar_results,
        "time_series": time_series,
        "curves": curves,
        "result_locations": result_locations,
        "notes": notes,
        "media": media,
    }
