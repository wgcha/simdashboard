"""Persistence transactions for password-account HTTP commands."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from ..database_connection import ConnectionLike


@contextmanager
def registration_transaction(connection: ConnectionLike) -> Iterator[None]:
    """Keep account creation and its audit event in one database transaction."""

    connection.execute("BEGIN TRANSACTION")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
