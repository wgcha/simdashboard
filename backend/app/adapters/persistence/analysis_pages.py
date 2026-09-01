from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from typing import Any

from ...database import json_value, rows
from ...database_connection import ConnectionLike, connect
from ...domains.analysis_pages.policies import analysis_page_meta, sort_analysis_pages, summarize_analysis_page
from ...domains.analysis_pages.ports import AnalysisPageCommandRepository, AnalysisPageRepository


class SQLAnalysisPageRepository:
    """Analysis-page read adapter bound to one already-open connection."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def load_case_context(self, load_case_id: str) -> tuple[str, str] | None:
        context = self._connection.execute(
            """
            SELECT ar.project_id, lc.request_id
            FROM load_cases lc
            JOIN analysis_requests ar ON ar.id = lc.request_id
            WHERE lc.id = ?
            """,
            [load_case_id],
        ).fetchone()
        return tuple(context) if context else None

    def authorize(self, callback: Callable[[object], None]) -> None:
        callback(self._connection)

    def list_analysis_pages(
        self,
        load_case_id: str,
        *,
        include_private: bool,
        include_archived: bool,
    ) -> list[dict[str, Any]]:
        stored = rows(
            self._connection.execute(
                """
                SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at
                FROM dashboards
                WHERE load_case_id = ? OR id IN ('dashboard-drop-default', 'dashboard-chassis-default', 'dashboard-run-comparison-default')
                """,
                [load_case_id],
            )
        )
        result: list[dict[str, Any]] = []
        for item in stored:
            definition = json_value(item.pop("definition_json")) or {}
            summary = summarize_analysis_page(
                item,
                definition,
                load_case_id=load_case_id,
                include_private=include_private,
                include_archived=include_archived,
            )
            if summary is not None:
                result.append(summary)
        return sort_analysis_pages(result)


class SQLAnalysisPageCommandRepository(SQLAnalysisPageRepository):
    """Command adapter that keeps all analysis-page work on one connection."""

    def authorize_resource(self, callback: Callable[[object], None]) -> None:
        callback(self._connection)

    def page_name_exists(self, load_case_id: str, name: str, exclude_id: str | None = None) -> bool:
        candidates = rows(
            self._connection.execute(
                """
                SELECT id, load_case_id, definition_json
                FROM dashboards
                WHERE load_case_id = ? OR id IN ('dashboard-drop-default', 'dashboard-chassis-default', 'dashboard-run-comparison-default')
                """,
                [load_case_id],
            )
        )
        normalized_name = name.strip().casefold()
        for item in candidates:
            if item["id"] == exclude_id:
                continue
            definition = json_value(item["definition_json"]) or {}
            page = analysis_page_meta(definition)
            if not page or page.get("status") == "archived":
                continue
            if str(definition.get("name", "")).strip().casefold() == normalized_name:
                return True
        return False

    def next_custom_display_order(self, load_case_id: str) -> int:
        existing = rows(
            self._connection.execute(
                "SELECT definition_json FROM dashboards WHERE load_case_id = ?",
                [load_case_id],
            )
        )
        custom_orders: list[int] = []
        for item in existing:
            page = analysis_page_meta(json_value(item["definition_json"]) or {})
            if page and page.get("analysis_key") == "custom" and page.get("is_system") is False:
                custom_orders.append(int(page.get("display_order", 99)))
        return max([99, *custom_orders]) + 1

    def insert_analysis_page(
        self,
        dashboard_id: str,
        project_id: str,
        request_id: str,
        load_case_id: str,
        definition: dict[str, Any],
        occurred_at: datetime,
    ) -> None:
        encoded = _encoded(definition)
        self._connection.execute(
            "INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                dashboard_id,
                project_id,
                request_id,
                load_case_id,
                definition["name"],
                definition.get("description", ""),
                1,
                encoded,
                occurred_at,
            ],
        )
        self._connection.execute(
            "INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)",
            [dashboard_id, 1, encoded, "관리자", occurred_at, True],
        )

    def get_analysis_page(self, dashboard_id: str) -> dict[str, Any] | None:
        stored_rows = rows(
            self._connection.execute(
                "SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at FROM dashboards WHERE id = ?",
                [dashboard_id],
            )
        )
        if not stored_rows:
            return None
        item = stored_rows[0]
        item["definition"] = json_value(item.pop("definition_json")) or {}
        return item

    def get_analysis_page_for_delete(self, dashboard_id: str) -> tuple[str | None, dict[str, Any]] | None:
        stored = self._connection.execute(
            "SELECT load_case_id, definition_json FROM dashboards WHERE id = ?",
            [dashboard_id],
        ).fetchone()
        if not stored:
            return None
        return stored[0], json_value(stored[1]) or {}

    def write_dashboard_definition(
        self,
        dashboard_id: str,
        definition: dict[str, Any],
        created_by: str,
    ) -> tuple[int, datetime]:
        next_version = int(
            self._connection.execute(
                "SELECT COALESCE(max(version), 0) + 1 FROM dashboard_versions WHERE dashboard_id = ?",
                [dashboard_id],
            ).fetchone()[0]
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        encoded = _encoded(definition)
        self._connection.execute(
            """
            UPDATE dashboards
            SET name = ?, description = ?, version = ?, definition_json = ?, updated_at = ?
            WHERE id = ?
            """,
            [definition["name"], definition.get("description", ""), next_version, encoded, now, dashboard_id],
        )
        self._connection.execute(
            "INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)",
            [dashboard_id, next_version, encoded, created_by, now, True],
        )
        return next_version, now

    def reorderable_analysis_pages(self, load_case_id: str) -> list[dict[str, Any]]:
        stored = rows(
            self._connection.execute(
                "SELECT id, version, definition_json FROM dashboards WHERE load_case_id = ?",
                [load_case_id],
            )
        )
        result: list[dict[str, Any]] = []
        for item in stored:
            definition = json_value(item.pop("definition_json")) or {}
            page = analysis_page_meta(definition)
            if page and page.get("analysis_key") == "custom" and page.get("is_system") is False and page.get("status") != "archived":
                result.append({**item, "definition": definition})
        return result

    def begin_transaction(self) -> None:
        self._connection.execute("BEGIN TRANSACTION")

    def commit_transaction(self) -> None:
        self._connection.execute("COMMIT")

    def rollback_transaction(self) -> None:
        self._connection.execute("ROLLBACK")

    def delete_analysis_page_records(self, dashboard_id: str) -> None:
        self._connection.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [dashboard_id])
        self._connection.execute("DELETE FROM dashboards WHERE id = ?", [dashboard_id])


class SQLAnalysisPageRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[AnalysisPageRepository]:
        with self._connection_provider() as connection:
            yield SQLAnalysisPageRepository(connection)


class SQLAnalysisPageCommandRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[AnalysisPageCommandRepository]:
        with self._connection_provider() as connection:
            yield SQLAnalysisPageCommandRepository(connection)


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
