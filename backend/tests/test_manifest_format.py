from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.parsers.manifest_format as manifest_format_module
from app.folder_import import FolderImportError, scan_folder
from app.parsers.manifest_format import (
    ManifestFormat,
    ManifestFormatError,
    detect_manifest_format,
    load_manifest,
    resolve_manifest_path,
)
from app.parsers.manifest_parser import LegacyResultFilesManifestParser, ManifestParser
from app.parsers.streaming_json import StreamingJsonComplexityLimitError


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


def test_canonical_incremental_loader_stops_at_mapping_cap_and_keeps_context(tmp_path: Path) -> None:
    path = _write_manifest(
        tmp_path,
        {
            "schema_id": "streaming",
            "context": {
                "project_id": "project-streaming",
                "request_id": "request-streaming",
                "load_case_id": "loadcase-streaming",
            },
            "mappings": [
                {"kind": "typed_scalars", "path": "summary-a.json", "display_name": "A"},
                {"kind": "typed_scalars", "path": "summary-b.json", "display_name": "B"},
                {"kind": "typed_scalars", "path": "summary-c.json", "display_name": "C"},
            ],
        },
    )

    with pytest.raises(ManifestFormatError) as error:
        load_manifest(
            path,
            expected_format=ManifestFormat.CANONICAL_MAPPINGS,
            max_bytes=path.stat().st_size,
            max_mapping_count=2,
        )

    assert error.value.code == "MANIFEST_MAPPING_COUNT_LIMIT"
    assert error.value.data is not None
    assert error.value.data["context"]["load_case_id"] == "loadcase-streaming"
    assert len(error.value.data["mappings"]) == 2


def test_canonical_incremental_loader_does_not_consume_after_cap_plus_one_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "manifest.json"
    events = [
        ("", "start_map", None),
        ("", "map_key", "mappings"),
        ("mappings", "start_array", None),
        ("mappings.item", "start_map", None),
        ("mappings.item", "end_map", None),
        ("mappings.item", "start_map", None),
        ("mappings.item", "end_map", None),
        ("mappings.item", "start_map", None),
    ]

    def synthetic_events(_path: Path, *, max_bytes: int, max_events: int | None = None):
        del max_bytes, max_events
        for event in events:
            yield event
        raise AssertionError("the parser consumed events after the cap+1 item")

    monkeypatch.setattr(manifest_format_module, "iter_json_events", synthetic_events)

    with pytest.raises(ManifestFormatError) as error:
        load_manifest(
            path,
            expected_format=ManifestFormat.CANONICAL_MAPPINGS,
            max_bytes=1,
            max_mapping_count=2,
        )

    assert error.value.code == "MANIFEST_MAPPING_COUNT_LIMIT"


def test_canonical_incremental_loader_stops_dense_context_at_event_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    max_mapping_count = 2
    expected_budget = 256 + 64 * max_mapping_count
    observed: dict[str, int] = {}

    class DenseContextEvents:
        def __init__(self, max_events: int):
            self.max_events = max_events
            self.index = 0
            self.closed = False

        def __iter__(self):
            return self

        def __next__(self):
            if self.index >= self.max_events:
                raise StreamingJsonComplexityLimitError("manifest event budget exceeded")
            index = self.index
            self.index += 1
            if index == 0:
                return "", "start_map", None
            if index == 1:
                return "", "map_key", "context"
            if index == 2:
                return "context", "start_map", None
            if index % 2:
                return "context", "map_key", f"padding_{index}"
            return f"context.padding_{index}", "string", "x"

        def close(self):
            self.closed = True

    stream: DenseContextEvents | None = None

    def synthetic_events(_path: Path, *, max_bytes: int, max_events: int | None = None):
        del max_bytes
        nonlocal stream
        assert max_events == expected_budget
        observed["max_events"] = max_events or 0
        stream = DenseContextEvents(max_events or 0)
        return stream

    monkeypatch.setattr(manifest_format_module, "iter_json_events", synthetic_events)

    with pytest.raises(ManifestFormatError) as error:
        load_manifest(
            tmp_path / "manifest.json",
            expected_format=ManifestFormat.CANONICAL_MAPPINGS,
            max_bytes=1,
            max_mapping_count=max_mapping_count,
        )

    assert error.value.code == "MANIFEST_COMPLEXITY_LIMIT"
    assert observed["max_events"] == expected_budget
    assert stream is not None and stream.closed


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
