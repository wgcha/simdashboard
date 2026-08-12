from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator
from uuid import uuid4

from ..media_policy import ALLOWED_MEDIA, validate_media_metadata
from ..repositories.media_repository import CHUNK_SIZE, BlobRecord, attach_media_asset, find_blob, insert_blob, insert_chunk, validate_blob_chunks


MAX_PREFIX_BYTES = 1024 * 1024
_SVG_ACTIVE_CONTENT = re.compile(
    rb"<(?:script|iframe|object|embed|foreignObject)\b|\bon[a-z][a-z0-9_-]*\s*=|(?:href|xlink:href)\s*=\s*['\"]\s*(?:https?:|//|javascript:|data:)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class StoredMedia:
    blob: BlobRecord
    original_filename: str
    mime_type: str


def _canonical_type(asset_type: str, filename: str) -> str:
    if asset_type == "IMAGE":
        return "CONTOUR_IMAGE"
    if asset_type in ALLOWED_MEDIA:
        return asset_type
    suffix = Path(filename).suffix.lower()
    for candidate, (extensions, _) in ALLOWED_MEDIA.items():
        if suffix in extensions:
            return candidate
    raise ValueError("지원하지 않는 미디어 유형입니다.")


def _validate_filename_and_mime(asset_type: str, filename: str, mime_type: str) -> str:
    if not filename or Path(filename).name != filename or "\x00" in filename:
        raise ValueError("파일명은 경로가 아닌 단일 파일명이어야 합니다.")
    canonical_type = _canonical_type(asset_type, filename)
    validate_media_metadata(canonical_type, filename, None)
    expected = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
        ".svg": "image/svg+xml", ".mp4": "video/mp4", ".webm": "video/webm",
        ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
    }.get(Path(filename).suffix.lower())
    if not isinstance(mime_type, str) or mime_type.lower() != expected:
        raise ValueError(f"파일 확장자와 MIME이 일치하지 않습니다: {filename} / {mime_type}")
    return canonical_type


def _iter_source(source: BinaryIO | Iterable[bytes]) -> Iterator[bytes]:
    if hasattr(source, "read"):
        while True:
            block = source.read(CHUNK_SIZE)
            if not block:
                return
            data = bytes(block)
            for offset in range(0, len(data), CHUNK_SIZE):
                yield data[offset:offset + CHUNK_SIZE]
        return
    for block in source:
        if not block:
            continue
        data = bytes(block)
        for offset in range(0, len(data), CHUNK_SIZE):
            yield data[offset:offset + CHUNK_SIZE]


def _validate_magic(asset_type: str, filename: str, mime_type: str, prefix: bytes, file_size: int) -> None:
    if file_size <= 0:
        raise ValueError("빈 미디어 파일은 저장할 수 없습니다.")
    suffix = Path(filename).suffix.lower()
    if suffix in {".jpg", ".jpeg"} and not prefix.startswith(b"\xff\xd8\xff"):
        raise ValueError("JPEG magic bytes가 올바르지 않습니다.")
    if suffix == ".png" and not prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("PNG signature가 올바르지 않습니다.")
    if suffix == ".webp" and not (prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP"):
        raise ValueError("WebP RIFF/WEBP 구조가 올바르지 않습니다.")
    if suffix == ".svg":
        normalized = prefix.lstrip(b"\xef\xbb\xbf \t\r\n")
        if b"<svg" not in normalized[:MAX_PREFIX_BYTES].lower() or _SVG_ACTIVE_CONTENT.search(prefix):
            raise ValueError("SVG 최소 구조 또는 active content 검증에 실패했습니다.")
    if suffix == ".mp4" and b"ftyp" not in prefix[:MAX_PREFIX_BYTES]:
        raise ValueError("MP4 ISO Base Media ftyp box가 없습니다.")
    if suffix == ".webm" and not prefix.startswith(b"\x1a\x45\xdf\xa3"):
        raise ValueError("WebM EBML signature가 올바르지 않습니다.")
    if suffix == ".glb" and not prefix.startswith(b"glTF"):
        raise ValueError("GLB magic bytes가 올바르지 않습니다.")
    if suffix == ".gltf" and b"{" not in prefix[:MAX_PREFIX_BYTES]:
        raise ValueError("glTF JSON 구조가 올바르지 않습니다.")


def store_stream(
    connection: object,
    source: BinaryIO | Iterable[bytes],
    *,
    filename: str,
    mime_type: str,
    asset_type: str,
) -> StoredMedia:
    """Validate and persist a stream without ever assembling the file in memory."""
    canonical_type = _validate_filename_and_mime(asset_type, filename, mime_type)
    digest = hashlib.sha256()
    prefix = bytearray()
    total = 0
    svg_tail = b""
    with tempfile.SpooledTemporaryFile(max_size=2 * CHUNK_SIZE, mode="w+b") as spool:
        for chunk in _iter_source(source):
            if len(chunk) > CHUNK_SIZE:
                raise ValueError("미디어 청크가 1 MiB 제한을 초과했습니다.")
            total += len(chunk)
            if total > ALLOWED_MEDIA[canonical_type][1]:
                raise ValueError("미디어 파일 크기 제한을 초과했습니다.")
            digest.update(chunk)
            spool.write(chunk)
            if len(prefix) < MAX_PREFIX_BYTES:
                prefix.extend(chunk[:MAX_PREFIX_BYTES - len(prefix)])
            if Path(filename).suffix.lower() == ".svg":
                scanned = svg_tail + chunk
                if _SVG_ACTIVE_CONTENT.search(scanned):
                    raise ValueError("SVG active content가 포함되어 있습니다.")
                svg_tail = scanned[-512:]

        _validate_magic(canonical_type, filename, mime_type, bytes(prefix), total)
        checksum = digest.hexdigest()
        chunk_count = (total + CHUNK_SIZE - 1) // CHUNK_SIZE
        existing = find_blob(connection, checksum, total, lock=True)
        if existing is None:
            candidate = BlobRecord(str(uuid4()), checksum, total, CHUNK_SIZE, chunk_count)
            stored = insert_blob(connection, candidate)
            if stored.id == candidate.id:
                spool.seek(0)
                index = 0
                while True:
                    chunk = spool.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    insert_chunk(connection, stored.id, index, chunk)
                    index += 1
                validate_blob_chunks(connection, stored)
            else:
                # A concurrent uploader won the identity race. Its complete
                # blob is the source of truth; this transaction only attaches it.
                stored = find_blob(connection, checksum, total, lock=True)
                if stored is None:
                    raise RuntimeError("동시 업로드 dedup 결과를 확인하지 못했습니다.")
        else:
            stored = existing
            validate_blob_chunks(connection, stored)
    return StoredMedia(stored, filename, mime_type)


def store_file(connection: object, path: Path, *, filename: str | None = None, mime_type: str, asset_type: str) -> StoredMedia:
    with path.open("rb") as source:
        return store_stream(
            connection,
            source,
            filename=filename or path.name,
            mime_type=mime_type,
            asset_type=asset_type,
        )


def attach_stored_media(connection: object, asset_id: str, stored: StoredMedia) -> None:
    attach_media_asset(
        connection,
        asset_id,
        stored.blob,
        original_filename=stored.original_filename,
        mime_type=stored.mime_type,
    )
