from __future__ import annotations


class AnalysisPageError(Exception):
    """Base error for analysis-page reads."""


class LoadCaseNotFoundError(AnalysisPageError, LookupError):
    message = "하중 경우를 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)
