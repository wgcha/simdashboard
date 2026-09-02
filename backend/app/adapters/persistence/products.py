from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

from ...database_connection import ConnectionLike, connect, rows
from ...domains.products.models import ProductInformation
from ...domains.products.ports import ProductInformationRepository


class SQLProductInformationRepository:
    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def list_for_load_case(self, load_case_id: str) -> list[ProductInformation]:
        items = rows(
            self._connection.execute(
                """
                SELECT pi.category, pi.name, pi.value_text, pi.file_path, pi.metadata_json
                FROM load_cases lc
                JOIN analysis_requests ar ON ar.id = lc.request_id
                JOIN product_information pi ON pi.project_id = ar.project_id
                WHERE lc.id = ?
                ORDER BY pi.category, pi.name
                """,
                [load_case_id],
            )
        )
        for item in items:
            metadata = item.pop("metadata_json")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except json.JSONDecodeError:
                    pass
            item["metadata"] = metadata
        return cast(list[ProductInformation], items)


class SQLProductInformationRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[ProductInformationRepository]:
        with self._connection_provider() as connection:
            yield SQLProductInformationRepository(connection)
