from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from ...domains.results.models import AnalysisRunSummary
from ...domains.results.ports import AnalysisRunSummaryRepository
from ...domains.results.policies import summarize_run


AuthorizationCheck = Callable[[], object]
AnalysisRunSummaryRepositoryProvider = Callable[
    [], AbstractContextManager[AnalysisRunSummaryRepository]
]


def list_analysis_runs(
    load_case_id: str,
    authorize: AuthorizationCheck,
    repository_provider: AnalysisRunSummaryRepositoryProvider,
) -> list[AnalysisRunSummary]:
    authorize()
    with repository_provider() as repository:
        read_data = repository.read_for_load_case(load_case_id)
    latest_id = read_data["runs"][0]["id"] if read_data["runs"] else None
    return [
        summarize_run(
            run,
            read_data["evidence_by_run"][run["id"]],
            read_data["catalog_units"],
            is_latest=run["id"] == latest_id,
        )
        for run in read_data["runs"]
    ]
