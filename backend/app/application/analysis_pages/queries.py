from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...domains.analysis_pages.errors import LoadCaseNotFoundError
from ...domains.analysis_pages.ports import AnalysisPageRepositoryProvider
from ...domains.analysis_pages.policies import sort_analysis_pages


def _require_context(repository: Any, load_case_id: str) -> tuple[Any, ...]:
    context = repository.load_case_context(load_case_id)
    if context is None:
        raise LoadCaseNotFoundError()
    return context


def list_public_analysis_pages(
    load_case_id: str,
    repository_provider: AnalysisPageRepositoryProvider,
) -> list[dict[str, Any]]:
    with repository_provider() as repository:
        _require_context(repository, load_case_id)
        return sort_analysis_pages(
            repository.list_analysis_pages(
                load_case_id,
                include_private=False,
                include_archived=False,
            )
        )


def list_admin_analysis_pages(
    load_case_id: str,
    include_archived: bool,
    authorize: Callable[[str, object], None],
    repository_provider: AnalysisPageRepositoryProvider,
) -> list[dict[str, Any]]:
    with repository_provider() as repository:
        context = _require_context(repository, load_case_id)
        repository.authorize(lambda connection: authorize(context[0], connection))
        _require_context(repository, load_case_id)
        return sort_analysis_pages(
            repository.list_analysis_pages(
                load_case_id,
                include_private=True,
                include_archived=include_archived,
            )
        )
