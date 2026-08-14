from __future__ import annotations

from typing import Any, TypedDict


class ProductInformation(TypedDict):
    category: str
    name: str
    value_text: str | None
    file_path: str | None
    metadata: Any
