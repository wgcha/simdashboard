from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from ...domains.variable_catalog.errors import LoadCaseNotFoundError
from ...domains.variable_catalog.models import VariableCatalogItem
from ...domains.variable_catalog.ports import VariableCatalogRepository


VariableCatalogRepositoryProvider = Callable[[], AbstractContextManager[VariableCatalogRepository]]


def list_variables(
    load_case_id: str,
    repository_provider: VariableCatalogRepositoryProvider,
) -> list[VariableCatalogItem]:
    with repository_provider() as repository:
        if not repository.load_case_exists(load_case_id):
            raise LoadCaseNotFoundError()
        return repository.list_for_load_case(load_case_id)
