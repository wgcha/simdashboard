from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from ..database_connection import ConnectionLike, rows


VOC_POST_COLUMNS = "id, author_user_id, author_username, author_display_name, content, created_at"


class VOCRepository:
    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def create(
        self,
        *,
        post_id: str,
        author_user_id: str,
        author_username: str,
        author_display_name: str,
        content: str,
        created_at: datetime,
    ) -> dict[str, Any]:
        # DuckDB's runtime adapter uses naive UTC timestamps. Binding an aware
        # timestamp to TIMESTAMP converts it through the session timezone, and
        # TIMESTAMPTZ retrieval requires an optional timezone package absent
        # from the supported local runtime. PostgreSQL retains the aware value.
        if self._connection.backend == "duckdb" and created_at.tzinfo is not None:
            created_at = created_at.astimezone(timezone.utc).replace(tzinfo=None)
        row = rows(
            self._connection.execute(
                f"""
                INSERT INTO voc_posts
                    ({VOC_POST_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?)
                RETURNING {VOC_POST_COLUMNS}
                """,
                [post_id, author_user_id, author_username, author_display_name, content, created_at],
            )
        )[0]
        return row

    def current_author(self, user_id: str) -> dict[str, Any] | None:
        authors = rows(
            self._connection.execute(
                """
                SELECT id, username, display_name
                FROM users
                WHERE id=? AND account_status='ACTIVE' AND is_active=true
                """,
                [user_id],
            )
        )
        return authors[0] if authors else None

    def list(self, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        items = rows(
            self._connection.execute(
                f"SELECT {VOC_POST_COLUMNS} FROM voc_posts ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                [limit, offset],
            )
        )
        total_row = self._connection.execute("SELECT COUNT(*) FROM voc_posts").fetchone()
        return items, int(total_row[0] if total_row else 0)

    def iter_all(self, *, batch_size: int = 250) -> Iterator[dict[str, Any]]:
        cursor = self._connection.execute(
            f"SELECT {VOC_POST_COLUMNS} FROM voc_posts ORDER BY created_at DESC, id DESC"
        )
        columns = list(cursor.keys()) if hasattr(cursor, "keys") else [column[0] for column in cursor.description]
        while batch := cursor.fetchmany(batch_size):
            yield from (dict(zip(columns, row)) for row in batch)
