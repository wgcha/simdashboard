from __future__ import annotations

import json
from typing import Any


def load_cases_with_parameters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in items:
        item["parameters"] = json_value(item.pop("parameters_json"))
    return items


def json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
