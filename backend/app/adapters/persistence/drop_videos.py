from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Callable

from ...database_connection import ConnectionLike, connect
from ...domains.drop_videos.ports import DropVideoRepository
from ...repositories.media_repository import list_drop_videos as list_media_drop_videos


class SQLDropVideoRepository:
    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def load_case_context(self, load_case_id: str) -> tuple[Any, ...] | None:
        row = self._connection.execute(
            """SELECT lc.id, lc.name, lc.analysis_type, ar.id, ar.title
               FROM load_cases lc JOIN analysis_requests ar ON ar.id = lc.request_id WHERE lc.id = ?""",
            [load_case_id],
        ).fetchone()
        return tuple(row) if row else None

    def authorize(self, callback: Callable[[object], None]) -> None:
        callback(self._connection)

    def list_drop_videos(self, load_case_id: str) -> list[dict[str, Any]]:
        return list_media_drop_videos(self._connection, load_case_id)


class SQLDropVideoRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[SQLDropVideoRepository]:
        with self._connection_provider() as connection:
            yield SQLDropVideoRepository(connection)
