from __future__ import annotations

import json
from typing import Any

from .errors import VariableCatalogValidationError
from .models import VariableCatalogDefinition


NUMBER_WIDGETS = {"kpi", "gauge", "edge_bar", "scatter", "result_table", "chassis_bar", "chassis_table"}
SERIES_WIDGETS = {"time_series", "scatter", "result_table"}
TEXT_WIDGETS = {"note", "result_table", "verdict"}
MEDIA_WIDGETS = {"contour", "video", "model3d", "result_table"}
NUMBER_AGGREGATIONS = {"MAX", "MIN", "AVG", "LATEST"}
SERIES_AGGREGATIONS = {"RAW", "MAX_BY_TIME"}
TEXT_AGGREGATIONS = {"LATEST"}
MEDIA_AGGREGATIONS = {"LATEST"}

SCALAR_TYPES = {"NUMBER", "FLOAT", "INTEGER", "TEXT", "VERDICT", "STATUS", "BOOLEAN"}
CURVE_TYPES = {"TIME_SERIES", "CURVE"}
MEDIA_TYPES = {"IMAGE", "VIDEO", "MODEL_3D"}


def normalize_definition(definition: VariableCatalogDefinition) -> list[Any]:
    """Apply the legacy catalog defaults and validations before persistence."""
    data_type = definition["data_type"]
    if data_type in SCALAR_TYPES:
        valid_widgets, valid_aggregations, source = NUMBER_WIDGETS, NUMBER_AGGREGATIONS, "scalar_results"
        if data_type in {"TEXT", "VERDICT", "STATUS"}:
            valid_widgets, valid_aggregations = TEXT_WIDGETS, TEXT_AGGREGATIONS
    elif data_type in CURVE_TYPES:
        valid_widgets, valid_aggregations = SERIES_WIDGETS, SERIES_AGGREGATIONS
        source = "curve_results" if data_type == "CURVE" else "time_series_results"
    elif data_type in MEDIA_TYPES:
        valid_widgets, valid_aggregations, source = MEDIA_WIDGETS, MEDIA_AGGREGATIONS, "media_assets"
    else:
        raise VariableCatalogValidationError("INVALID_DATA_TYPE")
    allowed_widgets = definition.get("allowed_widgets") or sorted(valid_widgets)
    allowed_aggregations = definition.get("allowed_aggregations") or sorted(valid_aggregations)
    if not set(allowed_widgets) <= valid_widgets or not set(allowed_aggregations) <= valid_aggregations:
        raise VariableCatalogValidationError("INVALID_CATALOG_OPTIONS")
    if data_type == "NUMBER" and definition.get("threshold") is None:
        raise VariableCatalogValidationError("NUMBER_THRESHOLD_REQUIRED")
    return [
        definition["display_name"].strip(), data_type, definition["unit"].strip(),
        definition.get("description", "").strip(), bool(definition.get("filterable", True)), source,
        definition.get("threshold"), json.dumps(allowed_widgets), json.dumps(allowed_aggregations),
        definition.get("result_group", "CUSTOM"),
    ]
