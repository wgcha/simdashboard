"""Versioned-folder importer used by the example and later upload/agent APIs."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from .parsers.manifest_format import ManifestFormat, ManifestFormatError, load_manifest


class FolderImportError(ValueError):
    def __init__(self, message: str, *, code: str | None = None):
        self.code = code
        super().__init__(message)


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_file(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if root.resolve() not in candidate.parents or not candidate.is_file():
        raise FolderImportError(f"가져오기 폴더에 필요한 파일이 없습니다: {relative}")
    return candidate


def _number(value: Any, field: str) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise FolderImportError(f"{field} 값은 숫자여야 합니다.") from exc
    if value != value or value in (float("inf"), float("-inf")):
        raise FolderImportError(f"{field} 값은 유한한 숫자여야 합니다.")
    return value


def scan_folder(root: Path) -> dict[str, Any]:
    """Read a manifest and return validated, database-neutral typed records."""
    manifest_path = _safe_file(root, "manifest.json")
    try:
        manifest = load_manifest(manifest_path, expected_format=ManifestFormat.CANONICAL_MAPPINGS).data
    except ManifestFormatError as exc:
        raise FolderImportError(str(exc), code=exc.code) from exc
    if not isinstance(manifest.get("mappings"), list):
        raise FolderImportError("manifest.json에는 mappings 배열이 필요합니다.")

    scalars: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    media: list[dict[str, Any]] = []
    for mapping in manifest["mappings"]:
        if not isinstance(mapping, dict):
            raise FolderImportError("mappings 항목은 객체여야 합니다.")
        kind = mapping.get("kind")
        source = _safe_file(root, str(mapping.get("path", "")))
        if kind == "typed_scalars":
            values = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(values, list):
                raise FolderImportError(f"{source.name}은 결과 배열이어야 합니다.")
            for item in values:
                data_type = str(item.get("data_type", ""))
                key = str(item.get("variable_key", "")).strip()
                if not key or data_type not in {"FLOAT", "INTEGER", "TEXT", "VERDICT", "STATUS", "BOOLEAN"}:
                    raise FolderImportError(f"{source.name}에 유효하지 않은 변수 정의가 있습니다.")
                value = item.get("value")
                if data_type == "FLOAT":
                    value = _number(value, key)
                elif data_type == "INTEGER":
                    number = _number(value, key)
                    if not number.is_integer():
                        raise FolderImportError(f"{key} 값은 정수여야 합니다.")
                    value = int(number)
                else:
                    value = str(value)
                scalars.append({
                    "variable_key": key, "display_name": str(item.get("display_name") or key),
                    "data_type": data_type, "value": value, "unit": str(item.get("unit") or ""),
                    "threshold": item.get("threshold"), "result_group": str(item.get("result_group") or "CUSTOM"),
                    "source_file": str(source.relative_to(root)).replace("\\", "/"), "source_checksum": _checksum(source),
                })
        elif kind == "curve_csv":
            x_column, y_column = str(mapping.get("x_column", "")), str(mapping.get("y_column", ""))
            reader = csv.DictReader(source.read_text(encoding="utf-8-sig").splitlines())
            if not reader.fieldnames or x_column not in reader.fieldnames or y_column not in reader.fieldnames:
                raise FolderImportError(f"{source.name}에 {x_column}, {y_column} 열이 필요합니다.")
            points = [{"x": _number(row[x_column], x_column), "y": _number(row[y_column], y_column)} for row in reader]
            if not points:
                raise FolderImportError(f"{source.name} 커브에는 하나 이상의 포인트가 필요합니다.")
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
                raise FolderImportError(f"{source.name}의 미디어 유형이 허용되지 않습니다.")
            media.append({
                "variable_key": str(mapping["variable_key"]), "display_name": str(mapping.get("display_name") or source.stem),
                "asset_type": asset_type, "path": source, "mime_type": str(mapping["mime_type"]),
                "result_group": str(mapping.get("result_group") or "CUSTOM"), "source_file": str(source.relative_to(root)).replace("\\", "/"),
                "source_checksum": _checksum(source),
            })
        else:
            raise FolderImportError(f"지원하지 않는 mapping kind: {kind}")

    if not scalars and not curves and not media:
        raise FolderImportError("가져올 결과가 없습니다.")
    return {
        "schema_id": str(manifest.get("schema_id") or "unspecified"),
        "schema_version": int(manifest.get("version") or 1),
        "solver": str(manifest.get("solver") or "Folder Import"),
        "note": str(manifest.get("note") or ""),
        "context": manifest.get("context") if isinstance(manifest.get("context"), dict) else {},
        "scalars": scalars, "curves": curves, "media": media,
    }
