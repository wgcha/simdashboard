from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from typing import Any, BinaryIO, Callable, Iterator, Protocol
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import Response, StreamingResponse

from ..config import database_settings
from ..database_connection import media_connect
from ..repositories.media_repository import iter_blob_range


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


class MediaBlobRead(Protocol):
    """Blob metadata required by the HTTP transport, independent of its reader."""

    id: str
    sha256: str
    file_size: int
    chunk_size: int
    chunk_count: int


def parse_single_range(value: str | None, size: int) -> ByteRange | None:
    if not value:
        return None
    if size <= 0 or not value.startswith("bytes="):
        raise ValueError("invalid range")
    ranges = value.removeprefix("bytes=").split(",")
    if len(ranges) != 1:
        raise ValueError("multiple ranges are not supported")
    token = ranges[0].strip()
    if "-" not in token:
        raise ValueError("invalid range")
    left, right = token.split("-", 1)
    try:
        if left == "":
            suffix = int(right)
            if suffix <= 0:
                raise ValueError
            return ByteRange(max(0, size - suffix), size - 1)
        start = int(left)
        if start < 0 or start >= size:
            raise ValueError
        if right == "":
            end = size - 1
        else:
            end = int(right)
            if end < start:
                raise ValueError
            end = min(end, size - 1)
        return ByteRange(start, end)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid range") from exc


def _etag_matches(value: str | None, etag: str) -> bool:
    if not value:
        return False
    return any(token.strip() in {etag, "*", f"W/{etag}"} for token in value.split(","))


def _strong_etag_matches(value: str | None, etag: str) -> bool:
    return value is not None and value.strip() == etag


def safe_filename(value: str | None) -> str:
    raw = re.sub(r"[\x00-\x1f\x7f]", "", value or "download").strip()
    raw = raw.replace("/", "_").replace("\\", "_").replace('"', "'")
    return raw[:240] or "download"


def content_disposition(filename: str, *, download: bool) -> str:
    name = safe_filename(filename)
    fallback = name.encode("ascii", "ignore").decode("ascii").strip(" .") or "download"
    disposition = "attachment" if download else "inline"
    return f'{disposition}; filename="{fallback}"; filename*=UTF-8\'\'{quote(name, safe=".!#$&+^_`|~-", encoding="utf-8")}'


def _base_headers(*, mime_type: str, size: int, etag: str, filename: str, download: bool) -> dict[str, str]:
    headers = {
        "Content-Type": mime_type,
        "Content-Length": str(size),
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Content-Disposition": content_disposition(filename, download=download),
        "Cache-Control": "private, max-age=0, must-revalidate, no-transform",
        "X-Content-Type-Options": "nosniff",
    }
    if mime_type.casefold() == "image/svg+xml":
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
    return headers


def _materialize_duckdb_range(blob: MediaBlobRead, start: int, end: int) -> BinaryIO:
    """Read a local blob under DuckDB's lock, then release it before ASGI yields."""
    spool = tempfile.SpooledTemporaryFile(max_size=2 * blob.chunk_size, mode="w+b")
    try:
        with media_connect() as connection:
            for chunk in iter_blob_range(connection, blob.id, start, end):
                spool.write(chunk)
        spool.seek(0)
        return spool
    except BaseException:
        spool.close()
        raise


def build_media_response(
    request: Request,
    *,
    blob: MediaBlobRead,
    mime_type: str,
    filename: str,
    download: bool = False,
    audit: Callable[[str, int, int], None] | None = None,
) -> Response:
    etag = f'"{blob.sha256}"'
    headers = _base_headers(mime_type=mime_type, size=blob.file_size, etag=etag, filename=filename, download=download)
    if _etag_matches(request.headers.get("if-none-match"), etag):
        headers.pop("Content-Length", None)
        return Response(status_code=304, headers=headers)

    byte_range: ByteRange | None = None
    range_header = request.headers.get("range")
    if range_header and request.headers.get("if-range") and not _strong_etag_matches(request.headers.get("if-range"), etag):
        range_header = None
    if range_header:
        try:
            byte_range = parse_single_range(range_header, blob.file_size)
        except ValueError:
            headers["Content-Range"] = f"bytes */{blob.file_size}"
            headers["Content-Length"] = "0"
            return Response(status_code=416, headers=headers)

    status = 206 if byte_range else 200
    start = byte_range.start if byte_range else 0
    end = byte_range.end if byte_range else max(0, blob.file_size - 1)
    length = byte_range.length if byte_range else blob.file_size
    headers["Content-Length"] = str(length)
    if byte_range:
        headers["Content-Range"] = f"bytes {byte_range.start}-{byte_range.end}/{blob.file_size}"
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers)

    if database_settings().backend == "duckdb":
        def stream_materialized() -> Iterator[bytes]:
            yielded = 0
            action = "STARTED"
            spool: BinaryIO | None = None
            try:
                # Do not create a temporary file until ASGI actually starts
                # consuming the body; an unstarted response owns no fd.
                spool = _materialize_duckdb_range(blob, start, end)
                if audit:
                    audit(action, 0, status)
                while chunk := spool.read(blob.chunk_size):
                    yielded += len(chunk)
                    yield chunk
                action = "COMPLETED"
            except GeneratorExit:
                action = "ABORTED"
                raise
            except Exception:
                action = "ABORTED"
                raise
            finally:
                if spool is not None:
                    spool.close()
                if audit:
                    audit(action, yielded, status)

        return StreamingResponse(stream_materialized(), status_code=status, headers=headers, media_type=None)

    def stream() -> Iterator[bytes]:
        yielded = 0
        action = "STARTED"
        if audit:
            audit(action, 0, status)
        try:
            position = start
            while position <= end:
                batch_end = min(end, position + blob.chunk_size - 1)
                # Materialize one bounded batch before yielding it. Yielding
                # from inside this context would keep a pool slot checked out
                # while a slow client consumes the response.
                with media_connect() as connection:
                    chunks = tuple(iter_blob_range(connection, blob.id, position, batch_end))
                for chunk in chunks:
                    yielded += len(chunk)
                    yield chunk
                position = batch_end + 1
            action = "COMPLETED"
        except GeneratorExit:
            action = "ABORTED"
            raise
        except Exception:
            action = "ABORTED"
            raise
        finally:
            if audit:
                audit(action, yielded, status)

    return StreamingResponse(stream(), status_code=status, headers=headers, media_type=None)
