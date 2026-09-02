from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...domains.drop_videos.policies import build_page
from ...domains.drop_videos.errors import LoadCaseNotFoundError
from ...domains.drop_videos.ports import DropVideoExampleSource, DropVideoRepositoryProvider


def list_drop_videos(
    load_case_id: str, page: int, page_size: int, authorize: Callable[[object], None],
    repository_provider: DropVideoRepositoryProvider,
    example_source: DropVideoExampleSource,
    *,
    storage_mode: Callable[[], str],
    demo_by_id: dict[str, Any], json_value: Callable[[Any], Any],
    build_evaluation: Callable[[Any], dict[str, Any]],
    summarize: Callable[[list[dict[str, Any]]], dict[str, Any]],
) -> dict[str, Any]:
    with repository_provider() as repository:
        context = repository.load_case_context(load_case_id)
        repository.authorize(authorize)
        stored = repository.list_drop_videos(load_case_id)
    if context is None:
        raise LoadCaseNotFoundError()
    database_only = storage_mode() == "database-only"
    examples = example_source(load_case_id) if not stored and not database_only else []
    return build_page(
        context,
        stored,
        examples,
        page,
        page_size,
        database_only=database_only,
        demo_by_id=demo_by_id,
        json_value=json_value,
        build_evaluation=build_evaluation,
        summarize=summarize,
    )
