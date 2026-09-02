from __future__ import annotations

import errno
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.config import ImportBundleLimits
from app.services.canonical_result_bundle import READY_MARKER_NAME, parse_ready_marker_bytes
from app.services.bundle_snapshot import capture_published_bundle
from app.services.result_bundle_publisher import (
    ResultBundlePublishError,
    publish_result_bundle,
)


pytestmark = pytest.mark.unit


def _limits() -> ImportBundleLimits:
    return ImportBundleLimits(
        max_manifest_bytes=4096,
        max_mapping_count=8,
        max_file_bytes=4096,
        max_total_bytes=8192,
        max_structured_bytes=4096,
    )


def _source(root: Path, *, mapping_path: str = "summary.json") -> Path:
    source = root / "source"
    source.mkdir()
    payload = source / mapping_path
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(
        b'[{"variable_key":"peak","data_type":"FLOAT","value":1}]'
    )
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "schema_id": "test",
                "version": 1,
                "context": {
                    "project_id": "project-1",
                    "request_id": "request-1",
                    "load_case_id": "loadcase-1",
                },
                "mappings": [{"kind": "typed_scalars", "path": mapping_path}],
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return source


def test_publish_writes_marker_last_and_returns_relative_result(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    result = publish_result_bundle(
        source,
        target,
        "publication-1",
        clock=lambda: datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )

    assert result.bundle_path == "project-1/request-1/loadcase-1/publication-1"
    assert set(result.as_dict()) == {
        "bundle_path", "manifest_checksum", "bundle_fingerprint", "entry_count"
    }
    assert result.entry_count == 2
    final = target / result.bundle_path
    assert (final / "manifest.json").read_bytes() == (source / "manifest.json").read_bytes()
    assert (final / "summary.json").read_bytes() == b'[{"variable_key":"peak","data_type":"FLOAT","value":1}]'
    marker = parse_ready_marker_bytes((final / READY_MARKER_NAME).read_bytes())
    assert marker.bundle_path == result.bundle_path
    assert marker.manifest_checksum == result.manifest_checksum
    assert marker.bundle_fingerprint == result.bundle_fingerprint
    assert marker.entry_count == result.entry_count
    with capture_published_bundle(
        target,
        f"{result.bundle_path}/manifest.json",
        _limits(),
    ) as captured:
        assert captured.manifest_checksum == result.manifest_checksum
        assert captured.bundle_fingerprint == result.bundle_fingerprint
        assert len(captured.entries) == result.entry_count


def test_check_only_does_not_create_destination_state(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    before = sorted(path.relative_to(target) for path in target.rglob("*"))

    result = publish_result_bundle(source, target, "publication-1", check_only=True)

    assert result.bundle_path == "project-1/request-1/loadcase-1/publication-1"
    assert sorted(path.relative_to(target) for path in target.rglob("*")) == before


def test_existing_final_and_rename_failures_leave_final_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    first = publish_result_bundle(source, target, "publication-1")
    final = target / first.bundle_path

    with pytest.raises(ResultBundlePublishError) as error:
        publish_result_bundle(source, target, "publication-1")
    assert error.value.code == "RESULT_BUNDLE_FINAL_EXISTS"
    assert final.is_dir()

    monkeypatch.setattr(
        "app.services.result_bundle_publisher._rename_noreplace",
        lambda *_args: (_ for _ in ()).throw(ResultBundlePublishError("RESULT_BUNDLE_RENAME_UNSUPPORTED")),
    )
    with pytest.raises(ResultBundlePublishError) as error:
        publish_result_bundle(source, target, "publication-2")
    assert error.value.code == "RESULT_BUNDLE_RENAME_UNSUPPORTED"
    assert not (target / "project-1" / "request-1" / "loadcase-1" / "publication-2").exists()
    assert not list((target / "project-1" / "request-1" / "loadcase-1").glob(".simdashboard-staging-publication-2-*"))


def test_unsafe_source_and_publication_id_fail_closed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    with pytest.raises(ResultBundlePublishError) as error:
        publish_result_bundle(source, target, "../escape", check_only=True)
    assert "INVALID" in error.value.code


def test_concurrent_same_publication_has_exactly_one_winner(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "published"
    target.mkdir()

    def publish() -> str:
        try:
            publish_result_bundle(source, target, "publication-race")
            return "success"
        except ResultBundlePublishError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _item: publish(), (1, 2)))
    assert outcomes.count("success") == 1
    assert outcomes.count("RESULT_BUNDLE_FINAL_EXISTS") == 1


@pytest.mark.parametrize("failure", ["payload", "marker"])
def test_pre_rename_failure_cleans_stage_and_leaves_no_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    if failure == "payload":
        monkeypatch.setattr(
            "app.services.result_bundle_publisher._copy_snapshot_file",
            lambda *_args: (_ for _ in ()).throw(ResultBundlePublishError("INJECTED_PAYLOAD_FAILURE")),
        )
    else:
        monkeypatch.setattr(
            "app.services.result_bundle_publisher._write_marker",
            lambda *_args: (_ for _ in ()).throw(ResultBundlePublishError("INJECTED_MARKER_FAILURE")),
        )
    with pytest.raises(ResultBundlePublishError):
        publish_result_bundle(source, target, "publication-failure")
    parent = target / "project-1" / "request-1" / "loadcase-1"
    assert not (parent / "publication-failure").exists()
    assert not list(parent.glob(".simdashboard-staging-publication-failure-*"))


def test_stale_unrelated_stage_is_untouched(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    stale = target / "project-1" / "request-1" / "loadcase-1" / ".simdashboard-staging-old-uuid"
    stale.mkdir(parents=True)
    (stale / "sentinel").write_text("keep", encoding="utf-8")
    publish_result_bundle(source, target, "publication-new")
    assert (stale / "sentinel").read_text(encoding="utf-8") == "keep"


def test_exdev_rename_error_is_stable_and_cleans_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    monkeypatch.setattr(
        "app.services.result_bundle_publisher._rename_noreplace",
        lambda *_args: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross-device")),
    )
    with pytest.raises(ResultBundlePublishError) as error:
        publish_result_bundle(source, target, "publication-exdev")
    assert error.value.code == "RESULT_BUNDLE_RENAME_CROSS_DEVICE"


def test_source_symlink_is_rejected_and_nested_mapping_is_copied(tmp_path: Path) -> None:
    source = _source(tmp_path, mapping_path="nested/summary.json")
    target = tmp_path / "published"
    target.mkdir()
    result = publish_result_bundle(source, target, "publication-nested")
    assert (target / result.bundle_path / "nested" / "summary.json").is_file()

    link = tmp_path / "source-link"
    try:
        link.symlink_to(source, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    with pytest.raises(ResultBundlePublishError) as error:
        publish_result_bundle(link, target, "publication-link", check_only=True)
    assert error.value.code == "RESULT_BUNDLE_SOURCE_UNSAFE"


def test_source_ready_marker_is_not_copied_or_fingerprinted(tmp_path: Path) -> None:
    source = _source(tmp_path)
    (source / READY_MARKER_NAME).write_bytes(b"source marker must not publish")
    target = tmp_path / "published"
    target.mkdir()
    result = publish_result_bundle(source, target, "publication-marker")
    final = target / result.bundle_path
    assert (final / READY_MARKER_NAME).is_file()
    assert (final / READY_MARKER_NAME).read_bytes() != b"source marker must not publish"
    assert result.entry_count == 2


def test_partial_payload_write_is_completed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    real_write = os.write
    state = {"calls": 0}

    def short_write(fd: int, data: bytes) -> int:
        if len(data) > 1 and state["calls"] < 2:
            state["calls"] += 1
            return real_write(fd, data[:1])
        return real_write(fd, data)

    monkeypatch.setattr("app.services.result_bundle_publisher.os.write", short_write)
    result = publish_result_bundle(source, target, "publication-short-write")
    assert (target / result.bundle_path / "summary.json").read_bytes() == (source / "summary.json").read_bytes()


def test_nested_containing_directory_is_fsynced_after_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source(tmp_path, mapping_path="nested/summary.json")
    target = tmp_path / "published"
    target.mkdir()
    real_fsync = os.fsync
    fsync_events: list[str] = []

    def record_fsync(fd: int) -> None:
        try:
            fsync_events.append(os.readlink(f"/proc/self/fd/{fd}"))
        except OSError:
            fsync_events.append("")
        real_fsync(fd)

    monkeypatch.setattr("app.services.result_bundle_publisher.os.fsync", record_fsync)
    publish_result_bundle(source, target, "publication-nested-fsync")
    payload_index = next(index for index, path in enumerate(fsync_events) if path.endswith("/nested/summary.json"))
    containing_dir_index = next(
        index for index, path in enumerate(fsync_events[payload_index + 1 :], payload_index + 1)
        if path.endswith("/nested")
    )
    assert containing_dir_index > payload_index


def test_zero_length_write_fails_closed_and_cleans_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    target = tmp_path / "published"
    target.mkdir()
    real_write = os.write
    state = {"first": True}

    def zero_write(fd: int, data: bytes) -> int:
        if state["first"]:
            state["first"] = False
            return 0
        return real_write(fd, data)

    monkeypatch.setattr("app.services.result_bundle_publisher.os.write", zero_write)
    with pytest.raises(ResultBundlePublishError) as error:
        publish_result_bundle(source, target, "publication-zero-write")
    assert error.value.code == "RESULT_BUNDLE_PAYLOAD_WRITE_FAILED"
    parent = target / "project-1" / "request-1" / "loadcase-1"
    assert not (parent / "publication-zero-write").exists()
    assert not list(parent.glob(".simdashboard-staging-publication-zero-write-*"))


@pytest.mark.parametrize("layout", ["same", "source-inside-root", "root-inside-source"])
def test_overlapping_source_and_import_roots_fail_closed(tmp_path: Path, layout: str) -> None:
    source = _source(tmp_path)
    if layout == "same":
        root = source
    elif layout == "source-inside-root":
        root = tmp_path / "container"
        root.mkdir()
        source = root / "source"
        source.mkdir()
        (source / "summary.json").write_bytes(
            b'[{"variable_key":"peak","data_type":"FLOAT","value":1}]'
        )
        (source / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_id": "test",
                    "version": 1,
                    "context": {
                        "project_id": "project-1",
                        "request_id": "request-1",
                        "load_case_id": "loadcase-1",
                    },
                    "mappings": [{"kind": "typed_scalars", "path": "summary.json"}],
                }
            ),
            encoding="utf-8",
        )
    else:
        root = source / "import-root"
        root.mkdir()
    with pytest.raises(ResultBundlePublishError) as error:
        publish_result_bundle(source, root, "publication-overlap", check_only=True)
    assert error.value.code == "RESULT_BUNDLE_ROOTS_OVERLAP"
