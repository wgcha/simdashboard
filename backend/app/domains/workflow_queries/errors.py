from __future__ import annotations


class WorkflowQueryError(Exception):
    """Base error for workflow read use cases."""


class AnalysisRequestNotFoundError(WorkflowQueryError, LookupError):
    message = "해석 의뢰를 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)
