from __future__ import annotations

from typing import Any

from ...application.products.queries import (
    AuthorizationCheck,
    ProductInformationRepositoryProvider,
    list_product_information,
)
from ...domains.load_case_overview.errors import LoadCaseNotFoundError, SelectedRunNotFoundError
from ...domains.load_case_overview.policies import overview_payload
from ...domains.load_case_overview.ports import LoadCaseOverviewRepositoryProvider
from ...domains.products.models import ProductInformation


def get_load_case_overview(
    load_case_id: str,
    run_id: str | None,
    authorize_product_information: AuthorizationCheck,
    product_repository_provider: ProductInformationRepositoryProvider,
    repository_provider: LoadCaseOverviewRepositoryProvider,
) -> dict[str, Any]:
    product_information: list[ProductInformation] = list_product_information(
        load_case_id,
        authorize_product_information,
        product_repository_provider,
    )
    with repository_provider() as repository:
        load_case = repository.load_case(load_case_id)
        if load_case is None:
            raise LoadCaseNotFoundError()
        selected_run_id = repository.selected_run(load_case_id, run_id)
        if run_id and selected_run_id is None:
            raise SelectedRunNotFoundError()
        projection = repository.run_projection(load_case_id, selected_run_id) if selected_run_id else None
    return overview_payload(load_case, selected_run_id, projection, product_information)
