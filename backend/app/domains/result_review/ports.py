from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime
from typing import Protocol

from .models import CurrentReviewItem, ResultReviewAuditRecord, ReviewItem, ReviewStatus


class ResultReviewRepository(Protocol):
    def transaction(self) -> AbstractContextManager[None]: ...

    def authorize_resource(self, resource_type: str, resource_id: str) -> str: ...

    def run_exists(self, run_id: str) -> bool: ...

    def result_keys(self, run_id: str) -> set[str]: ...

    def list_review_items(self, run_id: str, annotation_id: str | None = None) -> list[ReviewItem]: ...

    def get_annotation(self, annotation_id: str) -> CurrentReviewItem | None: ...

    def insert_bookmark(
        self,
        *,
        bookmark_id: str,
        run_id: str,
        variable_key: str | None,
        time_value: float | None,
        entity_type: str | None,
        entity_id: str | None,
        title: str,
        actor_name: str,
        occurred_at: datetime,
    ) -> None: ...

    def insert_annotation(
        self,
        *,
        annotation_id: str,
        bookmark_id: str,
        run_id: str,
        variable_key: str | None,
        body: str,
        review_status: ReviewStatus,
        actor_name: str,
        occurred_at: datetime,
    ) -> None: ...

    def update_annotation(
        self,
        *,
        annotation_id: str,
        body: str,
        review_status: ReviewStatus,
        occurred_at: datetime,
    ) -> None: ...

    def add_audit(self, audit: ResultReviewAuditRecord) -> None: ...
