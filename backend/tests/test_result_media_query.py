from __future__ import annotations

import pytest

from app.adapters.persistence import media as media_persistence
from app.adapters.persistence.media import SQLResultMediaQuery
from app.application.results.queries import get_result_media_read
from app.domains.results.models import ResultMediaAssetRead, ResultMediaBlobRead
from app.repositories.media_repository import BlobRecord


class _ResultMediaQuery:
    def __init__(
        self,
        asset: ResultMediaAssetRead | None,
        blob: ResultMediaBlobRead | None = ResultMediaBlobRead(
            "blob-1", "a" * 64, 3, 3, 1
        ),
    ) -> None:
        self.asset = asset
        self.blob = blob
        self.events: list[str] = []

    def get_result_media_asset(self, asset_id: str) -> ResultMediaAssetRead | None:
        assert asset_id == "asset-1"
        self.events.append("asset")
        return self.asset

    def get_result_media_blob(self, blob_id: str) -> ResultMediaBlobRead | None:
        assert blob_id == "blob-1"
        self.events.append("blob")
        return self.blob


def _asset(*, blob_id: str | None = "blob-1") -> ResultMediaAssetRead:
    return ResultMediaAssetRead(
        id="asset-1",
        file_path="assets/demo.mp4",
        mime_type="video/mp4",
        blob_id=blob_id,
        original_filename="demo.mp4",
    )


def test_result_media_query_preserves_metadata_permission_blob_order() -> None:
    query = _ResultMediaQuery(_asset())

    context = get_result_media_read(
        query,
        "asset-1",
        authorize=lambda asset: _authorize(query, asset),
    )

    assert context is not None
    assert context.blob is not None
    assert query.events == ["asset", "authorize", "blob"]


def test_result_media_query_does_not_authorize_or_lookup_blob_for_missing_asset() -> None:
    query = _ResultMediaQuery(None)

    assert (
        get_result_media_read(
            query,
            "asset-1",
            authorize=lambda _asset: query.events.append("authorize"),
        )
        is None
    )
    assert query.events == ["asset"]


def test_result_media_query_does_not_lookup_blob_when_authorization_rejects() -> None:
    query = _ResultMediaQuery(_asset())

    with pytest.raises(_AuthorizationRejected):
        get_result_media_read(
            query,
            "asset-1",
            authorize=lambda _asset: (_raise_authorization_rejected(query)),
        )

    assert query.events == ["asset", "authorize"]


def test_result_media_query_authorizes_legacy_asset_without_blob_lookup() -> None:
    query = _ResultMediaQuery(_asset(blob_id=None))

    context = get_result_media_read(
        query,
        "asset-1",
        authorize=lambda asset: _authorize(query, asset),
    )

    assert context is not None
    assert context.blob is None
    assert query.events == ["asset", "authorize"]


def test_result_media_query_preserves_missing_blob_for_http_404_mapping() -> None:
    query = _ResultMediaQuery(_asset(), blob=None)

    context = get_result_media_read(
        query,
        "asset-1",
        authorize=lambda asset: _authorize(query, asset),
    )

    assert context is not None
    assert context.asset.blob_id == "blob-1"
    assert context.blob is None
    assert query.events == ["asset", "authorize", "blob"]


def test_sql_result_media_query_keeps_asset_and_blob_reads_on_the_supplied_connection(
    monkeypatch,
) -> None:
    connection = object()
    calls: list[tuple[str, object]] = []

    def read_asset(actual_connection: object, asset_id: str) -> dict[str, object]:
        assert asset_id == "asset-1"
        calls.append(("asset", actual_connection))
        return {
            "id": "asset-1",
            "file_path": "assets/demo.mp4",
            "mime_type": "video/mp4",
            "blob_id": "blob-1",
            "original_filename": "demo.mp4",
        }

    def read_blob(actual_connection: object, blob_id: str) -> BlobRecord:
        assert blob_id == "blob-1"
        calls.append(("blob", actual_connection))
        return BlobRecord("blob-1", "a" * 64, 3, 3, 1)

    monkeypatch.setattr(media_persistence, "get_media_asset", read_asset)
    monkeypatch.setattr(media_persistence, "get_blob", read_blob)

    context = get_result_media_read(
        SQLResultMediaQuery(connection),  # type: ignore[arg-type]
        "asset-1",
        authorize=lambda _asset: calls.append(("authorize", connection)),
    )

    assert context is not None
    assert calls == [("asset", connection), ("authorize", connection), ("blob", connection)]


def _authorize(query: _ResultMediaQuery, asset: ResultMediaAssetRead) -> None:
    query.events.append("authorize")
    assert asset.id == "asset-1"


class _AuthorizationRejected(Exception):
    pass


def _raise_authorization_rejected(query: _ResultMediaQuery) -> None:
    query.events.append("authorize")
    raise _AuthorizationRejected
