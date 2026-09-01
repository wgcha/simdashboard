from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ...database_connection import ConnectionLike, connect, rows
from ...domains.dashboard_reads.errors import DashboardNotFoundError, DashboardVersionNotFoundError
from ...domains.dashboard_reads.policies import analysis_page_meta
from ...domains.dashboard_reads.ports import DashboardReadRepository
from ...database import json_value


class SQLDashboardReadRepository:
    def __init__(self, connection: ConnectionLike):
        self._connection = connection

    def get_dashboard(self, dashboard_id: str, authorize_project: Callable[[str, ConnectionLike], None]) -> dict[str, Any]:
        result = rows(self._connection.execute("SELECT * FROM dashboards WHERE id = ?", [dashboard_id]))
        if not result:
            raise DashboardNotFoundError
        item = result[0]
        definition = json_value(item.pop("definition_json"))
        page = analysis_page_meta(definition)
        if page and page.get("status") in {"draft", "archived"}:
            authorize_project(item["project_id"], self._connection)
        definition["version"] = item["version"]
        definition["updated_at"] = item["updated_at"]
        return definition

    def list_dashboards(
        self,
        project_id: str | None,
        has_project_permission: Callable[[str, ConnectionLike], bool],
    ) -> list[dict[str, Any]]:
        if project_id:
            stored = rows(
                self._connection.execute(
                    "SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at FROM dashboards WHERE project_id = ? ORDER BY updated_at DESC",
                    [project_id],
                )
            )
        else:
            stored = rows(
                self._connection.execute(
                    "SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at FROM dashboards ORDER BY updated_at DESC"
                )
            )
        result = []
        for item in stored:
            definition = json_value(item.pop("definition_json")) or {}
            page = analysis_page_meta(definition)
            if (
                page
                and page.get("status") in {"draft", "archived"}
                and not has_project_permission(item["project_id"], self._connection)
            ):
                continue
            result.append(item)
        return result

    def get_dashboard_versions(
        self,
        dashboard_id: str,
        include_invalid: bool,
        authorize_resource: Callable[[str, ConnectionLike], None],
    ) -> list[dict[str, Any]]:
        current = self._connection.execute("SELECT definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not current:
            raise DashboardNotFoundError
        page = analysis_page_meta(json_value(current[0]) or {})
        if page and page.get("status") in {"draft", "archived"}:
            authorize_resource(dashboard_id, self._connection)
        valid_filter = "" if include_invalid else " AND is_valid = true"
        return rows(
            self._connection.execute(
                f"SELECT dashboard_id, version, created_by, created_at, is_valid FROM dashboard_versions WHERE dashboard_id = ?{valid_filter} ORDER BY version DESC",
                [dashboard_id],
            )
        )

    def get_dashboard_version(
        self,
        dashboard_id: str,
        version: int,
        include_invalid: bool,
        authorize_resource: Callable[[str, ConnectionLike], None],
    ) -> dict[str, Any]:
        current = self._connection.execute("SELECT definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not current:
            raise DashboardNotFoundError
        page = analysis_page_meta(json_value(current[0]) or {})
        if page and page.get("status") in {"draft", "archived"}:
            authorize_resource(dashboard_id, self._connection)
        valid_filter = "" if include_invalid else " AND is_valid = true"
        stored = self._connection.execute(
            f"SELECT definition_json, created_by, created_at, is_valid FROM dashboard_versions WHERE dashboard_id = ? AND version = ?{valid_filter}",
            [dashboard_id, version],
        ).fetchone()
        if not stored:
            raise DashboardVersionNotFoundError
        return {
            "dashboard_id": dashboard_id,
            "version": version,
            "definition": json_value(stored[0]) or {},
            "created_by": stored[1],
            "created_at": stored[2],
            "is_valid": stored[3],
        }


class SQLDashboardReadRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[DashboardReadRepository]:
        with self._connection_provider() as connection:
            yield SQLDashboardReadRepository(connection)
