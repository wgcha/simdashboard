from __future__ import annotations

from ...domains.quality_thresholds.models import QualityThreshold
from ...domains.quality_thresholds.ports import QualityThresholdRepositoryProvider


def list_quality_thresholds(
    project_id: str,
    repository_provider: QualityThresholdRepositoryProvider,
) -> list[QualityThreshold]:
    with repository_provider() as repository:
        return repository.list_thresholds(project_id)
