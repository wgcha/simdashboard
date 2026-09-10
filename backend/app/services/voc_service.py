from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from ..database_connection import ConnectionLike
from ..repositories.voc_repository import VOC_POST_COLUMNS, VOCRepository


VOCExportFormat = Literal["csv", "json"]
VOC_EXPORT_FIELDS = tuple(VOC_POST_COLUMNS.split(", "))


def create_post(
    repository: VOCRepository,
    *,
    author_user_id: str,
    author_username: str,
    author_display_name: str,
    content: str,
) -> dict[str, Any]:
    return repository.create(
        post_id=str(uuid4()),
        author_user_id=author_user_id,
        author_username=author_username,
        author_display_name=author_display_name,
        content=content,
        created_at=datetime.now(timezone.utc),
    )


def serialize_post(post: dict[str, Any]) -> dict[str, Any]:
    serialized = {field: post[field] for field in VOC_EXPORT_FIELDS}
    created_at = serialized["created_at"]
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)
        serialized["created_at"] = created_at.isoformat().replace("+00:00", "Z")
    return serialized


def _excel_safe(value: Any) -> Any:
    if isinstance(value, str) and value.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value


def export_posts(export_format: VOCExportFormat, connection: ConnectionLike) -> Iterator[bytes]:
    """Serialize an already-authorized cursor in bounded batches."""

    posts = VOCRepository(connection).iter_all()
    if export_format == "json":
        yield b"["
        first = True
        for post in posts:
            if not first:
                yield b","
            first = False
            yield json.dumps(serialize_post(post), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        yield b"]"
        return

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=VOC_EXPORT_FIELDS, lineterminator="\r\n")
    writer.writeheader()
    yield "\ufeff".encode("utf-8") + buffer.getvalue().encode("utf-8")
    for post in posts:
        buffer.seek(0)
        buffer.truncate(0)
        writer.writerow({field: _excel_safe(serialize_post(post)[field]) for field in VOC_EXPORT_FIELDS})
        yield buffer.getvalue().encode("utf-8")
