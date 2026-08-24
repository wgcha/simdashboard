"""Adapt the Radioss mesh parser output to the canonical result contract.

The mesh parser intentionally exposes a small, API-friendly shape.  This
module is the boundary that adds the provenance and typed persistence fields
required by ``ingest_result_bundle``; SQL and HTTP concerns stay out of the
parser.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any


class RadiossResultIngestionAdapterError(ValueError):
    """Raised when a Radioss parser result cannot form canonical records."""


def to_canonical_radioss_result_payload(
    parsed: dict[str, Any],
    *,
    source_file: str,
    source_checksum: str,
) -> dict[str, Any]:
    """Convert a validated ``parse_radioss_mesh_csv`` result.

    ``locations`` are deliberately carried alongside scalar and curve rows so
    the SQL UoW can commit all result evidence in one transaction.
    """

    try:
        raw_scalars = parsed["scalars"]
        raw_series = parsed["time_series"]
        raw_locations = parsed["locations"]
    except KeyError as exc:
        raise RadiossResultIngestionAdapterError(f"Radioss 결과 필드가 없습니다: {exc.args[0]}") from exc

    scalars: list[dict[str, Any]] = []
    for item in raw_scalars:
        scalars.append(
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
        )

    grouped: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for item in raw_series:
        key = item["variable_key"]
        curve = grouped.get(key)
        if curve is None:
            curve = {
                "variable_key": key,
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
            grouped[key] = curve
        elif (
            curve["display_name"] != item["display_name"]
            or curve["x_unit"] != item["time_unit"]
            or curve["y_unit"] != item["value_unit"]
        ):
            raise RadiossResultIngestionAdapterError(
                f"시간 이력 {key}의 display_name 또는 단위가 일관되지 않습니다."
            )
        if item["value_unit"].casefold() == "mpa" and "stress" in key.casefold():
            curve["result_group"] = "OPEN_CELL"
        curve["points"].append({"x": item["time"], "y": item["value"]})

    locations = [
        {
            "variable_key": item["variable_key"],
            "entity_type": item["entity_type"],
            "entity_id": item["entity_id"],
            "x": item["x"],
            "y": item["y"],
            "z": item["z"],
            "time": item["time"],
            "time_unit": item["time_unit"],
            "method": item["method"],
        }
        for item in raw_locations
    ]

    return {
        "schema_id": "radioss-mesh-csv",
        "schema_version": 1,
        "solver": parsed["solver"],
        "note": parsed["note"],
        "scalars": scalars,
        "curves": list(grouped.values()),
        "locations": locations,
        "media": [],
    }
