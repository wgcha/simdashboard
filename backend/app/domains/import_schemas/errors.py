from __future__ import annotations


class ImportSchemaError(Exception):
    """Base error for import-schema use cases."""


class ImportSchemaValidationError(ImportSchemaError, ValueError):
    message = "스키마 정의에는 mappings 배열이 필요합니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class ImportSchemaNotFoundError(ImportSchemaError, LookupError):
    message = "폴더 스키마를 찾을 수 없습니다."

    def __init__(self) -> None:
        super().__init__(self.message)


class ImportSchemaInUseError(ImportSchemaError):
    message = "적재 이력이 있는 스키마는 삭제할 수 없습니다. 비활성화 정책이 필요합니다."

    def __init__(self) -> None:
        super().__init__(self.message)
