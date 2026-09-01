from __future__ import annotations


class DashboardWriteError(Exception):
    """Base error for framework-independent dashboard write commands."""


class DashboardIdMismatchError(DashboardWriteError):
    def __init__(self) -> None:
        super().__init__("대시보드 ID가 일치하지 않습니다.")


class DashboardNotFoundError(DashboardWriteError):
    def __init__(self) -> None:
        super().__init__("대시보드를 찾을 수 없습니다.")


class DashboardVersionNotFoundError(DashboardWriteError):
    def __init__(self) -> None:
        super().__init__("삭제할 수 있는 유효한 대시보드 버전을 찾을 수 없습니다.")


class SystemDashboardVersionError(DashboardWriteError):
    def __init__(self) -> None:
        super().__init__("시스템 대시보드의 최초 기준 버전은 삭제할 수 없습니다.")


class LiveDashboardVersionError(DashboardWriteError):
    def __init__(self) -> None:
        super().__init__("현재 사용 중인 live 버전은 삭제할 수 없습니다.")


class LastDashboardHistoryError(DashboardWriteError):
    def __init__(self) -> None:
        super().__init__("현재 버전 외에 최소 1개의 유효한 과거 버전을 유지해야 합니다.")


class AnalysisPageUserEditError(DashboardWriteError):
    def __init__(self) -> None:
        super().__init__("분석 페이지 이름·설명·상태·순서는 관리자 페이지 API에서 변경해야 합니다.")


class RestorableVersionNotFoundError(DashboardWriteError):
    def __init__(self) -> None:
        super().__init__("복구 가능한 정상 버전을 찾을 수 없습니다.")


class PublishedEmptyRestoreError(DashboardWriteError):
    def __init__(self) -> None:
        super().__init__("게시된 분석 페이지를 빈 위젯 버전으로 복구할 수 없습니다.")
