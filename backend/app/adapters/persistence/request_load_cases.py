from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ...database_connection import ConnectionLike, connect, rows
from ...domains.request_load_cases.ports import RequestLoadCasesRepository


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
