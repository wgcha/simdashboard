from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from .models import QualityThreshold, QualityThresholdAuditRecord, QualityThresholdCriterion


class QualityThresholdRepository(Protocol):
    def list_thresholds(self, project_id: str) -> list[QualityThreshold]: ...

    def find_criteria(
        self, criterion_key: str, expected_project_id: str | None
    ) -> list[QualityThresholdCriterion]: ...

    def authorize_mutation(self, project_id: str) -> None: ...

    def begin_transaction(self) -> None: ...

    def commit_transaction(self) -> None: ...

    def rollback_transaction(self) -> None: ...

    def update_threshold(
        self,
        project_id: str,
        criterion_key: str,
        threshold_double: float,
        actor_name: str,
        occurred_at: datetime,
    ) -> None: ...

    def recalculate_chassis_rear(self, project_id: str, threshold_double: float) -> None: ...

    def recalculate_open_cell(self, project_id: str, threshold_double: float) -> None: ...

    def add_audit(self, audit: QualityThresholdAuditRecord) -> None: ...

    def updated_threshold(self, project_id: str, criterion_key: str) -> QualityThreshold: ...


class QualityThresholdRepositoryProvider(Protocol):
    def __call__(self) -> AbstractContextManager[QualityThresholdRepository]: ...
