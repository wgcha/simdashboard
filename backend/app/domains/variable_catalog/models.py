from __future__ import annotations

from typing import Any, TypedDict


class VariableCatalogDefinition(TypedDict, total=False):
    variable_key: str
    display_name: str
    data_type: str
    unit: str
    description: str
    filterable: bool
    threshold: float | None
    allowed_widgets: list[str]
    allowed_aggregations: list[str]
    result_group: str
    updated_by: str | None


VariableCatalogItem = dict[str, Any]
