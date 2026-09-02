"""Persistence adapter for framework-neutral result-media read queries."""

from __future__ import annotations

from ...database_connection import ConnectionLike
from ...domains.results.models import ResultMediaAssetRead, ResultMediaBlobRead
from ...repositories.media_repository import get_blob, get_media_asset


class SQLResultMediaQuery:
    """Map the established media repository facade to the result-media port."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection

    def get_result_media_asset(self, asset_id: str) -> ResultMediaAssetRead | None:
        stored = get_media_asset(self._connection, asset_id)
        if stored is None:
            return None
        return ResultMediaAssetRead(
            id=str(stored["id"]),
            file_path=str(stored["file_path"]) if stored.get("file_path") is not None else None,
            mime_type=str(stored["mime_type"]) if stored.get("mime_type") is not None else None,
            blob_id=str(stored["blob_id"]) if stored.get("blob_id") else None,
            original_filename=(
                str(stored["original_filename"])
                if stored.get("original_filename") is not None
                else None
            ),
        )

    def get_result_media_blob(self, blob_id: str) -> ResultMediaBlobRead | None:
        stored = get_blob(self._connection, blob_id)
        if stored is None:
            return None
        return ResultMediaBlobRead(
            id=stored.id,
            sha256=stored.sha256,
            file_size=stored.file_size,
            chunk_size=stored.chunk_size,
            chunk_count=stored.chunk_count,
        )
