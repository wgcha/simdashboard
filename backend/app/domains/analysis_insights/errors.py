from __future__ import annotations


class AnalysisInsightsError(Exception):
    """Base error for read-only analysis-insight use cases."""


class MatchingRunComparisonError(AnalysisInsightsError, ValueError):
    message = "기준 Run과 대상 Run은 달라야 합니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class ComparisonRunsNotFoundError(AnalysisInsightsError, LookupError):
    message = "선택한 Run을 하중 경우에서 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class AnalysisRunNotFoundError(AnalysisInsightsError, LookupError):
    message = "해석 Run을 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)
