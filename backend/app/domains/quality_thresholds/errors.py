from __future__ import annotations


class QualityThresholdError(Exception):
    """Base error for quality-threshold use cases."""


class QualityThresholdNotFoundError(QualityThresholdError, LookupError):
    message = "품질 판정 기준을 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class ProjectScopedThresholdUrlRequiredError(QualityThresholdError):
    message = "프로젝트 범위 품질 기준 URL을 사용해야 합니다."

    def __init__(self) -> None:
        super().__init__(self.message)
