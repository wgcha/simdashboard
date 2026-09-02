class DropVideoError(Exception):
    """Base error for catalog reads."""


class LoadCaseNotFoundError(DropVideoError, LookupError):
    message = "하중 경우를 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)
