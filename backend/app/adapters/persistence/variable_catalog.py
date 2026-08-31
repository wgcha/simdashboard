from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ...database import json_value, rows
from ...database_connection import ConnectionLike, connect
from ...domains.variable_catalog.errors import (
    LoadCaseNotFoundError,
    VariableAlreadyExistsError,
    VariableNotFoundError,
)
from ...domains.variable_catalog.models import VariableCatalogDefinition, VariableCatalogItem
from ...domains.variable_catalog.policies import (
    CURVE_TYPES,
    MEDIA_TYPES,
    SCALAR_TYPES,
    normalize_definition,
)


class SQLVariableCatalogRepository:
    """SQL persistence adapter preserving the variable-catalog query contract."""

    def __init__(
        self,
        connection: ConnectionLike,
        authorize: Callable[[str, ConnectionLike], None] | None = None,
    ) -> None:
        self._connection = connection
        self.conn = connection  # Legacy repository callers accessed this attribute directly.
        self._authorize = authorize

    def load_case_exists(self, load_case_id: str) -> bool:
        return bool(self._connection.execute("SELECT 1 FROM load_cases WHERE id = ?", [load_case_id]).fetchone())

    def authorize_mutation(self, load_case_id: str) -> None:
        if self._authorize is not None:
            self._authorize(load_case_id, self._connection)

    def list_for_load_case(self, load_case_id: str) -> list[VariableCatalogItem]:
        items = rows(
            self._connection.execute(
                """
                SELECT * FROM variable_definitions
                WHERE load_case_id = ? AND is_active = true
                ORDER BY result_group, display_name, variable_key
                """,
                [load_case_id],
            )
        )
        for item in items:
            item["definition_id"] = item["id"]
            item["id"] = item["variable_key"]
            item["threshold"] = item.pop("threshold_double")
            item["allowed_widgets"] = json_value(item.pop("allowed_widgets_json")) or []
            item["allowed_aggregations"] = json_value(item.pop("allowed_aggregations_json")) or []
            item["has_data"] = self._has_data(load_case_id, item["variable_key"], item["data_type"])
            item["dashboard_usage_count"] = len(self.dashboard_references(load_case_id, item["variable_key"]))
        return items

    def get(
        self,
        load_case_id: str,
        variable_key: str,
        include_inactive: bool = False,
    ) -> dict[str, Any] | None:
        condition = "" if include_inactive else " AND is_active = true"
        result = rows(
            self._connection.execute(
                f"SELECT * FROM variable_definitions WHERE load_case_id = ? AND variable_key = ?{condition}",
                [load_case_id, variable_key],
            )
        )
        return result[0] if result else None

    def create(self, load_case_id: str, definition: VariableCatalogDefinition) -> VariableCatalogItem:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        existing = self.get(load_case_id, definition["variable_key"], include_inactive=True)
        values = normalize_definition(definition)
        if existing:
            if existing["is_active"]:
                raise VariableAlreadyExistsError()
            self._connection.execute(
                """
                UPDATE variable_definitions
                SET display_name=?, data_type=?, unit=?, description=?, filterable=?, source=?,
                    threshold_double=?, allowed_widgets_json=?, allowed_aggregations_json=?,
                    result_group=?, is_active=true, updated_at=?, updated_by=?
                WHERE load_case_id=? AND variable_key=?
                """,
                [*values, now, definition["updated_by"], load_case_id, definition["variable_key"]],
            )
        else:
            analysis_type = self._connection.execute("SELECT analysis_type FROM load_cases WHERE id = ?", [load_case_id]).fetchone()
            if not analysis_type:
                raise LoadCaseNotFoundError()
            self._connection.execute(
                """
                INSERT INTO variable_definitions VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, true, ?, ?, ?)
                """,
                [
                    f"variable-{uuid4().hex[:16]}", load_case_id, definition["variable_key"],
                    *values[:9], analysis_type[0], values[9], now, now, definition["updated_by"],
                ],
            )
        return next(item for item in self.list_for_load_case(load_case_id) if item["variable_key"] == definition["variable_key"])

    def update(
        self,
        load_case_id: str,
        variable_key: str,
        definition: VariableCatalogDefinition,
    ) -> VariableCatalogItem:
        existing = self.get(load_case_id, variable_key)
        if not existing:
            raise VariableNotFoundError()
        merged: VariableCatalogDefinition = {
            "variable_key": variable_key,
            "data_type": existing["data_type"],
            **definition,
        }
        values = normalize_definition(merged)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self._connection.execute(
            """
            UPDATE variable_definitions
            SET display_name=?, data_type=?, unit=?, description=?, filterable=?, source=?,
                threshold_double=?, allowed_widgets_json=?, allowed_aggregations_json=?,
                result_group=?, updated_at=?, updated_by=?
            WHERE load_case_id=? AND variable_key=? AND is_active=true
            """,
            [*values, now, definition["updated_by"], load_case_id, variable_key],
        )
        return next(item for item in self.list_for_load_case(load_case_id) if item["variable_key"] == variable_key)

    def deactivate(self, load_case_id: str, variable_key: str, updated_by: str | None) -> None:
        if not self.get(load_case_id, variable_key):
            raise VariableNotFoundError()
        self._connection.execute(
            """
            UPDATE variable_definitions
            SET is_active=false, updated_at=?, updated_by=?
            WHERE load_case_id=? AND variable_key=?
            """,
            [datetime.now(timezone.utc).replace(tzinfo=None), updated_by, load_case_id, variable_key],
        )

    def dashboard_references(self, load_case_id: str, variable_key: str) -> list[str]:
        references: list[str] = []
        for dashboard_id, definition_json in self._connection.execute(
            "SELECT id, definition_json FROM dashboards WHERE load_case_id = ?", [load_case_id]
        ).fetchall():
            definition = json_value(definition_json) or {}
            if any((widget.get("settings") or {}).get("variableId") == variable_key for widget in definition.get("widgets", [])):
                references.append(dashboard_id)
        return references

    def _has_data(self, load_case_id: str, variable_key: str, data_type: str) -> bool:
        if data_type in SCALAR_TYPES:
            table = "scalar_results"
        elif data_type in CURVE_TYPES:
            table = "curve_results" if data_type == "CURVE" else "time_series_results"
        elif data_type in MEDIA_TYPES:
            result = self._connection.execute(
                """
                SELECT count(*) FROM media_assets result
                JOIN analysis_runs run ON run.id = result.analysis_run_id
                WHERE run.load_case_id = ?
                  AND json_extract_string(result.metadata_json, '$.variable_key') = ?
                """,
                [load_case_id, variable_key],
            ).fetchone()
            return bool(result and result[0])
        else:
            return False
        result = self._connection.execute(
            f"""
            SELECT count(*) FROM {table} result
            JOIN analysis_runs run ON run.id = result.analysis_run_id
            WHERE run.load_case_id = ? AND result.variable_key = ?
            """,
            [load_case_id, variable_key],
        ).fetchone()
        return bool(result and result[0])


class SQLVariableCatalogRepositoryProvider:
    def __init__(
        self,
        connection_provider: Callable[[], Any] = connect,
        *,
        authorize: Callable[[str, ConnectionLike], None] | None = None,
    ) -> None:
        self._connection_provider = connection_provider
        self._authorize = authorize

    @contextmanager
    def __call__(self) -> Iterator[SQLVariableCatalogRepository]:
        with self._connection_provider() as connection:
            yield SQLVariableCatalogRepository(connection, self._authorize)
