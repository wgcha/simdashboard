from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.config import ImportBundleLimits, import_readiness_policy
from app.services.bundle_snapshot import BundleSnapshotError, capture_bundle, capture_published_bundle
from app.services.canonical_result_bundle import (
    READY_MARKER_NAME,
    READY_MARKER_MAX_BYTES,
    build_ready_marker,
    parse_ready_marker_bytes,
    serialize_ready_marker,
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


def _published_bundle(root: Path) -> tuple[Path, str]:
    relative = "project/request/loadcase/publication/manifest.json"
    bundle = root / "project" / "request" / "loadcase" / "publication"
    bundle.mkdir(parents=True)
    (bundle / "summary.json").write_text(
        '[{"variable_key":"peak","data_type":"FLOAT","value":1}]', encoding="utf-8"
    )
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "schema_id": "readiness-test",
                "version": 1,
                "context": {
                    "project_id": "project",
                    "request_id": "request",
                    "load_case_id": "loadcase",
                },
                "mappings": [{"kind": "typed_scalars", "path": "summary.json"}],
            }
        ),
        encoding="utf-8",
    )
    with capture_bundle(root, relative, _limits()) as captured:
        marker = build_ready_marker(
            bundle_path="project/request/loadcase/publication",
            manifest_checksum=captured.manifest_checksum,
            bundle_fingerprint=captured.bundle_fingerprint,
            entry_count=len(captured.entries),
            published_at="2026-08-25T00:00:00Z",
        )
    (bundle / READY_MARKER_NAME).write_bytes(serialize_ready_marker(marker))
    return bundle, relative


def _marker_raw(bundle: Path) -> dict[str, object]:
    return json.loads((bundle / READY_MARKER_NAME).read_text(encoding="utf-8"))


def _write_marker_raw(bundle: Path, raw: object) -> None:
    (bundle / READY_MARKER_NAME).write_text(json.dumps(raw), encoding="utf-8")


def test_capture_published_bundle_accepts_valid_marker_and_excludes_it_from_fingerprint(tmp_path: Path) -> None:
    _bundle, relative = _published_bundle(tmp_path)
    with capture_published_bundle(tmp_path, relative, _limits()) as captured:
        assert len(captured.entries) == 2
        assert READY_MARKER_NAME not in {entry.relative_path for entry in captured.entries}


def test_capture_published_bundle_requires_marker(tmp_path: Path) -> None:
    bundle, relative = _published_bundle(tmp_path)
    (bundle / READY_MARKER_NAME).unlink()
    with pytest.raises(BundleSnapshotError, match="BUNDLE_READY_MARKER_MISSING") as error:
        capture_published_bundle(tmp_path, relative, _limits())
    assert error.value.code == "BUNDLE_READY_MARKER_MISSING"


def test_capture_paths_reserve_the_readiness_marker_from_payload_mappings(tmp_path: Path) -> None:
    bundle, relative = _published_bundle(tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["mappings"] = [{"kind": "typed_scalars", "path": READY_MARKER_NAME}]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    for capture in (capture_bundle, capture_published_bundle):
        with pytest.raises(BundleSnapshotError) as error:
            capture(tmp_path, relative, _limits())
        assert error.value.code == "BUNDLE_MAPPING_PATH_RESERVED"


def test_capture_published_bundle_requires_manifest_target_to_match_physical_path(tmp_path: Path) -> None:
    bundle, relative = _published_bundle(tmp_path)
    manifest_path = bundle / "manifest.json"
    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_manifest["context"]["project_id"] = "another-project"
    manifest_path.write_text(json.dumps(raw_manifest), encoding="utf-8")
    # The marker still binds the old manifest bytes, but strict path binding is
    # deliberately checked before any importer can create a Run.
    with pytest.raises(BundleSnapshotError) as error:
        capture_published_bundle(tmp_path, relative, _limits())
    assert error.value.code == "BUNDLE_READY_MARKER_PATH_MISMATCH"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda raw: raw.update({"unexpected": True}), "BUNDLE_READY_MARKER_UNKNOWN_FIELD"),
        (lambda raw: raw.pop("state"), "BUNDLE_READY_MARKER_MISSING_FIELD"),
        (lambda raw: raw.update({"version": "1"}), "BUNDLE_READY_MARKER_FIELD_TYPE_INVALID"),
        (lambda raw: raw.update({"schema_id": "wrong"}), "BUNDLE_READY_MARKER_SCHEMA_INVALID"),
        (lambda raw: raw.update({"version": 2}), "BUNDLE_READY_MARKER_VERSION_INVALID"),
        (lambda raw: raw.update({"state": "PENDING"}), "BUNDLE_READY_MARKER_STATE_INVALID"),
        (lambda raw: raw.update({"bundle_path": "project/request/loadcase/other"}), "BUNDLE_READY_MARKER_PATH_MISMATCH"),
        (lambda raw: raw.update({"manifest_checksum": "0" * 64}), "BUNDLE_READY_MARKER_MANIFEST_CHECKSUM_MISMATCH"),
        (lambda raw: raw.update({"bundle_fingerprint": "0" * 64}), "BUNDLE_READY_MARKER_BUNDLE_FINGERPRINT_MISMATCH"),
        (lambda raw: raw.update({"entry_count": 99}), "BUNDLE_READY_MARKER_ENTRY_COUNT_MISMATCH"),
    ],
)
def test_capture_published_bundle_fails_closed_for_marker_contract(
    tmp_path: Path, mutate: object, code: str
) -> None:
    bundle, relative = _published_bundle(tmp_path)
    raw = _marker_raw(bundle)
    mutate(raw)  # type: ignore[operator]
    _write_marker_raw(bundle, raw)
    with pytest.raises(BundleSnapshotError) as error:
        capture_published_bundle(tmp_path, relative, _limits())
    assert error.value.code == code


