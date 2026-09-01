from __future__ import annotations


class RequestLoadCaseWriteError(Exception):
    """Base error for framework-independent request load-case writes."""


class RequestNotFoundError(RequestLoadCaseWriteError):
    def __init__(self) -> None:
        super().__init__("해석 의뢰를 찾을 수 없습니다.")
