from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from typing import Any

from ...database import json_value
from ...database_connection import ConnectionLike, connect
from ...domains.dashboard_writes.ports import DashboardWriteRepository


class SQLDashboardWriteRepository:
    """Persistence adapter for dashboard mutations on one open connection."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def authorize_resource(self, callback: Callable[[object], None]) -> None:
        callback(self._connection)

    def get_dashboard(self, dashboard_id: str) -> tuple[Any, Any] | None:
        stored = self._connection.execute(
            "SELECT version, definition_json FROM dashboards WHERE id = ?",
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
        encoded = json.dumps(definition, ensure_ascii=False)
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

    def get_dashboard_version_for_delete(self, dashboard_id: str, version: int) -> tuple[Any] | None:
        return self._connection.execute(
            "SELECT is_valid FROM dashboard_versions WHERE dashboard_id = ? AND version = ?",
            [dashboard_id, version],
        ).fetchone()

    def count_valid_history(self, dashboard_id: str, current_version: int) -> int:
        return int(
            self._connection.execute(
                "SELECT count(*) FROM dashboard_versions WHERE dashboard_id = ? AND is_valid = true AND version <> ?",
                [dashboard_id, current_version],
            ).fetchone()[0]
        )

    def invalidate_dashboard_version(self, dashboard_id: str, version: int) -> None:
        self._connection.execute(
            "UPDATE dashboard_versions SET is_valid = false WHERE dashboard_id = ? AND version = ? AND is_valid = true",
            [dashboard_id, version],
        )

    def get_clone_source(self, dashboard_id: str) -> tuple[Any, ...] | None:
        source = self._connection.execute(
            "SELECT project_id, request_id, load_case_id, definition_json FROM dashboards WHERE id = ?",
            [dashboard_id],
        ).fetchone()
        if not source:
            return None
        return (source[0], source[1], source[2], json_value(source[3]))

    def insert_cloned_dashboard(
        self,
        dashboard_id: str,
        project_id: str,
        request_id: str | None,
        load_case_id: str | None,
        definition: dict[str, Any],
        principal_user_id: str,
        occurred_at: datetime,
    ) -> None:
        encoded = json.dumps(definition, ensure_ascii=False)
        self._connection.execute(
            "INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                dashboard_id,
                project_id,
                request_id,
                load_case_id,
                definition["name"],
                definition["description"],
                1,
                encoded,
                occurred_at,
            ],
        )
        self._connection.execute(
            "INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)",
            [dashboard_id, 1, encoded, principal_user_id, occurred_at, True],
        )

    def get_valid_dashboard_version(self, dashboard_id: str, version: int) -> tuple[Any] | None:
        stored = self._connection.execute(
            "SELECT definition_json FROM dashboard_versions WHERE dashboard_id = ? AND version = ? AND is_valid = true",
            [dashboard_id, version],
        ).fetchone()
        if not stored:
            return None
        return (json_value(stored[0]),)


class SQLDashboardWriteRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[DashboardWriteRepository]:
        with self._connection_provider() as connection:
            yield SQLDashboardWriteRepository(connection)
