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


@pytest.mark.skipif(os.name != "nt", reason="real Windows sharing violation (runs in the Windows CI job)")
def test_real_windows_lock_while_another_handle_is_open(tmp_path):
    """No mocks: on Windows an open handle (opened without FILE_SHARE_DELETE, as Python and most
    programs do) makes rename/delete fail with WinError 32 -- exactly the server log of issue #51."""
    source = tmp_path / "staging" / "LAYER_ALIGN_C15_SIDE_BOT_Stress_P1_(major)_Max.h3d.csv"
    source.parent.mkdir()
    data = b"edge,value\n1,2\n" * 1000
    source.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    with source.open("rb"):  # another process still holding the downloaded file
        with pytest.raises(PermissionError):
            os.replace(source, tmp_path / "probe")  # proves the lock is real on this runner
        stored = BlobStore(tmp_path / "blobs", 10 ** 9).put(source, sha)
    assert stored.read_bytes() == data and stored.name == sha


def test_adapter_sharing_violation_is_a_retryable_busy_file():
    """Issue #51: the adapter's own download_to failed with WinError 32 (DRIVE_INTERNAL) -> retry later."""
    from app.services.drive import gateway

    class AdapterError(Exception):
        def __init__(self, code, message):
            super().__init__(message)
            self.code, self.message = code, message

    locked = AdapterError("INTERNAL", "PermissionError: [WinError 32] 다른 프로세스가 파일을 사용 중이기 때문에 프로세스가 액세스 할 수 없습니다: 'E:\\\\x\\\\.scx-part'")
    assert gateway.to_storage_error(locked).code == "SPDM_FILE_BUSY"
    assert gateway.map_drive_error(locked).retryable is True
    assert gateway.to_storage_error(AdapterError("INTERNAL", "boom")).code == "DRIVE_INTERNAL"


def test_download_is_retried_while_the_scanner_holds_the_new_file(monkeypatch, tmp_path):
    """Issue #51: no scanner exclusion is allowed on the server, so download_to is retried a few times."""
    from app.services.drive import reads
    from app.services.storage.provider import SpdmStorageError

    monkeypatch.setattr(reads, "DOWNLOAD_LOCK_RETRY_SECONDS", (0.0, 0.0))
    calls = {"n": 0}

    class Done(Exception):
        pass

    def fake_call(fn, op, rel):
        calls["n"] += 1
        if calls["n"] < 3:
            raise SpdmStorageError("SPDM_FILE_BUSY", "WinError 32")
        raise Done()

    class Session:
        resumable = False
        staged_bytes = 0
        def next_dir(self):
            return tmp_path

    monkeypatch.setattr(reads, "_call", fake_call)
    monkeypatch.setattr(reads, "_reserve_staging", lambda session, size: None)
    monkeypatch.setattr(reads, "stop_reason", lambda: None)
    version = type("V", (), {"size": 10, "token": "t"})()
    with pytest.raises(Done):
        reads._download(Session(), "root", "a/b.csv", version, 100)
    assert calls["n"] == 3
