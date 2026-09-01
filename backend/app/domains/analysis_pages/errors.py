from __future__ import annotations


class AnalysisPageError(Exception):
    """Base error for analysis-page use cases."""


class LoadCaseNotFoundError(AnalysisPageError, LookupError):
    message = "하중 경우를 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class AnalysisPageCommandError(AnalysisPageError):
    """Framework-neutral command error carrying a user-facing message."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class AnalysisPageNameTooShortError(AnalysisPageCommandError):
    def __init__(self) -> None:
        super().__init__("분석 페이지 이름은 두 글자 이상이어야 합니다.")


class AnalysisPageNameConflictError(AnalysisPageCommandError):
    def __init__(self) -> None:
        super().__init__("같은 하중 경우에 동일한 분석 페이지 이름이 이미 있습니다.")


class AnalysisPageNotFoundError(AnalysisPageCommandError):
    def __init__(self) -> None:
        super().__init__("분석 페이지를 찾을 수 없습니다.")


class AnalysisPageNotManageableError(AnalysisPageCommandError):
    def __init__(self) -> None:
        super().__init__("관리 가능한 분석 페이지가 아닙니다.")


class SystemAnalysisPageLifecycleError(AnalysisPageCommandError):
    def __init__(self) -> None:
        super().__init__("시스템 기본 분석 페이지의 생명주기는 변경할 수 없습니다.")


class EmptyPublishedAnalysisPageError(AnalysisPageCommandError):
    def __init__(self) -> None:
        super().__init__("위젯이 없는 분석 페이지는 게시할 수 없습니다.")


class CustomAnalysisPageDeletionError(AnalysisPageCommandError):
    def __init__(self) -> None:
        super().__init__("사용자 정의 분석 페이지만 영구 삭제할 수 있습니다.")


class AnalysisPageLoadCaseMismatchError(AnalysisPageCommandError):
    def __init__(self) -> None:
        super().__init__("분석 페이지가 요청한 하중 경우에 속하지 않습니다.")


class DuplicateAnalysisPageOrderError(AnalysisPageCommandError):
    def __init__(self) -> None:
        super().__init__("분석 페이지 순서에 중복 ID가 있습니다.")


class AnalysisPageOrderMismatchError(AnalysisPageCommandError):
    def __init__(self) -> None:
        super().__init__("현재 하중 경우의 보관되지 않은 사용자 분석 페이지를 모두 한 번씩 지정해야 합니다.")
