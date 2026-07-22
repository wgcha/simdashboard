from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..database import json_value, rows


NUMBER_WIDGETS = {"kpi", "gauge", "edge_bar", "scatter", "result_table", "chassis_bar", "chassis_table"}
SERIES_WIDGETS = {"time_series", "scatter", "result_table"}
NUMBER_AGGREGATIONS = {"MAX", "MIN", "AVG", "LATEST"}
SERIES_AGGREGATIONS = {"RAW", "MAX_BY_TIME"}


class VariableCatalogRepository:
    """DuckDB implementation of the variable catalog persistence contract."""

    def __init__(self, conn: Any):
        self.conn = conn

    def list_for_load_case(self, load_case_id: str) -> list[dict[str, Any]]:
        items = rows(
            self.conn.execute(
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

    def get(self, load_case_id: str, variable_key: str, include_inactive: bool = False) -> dict[str, Any] | None:
        condition = "" if include_inactive else " AND is_active = true"
        result = rows(
            self.conn.execute(
                f"SELECT * FROM variable_definitions WHERE load_case_id = ? AND variable_key = ?{condition}",
                [load_case_id, variable_key],
            )
        )
        return result[0] if result else None

    def create(self, load_case_id: str, definition: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        existing = self.get(load_case_id, definition["variable_key"], include_inactive=True)
        values = self._normalized(definition)
        if existing:
            if existing["is_active"]:
                raise ValueError("VARIABLE_EXISTS")
            self.conn.execute(
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
            analysis_type = self.conn.execute("SELECT analysis_type FROM load_cases WHERE id = ?", [load_case_id]).fetchone()
            if not analysis_type:
                raise LookupError("LOAD_CASE_NOT_FOUND")
            self.conn.execute(
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

    def update(self, load_case_id: str, variable_key: str, definition: dict[str, Any]) -> dict[str, Any]:
        existing = self.get(load_case_id, variable_key)
        if not existing:
            raise LookupError("VARIABLE_NOT_FOUND")
        merged = {
            "variable_key": variable_key,
            "data_type": existing["data_type"],
            **definition,
        }
        values = self._normalized(merged)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.conn.execute(
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

    def deactivate(self, load_case_id: str, variable_key: str, updated_by: str) -> None:
        if not self.get(load_case_id, variable_key):
            raise LookupError("VARIABLE_NOT_FOUND")
        self.conn.execute(
            """
            UPDATE variable_definitions
            SET is_active=false, updated_at=?, updated_by=?
            WHERE load_case_id=? AND variable_key=?
            """,
            [datetime.now(timezone.utc).replace(tzinfo=None), updated_by, load_case_id, variable_key],
        )

    def dashboard_references(self, load_case_id: str, variable_key: str) -> list[str]:
        references: list[str] = []
        for dashboard_id, definition_json in self.conn.execute(
            "SELECT id, definition_json FROM dashboards WHERE load_case_id = ?", [load_case_id]
        ).fetchall():
            definition = json_value(definition_json) or {}
            if any((widget.get("settings") or {}).get("variableId") == variable_key for widget in definition.get("widgets", [])):
                references.append(dashboard_id)
        return references

    def _has_data(self, load_case_id: str, variable_key: str, data_type: str) -> bool:
        table = "scalar_results" if data_type == "NUMBER" else "time_series_results"
        result = self.conn.execute(
            f"""
            SELECT count(*) FROM {table} result
            JOIN analysis_runs run ON run.id = result.analysis_run_id
            WHERE run.load_case_id = ? AND result.variable_key = ?
            """,
            [load_case_id, variable_key],
        ).fetchone()
        return bool(result and result[0])

    @staticmethod
    def _normalized(definition: dict[str, Any]) -> list[Any]:
        data_type = definition["data_type"]
        allowed_widgets = definition.get("allowed_widgets") or sorted(NUMBER_WIDGETS if data_type == "NUMBER" else SERIES_WIDGETS)
        allowed_aggregations = definition.get("allowed_aggregations") or sorted(NUMBER_AGGREGATIONS if data_type == "NUMBER" else SERIES_AGGREGATIONS)
        valid_widgets = NUMBER_WIDGETS if data_type == "NUMBER" else SERIES_WIDGETS
        valid_aggregations = NUMBER_AGGREGATIONS if data_type == "NUMBER" else SERIES_AGGREGATIONS
        if not set(allowed_widgets) <= valid_widgets or not set(allowed_aggregations) <= valid_aggregations:
            raise ValueError("INVALID_CATALOG_OPTIONS")
        if data_type == "NUMBER" and definition.get("threshold") is None:
            raise ValueError("NUMBER_THRESHOLD_REQUIRED")
        source = "scalar_results" if data_type == "NUMBER" else "time_series_results"
        return [
            definition["display_name"].strip(), data_type, definition["unit"].strip(),
            definition.get("description", "").strip(), bool(definition.get("filterable", True)), source,
            definition.get("threshold"), json.dumps(allowed_widgets), json.dumps(allowed_aggregations),
            definition.get("result_group", "CUSTOM"),
        ]