def test_marker_malformed_oversize_and_symlink_are_rejected(tmp_path: Path) -> None:
    bundle, relative = _published_bundle(tmp_path)
    (bundle / READY_MARKER_NAME).write_text("not json", encoding="utf-8")
    with pytest.raises(BundleSnapshotError) as error:
        capture_published_bundle(tmp_path, relative, _limits())
    assert error.value.code == "BUNDLE_READY_MARKER_MALFORMED"

    (bundle / READY_MARKER_NAME).write_bytes(b"x" * (READY_MARKER_MAX_BYTES + 1))
    with pytest.raises(BundleSnapshotError) as error:
        capture_published_bundle(tmp_path, relative, _limits())
    assert error.value.code == "BUNDLE_READY_MARKER_FILE_LIMIT"

    target = bundle / "marker-target.json"
    target.write_text("{}", encoding="utf-8")
    (bundle / READY_MARKER_NAME).unlink()
    try:
        (bundle / READY_MARKER_NAME).symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    with pytest.raises(BundleSnapshotError) as error:
        capture_published_bundle(tmp_path, relative, _limits())
    assert error.value.code == "BUNDLE_READY_MARKER_FILE_UNSAFE"


def test_marker_parser_rejects_non_utc_timestamp() -> None:
    marker = build_ready_marker(
        bundle_path="project/request/loadcase/publication",
        manifest_checksum="a" * 64,
        bundle_fingerprint="b" * 64,
        entry_count=2,
        published_at="2026-08-25T00:00:00Z",
    )
    raw = json.loads(serialize_ready_marker(marker))
    raw["published_at"] = "2026-08-25T00:00:00+09:00"
    with pytest.raises(ValueError) as error:
        parse_ready_marker_bytes(json.dumps(raw).encode())
    assert getattr(error.value, "code") == "BUNDLE_READY_MARKER_PUBLISHED_AT_INVALID"


@pytest.mark.parametrize(
    "published_at",
    [
        "2026-08-25T00:00:00+00:00",
        "2026-08-25T00:00:00.000Z",
        "2026-08-25 00:00:00Z",
        "2026-08-25",
    ],
)
def test_marker_timestamp_requires_canonical_utc_z_seconds(published_at: str) -> None:
    with pytest.raises(ValueError) as error:
        build_ready_marker(
            bundle_path="project/request/loadcase/publication",
            manifest_checksum="a" * 64,
            bundle_fingerprint="b" * 64,
            entry_count=2,
            published_at=published_at,
        )
    assert getattr(error.value, "code") == "BUNDLE_READY_MARKER_PUBLISHED_AT_INVALID"


def test_marker_parser_rejects_duplicate_fields_and_nonstandard_json_constants() -> None:
    marker = build_ready_marker(
        bundle_path="project/request/loadcase/publication",
        manifest_checksum="a" * 64,
        bundle_fingerprint="b" * 64,
        entry_count=2,
        published_at="2026-08-25T00:00:00Z",
    )
    raw = serialize_ready_marker(marker)
    duplicate = raw.replace(b'"state":"READY"', b'"state":"READY","state":"READY"')
    with pytest.raises(ValueError) as error:
        parse_ready_marker_bytes(duplicate)
    assert getattr(error.value, "code") == "BUNDLE_READY_MARKER_DUPLICATE_FIELD"

    nonstandard_constant = raw.replace(b'"entry_count":2', b'"entry_count":NaN')
    with pytest.raises(ValueError) as error:
        parse_ready_marker_bytes(nonstandard_constant)
    assert getattr(error.value, "code") == "BUNDLE_READY_MARKER_MALFORMED"


def test_marker_directory_is_rejected_as_an_unsafe_file(tmp_path: Path) -> None:
    bundle, relative = _published_bundle(tmp_path)
    (bundle / READY_MARKER_NAME).unlink()
    (bundle / READY_MARKER_NAME).mkdir()
    with pytest.raises(BundleSnapshotError) as error:
        capture_published_bundle(tmp_path, relative, _limits())
    assert error.value.code == "BUNDLE_READY_MARKER_FILE_UNSAFE"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO tests require POSIX os.mkfifo")
def test_marker_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    bundle, relative = _published_bundle(tmp_path)
    (bundle / READY_MARKER_NAME).unlink()
    try:
        os.mkfifo(bundle / READY_MARKER_NAME)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"FIFO unavailable on this filesystem: {exc}")
    with pytest.raises(BundleSnapshotError) as error:
        capture_published_bundle(tmp_path, relative, _limits())
    assert error.value.code == "BUNDLE_READY_MARKER_FILE_UNSAFE"


def test_import_readiness_policy_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIMDASH_IMPORT_READINESS_POLICY", raising=False)
    assert import_readiness_policy() == "legacy"
    monkeypatch.setenv("SIMDASH_IMPORT_READINESS_POLICY", "required")
    assert import_readiness_policy() == "required"
    monkeypatch.setenv("SIMDASH_IMPORT_READINESS_POLICY", "invalid")
    with pytest.raises(RuntimeError):
        import_readiness_policy()
