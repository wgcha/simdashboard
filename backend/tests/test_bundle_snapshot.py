from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import queue
from types import SimpleNamespace
from pathlib import Path

import pytest

from app.config import ImportBundleLimits
from app.folder_import import scan_folder
import app.services.bundle_snapshot as bundle_snapshot_module
from app.services.bundle_fingerprint import FingerprintEntry, calculate_bundle_fingerprint
from app.services.bundle_snapshot import BundleSnapshotError, capture_bundle


pytestmark = pytest.mark.unit


def _limits(
    *,
    manifest: int = 4096,
    mappings: int = 8,
    file: int = 4096,
    total: int = 8192,
    structured: int = 4096,
) -> ImportBundleLimits:
    return ImportBundleLimits(
        max_manifest_bytes=manifest,
        max_mapping_count=mappings,
        max_file_bytes=file,
        max_total_bytes=total,
        max_structured_bytes=structured,
    )


def _write_bundle(
    root: Path,
    *,
    mapping_paths: list[str] | None = None,
    payload: bytes = b"[{\"variable_key\":\"peak\",\"data_type\":\"FLOAT\",\"value\":1}]",
) -> tuple[Path, Path, Path]:
    bundle = root / "bundle"
    bundle.mkdir()
    result = bundle / "summary.json"
    result.write_bytes(payload)
    paths = mapping_paths or ["summary.json"]
    manifest = bundle / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_id": "snapshot-test",
                "version": 1,
                "context": {
                    "project_id": "project-test",
                    "request_id": "request-test",
                    "load_case_id": "loadcase-test",
                },
                "mappings": [{"kind": "typed_scalars", "path": path} for path in paths],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return bundle, manifest, result


def _capture(root: Path, limits: ImportBundleLimits | None = None):
    return capture_bundle(root, "bundle/manifest.json", limits or _limits())


def _skip_without_symlinks(path: Path, target: Path) -> None:
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"현재 파일 시스템은 심볼릭 링크 테스트를 지원하지 않습니다: {exc}")


def _capture_bundle_error_worker(root_text: str, result_queue: object) -> None:
    """Run a capture in a killable process for FIFO no-writer regression tests."""
    from app.services.bundle_snapshot import BundleSnapshotError, capture_bundle

    limits = ImportBundleLimits(
        max_manifest_bytes=4096,
        max_mapping_count=8,
        max_file_bytes=4096,
        max_total_bytes=8192,
        max_structured_bytes=4096,
    )
    try:
        with capture_bundle(Path(root_text), "bundle/manifest.json", limits):
            pass
    except BundleSnapshotError as error:
        result_queue.put(error.code)
    except BaseException as error:  # pragma: no cover - diagnostic for child failures
        result_queue.put(f"UNEXPECTED:{type(error).__name__}")


def _assert_fifo_capture_error(root: Path, expected_code: str) -> None:
    context = mp.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_capture_bundle_error_worker,
        args=(str(root), result_queue),
    )
    process.start()
    process.join(timeout=2)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
        result_queue.close()
        result_queue.join_thread()
        pytest.fail("FIFO capture did not return within the bounded timeout")
    try:
        actual_code = result_queue.get(timeout=1)
    except queue.Empty as exc:  # pragma: no cover - diagnostic for child failures
        pytest.fail(f"FIFO capture child exited without an error code: {process.exitcode}")
        raise AssertionError from exc
    finally:
        result_queue.close()
        result_queue.join_thread()
    assert actual_code == expected_code


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO tests require POSIX os.mkfifo")
def test_manifest_fifo_without_writer_is_rejected_without_blocking(tmp_path: Path) -> None:
    _bundle, manifest_path, _result_path = _write_bundle(tmp_path)
    manifest_path.unlink()
    os.mkfifo(manifest_path)

    _assert_fifo_capture_error(tmp_path, "BUNDLE_MANIFEST_FILE_UNSAFE")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO tests require POSIX os.mkfifo")
