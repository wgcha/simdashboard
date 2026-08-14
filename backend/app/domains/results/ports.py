from __future__ import annotations

from typing import Protocol

from .models import RunSummaryReadData


class AnalysisRunSummaryRepository(Protocol):
    def read_for_load_case(self, load_case_id: str) -> RunSummaryReadData: ...
