from __future__ import annotations

from typing import Protocol

from .models import VariableCatalogDefinition, VariableCatalogItem


class VariableCatalogRepository(Protocol):
    def load_case_exists(self, load_case_id: str) -> bool: ...

    def authorize_mutation(self, load_case_id: str) -> None: ...

    def list_for_load_case(self, load_case_id: str) -> list[VariableCatalogItem]: ...

    def create(self, load_case_id: str, definition: VariableCatalogDefinition) -> VariableCatalogItem: ...

    def update(
        self,
        load_case_id: str,
        variable_key: str,
        definition: VariableCatalogDefinition,
    ) -> VariableCatalogItem: ...

    def deactivate(self, load_case_id: str, variable_key: str, updated_by: str | None) -> None: ...

    def dashboard_references(self, load_case_id: str, variable_key: str) -> list[str]: ...
