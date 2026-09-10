from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.database import initialize_database
from app.database_connection import connect
from app.repositories.voc_repository import VOCRepository


@pytest.mark.duckdb_integration
def test_duckdb_voc_posts_schema_is_idempotent_and_preserves_author_snapshots() -> None:
    initialize_database()
    initialize_database()
    created_at = datetime(2026, 9, 10, 12, 0, 0)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO voc_posts
                (id, author_user_id, author_username, author_display_name, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ["voc-test-1", "retired-user", "retired.username", "Retired User", "A useful request", created_at],
        )
        assert conn.execute(
            "SELECT author_user_id, author_username, author_display_name, content FROM voc_posts WHERE id=?",
            ["voc-test-1"],
        ).fetchone() == ("retired-user", "retired.username", "Retired User", "A useful request")
        with pytest.raises(Exception):
            conn.execute(
                """INSERT INTO voc_posts
                    (id, author_user_id, author_username, author_display_name, content, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                ["voc-test-empty", "user", "user", "User", "", created_at],
            )
        indexes = conn.execute(
            "SELECT count(*) FROM duckdb_indexes() WHERE index_name='ix_voc_posts_created_at_id'"
        ).fetchone()
    assert indexes == (1,)


@pytest.mark.duckdb_integration
def test_duckdb_voc_posts_bind_aware_utc_as_naive_utc_in_a_non_utc_session() -> None:
    initialize_database()
    created_at = datetime(2026, 9, 10, 18, 49, 0, tzinfo=timezone.utc)
    with connect() as conn:
        conn.execute("SET TimeZone='Asia/Seoul'")
        try:
            VOCRepository(conn).create(
                post_id="voc-timezone-test",
                author_user_id="user",
                author_username="user",
                author_display_name="User",
                content="Timezone-safe post",
                created_at=created_at,
            )
            stored_at = conn.execute(
                "SELECT created_at FROM voc_posts WHERE id=?", ["voc-timezone-test"]
            ).fetchone()[0]
        finally:
            conn.execute("SET TimeZone='UTC'")

    assert stored_at == created_at.replace(tzinfo=None)
