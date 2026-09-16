from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
import json
from typing import Any

from ...database_connection import ConnectionLike, connect, rows
from ...domains.request_load_cases.ports import RequestLoadCaseWriteRepository, RequestLoadCasesRepository
from ...domains.selection_metadata import attach_selection_metadata


RequestLoadCasesAuthorizationCallback = Callable[[str, ConnectionLike], bool]


class SQLRequestLoadCasesRepository:
    """SQL read adapter for the request-scoped load-case listing."""

    def __init__(
        self,
        connection: ConnectionLike,
        authorize: RequestLoadCasesAuthorizationCallback = lambda _request_id, _connection: True,
    ) -> None:
        self._connection = connection
        self._authorize = authorize

    def authorize_request(self, request_id: str) -> bool:
        return bool(self._authorize(request_id, self._connection))

    def list_load_cases(self, request_id: str) -> list[dict[str, Any]]:
        items = rows(
            self._connection.execute(
                """
                SELECT load_cases.*, requests.title AS selection_request_name,
                       requests.project_id AS selection_project_id,
                       projects.name AS selection_project_name,
                       registry.code AS selection_code,
                       registry.relative_path AS selection_relative_path
                FROM load_cases
                JOIN analysis_requests requests ON requests.id=load_cases.request_id
                JOIN projects ON projects.id=requests.project_id
                LEFT JOIN folder_discovery_registry registry
                  ON registry.target_id=load_cases.id AND registry.role_kind='LOAD_CASE'
                WHERE load_cases.request_id = ?
                ORDER BY load_cases.created_at
                """,
                [request_id],
            )
        )
        return [
            attach_selection_metadata(
                item,
                name_key="name",
                project={
                    "id": str(item.pop("selection_project_id", "")),
                    "name": str(item.pop("selection_project_name", "")),
                },
                request={"id": request_id, "name": str(item.pop("selection_request_name", ""))},
                analysis_type=str(item["analysis_type"]),
            )
            for item in items
        ]


class SQLRequestLoadCasesRepositoryProvider:
    def __init__(
        self,
        authorize: RequestLoadCasesAuthorizationCallback = lambda _request_id, _connection: True,
        connection_provider: Callable[[], Any] = connect,
    ) -> None:
        self._authorize = authorize
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[RequestLoadCasesRepository]:
        with self._connection_provider() as connection:
            yield SQLRequestLoadCasesRepository(connection, self._authorize)


class SQLRequestLoadCaseWriteRepository:
    """SQL write adapter for request-scoped load-case creation."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def authorize_resource(self, callback: Callable[[object], None]) -> None:
        callback(self._connection)

    def request_exists(self, request_id: str) -> bool:
        return self._connection.execute(
            "SELECT id FROM analysis_requests WHERE id = ?",
            [request_id],
        ).fetchone() is not None

    def insert_load_case(
        self,
        load_case_id: str,
        request_id: str,
        name: str,
        analysis_type: str,
        parameters: dict[str, Any],
        created_at: datetime,
    ) -> None:
        self._connection.execute(
            "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                load_case_id,
                request_id,
                name,
                analysis_type,
                "READY",
                json.dumps(parameters, ensure_ascii=False),
                created_at,
            ],
        )


class SQLRequestLoadCaseWriteRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[RequestLoadCaseWriteRepository]:
        with self._connection_provider() as connection:
            yield SQLRequestLoadCaseWriteRepository(connection)
