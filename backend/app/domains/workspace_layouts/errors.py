from __future__ import annotations


class WorkspaceLayoutError(Exception):
    """Base error for the workspace-layout use cases."""


class WorkspaceLayoutValidationError(WorkspaceLayoutError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UnsupportedWorkspaceLayoutKindError(WorkspaceLayoutError):
    message = "지원하지 않는 레이아웃 종류입니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class WorkspaceLayoutProjectNotFoundError(WorkspaceLayoutError):
    message = "프로젝트를 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class WorkspaceLayoutNotFoundError(WorkspaceLayoutError):
    message = "저장된 레이아웃이 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)
