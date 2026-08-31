from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from ...domains.variable_catalog.errors import DashboardVariableReferenceError
from ...domains.variable_catalog.models import VariableCatalogDefinition, VariableCatalogItem
from ...domains.variable_catalog.ports import VariableCatalogRepository


VariableCatalogRepositoryProvider = Callable[[], AbstractContextManager[VariableCatalogRepository]]


def create_variable(
    load_case_id: str,
    definition: VariableCatalogDefinition,
    repository_provider: VariableCatalogRepositoryProvider,
) -> VariableCatalogItem:
    with repository_provider() as repository:
        repository.authorize_mutation(load_case_id)
        return repository.create(load_case_id, definition)


def update_variable(
    load_case_id: str,
    variable_key: str,
    definition: VariableCatalogDefinition,
    repository_provider: VariableCatalogRepositoryProvider,
) -> VariableCatalogItem:
    with repository_provider() as repository:
        repository.authorize_mutation(load_case_id)
        return repository.update(load_case_id, variable_key, definition)


def delete_variable(
    load_case_id: str,
    variable_key: str,
    updated_by: str | None,
    repository_provider: VariableCatalogRepositoryProvider,
) -> dict[str, str]:
    with repository_provider() as repository:
        repository.authorize_mutation(load_case_id)
        references = repository.dashboard_references(load_case_id, variable_key)
        if references:
            raise DashboardVariableReferenceError(references)
        repository.deactivate(load_case_id, variable_key, updated_by)
    return {"status": "deactivated", "variable_key": variable_key}
