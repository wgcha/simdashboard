from __future__ import annotations

import duckdb


def ensure_voc_posts_schema(conn: duckdb.DuckDBPyConnection) -> None:
    """Install the embedded equivalent of the append-only VOC post store."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS voc_posts (
            id VARCHAR PRIMARY KEY,
            author_user_id VARCHAR NOT NULL,
            author_username VARCHAR NOT NULL,
            author_display_name VARCHAR NOT NULL,
            content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 10000),
            created_at TIMESTAMP NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_voc_posts_created_at_id ON voc_posts(created_at, id)")
