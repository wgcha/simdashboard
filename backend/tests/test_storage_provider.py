"""LocalFsProvider unit tests (docs/contracts/storage-provider.md §2, §3.3). Synthetic temp roots only."""
from __future__ import annotations

import os
import sys
import types

import pytest

from app.services.storage import LocalFsProvider, NOT_ALLOWED_WRITE, StorageError
from app.services.storage import local as storage_local
from app.services.storage.provider import FINAL, LEGACY, SpdmStorageError, final_zone_allows

pytestmark = pytest.mark.unit


@pytest.fixture
def fs(tmp_path):
    root = tmp_path / "spdm"
    root.mkdir()
    return LocalFsProvider(root.resolve())


def _call_as(module_name: str, function, *args, **kwargs):
    """Invoke ``function`` from a frame whose module is ``module_name`` (LEGACY caller check)."""
    namespace = {"__name__": module_name, "function": function, "args": args, "kwargs": kwargs}
    module = types.ModuleType(module_name)
    module.__dict__.update(namespace)
    exec("def run():\n    return function(*args, **kwargs)\n", module.__dict__)
    return module.run()


def test_root_identity_matches_previous_computation(fs):
    info = fs.root.stat()
    assert fs.root_identity() == f"{info.st_dev}:{info.st_ino}:{str(fs.root).casefold()}"
    import hashlib
    from app.services import dashboard_capture, folder_discovery_scan, spdm_storage
    assert folder_discovery_scan.root_identity(fs.root) == hashlib.sha256(fs.root_identity().encode("utf-8")).hexdigest()
    assert dashboard_capture._root_id(fs.root) == "dashboard-root-" + hashlib.sha256(fs.root_identity().encode()).hexdigest()
    assert spdm_storage._root_identity(fs.root) == fs.root_identity()


@pytest.mark.parametrize("relative", ["../outside", "a/../../b", "/etc", "a/\x00b", "a\\..\\..\\b",
                                      "C:/Windows", "a/C:x", "a/file.txt:stream", "\\\\server\\share"])
def test_path_traversal_and_absolute_paths_are_rejected(fs, relative):
    with pytest.raises(SpdmStorageError) as error:
        fs.path(relative)
    assert error.value.code == "SPDM_PATH_INVALID"
    with pytest.raises(SpdmStorageError):
        fs.stat(relative)


