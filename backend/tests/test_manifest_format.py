from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.folder_import import FolderImportError, scan_folder
from app.parsers.manifest_format import (
    ManifestFormat,
    ManifestFormatError,
    detect_manifest_format,
    load_manifest,
    resolve_manifest_path,
)
from app.parsers.manifest_parser import LegacyResultFilesManifestParser, ManifestParser


pytestmark = pytest.mark.unit


def _write_manifest(root: Path, value: object) -> Path:
    path = root / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _legacy_manifest() -> dict:
    return {
        "schema_version": "1.0",
        "project_id": "project-001",
        "request_id": "request-001",
        "load_case_id": "loadcase-001",
        "run_id": "run-001",
        "run_no": 1,
        "source_program": "legacy-solver",
        "overwrite_policy": "REJECT",
        "result_files": [
            {"type": "SCALAR_RESULTS", "path": "summary.json", "format": "JSON"},
        ],
    }


def test_detector_selects_each_disjoint_manifest_contract() -> None:
    assert detect_manifest_format({"mappings": []}) is ManifestFormat.CANONICAL_MAPPINGS
    assert detect_manifest_format({"result_files": []}) is ManifestFormat.LEGACY_RESULT_FILES


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"mappings": [], "result_files": []}, "MANIFEST_FORMAT_MIXED"),
        ({"schema_id": "missing-discriminator"}, "MANIFEST_FORMAT_UNKNOWN"),
        ([], "MANIFEST_ROOT_INVALID"),
    ],
)
def test_detector_fails_closed_for_mixed_unknown_or_non_object(payload: object, code: str) -> None:
    with pytest.raises(ManifestFormatError, match=code) as error:
        detect_manifest_format(payload)
    assert error.value.code == code


def test_canonical_loader_rejects_legacy_manifest(tmp_path: Path) -> None:
    root = tmp_path
    path = _write_manifest(root, _legacy_manifest())

    with pytest.raises(ManifestFormatError, match="MANIFEST_FORMAT_UNEXPECTED") as error:
        load_manifest(path, expected_format=ManifestFormat.CANONICAL_MAPPINGS)
    assert error.value.code == "MANIFEST_FORMAT_UNEXPECTED"


def test_canonical_folder_import_rejects_legacy_manifest(tmp_path: Path) -> None:
    root = tmp_path
    _write_manifest(root, _legacy_manifest())

    with pytest.raises(FolderImportError, match="MANIFEST_FORMAT_UNEXPECTED") as error:
        scan_folder(root)
    assert error.value.code == "MANIFEST_FORMAT_UNEXPECTED"


def test_legacy_parser_keeps_external_import_contract(tmp_path: Path) -> None:
    root = tmp_path
    _write_manifest(root, _legacy_manifest())

    parsed = LegacyResultFilesManifestParser(str(root)).parse("manifest.json")

    assert parsed.run_id == "run-001"
    assert parsed.result_files[0].path == "summary.json"
    assert ManifestParser is LegacyResultFilesManifestParser


def test_legacy_parser_rejects_canonical_manifest(tmp_path: Path) -> None:
    root = tmp_path
    path = _write_manifest(root, {"mappings": []})

    with pytest.raises(ManifestFormatError, match="MANIFEST_FORMAT_UNEXPECTED"):
        ManifestParser(str(root)).parse(path.name)


def test_legacy_parser_rejects_manifest_outside_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-manifest.json"
    _write_manifest(tmp_path, _legacy_manifest())
    outside.write_text(json.dumps(_legacy_manifest()), encoding="utf-8")

    with pytest.raises(ManifestFormatError, match="MANIFEST_PATH_OUTSIDE_ROOT"):
        ManifestParser(str(tmp_path)).parse(f"../{outside.name}")


def test_legacy_parser_rejects_manifest_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps(_legacy_manifest()), encoding="utf-8")
    link = tmp_path / "manifest.json"
    try:
        link.symlink_to(source)
    except OSError as exc:
        pytest.skip(f"symlink is unavailable: {exc}")

    with pytest.raises(ManifestFormatError, match="MANIFEST_SYMLINK_FORBIDDEN"):
        ManifestParser(str(tmp_path)).parse("manifest.json")


def test_manifest_path_resolver_keeps_root_containment_for_nested_paths(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    manifest = nested / "manifest.json"
    manifest.write_text(json.dumps(_legacy_manifest()), encoding="utf-8")

    assert resolve_manifest_path(tmp_path, "nested/manifest.json") == manifest


def test_manifest_path_resolver_does_not_expose_absolute_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as error:
        resolve_manifest_path(tmp_path, "missing/manifest.json")

    assert str(tmp_path) not in str(error.value)
