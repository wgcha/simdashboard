"""Issue #51 (2026-10-23): on Windows a freshly downloaded staging file can stay locked by another
process (adapter worker / virus scanner) for a moment -> ``WinError 32`` on move and delete.  The
blob store must still store the content and never fail the capture."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.services.storage import server_local
from app.services.storage.server_local import BlobStore


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(server_local, "_LOCK_RETRY_SECONDS", (0.0, 0.0))


def _locked(monkeypatch, source: Path):
    real_replace, real_unlink = os.replace, Path.unlink

    def replace(src, dst, *args, **kwargs):
        if Path(src) == source:
            raise PermissionError(32, "being used by another process")
        return real_replace(src, dst, *args, **kwargs)

    def unlink(self, *args, **kwargs):
        if self == source:
            raise PermissionError(32, "being used by another process")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(server_local.os, "replace", replace)
    monkeypatch.setattr(Path, "unlink", unlink)


def test_locked_staging_file_is_copied_and_left_for_cleanup(tmp_path, monkeypatch):
    source = tmp_path / "staging" / "LAYER_ALIGN_(major).h3d.csv"
    source.parent.mkdir()
    data = b"edge,value\n1,2\n"
    source.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    _locked(monkeypatch, source)
    stored = BlobStore(tmp_path / "blobs", 10 ** 9).put(source, sha)
    assert stored.read_bytes() == data and stored.name == sha
    assert source.exists()  # still locked: left for the staging cleanup, not an error
    assert not list(stored.parent.glob("*.part"))


def test_locked_source_when_blob_already_exists(tmp_path, monkeypatch):
    data = b"same content"
    sha = hashlib.sha256(data).hexdigest()
    store = BlobStore(tmp_path / "blobs", 10 ** 9)
    first = tmp_path / "a.csv"
    first.write_bytes(data)
    store.put(first, sha)
    second = tmp_path / "b.csv"
    second.write_bytes(data)
    _locked(monkeypatch, second)
    assert store.put(second, sha).read_bytes() == data


def test_content_still_being_written_is_not_stored(tmp_path, monkeypatch):
    source = tmp_path / "partial.csv"
    source.write_bytes(b"half")
    _locked(monkeypatch, source)
    store = BlobStore(tmp_path / "blobs", 10 ** 9)
    with pytest.raises(OSError):
        store.put(source, hashlib.sha256(b"half and the rest").hexdigest())
    assert not [p for p in (tmp_path / "blobs").rglob("*") if p.is_file()]
