"""Versioned-folder importer used by the example and later upload/agent APIs."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any

from .config import ImportBundleLimits, import_bundle_limits
from .parsers.manifest_format import ManifestFormat, ManifestFormatError, load_manifest
from .parsers.streaming_json import (
    StreamingJsonComplexityLimitError,
    StreamingJsonError,
    StreamingJsonRootArrayError,
    StreamingJsonSizeLimitError,
    StreamingJsonSourceError,
    iter_json_array_items,
)


_MAX_TYPED_SCALAR_ITEM_EVENTS = 64


class FolderImportError(ValueError):
    def __init__(self, message: str, *, code: str | None = None):
        self.code = code
        text = str(message)
        super().__init__(text if len(text) <= 500 else f"{text[:497]}...")


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if root.resolve() not in candidate.parents or not candidate.is_file():
        raise FolderImportError(
            "가져오기 폴더에 필요한 결과 파일이 없습니다.",
            code="IMPORT_SOURCE_FILE_INVALID",
        )
    return candidate


def _number(value: Any) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise FolderImportError("결과 값은 숫자여야 합니다.", code="IMPORT_NUMBER_INVALID") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise FolderImportError("결과 값은 유한한 숫자여야 합니다.", code="IMPORT_NUMBER_NONFINITE")
    return value


def _require_file_size(path: Path, limit: int, *, code: str, message: str) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FolderImportError(message, code=code) from exc
    if size > limit:
        raise FolderImportError(message, code=code)


def scan_folder(root: Path, *, limits: ImportBundleLimits | None = None) -> dict[str, Any]:
    """Read a manifest and return validated, database-neutral typed records."""
    limits = import_bundle_limits() if limits is None else limits
    manifest_path = _safe_file(root, "manifest.json")
    _require_file_size(
        manifest_path,
        limits.max_manifest_bytes,
        code="IMPORT_MANIFEST_SIZE_LIMIT",
        message="manifest.json 크기가 허용 한도를 초과했습니다.",
    )
    try:
        manifest = load_manifest(
            manifest_path,
            expected_format=ManifestFormat.CANONICAL_MAPPINGS,
            max_bytes=limits.max_manifest_bytes,
            max_mapping_count=limits.max_mapping_count,
        ).data
    except ManifestFormatError as exc:
        code = {
            "MANIFEST_COMPLEXITY_LIMIT": "IMPORT_MANIFEST_COMPLEXITY_LIMIT",
            "MANIFEST_MAPPING_COUNT_LIMIT": "IMPORT_MAPPING_COUNT_LIMIT",
            "MANIFEST_SIZE_LIMIT": "IMPORT_MANIFEST_SIZE_LIMIT",
        }.get(exc.code, exc.code)
        raise FolderImportError(str(exc), code=code) from exc
    if not isinstance(manifest.get("mappings"), list):
        raise FolderImportError("manifest.json에는 mappings 배열이 필요합니다.", code="IMPORT_MAPPINGS_INVALID")
    if len(manifest["mappings"]) > limits.max_mapping_count:
        raise FolderImportError(
            "manifest mapping 개수가 허용 한도를 초과했습니다.",
            code="IMPORT_MAPPING_COUNT_LIMIT",
        )

    scalars: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    media: list[dict[str, Any]] = []
    curve_points_total = 0
    for mapping in manifest["mappings"]:
        if not isinstance(mapping, dict):
            raise FolderImportError("mappings 항목은 객체여야 합니다.", code="IMPORT_MAPPING_INVALID")
        kind = mapping.get("kind")
        source = _safe_file(root, str(mapping.get("path", "")))
        if kind == "typed_scalars":
            _require_file_size(
                source,
                limits.max_structured_bytes,
                code="IMPORT_STRUCTURED_FILE_SIZE_LIMIT",
                message="구조화 결과 파일 크기가 허용 한도를 초과했습니다.",
            )
            mapping_scalars: list[dict[str, Any]] = []
            source_checksum: str | None = None

            def set_source_checksum(checksum: str) -> None:
                nonlocal source_checksum
                source_checksum = checksum

            scalar_items = None
            try:
                scalar_items = iter_json_array_items(
                    source,
                    max_bytes=limits.max_structured_bytes,
                    max_item_events=_MAX_TYPED_SCALAR_ITEM_EVENTS,
                    on_complete=set_source_checksum,
                )
                for item in scalar_items:
                    if not isinstance(item, dict):
                        raise FolderImportError(
                            "scalar 결과 항목은 객체여야 합니다.",
                            code="IMPORT_SCALAR_ITEM_INVALID",
                        )
                    if len(scalars) + len(mapping_scalars) >= limits.max_scalar_records:
                        raise FolderImportError(
                            "scalar 결과 레코드 수가 허용 한도를 초과했습니다.",
                            code="IMPORT_SCALAR_RECORD_LIMIT",
                        )
                    data_type = str(item.get("data_type", ""))
                    key = str(item.get("variable_key", "")).strip()
                    if not key or data_type not in {"FLOAT", "INTEGER", "TEXT", "VERDICT", "STATUS", "BOOLEAN"}:
                        raise FolderImportError(
                            "scalar 결과에 유효하지 않은 변수 정의가 있습니다.",
                            code="IMPORT_SCALAR_DEFINITION_INVALID",
                        )
                    value = item.get("value")
                    if data_type == "FLOAT":
                        value = _number(value)
                    elif data_type == "INTEGER":
                        number = _number(value)
                        if not number.is_integer():
                            raise FolderImportError(
                                "결과 값은 정수여야 합니다.",
                                code="IMPORT_INTEGER_VALUE_INVALID",
                            )
                        value = int(number)
                    else:
                        value = str(value)
                    mapping_scalars.append({
                        "variable_key": key, "display_name": str(item.get("display_name") or key),
                        "data_type": data_type, "value": value, "unit": str(item.get("unit") or ""),
                        "threshold": item.get("threshold"), "result_group": str(item.get("result_group") or "CUSTOM"),
                        "source_file": str(source.relative_to(root)).replace("\\", "/"), "source_checksum": None,
                    })
                if source_checksum is None:
                    raise FolderImportError(
                        "구조화 결과 JSON을 읽을 수 없습니다.",
                        code="IMPORT_STRUCTURED_JSON_INVALID",
                    )
                for scalar in mapping_scalars:
                    scalar["source_checksum"] = source_checksum
                scalars.extend(mapping_scalars)
            except StreamingJsonSizeLimitError as exc:
                raise FolderImportError(
                    "구조화 결과 파일 크기가 허용 한도를 초과했습니다.",
                    code="IMPORT_STRUCTURED_FILE_SIZE_LIMIT",
                ) from exc
            except StreamingJsonRootArrayError as exc:
                raise FolderImportError("scalar 결과 파일은 배열이어야 합니다.", code="IMPORT_SCALAR_ARRAY_INVALID") from exc
            except StreamingJsonComplexityLimitError as exc:
                raise FolderImportError(
                    "scalar 결과 항목의 복잡도가 허용 한도를 초과했습니다.",
                    code="IMPORT_SCALAR_ITEM_COMPLEXITY_LIMIT",
                ) from exc
            except StreamingJsonSourceError as exc:
                raise FolderImportError(
                    "구조화 결과 파일을 읽을 수 없습니다.",
                    code="IMPORT_STRUCTURED_FILE_INVALID",
                ) from exc
            except StreamingJsonError as exc:
                raise FolderImportError(
                    "구조화 결과 JSON을 읽을 수 없습니다.",
                    code="IMPORT_STRUCTURED_JSON_INVALID",
                ) from exc
            finally:
                # A record-cap failure exits the for-loop before the source is
                # exhausted.  Explicitly close the generator so its bounded
                # reader and source descriptor are released immediately.
                if scalar_items is not None:
                    scalar_items.close()
        elif kind == "curve_csv":
            if len(curves) >= limits.max_curves:
                raise FolderImportError(
                    "curve 결과 수가 허용 한도를 초과했습니다.",
                    code="IMPORT_CURVE_COUNT_LIMIT",
                )
            _require_file_size(
                source,
                limits.max_structured_bytes,
                code="IMPORT_STRUCTURED_FILE_SIZE_LIMIT",
                message="구조화 결과 파일 크기가 허용 한도를 초과했습니다.",
            )
            x_column, y_column = str(mapping.get("x_column", "")), str(mapping.get("y_column", ""))
            points: list[dict[str, float]] = []
            with source.open("r", encoding="utf-8-sig", newline="") as source_stream:
                reader = csv.DictReader(source_stream)
                if not reader.fieldnames or x_column not in reader.fieldnames or y_column not in reader.fieldnames:
                    raise FolderImportError(
                        "curve 결과 파일에 필요한 열이 없습니다.",
                        code="IMPORT_CURVE_COLUMNS_INVALID",
                    )
                for row in reader:
                    if len(points) >= limits.max_curve_points:
                        raise FolderImportError(
                            "개별 curve point 수가 허용 한도를 초과했습니다.",
                            code="IMPORT_CURVE_POINT_LIMIT",
                        )
                    # ``max_curve_points`` is deliberately both the per-curve
                    # and cumulative bundle ceiling.  A bounded curve list
                    # alone would still allow aggregate parser memory growth.
                    if curve_points_total >= limits.max_curve_points:
                        raise FolderImportError(
                            "bundle 전체 curve point 수가 허용 한도를 초과했습니다.",
                            code="IMPORT_CURVE_POINTS_TOTAL_LIMIT",
                        )
                    try:
                        x_value = row[x_column]
                        y_value = row[y_column]
                    except KeyError as exc:
                        raise FolderImportError(
                            "curve 결과 행에 필요한 열이 없습니다.",
                            code="IMPORT_CURVE_COLUMNS_INVALID",
                        ) from exc
                    points.append({"x": _number(x_value), "y": _number(y_value)})
                    curve_points_total += 1
            if not points:
                raise FolderImportError("curve 결과에는 하나 이상의 point가 필요합니다.", code="IMPORT_CURVE_EMPTY")
            curves.append({
                "variable_key": str(mapping["variable_key"]), "display_name": str(mapping.get("display_name") or mapping["variable_key"]),
                "series_key": str(mapping.get("series_key") or "default"), "x_label": str(mapping.get("x_label") or "시간"),
                "x_unit": str(mapping.get("x_unit") or "ms"), "y_label": str(mapping.get("y_label") or "값"),
                "y_unit": str(mapping.get("y_unit") or ""), "result_group": str(mapping.get("result_group") or "CUSTOM"),
                "points": points, "source_file": str(source.relative_to(root)).replace("\\", "/"), "source_checksum": _checksum(source),
            })
        elif kind == "media":
            asset_type = str(mapping.get("asset_type", ""))
            if asset_type not in {"IMAGE", "VIDEO", "MODEL_3D"}:
                raise FolderImportError("허용되지 않는 미디어 유형입니다.", code="IMPORT_MEDIA_TYPE_INVALID")
            _require_file_size(
                source,
                limits.max_file_bytes,
                code="IMPORT_MEDIA_FILE_SIZE_LIMIT",
                message="미디어 파일 크기가 허용 한도를 초과했습니다.",
            )
            media.append({
                "variable_key": str(mapping["variable_key"]), "display_name": str(mapping.get("display_name") or source.stem),
                "asset_type": asset_type, "path": source, "mime_type": str(mapping["mime_type"]),
                "result_group": str(mapping.get("result_group") or "CUSTOM"), "source_file": str(source.relative_to(root)).replace("\\", "/"),
                "source_checksum": _checksum(source),
            })
        else:
            raise FolderImportError("지원하지 않는 결과 mapping 유형입니다.", code="IMPORT_MAPPING_KIND_UNSUPPORTED")

    if not scalars and not curves and not media:
        raise FolderImportError("가져올 결과가 없습니다.", code="IMPORT_BUNDLE_EMPTY")
    return {
        "schema_id": str(manifest.get("schema_id") or "unspecified"),
        "schema_version": int(manifest.get("version") or 1),
        "solver": str(manifest.get("solver") or "Folder Import"),
        "note": str(manifest.get("note") or ""),
        "context": manifest.get("context") if isinstance(manifest.get("context"), dict) else {},
        "scalars": scalars, "curves": curves, "media": media,
    }
