from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict


class ReportLayout(TypedDict):
    """The existing report-layout read response, expressed without an HTTP DTO."""

    id: str
    name: str
    description: str | None
    version: int
    is_system: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    updated_by: str
    definition: Any
