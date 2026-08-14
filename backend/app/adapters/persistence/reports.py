from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, cast

from ...database_connection import ConnectionLike, connect, rows
from ...domains.reports.models import ReportLayout
from ...domains.reports.ports import ReportLayoutRepository


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


class SQLReportLayoutRepository:
    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def list_active_layouts(self) -> list[ReportLayout]:
        items = rows(
            self._connection.execute(
                "SELECT * FROM report_layouts WHERE is_active=true "
                "ORDER BY is_system DESC, updated_at DESC, name"
            )
        )
        layouts: list[ReportLayout] = []
        for item in items:
            definition = _json_value(item.pop("definition_json")) or {}
            definition.update(
                {
                    "id": item["id"],
                    "name": item["name"],
                    "description": item.get("description") or "",
                    "version": item["version"],
                }
            )
            layouts.append(cast(ReportLayout, {**item, "definition": definition}))
        return layouts


class SQLReportLayoutRepositoryProvider:
    def __init__(self, connection_provider: Callable[[], Any] = connect) -> None:
        self._connection_provider = connection_provider

    @contextmanager
    def __call__(self) -> Iterator[ReportLayoutRepository]:
        with self._connection_provider() as connection:
            yield SQLReportLayoutRepository(connection)
