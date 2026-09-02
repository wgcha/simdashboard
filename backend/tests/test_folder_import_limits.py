from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from app.config import ImportBundleLimits
from app.folder_import import FolderImportError, scan_folder


pytestmark = pytest.mark.unit


def _limits(
    *,
    manifest: int = 1_000_000,
    mappings: int = 16,
    structured: int = 1_000_000,
    file_bytes: int = 1_000_000,
    scalars: int = 100,
    curves: int = 100,
    points: int = 100,
) -> ImportBundleLimits:
    """Keep byte ceilings out of tests that exercise parser record ceilings."""
    return ImportBundleLimits(
        max_manifest_bytes=manifest,
        max_mapping_count=mappings,
        max_file_bytes=file_bytes,
        max_total_bytes=2_000_000,
        max_structured_bytes=structured,
        max_scalar_records=scalars,
        max_curves=curves,
        max_curve_points=points,
    )


def _manifest(root: Path, mappings: list[dict[str, object]]) -> None:
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_id": "folder-limit-test",
                "version": 1,
                "mappings": mappings,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _scalar_file(root: Path, name: str, count: int) -> None:
    (root / name).write_text(
        json.dumps(
            [
                {
                    "variable_key": f"scalar_{index}",
                    "data_type": "FLOAT",
                    "value": index,
                }
                for index in range(count)
            ],
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _curve_file(root: Path, name: str, count: int) -> None:
    with (root / name).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("x", "y"))
        writer.writerows((index, index * 2) for index in range(count))


def _scalar_mapping(name: str) -> dict[str, object]:
    return {"kind": "typed_scalars", "path": name}


def _curve_mapping(name: str, key: str) -> dict[str, object]:
    return {
        "kind": "curve_csv",
        "path": name,
        "variable_key": key,
        "x_column": "x",
        "y_column": "y",
    }


def _assert_limit(root: Path, limits: ImportBundleLimits, code: str) -> None:
    with pytest.raises(FolderImportError) as error:
        scan_folder(root, limits=limits)
    assert error.value.code == code


def test_structured_file_size_accepts_exact_boundary_and_rejects_one_byte_over(tmp_path: Path) -> None:
    _scalar_file(tmp_path, "summary.json", 1)
    _manifest(tmp_path, [_scalar_mapping("summary.json")])
    size = (tmp_path / "summary.json").stat().st_size

    assert scan_folder(tmp_path, limits=_limits(structured=size))["scalars"]
    _assert_limit(tmp_path, _limits(structured=size - 1), "IMPORT_STRUCTURED_FILE_SIZE_LIMIT")


def test_structured_json_over_limit_is_rejected_before_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "summary.json").write_text("[]", encoding="utf-8")
    _manifest(tmp_path, [_scalar_mapping("summary.json")])
    original_read_text = Path.read_text

    def reject_structured_read(path: Path, *args: object, **kwargs: object) -> str:
        if path.name == "summary.json":
            raise AssertionError("over-limit JSON must be rejected before read_text")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_structured_read)

    _assert_limit(tmp_path, _limits(structured=1), "IMPORT_STRUCTURED_FILE_SIZE_LIMIT")


def test_structured_json_reader_rechecks_limit_after_stat_growth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "summary.json").write_bytes(b"[        ]")
    _manifest(tmp_path, [_scalar_mapping("summary.json")])

    # Simulate a source that grew after the initial stat-based preflight. The
    # bounded reader must still stop at limit + 1 bytes.
    monkeypatch.setattr("app.folder_import._require_file_size", lambda *args, **kwargs: None)

    _assert_limit(tmp_path, _limits(structured=2), "IMPORT_STRUCTURED_FILE_SIZE_LIMIT")


def test_typed_scalars_do_not_use_stdlib_json_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scalar_file(tmp_path, "summary.json", 1)
    manifest = {"schema_id": "folder-limit-test", "version": 1, "mappings": [_scalar_mapping("summary.json")]}
    _manifest(tmp_path, manifest["mappings"])

    def reject_full_materialization(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("typed scalars must not call json.loads")

    monkeypatch.setattr("app.folder_import.load_manifest", lambda *args, **kwargs: SimpleNamespace(data=manifest))
    monkeypatch.setattr(json, "loads", reject_full_materialization)

    assert scan_folder(tmp_path, limits=_limits())["scalars"]


def test_scalar_record_cap_stops_stream_at_cap_plus_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "summary.json").write_text("[]", encoding="utf-8")
    _manifest(tmp_path, [_scalar_mapping("summary.json")])
    yielded = 0
    closed = False

    def dense_scalar_stream(*args: object, **kwargs: object) -> Iterator[dict[str, Any]]:
        del args, kwargs
        nonlocal closed, yielded
        try:
            for index in range(3):
                yielded += 1
                yield {"variable_key": f"scalar_{index}", "data_type": "FLOAT", "value": index}
            raise AssertionError("record cap must stop before a fourth streamed item")
        finally:
            closed = True

    monkeypatch.setattr("app.folder_import.iter_json_array_items", dense_scalar_stream)

    _assert_limit(tmp_path, _limits(scalars=2), "IMPORT_SCALAR_RECORD_LIMIT")
    assert yielded == 3
    assert closed


