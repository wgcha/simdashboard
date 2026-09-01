from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, cast

from ...database_connection import ConnectionLike, connect, rows
from ...domains.quality_thresholds.models import (
    QualityThreshold,
    QualityThresholdAuditRecord,
    QualityThresholdCriterion,
)
from ...domains.quality_thresholds.ports import QualityThresholdRepository


class SQLQualityThresholdRepository:
    """SQL adapter for the project-scoped quality-threshold contract."""

    def __init__(
        self,
        connection: ConnectionLike,
        *,
        authorize: Callable[[str, ConnectionLike], None] | None = None,
        audit_writer: Callable[[QualityThresholdAuditRecord, ConnectionLike], None] | None = None,
    ) -> None:
        self._connection = connection
        self._authorize = authorize
        self._audit_writer = audit_writer

    def list_thresholds(self, project_id: str) -> list[QualityThreshold]:
        return cast(
            list[QualityThreshold],
            rows(
                self._connection.execute(
                    "SELECT * FROM quality_thresholds WHERE project_id = ? ORDER BY analysis_key, criterion_key",
                    [project_id],
                )
            ),
        )

    def find_criteria(
        self,
        criterion_key: str,
        expected_project_id: str | None,
    ) -> list[QualityThresholdCriterion]:
        if expected_project_id is not None:
            criteria = rows(
                self._connection.execute(
                    "SELECT project_id, unit, threshold_double FROM quality_thresholds WHERE project_id = ? AND criterion_key = ?",
                    [expected_project_id, criterion_key],
                )
            )
        else:
            criteria = rows(
                self._connection.execute(
                    "SELECT project_id, unit, threshold_double FROM quality_thresholds WHERE criterion_key = ? ORDER BY project_id",
                    [criterion_key],
                )
            )
        return cast(list[QualityThresholdCriterion], criteria)

    def authorize_mutation(self, project_id: str) -> None:
        if self._authorize is None:
            raise RuntimeError("quality threshold mutation requires an authorization callback")
        self._authorize(project_id, self._connection)

    def begin_transaction(self) -> None:
        self._connection.execute("BEGIN TRANSACTION")

    def commit_transaction(self) -> None:
        self._connection.execute("COMMIT")

    def rollback_transaction(self) -> None:
        self._connection.execute("ROLLBACK")

    def update_threshold(
        self,
        project_id: str,
        criterion_key: str,
        threshold_double: float,
        actor_name: str,
        occurred_at: datetime,
    ) -> None:
        self._connection.execute(
            """
            UPDATE quality_thresholds
            SET threshold_double = ?, updated_by = ?, updated_at = ?
            WHERE project_id = ? AND criterion_key = ?
            """,
            [threshold_double, actor_name, occurred_at, project_id, criterion_key],
        )

    def recalculate_chassis_rear(self, project_id: str, threshold_double: float) -> None:
        self._connection.execute(
            """
            UPDATE scalar_results
            SET threshold_double = ?,
                verdict = CASE WHEN value_double >= ? THEN 'FAIL' ELSE 'PASS' END
            WHERE lower(unit) = 'mm'
              AND variable_key LIKE '%permanent_deformation%'
              AND analysis_run_id IN (
                  SELECT run.id
                  FROM analysis_runs run
                  JOIN load_cases lc ON lc.id = run.load_case_id
                  JOIN analysis_requests ar ON ar.id = lc.request_id
                  JOIN variable_definitions vd
                    ON vd.load_case_id = lc.id
                   AND vd.variable_key = scalar_results.variable_key
                  WHERE ar.project_id = ? AND vd.result_group = 'CHASSIS_REAR'
              )
            """,
            [threshold_double, threshold_double, project_id],
        )

    def recalculate_open_cell(self, project_id: str, threshold_double: float) -> None:
        self._connection.execute(
            """
            UPDATE scalar_results
            SET threshold_double = ?,
                verdict = CASE WHEN value_double >= ? THEN 'FAIL' ELSE 'PASS' END
            WHERE lower(unit) = 'mpa'
              AND lower(variable_key) LIKE '%stress%'
              AND analysis_run_id IN (
                  SELECT run.id
                  FROM analysis_runs run
                  JOIN load_cases lc ON lc.id = run.load_case_id
                  JOIN analysis_requests ar ON ar.id = lc.request_id
                  JOIN variable_definitions vd
                    ON vd.load_case_id = lc.id
                   AND vd.variable_key = scalar_results.variable_key
                  WHERE ar.project_id = ? AND vd.result_group = 'OPEN_CELL'
              )
            """,
            [threshold_double, threshold_double, project_id],
        )

    def add_audit(self, audit: QualityThresholdAuditRecord) -> None:
        if self._audit_writer is None:
            raise RuntimeError("quality threshold mutation requires an audit callback")
        self._audit_writer(audit, self._connection)

    def updated_threshold(self, project_id: str, criterion_key: str) -> QualityThreshold:
        return cast(
            QualityThreshold,
            rows(
                self._connection.execute(
                    "SELECT * FROM quality_thresholds WHERE project_id=? AND criterion_key=?",
                    [project_id, criterion_key],
                )
            )[0],
        )


class SQLQualityThresholdRepositoryProvider:
    def __init__(
        self,
        connection_provider: Callable[[], Any] = connect,
        *,
        authorize: Callable[[str, ConnectionLike], None] | None = None,
        audit_writer: Callable[[QualityThresholdAuditRecord, ConnectionLike], None] | None = None,
    ) -> None:
        self._connection_provider = connection_provider
        self._authorize = authorize
        self._audit_writer = audit_writer

    @contextmanager
    def __call__(self) -> Iterator[QualityThresholdRepository]:
        with self._connection_provider() as connection:
            yield SQLQualityThresholdRepository(
                connection,
                authorize=self._authorize,
                audit_writer=self._audit_writer,
            )
