from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from ...domains.products.models import ProductInformation
from ...domains.products.ports import ProductInformationRepository


AuthorizationCheck = Callable[[], object]
ProductInformationRepositoryProvider = Callable[
    [], AbstractContextManager[ProductInformationRepository]
]


def list_product_information(
    load_case_id: str,
    authorize: AuthorizationCheck,
    repository_provider: ProductInformationRepositoryProvider,
) -> list[ProductInformation]:
    authorize()
    with repository_provider() as repository:
        return repository.list_for_load_case(load_case_id)
