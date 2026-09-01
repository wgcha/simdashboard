from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from ...domains.quality_thresholds.errors import (
    ProjectScopedThresholdUrlRequiredError,
    QualityThresholdNotFoundError,
)
from ...domains.quality_thresholds.models import (
    QualityThreshold,
    QualityThresholdAuditContext,
)
from ...domains.quality_thresholds.policies import scalar_recalculation_scope
from ...domains.quality_thresholds.ports import QualityThresholdRepositoryProvider


Clock = Callable[[], datetime]
ActorNameProvider = Callable[[], str]
AuditContextProvider = Callable[[], QualityThresholdAuditContext]


def update_quality_threshold(
    *,
    criterion_key: str,
    threshold_double: float,
    expected_project_id: str | None,
    actor_name: ActorNameProvider,
    audit: AuditContextProvider,
    repository_provider: QualityThresholdRepositoryProvider,
    clock: Clock | None = None,
) -> QualityThreshold:
    with repository_provider() as repository:
        criteria = repository.find_criteria(criterion_key, expected_project_id)
        if not criteria:
            raise QualityThresholdNotFoundError()
        if expected_project_id is None and len(criteria) > 1:
            raise ProjectScopedThresholdUrlRequiredError()

        criterion = criteria[0]
        project_id, unit, old_threshold = (
            criterion["project_id"],
            criterion["unit"],
            criterion["threshold_double"],
        )
        repository.authorize_mutation(project_id)
        principal_name = actor_name()
        now = (clock or utc_now)()
        repository.begin_transaction()
        try:
            repository.update_threshold(
                project_id,
                criterion_key,
                threshold_double,
                principal_name,
                now,
            )
            recalculation_scope = scalar_recalculation_scope(criterion_key)
            if recalculation_scope == "CHASSIS_REAR":
                repository.recalculate_chassis_rear(project_id, threshold_double)
            elif recalculation_scope == "OPEN_CELL":
                repository.recalculate_open_cell(project_id, threshold_double)
            repository.add_audit(
                {
                    **audit(),
                    "status_code": 200,
                    "action": "PROJECT_THRESHOLD_CHANGED",
                    "detail": {
                        "project_id": project_id,
                        "criterion_key": criterion_key,
                        "old_value": old_threshold,
                        "new_value": threshold_double,
                        "unit": unit,
                    },
                }
            )
            repository.commit_transaction()
            return repository.updated_threshold(project_id, criterion_key)
        except Exception:
            repository.rollback_transaction()
            raise


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