def test_assert_safe_rejects_link_ancestor_and_escape(fs, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (fs.root / "Project").mkdir()
    try:
        os.symlink(outside, fs.root / "Project" / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert fs.is_link("Project/linked") is True and fs.is_link("Project") is False
    with pytest.raises(SpdmStorageError) as error:
        fs.assert_safe("Project/linked/file.csv")
    assert error.value.code == "SPDM_PATH_UNSAFE"
    with pytest.raises(SpdmStorageError) as escape:
        storage_local._assert_safe_existing(outside, fs.root)
    assert escape.value.code == "SPDM_PATH_ESCAPE"


def test_list_keeps_scandir_order_and_reports_kind_hidden_and_links(fs, tmp_path):
    (fs.root / "b_dir").mkdir()
    (fs.root / ".hidden").write_bytes(b"x")
    (fs.root / "A.csv").write_bytes(b"12345")
    linked = False
    try:
        os.symlink(tmp_path, fs.root / "z_link", target_is_directory=True)
        linked = True
    except (OSError, NotImplementedError):
        pass
    entries = fs.list("")
    assert [entry.name for entry in entries] == [entry.name for entry in os.scandir(fs.root)]
    by_name = {entry.name: entry for entry in entries}
    assert by_name["b_dir"].kind == "dir" and by_name["A.csv"].kind == "file"
    assert ".hidden" in by_name  # hidden names are listed; callers decide to skip them
    assert not any(entry.is_link for name, entry in by_name.items() if name != "z_link")
    if linked:
        assert by_name["z_link"].is_link is True and by_name["z_link"].kind == "other"
    stated = {entry.name: entry for entry in fs.list("", stat=True)}
    info = (fs.root / "A.csv").lstat()
    assert (stated["A.csv"].size, stated["A.csv"].modified_ns) == (5, info.st_mtime_ns)
    assert stated["A.csv"].item_id == f"{info.st_dev}:{info.st_ino}" and stated["A.csv"].etag is None
    assert fs.stat("missing") is None
    with pytest.raises(FileNotFoundError):
        fs.stat("missing", missing_ok=False)
    assert fs.changes("", None) is None


def test_case_collision_returns_existing_spelling(fs):
    (fs.root / "Final").mkdir()
    assert fs.case_collision("", "final") == "Final"
    assert fs.case_collision("", "other") is None
    assert fs.case_collision("absent", "x") is None


def test_read_stable_and_open_read(fs):
    (fs.root / "a.json").write_bytes(b'{"a": 1}')
    assert fs.read_stable("a.json") == b'{"a": 1}'
    data, digest = fs.read_stable_digest("a.json")
    import hashlib
    assert digest == hashlib.sha256(data).hexdigest()
    with fs.open_read("a.json") as stream:
        assert stream.read() == data
    with pytest.raises(SpdmStorageError) as error:
        fs.read_stable("a.json", max_bytes=2)
    assert error.value.code == "SPDM_FILE_TOO_LARGE"


@pytest.mark.parametrize("relative, allowed", [
    ("P/WR/Final", True),
    ("Final", False),
    ("Final/CAE/x", False),
    ("P/WR/Working/Final/CAE/f", False),
    ("P/WR/working/Case/Final/Reports/r.pptx", False),
    ("P/WR/Final/CAE/Case/op/file.csv", True),
    ("P/WR/final/reports/Case/op/r.pptx", True),
    ("P/WR/Final/.finalizations/op/plan.json", True),
    ("P/WR/Final/Other/x", False),
    ("P/WR/Working/Case/x.csv", False),
    ("P/WR", False),
    ("", False),
])
def test_final_zone_patterns(relative, allowed):
    assert final_zone_allows(relative) is allowed


def test_writes_outside_zone_are_not_allowed(fs):
    for call in (
        lambda: fs.mkdirs("P/WR/Working", zone=FINAL),
        lambda: fs.create_exclusive("P/WR/Working/a.csv", b"x", zone=FINAL),
        lambda: fs.replace("P/WR/Final/CAE/a", "P/WR/Working/a", zone=FINAL),
        lambda: fs.move_no_overwrite("P/WR/Final/CAE/a", "P/a", zone=FINAL),
        lambda: fs.remove("P/WR/Working/a", zone=FINAL),
        lambda: fs.lock("P/WR/Working/.request.lock", zone=FINAL),
        lambda: fs.mkdirs("P/WR/Final", zone="OTHER"),
    ):
        with pytest.raises(StorageError) as error:
            call()
        assert error.value.code == NOT_ALLOWED_WRITE
    assert list(fs.root.iterdir()) == []


def test_legacy_zone_is_limited_to_its_modules_and_paths(fs):
    # This test module is not a LEGACY writer.
    with pytest.raises(StorageError) as error:
        fs.mkdirs("Project_A_B_C", zone=LEGACY)
    assert error.value.code == NOT_ALLOWED_WRITE
    # The legacy SPDM storage writer may create only its exact Issue #13 folders.
    _call_as("app.services.spdm_storage", fs.mkdirs, "Project_A_B_C/WR_X_SimType2/CAE/Case", zone=LEGACY)
    assert (fs.root / "Project_A_B_C" / "WR_X_SimType2" / "CAE" / "Case").is_dir()
    with pytest.raises(StorageError):
        _call_as("app.services.spdm_storage", fs.mkdirs, "Other/WR_X_SimType2/CAE", zone=LEGACY)
    with pytest.raises(StorageError):
        _call_as("app.services.spdm_storage", fs.mkdirs, "Project_A_B_C/WR_X_SimType2/Working", zone=LEGACY)
    # Result registration may prepare result folders anywhere below the root, never the root itself.
    _call_as("app.services.result_registration_paths", fs.mkdirs, "P/WR/Working/Case/results", zone=LEGACY)
    with pytest.raises(StorageError):
        _call_as("app.services.result_registration_paths", fs.remove, "", zone=LEGACY, directory=True)
    with pytest.raises(StorageError):
        _call_as("app.services.case_finalization", fs.mkdirs, "P/WR/Working/x", zone=LEGACY)


def test_final_zone_is_limited_to_case_finalization(fs):
    with pytest.raises(StorageError) as error:
        fs.mkdirs("P/WR/Final", zone=FINAL)  # this test module is not the Final writer
    assert error.value.code == NOT_ALLOWED_WRITE
    with pytest.raises(StorageError) as working:
        _call_as("app.services.case_finalization", fs.mkdirs, "P/WR/Working/Final/CAE/f", zone=FINAL)
    assert working.value.code == NOT_ALLOWED_WRITE
    with pytest.raises(StorageError):
        _call_as("app.services.spdm_storage", fs.mkdirs, "P/WR/Final/CAE", zone=FINAL)
    assert list(fs.root.iterdir()) == []


def test_final_writes_publish_without_overwrite(fs):
    final = lambda function, *args, **kwargs: _call_as("app.services.case_finalization", function, *args, **kwargs)  # noqa: E731
    final(fs.mkdirs, "P/WR/Final/CAE/Case", zone=FINAL)
    final(fs.create_exclusive, "P/WR/Final/CAE/Case/.tmp", [b"ab", b"c"], zone=FINAL)
    with pytest.raises(FileExistsError):
        final(fs.create_exclusive, "P/WR/Final/CAE/Case/.tmp", b"x", zone=FINAL)
    final(fs.move_no_overwrite, "P/WR/Final/CAE/Case/.tmp", "P/WR/Final/CAE/Case/a.csv", zone=FINAL)
    assert (fs.root / "P/WR/Final/CAE/Case/a.csv").read_bytes() == b"abc"
    # POSIX publishes by hardlink and leaves the temporary name to the caller's own cleanup.
    assert (fs.root / "P/WR/Final/CAE/Case/.tmp").exists() is (sys.platform != "win32")
    final(fs.remove, "P/WR/Final/CAE/Case/.tmp", zone=FINAL, missing_ok=True)
    final(fs.create_exclusive, "P/WR/Final/CAE/Case/.tmp2", b"zz", zone=FINAL)
    with pytest.raises(FileExistsError):
        final(fs.move_no_overwrite, "P/WR/Final/CAE/Case/.tmp2", "P/WR/Final/CAE/Case/a.csv", zone=FINAL)
    assert (fs.root / "P/WR/Final/CAE/Case/a.csv").read_bytes() == b"abc"
    final(fs.replace, "P/WR/Final/CAE/Case/.tmp2", "P/WR/Final/CAE/Case/a.csv", zone=FINAL)
    assert (fs.root / "P/WR/Final/CAE/Case/a.csv").read_bytes() == b"zz"
    final(fs.remove, "P/WR/Final/CAE/Case/a.csv", zone=FINAL)
    final(fs.remove, "P/WR/Final/CAE/Case/a.csv", zone=FINAL, missing_ok=True)
    final(fs.remove, "P/WR/Final/CAE/Case", zone=FINAL, directory=True)
    final(fs.mkdirs, "P/WR/Final/.finalizations", zone=FINAL)
    lock = final(fs.lock, "P/WR/Final/.finalizations/.request.lock", zone=FINAL)
    with lock:
        assert (fs.root / "P/WR/Final/.finalizations/.request.lock").read_bytes() == b"\0"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX no-follow metadata read")
def test_read_small_nofollow_rejects_links_and_oversize(fs):
    (fs.root / "plan.json").write_bytes(b"{}")
    assert fs.read_small_nofollow("plan.json", max_bytes=10) == b"{}"
    assert fs.read_small_nofollow("plan.json", max_bytes=1) is None
    seen = []
    assert fs.read_small_nofollow("plan.json", max_bytes=10, before_read=seen.append) == b"{}" and seen == [2]
    os.symlink(fs.root / "plan.json", fs.root / "link.json")
    assert fs.read_small_nofollow("link.json", max_bytes=10) is None
    assert fs.read_small_nofollow("missing.json", max_bytes=10) is None
