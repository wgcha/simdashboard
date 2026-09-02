from __future__ import annotations

from typing import Any

from ...domains.request_load_cases.policies import load_cases_with_parameters
from ...domains.request_load_cases.ports import RequestLoadCasesRepositoryProvider


def get_load_cases(
    request_id: str,
    repository_provider: RequestLoadCasesRepositoryProvider,
) -> list[dict[str, Any]]:
    with repository_provider() as repository:
        items = repository.list_load_cases(request_id)
    return load_cases_with_parameters(items)
