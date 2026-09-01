from __future__ import annotations


class LoadCaseOverviewError(Exception):
    """Base error for load-case overview reads."""


class LoadCaseNotFoundError(LoadCaseOverviewError, LookupError):
    message = "하중 경우를 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class SelectedRunNotFoundError(LoadCaseOverviewError, LookupError):
    message = "선택한 Run이 이 하중 경우에 존재하지 않습니다."

    def __init__(self) -> None:
        super().__init__(self.message)
