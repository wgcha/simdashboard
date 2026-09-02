from __future__ import annotations


class ResultReviewError(Exception):
    """Base error for result-review use cases."""


class AnalysisRunNotFoundError(ResultReviewError, LookupError):
    message = "해석 Run을 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class ReviewItemNotFoundError(ResultReviewError, LookupError):
    message = "검토 의견을 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class ResultVariableNotFoundError(ResultReviewError, ValueError):
    message = "선택한 변수는 이 Run의 결과에 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class ReviewEntityIdRequiredError(ResultReviewError, ValueError):
    message = "엔티티 유형을 지정하면 엔티티 ID도 필요합니다."

    def __init__(self) -> None:
        super().__init__(self.message)
