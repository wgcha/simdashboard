from __future__ import annotations

from typing import Protocol

from .models import ReportLayout


class ReportLayoutRepository(Protocol):
    def list_active_layouts(self) -> list[ReportLayout]: ...