def test_mapping_fifo_without_writer_is_rejected_without_blocking(tmp_path: Path) -> None:
    _bundle, _manifest_path, result_path = _write_bundle(tmp_path)
    result_path.unlink()
    os.mkfifo(result_path)

    _assert_fifo_capture_error(tmp_path, "BUNDLE_MAPPING_FILE_UNSAFE")


@pytest.mark.skipif(os.name != "posix", reason="secure traversal flag test requires POSIX")
def test_secure_traversal_fails_closed_when_nonblocking_flag_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_os = bundle_snapshot_module.os
    fake_os = SimpleNamespace(
        name="posix",
        open=real_os.open,
        supports_dir_fd=real_os.supports_dir_fd,
        O_NOFOLLOW=real_os.O_NOFOLLOW,
        O_DIRECTORY=real_os.O_DIRECTORY,
    )
    # Replace only this module's os reference; do not mutate the process-wide
    # os module or delete a real platform constant used by other tests.
    monkeypatch.setattr(bundle_snapshot_module, "os", fake_os)

    with pytest.raises(BundleSnapshotError) as error:
        bundle_snapshot_module._require_secure_traversal()

    assert error.value.code == "BUNDLE_SECURE_TRAVERSAL_UNSUPPORTED"


def test_capture_returns_immutable_metadata_and_fingerprint(tmp_path: Path) -> None:
    bundle, manifest_path, result_path = _write_bundle(tmp_path)

    with _capture(tmp_path) as captured:
        assert captured.bundle_root != bundle
        assert (captured.bundle_root / "manifest.json").read_bytes() == manifest_path.read_bytes()
        assert (captured.bundle_root / "summary.json").read_bytes() == result_path.read_bytes()
        assert captured.manifest_checksum == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        assert captured.bundle_fingerprint == calculate_bundle_fingerprint(captured.entries)
        assert [entry.relative_path for entry in captured.entries] == ["manifest.json", "summary.json"]


def test_parser_reads_captured_bytes_after_source_overwrite_and_delete(tmp_path: Path) -> None:
    _bundle, _manifest_path, result_path = _write_bundle(tmp_path)

    with _capture(tmp_path) as captured:
        result_path.write_bytes(b"[{\"variable_key\":\"changed\",\"data_type\":\"FLOAT\",\"value\":99}]")
        result_path.unlink()

        parsed = scan_folder(captured.bundle_root)

    assert parsed["scalars"][0]["variable_key"] == "peak"
    assert parsed["scalars"][0]["value"] == 1.0


@pytest.mark.parametrize("path", [
    "/summary.json",
    "../summary.json",
    "nested\\summary.json",
    "./summary.json",
    "nested//summary.json",
    "summary\x00.json",
    "",
])
def test_capture_rejects_noncanonical_mapping_paths(tmp_path: Path, path: str) -> None:
    _write_bundle(tmp_path, mapping_paths=[path])

    with pytest.raises(BundleSnapshotError) as error:
        _capture(tmp_path)

    assert error.value.code == "BUNDLE_MAPPING_PATH_INVALID"


def test_capture_rejects_duplicate_mapping_paths(tmp_path: Path) -> None:
    _write_bundle(tmp_path, mapping_paths=["summary.json", "summary.json"])

    with pytest.raises(BundleSnapshotError) as error:
        _capture(tmp_path)

    assert error.value.code == "BUNDLE_MAPPING_PATH_DUPLICATE"


def test_capture_rejects_reserved_manifest_mapping(tmp_path: Path) -> None:
    _write_bundle(tmp_path, mapping_paths=["manifest.json"])

    with pytest.raises(BundleSnapshotError) as error:
        _capture(tmp_path)

    assert error.value.code == "BUNDLE_MAPPING_PATH_RESERVED"


