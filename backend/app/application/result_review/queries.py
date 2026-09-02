from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager

from ...domains.result_review.errors import AnalysisRunNotFoundError
from ...domains.result_review.models import ReviewItem
from ...domains.result_review.ports import ResultReviewRepository


ResultReviewRepositoryProvider = Callable[[], AbstractContextManager[ResultReviewRepository]]


def list_review_items(run_id: str, repository_provider: ResultReviewRepositoryProvider) -> list[ReviewItem]:
    with repository_provider() as repository:
        if not repository.run_exists(run_id):
            raise AnalysisRunNotFoundError()
        return repository.list_review_items(run_id)
