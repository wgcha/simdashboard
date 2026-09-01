from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...database_connection import connect


class SQLSystemHealthProbe:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    def __call__(self) -> None:
        with self._connection_provider() as connection:
            connection.execute("SELECT 1").fetchone()
