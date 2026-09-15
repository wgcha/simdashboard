"""Owned, expiring sample uploads. Saved recipe versions keep independent bytes."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException

from ..config import database_settings

MAX_SAMPLE_BYTES = 64 * 1024 * 1024
UPLOAD_TTL_SECONDS = 24 * 60 * 60


def _root() -> Path:
    settings = database_settings()
    identity = settings.database_url if settings.backend == "postgresql" else str(settings.duckdb_path.resolve())
    namespace = hashlib.sha256((identity or "").encode()).hexdigest()[:24]
    root = Path(tempfile.gettempdir()) / "simdashboard-semantic-samples" / namespace
    root.mkdir(parents=True, exist_ok=True)
    return root


def _paths(upload_id: str) -> tuple[Path, Path]:
    if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
        raise HTTPException(404, {"code": "SAMPLE_UPLOAD_NOT_FOUND"})
    root = _root()
    return root / f"{upload_id}.json", root / f"{upload_id}.sample"


def cleanup_expired() -> None:
    for metadata in _root().glob("*.json"):
        try:
            value = json.loads(metadata.read_text(encoding="utf-8"))
            if float(value["expires_at"]) > time.time():
                continue
            _, sample = _paths(metadata.stem)
            sample.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError):
            continue


def save(owner_id: str, filename: str, content: bytes) -> str:
    if not content or len(content) > MAX_SAMPLE_BYTES:
        raise HTTPException(413, {"code": "SEMANTIC_SAMPLE_TOO_LARGE", "message": "샘플 파일은 64 MiB 이하여야 합니다."})
    cleanup_expired()
    upload_id = uuid4().hex
    metadata, sample = _paths(upload_id)
    value = {"owner_id": owner_id, "filename": filename, "sha256": hashlib.sha256(content).hexdigest(),
             "expires_at": time.time() + UPLOAD_TTL_SECONDS, "size": len(content)}
    try:
        with sample.open("xb") as target:
            target.write(content)
        with metadata.open("x", encoding="utf-8") as target:
            json.dump(value, target, ensure_ascii=False)
    except BaseException:
        sample.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)
        raise
    return upload_id


def _owned(owner_id: str, upload_id: str) -> tuple[dict, Path, Path]:
    metadata, sample = _paths(upload_id)
    try:
        if metadata.is_symlink() or sample.is_symlink():
            raise FileNotFoundError()
        value = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, {"code": "SAMPLE_UPLOAD_NOT_FOUND"}) from None
    if value.get("owner_id") != owner_id:
        raise HTTPException(404, {"code": "SAMPLE_UPLOAD_NOT_FOUND"})
    if value.get("expires_at", 0) <= time.time():
        sample.unlink(missing_ok=True)
        metadata.unlink(missing_ok=True)
        raise HTTPException(410, {"code": "SAMPLE_UPLOAD_EXPIRED", "message": "샘플 보관 시간이 지났습니다. 파일을 다시 올려주세요."})
    return value, metadata, sample


def load(owner_id: str, upload_id: str) -> tuple[str, bytes]:
    value, _, sample = _owned(owner_id, upload_id)
    try:
        with sample.open("rb") as source:
            content = source.read(MAX_SAMPLE_BYTES + 1)
    except OSError:
        raise HTTPException(404, {"code": "SAMPLE_UPLOAD_NOT_FOUND"}) from None
    if len(content) > MAX_SAMPLE_BYTES or hashlib.sha256(content).hexdigest() != value.get("sha256"):
        raise HTTPException(409, {"code": "SAMPLE_UPLOAD_CHANGED"})
    return value["filename"], content


def delete(owner_id: str, upload_id: str) -> None:
    _, metadata, sample = _owned(owner_id, upload_id)
    sample.unlink(missing_ok=True)
    metadata.unlink(missing_ok=True)
