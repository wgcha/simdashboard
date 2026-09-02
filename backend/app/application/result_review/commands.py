from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from uuid import uuid4

from ...domains.result_review.errors import AnalysisRunNotFoundError, ReviewItemNotFoundError
from ...domains.result_review.models import ResultReviewAuditContext, ReviewItem, ReviewStatus
from ...domains.result_review.policies import body_for_update, validate_bookmark_target
from ...domains.result_review.ports import ResultReviewRepository


Clock = Callable[[], datetime]
IdentifierFactory = Callable[[], str]
ResultReviewRepositoryProvider = Callable[[], AbstractContextManager[ResultReviewRepository]]


def create_review_item(
    *,
    run_id: str,
    title: str,
    body: str,
    variable_key: str | None,
    time_value: float | None,
    entity_type: str | None,
    entity_id: str | None,
    review_status: ReviewStatus,
    actor_name: str,
    audit: ResultReviewAuditContext,
    repository_provider: ResultReviewRepositoryProvider,
    identifier_factory: IdentifierFactory | None = None,
    clock: Clock | None = None,
) -> ReviewItem:
    make_identifier = identifier_factory or new_identifier
    with repository_provider() as repository:
        bookmark_id, annotation_id = f"bookmark-{make_identifier()}", f"review-{make_identifier()}"
        now = (clock or utc_now)()
        with repository.transaction():
            project_id = repository.authorize_resource("run", run_id)
            if not repository.run_exists(run_id):
                raise AnalysisRunNotFoundError()
            available_result_keys = repository.result_keys(run_id) if variable_key else set()
            validate_bookmark_target(variable_key, available_result_keys, entity_type, entity_id)
            repository.insert_bookmark(
                bookmark_id=bookmark_id,
                run_id=run_id,
                variable_key=variable_key,
                time_value=time_value,
                entity_type=entity_type,
                entity_id=entity_id,
                title=title.strip(),
                actor_name=actor_name,
                occurred_at=now,
            )
            repository.insert_annotation(
                annotation_id=annotation_id,
                bookmark_id=bookmark_id,
                run_id=run_id,
                variable_key=variable_key,
                body=body.strip(),
                review_status=review_status,
                actor_name=actor_name,
                occurred_at=now,
            )
            repository.add_audit(
                {
                    **audit,
                    "status_code": 201,
                    "action": "RESULT_REVIEW_CREATED",
                    "detail": {"project_id": project_id, "run_id": run_id, "review_item_id": annotation_id},
                }
            )
        return repository.list_review_items(run_id, annotation_id)[0]


def update_review_item(
    *,
    annotation_id: str,
    body: str | None,
    review_status: ReviewStatus,
    audit: ResultReviewAuditContext,
    repository_provider: ResultReviewRepositoryProvider,
    clock: Clock | None = None,
) -> ReviewItem:
    with repository_provider() as repository:
        with repository.transaction():
            project_id = repository.authorize_resource("review_item", annotation_id)
            current = repository.get_annotation(annotation_id)
            if current is None:
                raise ReviewItemNotFoundError()
            now = (clock or utc_now)()
            repository.update_annotation(
                annotation_id=annotation_id,
                body=body_for_update(body, current["body"]),
                review_status=review_status,
                occurred_at=now,
            )
            repository.add_audit(
                {
                    **audit,
                    "status_code": 200,
                    "action": "RESULT_REVIEW_UPDATED",
                    "detail": {
                        "project_id": project_id,
                        "run_id": current["analysis_run_id"],
                        "review_item_id": annotation_id,
                        "old_status": current["review_status"],
                        "new_status": review_status,
                    },
                }
            )
            result = repository.list_review_items(current["analysis_run_id"], annotation_id)[0]
        return result


def new_identifier() -> str:
    return uuid4().hex[:12]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