def test_scalar_item_complexity_limit_rejects_nested_padding_before_item_validation(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text(
        json.dumps([{"padding": [{} for _index in range(30)]}], separators=(",", ":")),
        encoding="utf-8",
    )
    _manifest(tmp_path, [_scalar_mapping("summary.json")])

    with pytest.raises(FolderImportError) as error:
        scan_folder(tmp_path, limits=_limits())

    assert error.value.code == "IMPORT_SCALAR_ITEM_COMPLEXITY_LIMIT"
    assert str(error.value) == "scalar 결과 항목의 복잡도가 허용 한도를 초과했습니다."


def test_normal_seven_field_scalar_remains_importable_with_stream_checksum(tmp_path: Path) -> None:
    scalar = {
        "variable_key": "peak_stress",
        "display_name": "Peak stress",
        "data_type": "FLOAT",
        "value": 42.5,
        "unit": "MPa",
        "threshold": 50.0,
        "result_group": "STRESS",
    }
    source = tmp_path / "summary.json"
    source.write_text(json.dumps([scalar], separators=(",", ":")), encoding="utf-8")
    _manifest(tmp_path, [_scalar_mapping(source.name)])

    parsed = scan_folder(tmp_path, limits=_limits())

    assert parsed["scalars"][0]["variable_key"] == "peak_stress"
    assert parsed["scalars"][0]["source_checksum"] == hashlib.sha256(source.read_bytes()).hexdigest()


def test_media_file_size_accepts_exact_boundary_and_rejects_one_byte_over(tmp_path: Path) -> None:
    media = tmp_path / "preview.bin"
    media.write_bytes(b"data")
    _manifest(
        tmp_path,
        [{
            "kind": "media",
            "path": media.name,
            "variable_key": "preview",
            "asset_type": "IMAGE",
            "mime_type": "image/png",
        }],
    )

    assert scan_folder(tmp_path, limits=_limits(file_bytes=media.stat().st_size))["media"]
    with pytest.raises(FolderImportError) as error:
        scan_folder(tmp_path, limits=_limits(file_bytes=media.stat().st_size - 1))

    assert error.value.code == "IMPORT_MEDIA_FILE_SIZE_LIMIT"
    assert str(error.value) == "미디어 파일 크기가 허용 한도를 초과했습니다."


def test_invalid_structured_json_is_categorized(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text("{not-json", encoding="utf-8")
    _manifest(tmp_path, [_scalar_mapping("summary.json")])

    with pytest.raises(FolderImportError) as error:
        scan_folder(tmp_path, limits=_limits())

    assert error.value.code == "IMPORT_STRUCTURED_JSON_INVALID"


def test_scalar_non_array_and_trailing_json_keep_distinct_import_codes(tmp_path: Path) -> None:
    summary = tmp_path / "summary.json"
    _manifest(tmp_path, [_scalar_mapping(summary.name)])

    summary.write_text('{"not": "an array"}', encoding="utf-8")
    _assert_limit(tmp_path, _limits(), "IMPORT_SCALAR_ARRAY_INVALID")

    summary.write_text("[] trailing", encoding="utf-8")
    _assert_limit(tmp_path, _limits(), "IMPORT_STRUCTURED_JSON_INVALID")


def test_scalar_element_must_be_an_object(tmp_path: Path) -> None:
    (tmp_path / "summary.json").write_text("[1]", encoding="utf-8")
    _manifest(tmp_path, [_scalar_mapping("summary.json")])

    with pytest.raises(FolderImportError) as error:
        scan_folder(tmp_path, limits=_limits())

    assert error.value.code == "IMPORT_SCALAR_ITEM_INVALID"


def test_huge_variable_key_is_not_echoed_in_controlled_error(tmp_path: Path) -> None:
    huge_key = "variable_" + ("x" * 10_000)
    (tmp_path / "summary.json").write_text(
        json.dumps([{"variable_key": huge_key, "data_type": "INTEGER", "value": 1.5}]),
        encoding="utf-8",
    )
    _manifest(tmp_path, [_scalar_mapping("summary.json")])

    with pytest.raises(FolderImportError) as error:
        scan_folder(tmp_path, limits=_limits())

    message = str(error.value)
    assert error.value.code == "IMPORT_INTEGER_VALUE_INVALID"
    assert len(message) <= 500
    assert huge_key not in message


def test_manifest_size_accepts_exact_boundary_and_rejects_one_byte_over(tmp_path: Path) -> None:
    _scalar_file(tmp_path, "summary.json", 1)
    _manifest(tmp_path, [_scalar_mapping("summary.json")])
    size = (tmp_path / "manifest.json").stat().st_size

    assert scan_folder(tmp_path, limits=_limits(manifest=size))["scalars"]
    _assert_limit(tmp_path, _limits(manifest=size - 1), "IMPORT_MANIFEST_SIZE_LIMIT")


def test_mapping_count_accepts_exact_boundary_and_rejects_one_over(tmp_path: Path) -> None:
    mappings = []
    for index in range(2):
        name = f"summary-{index}.json"
        _scalar_file(tmp_path, name, 1)
        mappings.append(_scalar_mapping(name))
    _manifest(tmp_path, mappings)

    assert len(scan_folder(tmp_path, limits=_limits(mappings=2))["scalars"]) == 2
    _assert_limit(tmp_path, _limits(mappings=1), "IMPORT_MAPPING_COUNT_LIMIT")


def test_scalar_record_count_is_cumulative_across_structured_mappings(tmp_path: Path) -> None:
    _scalar_file(tmp_path, "summary-a.json", 2)
    _scalar_file(tmp_path, "summary-b.json", 1)
    _manifest(tmp_path, [_scalar_mapping("summary-a.json"), _scalar_mapping("summary-b.json")])

    assert len(scan_folder(tmp_path, limits=_limits(scalars=3))["scalars"]) == 3
    _assert_limit(tmp_path, _limits(scalars=2), "IMPORT_SCALAR_RECORD_LIMIT")


def test_typed_scalar_mapping_uses_one_bounded_stream_checksum_for_all_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _scalar_file(tmp_path, "summary-a.json", 2)
    _scalar_file(tmp_path, "summary-b.json", 2)
    _manifest(tmp_path, [_scalar_mapping("summary-a.json"), _scalar_mapping("summary-b.json")])
    def reject_unbounded_checksum(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise AssertionError("typed scalars must use the bounded stream checksum")

    monkeypatch.setattr("app.folder_import._checksum", reject_unbounded_checksum)

    parsed = scan_folder(tmp_path, limits=_limits(scalars=4))

    checksum_a = hashlib.sha256((tmp_path / "summary-a.json").read_bytes()).hexdigest()
    checksum_b = hashlib.sha256((tmp_path / "summary-b.json").read_bytes()).hexdigest()
    assert [item["source_checksum"] for item in parsed["scalars"]] == [
        checksum_a,
        checksum_a,
        checksum_b,
        checksum_b,
    ]


def test_curve_count_accepts_exact_boundary_and_rejects_one_over(tmp_path: Path) -> None:
    mappings = []
    for index in range(2):
        name = f"curve-{index}.csv"
        _curve_file(tmp_path, name, 1)
        mappings.append(_curve_mapping(name, f"curve_{index}"))
    _manifest(tmp_path, mappings)

    assert len(scan_folder(tmp_path, limits=_limits(curves=2))["curves"]) == 2
    _assert_limit(tmp_path, _limits(curves=1), "IMPORT_CURVE_COUNT_LIMIT")


def test_curve_point_count_accepts_exact_boundary_and_rejects_one_over(tmp_path: Path) -> None:
    _curve_file(tmp_path, "curve.csv", 3)
    _manifest(tmp_path, [_curve_mapping("curve.csv", "curve")])

    assert len(scan_folder(tmp_path, limits=_limits(points=3))["curves"][0]["points"]) == 3
    _assert_limit(tmp_path, _limits(points=2), "IMPORT_CURVE_POINT_LIMIT")


def test_curve_point_count_is_cumulative_across_curves(tmp_path: Path) -> None:
    _curve_file(tmp_path, "curve-a.csv", 2)
    _curve_file(tmp_path, "curve-b.csv", 2)
    _manifest(
        tmp_path,
        [_curve_mapping("curve-a.csv", "curve_a"), _curve_mapping("curve-b.csv", "curve_b")],
    )

    assert len(scan_folder(tmp_path, limits=_limits(points=4))["curves"]) == 2
    _assert_limit(tmp_path, _limits(points=3), "IMPORT_CURVE_POINTS_TOTAL_LIMIT")
