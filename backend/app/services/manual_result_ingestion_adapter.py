"""Adapt the ordinary manual result-file parser to the canonical payload.

This adapter handles ``SUMMARY_RESULT`` payloads and has no database or HTTP
concerns. Radioss mesh payloads use their dedicated adapter because they also
carry entity locations.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


class ManualResultIngestionAdapterError(ValueError):
    """Raised when a manual time-series cannot form one canonical curve."""


def to_canonical_result_payload(
    parsed: dict[str, Any],
    *,
    source_file: str,
    source_checksum: str,
) -> dict[str, Any]:
    """Convert a validated summary parser result into canonical typed records."""

    scalars = [
        {
            "variable_key": item["variable_key"],
            "display_name": item["display_name"],
            "data_type": "FLOAT",
            "value": item["value"],
            "unit": item["unit"],
            "threshold": item["threshold"],
            "result_group": item.get("analysis", "CUSTOM"),
            "source_file": source_file,
            "source_checksum": source_checksum,
        }
        for item in parsed["scalars"]
    ]

    grouped: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for item in parsed["time_series"]:
        variable_key = item["variable_key"]
        curve = grouped.get(variable_key)
        if curve is None:
            curve = {
                "variable_key": variable_key,
                "display_name": item["display_name"],
                "series_key": "default",
                "catalog_data_type": "TIME_SERIES",
                "x_label": "시간",
                "x_unit": item["time_unit"],
                "y_label": item["display_name"],
                "y_unit": item["value_unit"],
                "result_group": "CUSTOM",
                "points": [],
                "source_file": source_file,
                "source_checksum": source_checksum,
            }
            grouped[variable_key] = curve
        elif (
            curve["display_name"] != item["display_name"]
            or curve["x_unit"] != item["time_unit"]
            or curve["y_unit"] != item["value_unit"]
        ):
            raise ManualResultIngestionAdapterError(
                f"시간 이력 {variable_key}의 display_name 또는 단위가 일관되지 않습니다."
            )
        if item["value_unit"].casefold() == "mpa" and "stress" in variable_key.casefold():
            curve["result_group"] = "OPEN_CELL"
        curve["points"].append({"x": item["time"], "y": item["value"]})

    return {
        "schema_id": "manual-file-upload",
        "schema_version": 1,
        "solver": parsed["solver"],
        "note": parsed["note"],
        "scalars": scalars,
        "curves": list(grouped.values()),
        "media": [],
    }
