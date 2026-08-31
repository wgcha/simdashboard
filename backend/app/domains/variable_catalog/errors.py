from __future__ import annotations


class VariableCatalogError(Exception):
    """A stable, transport-neutral variable-catalog error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class VariableAlreadyExistsError(VariableCatalogError, ValueError):
    def __init__(self) -> None:
        super().__init__("VARIABLE_EXISTS")


class VariableNotFoundError(VariableCatalogError, LookupError):
    def __init__(self) -> None:
        super().__init__("VARIABLE_NOT_FOUND")


class LoadCaseNotFoundError(VariableCatalogError, LookupError):
    def __init__(self) -> None:
        super().__init__("LOAD_CASE_NOT_FOUND")


class DashboardVariableReferenceError(VariableCatalogError):
    def __init__(self, dashboard_ids: list[str]) -> None:
        super().__init__("VARIABLE_IN_USE")
        self.dashboard_ids = dashboard_ids


class VariableCatalogValidationError(VariableCatalogError, ValueError):
    pass