def test_capture_rejects_final_file_symlink(tmp_path: Path) -> None:
    bundle, _manifest_path, result_path = _write_bundle(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(result_path.read_bytes())
    result_path.unlink()
    _skip_without_symlinks(result_path, outside)

    with pytest.raises(BundleSnapshotError) as error:
        _capture(tmp_path)

    assert error.value.code == "BUNDLE_MAPPING_FILE_UNSAFE"


def test_capture_rejects_intermediate_directory_symlink(tmp_path: Path) -> None:
    bundle, manifest_path, result_path = _write_bundle(tmp_path)
    result_path.unlink()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "summary.json").write_bytes(b"[]")
    nested = bundle / "nested"
    _skip_without_symlinks(nested, outside)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_id": "snapshot-test",
                "version": 1,
                "mappings": [{"kind": "typed_scalars", "path": "nested/summary.json"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BundleSnapshotError) as error:
        _capture(tmp_path)

    assert error.value.code == "BUNDLE_MAPPING_FILE_UNSAFE"


def test_capture_rejects_non_regular_mapping_source(tmp_path: Path) -> None:
    bundle, _manifest_path, result_path = _write_bundle(tmp_path)
    result_path.unlink()
    result_path.mkdir()

    with pytest.raises(BundleSnapshotError) as error:
        _capture(tmp_path)

    assert error.value.code == "BUNDLE_MAPPING_FILE_UNSAFE"


def test_manifest_size_exact_boundary_and_one_byte_below(tmp_path: Path) -> None:
    _bundle, manifest_path, result_path = _write_bundle(tmp_path)
    manifest_size = manifest_path.stat().st_size
    total_size = manifest_size + result_path.stat().st_size

    with capture_bundle(
        tmp_path,
        "bundle/manifest.json",
        _limits(manifest=manifest_size, file=result_path.stat().st_size, total=total_size),
    ):
        pass

    with pytest.raises(BundleSnapshotError) as error:
        capture_bundle(
            tmp_path,
            "bundle/manifest.json",
            _limits(manifest=manifest_size - 1, file=result_path.stat().st_size, total=total_size),
        )
    assert error.value.code == "BUNDLE_MANIFEST_FILE_LIMIT"


def test_mapping_size_exact_boundary_and_one_byte_below(tmp_path: Path) -> None:
    _bundle, manifest_path, result_path = _write_bundle(tmp_path)
    manifest_size = manifest_path.stat().st_size
    result_size = result_path.stat().st_size
    total_size = manifest_size + result_size

    with capture_bundle(
        tmp_path,
        "bundle/manifest.json",
        _limits(manifest=manifest_size, file=result_size + 1, structured=result_size, total=total_size),
    ):
        pass

    with pytest.raises(BundleSnapshotError) as error:
        capture_bundle(
            tmp_path,
            "bundle/manifest.json",
            _limits(manifest=manifest_size, file=result_size + 1, structured=result_size - 1, total=total_size),
        )
    assert error.value.code == "BUNDLE_MAPPING_FILE_LIMIT"


def test_curve_mapping_uses_structured_limit_when_file_limit_is_larger(tmp_path: Path) -> None:
    _bundle, manifest_path, result_path = _write_bundle(tmp_path, payload=b"x,y\n1,2\n")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mappings"][0].update({"kind": "curve_csv", "x_column": "x", "y_column": "y"})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result_size = result_path.stat().st_size
    total_size = manifest_path.stat().st_size + result_size

    with capture_bundle(
        tmp_path,
        "bundle/manifest.json",
        _limits(
            manifest=manifest_path.stat().st_size,
            file=result_size + 1,
            structured=result_size,
            total=total_size,
        ),
    ):
        pass

    with pytest.raises(BundleSnapshotError) as error:
        capture_bundle(
            tmp_path,
            "bundle/manifest.json",
            _limits(
                manifest=manifest_path.stat().st_size,
                file=result_size + 1,
                structured=result_size - 1,
                total=total_size,
            ),
        )
    assert error.value.code == "BUNDLE_MAPPING_FILE_LIMIT"


def test_media_mapping_uses_file_limit_when_structured_limit_is_smaller(tmp_path: Path) -> None:
    _bundle, manifest_path, result_path = _write_bundle(tmp_path, payload=b"media-bytes")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mappings"][0].update({"kind": "media", "asset_type": "IMAGE", "mime_type": "image/png"})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result_size = result_path.stat().st_size
    manifest_size = manifest_path.stat().st_size
    total_size = manifest_size + result_size

    with capture_bundle(
        tmp_path,
        "bundle/manifest.json",
        _limits(
            manifest=manifest_size,
            file=result_size,
            structured=1,
            total=total_size,
        ),
    ):
        pass

    with pytest.raises(BundleSnapshotError) as error:
        capture_bundle(
            tmp_path,
            "bundle/manifest.json",
            _limits(
                manifest=manifest_size,
                file=result_size - 1,
                structured=1,
                total=total_size,
            ),
        )
    assert error.value.code == "BUNDLE_MAPPING_FILE_LIMIT"


@pytest.mark.parametrize("kind", ["unknown_kind", [], {}])
def test_unsupported_mapping_kind_is_rejected_before_opening_missing_file(
    tmp_path: Path,
    kind: object,
) -> None:
    _bundle, manifest_path, _result_path = _write_bundle(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mappings"][0].update({"kind": kind, "path": "missing.result"})
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BundleSnapshotError) as error:
        _capture(tmp_path)

    assert error.value.code == "BUNDLE_MAPPING_KIND_UNSUPPORTED"


def test_total_size_exact_boundary_and_one_byte_below(tmp_path: Path) -> None:
    _bundle, manifest_path, result_path = _write_bundle(tmp_path)
    manifest_size = manifest_path.stat().st_size
    result_size = result_path.stat().st_size
    total_size = manifest_size + result_size

    with capture_bundle(
        tmp_path,
        "bundle/manifest.json",
        _limits(manifest=manifest_size, file=total_size + 1, total=total_size),
    ):
        pass

    with pytest.raises(BundleSnapshotError) as error:
        capture_bundle(
            tmp_path,
            "bundle/manifest.json",
            _limits(manifest=manifest_size, file=total_size + 1, total=total_size - 1),
        )
    assert error.value.code == "BUNDLE_TOTAL_SIZE_LIMIT"


def test_mapping_count_exact_boundary_and_one_over(tmp_path: Path) -> None:
    bundle, manifest_path, result_path = _write_bundle(tmp_path)
    second = bundle / "second.json"
    second.write_bytes(result_path.read_bytes())
    manifest_path.write_text(
        json.dumps(
            {
                "schema_id": "snapshot-test",
                "version": 1,
                "mappings": [
                    {"kind": "typed_scalars", "path": "summary.json"},
                    {"kind": "typed_scalars", "path": "second.json"},
                ],
            }
        ),
        encoding="utf-8",
    )

    with capture_bundle(tmp_path, "bundle/manifest.json", _limits(mappings=2)):
        pass

    with pytest.raises(BundleSnapshotError) as error:
        capture_bundle(tmp_path, "bundle/manifest.json", _limits(mappings=1))
    assert error.value.code == "BUNDLE_MAPPING_COUNT_LIMIT"


def test_snapshot_is_removed_after_context_exit(tmp_path: Path) -> None:
    _write_bundle(tmp_path)
    with _capture(tmp_path) as captured:
        snapshot_root = captured.bundle_root

    assert not snapshot_root.exists()


def test_fingerprint_accepts_metadata_without_filesystem_access() -> None:
    entries = [
        FingerprintEntry("result.json", 3, hashlib.sha256(b"abc").hexdigest()),
        FingerprintEntry("manifest.json", 2, hashlib.sha256(b"{} ").hexdigest()),
    ]

    first = calculate_bundle_fingerprint(entries)
    second = calculate_bundle_fingerprint(reversed(entries))

    assert first == second
