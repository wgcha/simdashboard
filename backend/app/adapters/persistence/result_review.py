from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, cast

from ...database_connection import ConnectionLike, connect, rows
from ...domains.result_review.models import (
    CurrentReviewItem,
    ResultReviewAuditRecord,
    ReviewItem,
    ReviewStatus,
)
from ...domains.result_review.ports import ResultReviewRepository
from .result_keys import run_result_keys


class SQLResultReviewRepository:
    """SQL adapter for result bookmarks and their review annotations."""

    def __init__(
        self,
        connection: ConnectionLike,
        *,
        authorize: Callable[[str, str, ConnectionLike], str] | None = None,
        audit_writer: Callable[[ResultReviewAuditRecord, ConnectionLike], None] | None = None,
    ) -> None:
        self._connection = connection
        self._authorize = authorize
        self._audit_writer = audit_writer

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self._connection.execute("BEGIN TRANSACTION")
        try:
            yield
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def authorize_resource(self, resource_type: str, resource_id: str) -> str:
        if self._authorize is None:
            raise RuntimeError("result review mutation requires an authorization callback")
        return self._authorize(resource_type, resource_id, self._connection)

    def run_exists(self, run_id: str) -> bool:
        return bool(self._connection.execute("SELECT 1 FROM analysis_runs WHERE id=?", [run_id]).fetchone())

    def result_keys(self, run_id: str) -> set[str]:
        return run_result_keys(self._connection, run_id)

    def list_review_items(self, run_id: str, annotation_id: str | None = None) -> list[ReviewItem]:
        conditions = "a.analysis_run_id=?"
        parameters: list[Any] = [run_id]
        if annotation_id:
            conditions += " AND a.id=?"
            parameters.append(annotation_id)
        return cast(
            list[ReviewItem],
            rows(
                self._connection.execute(
                    f"""
                    SELECT a.id, a.bookmark_id, a.analysis_run_id, a.variable_key, b.title, b.time_value,
                           b.entity_type, b.entity_id, a.body, a.review_status, a.created_by, a.created_at, a.updated_at
                    FROM review_annotations a JOIN result_bookmarks b ON b.id=a.bookmark_id
                    WHERE {conditions} ORDER BY a.updated_at DESC
                    """,
                    parameters,
                )
            ),
        )

    def get_annotation(self, annotation_id: str) -> CurrentReviewItem | None:
        current = self._connection.execute(
            "SELECT analysis_run_id, body, review_status FROM review_annotations WHERE id=?",
            [annotation_id],
        ).fetchone()
        if not current:
            return None
        return cast(
            CurrentReviewItem,
            {"analysis_run_id": current[0], "body": current[1], "review_status": current[2]},
        )

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
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO result_bookmarks
                (id, analysis_run_id, variable_key, time_value, entity_type, entity_id, title, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [bookmark_id, run_id, variable_key, time_value, entity_type, entity_id, title, actor_name, occurred_at],
        )

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
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO review_annotations
                (id, bookmark_id, analysis_run_id, variable_key, body, review_status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [annotation_id, bookmark_id, run_id, variable_key, body, review_status, actor_name, occurred_at, occurred_at],
        )

    def update_annotation(
        self,
        *,
        annotation_id: str,
        body: str,
        review_status: ReviewStatus,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            "UPDATE review_annotations SET body=?, review_status=?, updated_at=? WHERE id=?",
            [body, review_status, occurred_at, annotation_id],
        )

    def add_audit(self, audit: ResultReviewAuditRecord) -> None:
        if self._audit_writer is None:
            raise RuntimeError("result review mutation requires an audit callback")
        self._audit_writer(audit, self._connection)


class SQLResultReviewRepositoryProvider:
    def __init__(
        self,
        connection_provider: Callable[[], Any] = connect,
        *,
        authorize: Callable[[str, str, ConnectionLike], str] | None = None,
        audit_writer: Callable[[ResultReviewAuditRecord, ConnectionLike], None] | None = None,
    ) -> None:
        self._connection_provider = connection_provider
        self._authorize = authorize
        self._audit_writer = audit_writer

    @contextmanager
    def __call__(self) -> Iterator[ResultReviewRepository]:
        with self._connection_provider() as connection:
            yield SQLResultReviewRepository(
                connection,
                authorize=self._authorize,
                audit_writer=self._audit_writer,
            )
