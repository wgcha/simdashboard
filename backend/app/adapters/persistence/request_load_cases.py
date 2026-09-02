from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
import json
from typing import Any

from ...database_connection import ConnectionLike, connect, rows
from ...domains.request_load_cases.ports import RequestLoadCaseWriteRepository, RequestLoadCasesRepository


class SQLRequestLoadCasesRepository:
    """SQL read adapter for the request-scoped load-case listing."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def list_load_cases(self, request_id: str) -> list[dict[str, Any]]:
        return rows(
            self._connection.execute(
                "SELECT * FROM load_cases WHERE request_id = ? ORDER BY created_at",
                [request_id],
            )
        )


class SQLRequestLoadCasesRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[RequestLoadCasesRepository]:
        with self._connection_provider() as connection:
            yield SQLRequestLoadCasesRepository(connection)


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
