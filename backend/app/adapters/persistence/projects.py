from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import json
from datetime import datetime
from typing import Any, cast

from ...database import ensure_project_quality_thresholds, ensure_workspace_layouts
from ...database_connection import ConnectionLike, connect, rows
from ...domains.projects.models import CreateProjectCommand, PersistedProjectAuditRecord, Project
from ...domains.projects.ports import ProjectRepository, ProjectUnitOfWork


class SQLProjectRepository:
    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def list_projects(self) -> list[Project]:
        return cast(
            list[Project],
            rows(self._connection.execute("SELECT * FROM projects ORDER BY created_at DESC")),
        )


class SQLProjectRepositoryProvider:
    """Adapt the configured database connection to the projects read port."""

    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[ProjectRepository]:
        with self._connection_provider() as connection:
            yield SQLProjectRepository(connection)


class SQLProjectUnitOfWork(ProjectUnitOfWork):
    def __init__(self, connection: ConnectionLike, id_factory: Callable[[str, int], str]) -> None:
        self._connection = connection
        self._id_factory = id_factory

    def add_project(self, project_id: str, command: CreateProjectCommand, created_at: datetime) -> None:
        self._connection.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
            [
                project_id,
                command["name"].strip(),
                command["product_name"].strip(),
                command["description"].strip(),
                created_at,
            ],
        )

    def add_product_information(self, project_id: str, command: CreateProjectCommand) -> None:
        display_size = command["display_size_inch"]
        product_rows = [
            (
                "MODEL",
                "제품 모델명",
                command["product_name"].strip(),
                {"source": "project_registration"},
            ),
            (
                "MANUFACTURER",
                "제조사",
                command["manufacturer"].strip(),
                {"source": "project_registration"},
            ),
            (
                "SPEC",
                "화면 크기",
                f"{display_size:g} inch" if display_size else "",
                {"diagonal_inch": display_size},
            ),
        ]
        for category, label, value, metadata in product_rows:
            if value:
                self._connection.execute(
                    "INSERT INTO product_information VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        self._id_factory("product", 12),
                        project_id,
                        category,
                        label,
                        value,
                        None,
                        json.dumps(metadata, ensure_ascii=False),
                    ],
                )

    def add_quality_thresholds(self, project_id: str, created_at: datetime) -> None:
        ensure_project_quality_thresholds(self._connection)

    def add_workspace_layouts(self, project_id: str) -> None:
        ensure_workspace_layouts(self._connection)

    def add_admin_membership(
        self,
        membership_id: str,
        project_id: str,
        command: CreateProjectCommand,
        created_at: datetime,
    ) -> None:
        creator_id = command["creator"]["user_id"]
        self._connection.execute(
            """
            INSERT OR IGNORE INTO project_memberships
                (id, project_id, user_id, role, created_by, created_at, updated_by, updated_at)
            VALUES (?, ?, ?, 'admin', ?, ?, ?, ?)
            """,
            [membership_id, project_id, creator_id, creator_id, created_at, creator_id, created_at],
        )

    def add_audit_event(self, record: PersistedProjectAuditRecord) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_events
                (id, occurred_at, user_id, username, role, action, method, path,
                 status_code, request_id, client_ip, user_agent, detail_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                record["id"],
                record["occurred_at"],
                record["user_id"],
                record["username"],
                record["role"],
                record["action"],
                record["method"],
                record["path"],
                record["status_code"],
                record["request_id"],
                record["client_ip"],
                record["user_agent"],
                json.dumps({"project_id": record["project_id"]}, ensure_ascii=False),
            ],
        )


class SQLProjectUnitOfWorkProvider:
    def __init__(
        self,
        id_factory: Callable[[str, int], str],
        connection_provider: Callable[[], Any] = connect,
    ) -> None:
        self._id_factory = id_factory
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[ProjectUnitOfWork]:
        with self._connection_provider() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                yield SQLProjectUnitOfWork(connection, self._id_factory)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
