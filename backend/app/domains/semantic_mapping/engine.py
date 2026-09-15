"""Bounded, deterministic recipe execution; no code evaluation or filesystem I/O."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
from collections import defaultdict
from pathlib import PurePath
from typing import Any

ENGINE_VERSION = "semantic-1"
MAX_BYTES = 5_000_000
MAX_ROWS = 50_000
MAX_FIELDS = 256
MAX_MAPPINGS = 64
MAX_WIDGETS = 32
MAX_OUTPUT_VALUES = 100_000
MAX_OUTPUT_BYTES = 8_000_000
_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")
_PATH = re.compile(r"(?:[\w\-]+|\[[0-9]+\])(?:\.[\w\-]+|\[[0-9]+\])*")
# (physical dimension, scale to base unit, offset to base unit)
_UNITS = {
    "": ("dimensionless", 1., 0.), "1": ("dimensionless", 1., 0.),
    "%": ("dimensionless", .01, 0.),
    "Pa": ("pressure", 1., 0.), "kPa": ("pressure", 1000., 0.),
    "MPa": ("pressure", 1e6, 0.), "GPa": ("pressure", 1e9, 0.),
    "m": ("length", 1., 0.), "mm": ("length", .001, 0.),
    "cm": ("length", .01, 0.), "um": ("length", 1e-6, 0.),
    "s": ("time", 1., 0.), "ms": ("time", .001, 0.), "us": ("time", 1e-6, 0.),
    "N": ("force", 1., 0.), "kN": ("force", 1000., 0.),
    "kg": ("mass", 1., 0.), "g": ("mass", .001, 0.),
    "Hz": ("frequency", 1., 0.), "kHz": ("frequency", 1000., 0.),
    "rad": ("angle", 1., 0.), "deg": ("angle", math.pi / 180., 0.),
    "K": ("temperature", 1., 0.), "°C": ("temperature", 1., 273.15),
}


class SemanticValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _fail(code: str, message: str):
    raise SemanticValidationError(code, message)


def _number(value: Any) -> float:
    if isinstance(value, bool):
        _fail("TYPE_MISMATCH", "참/거짓 값을 숫자로 변환할 수 없습니다.")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        _fail("TYPE_MISMATCH", f"숫자로 읽을 수 없는 값입니다: {str(value)[:60]}")
    if not math.isfinite(result):
        _fail("NONFINITE_VALUE", "NaN 또는 무한대는 결과로 등록할 수 없습니다.")
    return result


def convert_unit(value: Any, source: str, target: str) -> float:
    if not isinstance(source, str) or not isinstance(target, str):
        _fail("UNIT_MISMATCH", "단위는 문자열이어야 합니다.")
    value = _number(value)
    if source == target:
        return value
    if source not in _UNITS or target not in _UNITS or _UNITS[source][0] != _UNITS[target][0]:
        _fail("UNIT_MISMATCH", f"단위 변환을 지원하지 않습니다: {source or '(없음)'} → {target or '(없음)'}")
    _, scale, offset = _UNITS[source]
    _, target_scale, target_offset = _UNITS[target]
    return _number((value * scale + offset - target_offset) / target_scale)


def _definition(value: dict) -> dict:
    if not isinstance(value, dict):
        _fail("DEFINITION_INVALID", "정의는 객체여야 합니다.")
    definition = value.get("definition", value)
    if not isinstance(definition, dict):
        _fail("DEFINITION_INVALID", "정의는 객체여야 합니다.")
    return definition


def _items(items: list[dict]) -> dict[str, dict]:
    if not isinstance(items, list):
        _fail("ITEM_INVALID", "결과 항목 목록이 필요합니다.")
    result = {}
    for raw in items:
        item = dict(_definition(raw))
        item["id"] = raw.get("id", item.get("id"))
        if not isinstance(item.get("id"), str) or not item["id"] or item["id"] in result:
            _fail("ITEM_INVALID", "결과 항목 ID가 없거나 중복됩니다.")
        item.setdefault("key", str(item["id"]))
        item.setdefault("label", item.get("name", item["key"]))
        item.setdefault("kind", "scalar")
        item.setdefault("data_type", "FLOAT")
        item.setdefault("unit", "")
        item.setdefault("dimensions", [])
        if not isinstance(item["key"], str) or not _KEY.fullmatch(item["key"]):
            _fail("ITEM_INVALID", "결과 변수 키는 영문자로 시작하는 영문·숫자·._- 조합이어야 합니다.")
        if not isinstance(item["kind"], str) or item["kind"] not in {"scalar", "curve", "image", "video"}:
            _fail("ITEM_INVALID", "지원하지 않는 결과 항목 유형입니다.")
        if not isinstance(item["data_type"], str) or item["data_type"] not in {"FLOAT", "INTEGER", "TEXT", "BOOLEAN"}:
            _fail("ITEM_INVALID", "지원하지 않는 자료형입니다.")
        dims = item["dimensions"]
        if not isinstance(item["label"], str) or not item["label"].strip() or len(item["label"]) > 240 or not isinstance(item["unit"], str):
            _fail("ITEM_INVALID", "표시명과 단위를 올바르게 지정하세요.")
        if not isinstance(dims, list) or len(dims) > 8 or any(not isinstance(d, str) or not _KEY.fullmatch(d) for d in dims) or len(dims) != len(set(dims)):
            _fail("ITEM_INVALID", "차원 이름은 중복 없는 최대 8개 변수 키여야 합니다.")
        if any(existing["key"] == item["key"] for existing in result.values()):
            _fail("ITEM_INVALID", "서로 다른 항목에 같은 결과 변수 키를 사용할 수 없습니다.")
        result[item["id"]] = item
    return result


def validate_item(item: dict) -> dict:
    return next(iter(_items([item]).values()))


def _path(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 512 or not value:
        _fail("PATH_INVALID", "읽을 필드 경로를 지정하세요.")
    return value


def _get(row: Any, path: str) -> Any:
    if isinstance(row, dict) and path in row:
        return row[path]
    if not _PATH.fullmatch(path):
        return None
    current = row
    for token in re.findall(r"[\w\-]+|\[[0-9]+\]", path):
        if token.startswith("["):
            idx = int(token[1:-1])
            if not isinstance(current, list) or idx >= len(current):
                return None
            current = current[idx]
        elif isinstance(current, dict):
            current = current.get(token)
        else:
            return None
    return current


def validate_recipe(recipe: dict, items: list[dict]) -> dict:
    recipe = _definition(recipe)
    catalog = _items(items)
    if recipe.get("format") not in ("csv", "json"):
        _fail("FORMAT_UNSUPPORTED", "CSV 또는 JSON 읽기 형식을 선택하세요.")
    if recipe.get("encoding", "utf-8-sig") not in ("utf-8", "utf-8-sig", "cp949", "utf-16"):
        _fail("ENCODING_UNSUPPORTED", "지원하지 않는 인코딩입니다.")
    if recipe.get("delimiter", ",") not in (",", ";", "\t", "|"):
        _fail("DELIMITER_INVALID", "지원하지 않는 구분자입니다.")
    header = recipe.get("header_row", 1)
    if type(header) is not int or not 1 <= header <= 100:
        _fail("HEADER_INVALID", "헤더 행은 1~100 사이의 정수여야 합니다.")
    mappings = recipe.get("mappings")
    if not isinstance(mappings, list) or not 1 <= len(mappings) <= MAX_MAPPINGS:
        _fail("MAPPINGS_INVALID", "읽기 매핑은 1~64개여야 합니다.")
    required = recipe.get("required_fields", [])
    if not isinstance(required, list) or len(required) > MAX_FIELDS:
        _fail("FIELDS_INVALID", "필수 필드 목록이 올바르지 않습니다.")
    for field in required:
        _path(field)
    if not isinstance(recipe.get("records_path", ""), str) or len(recipe.get("records_path", "")) > 512:
        _fail("PATH_INVALID", "JSON 레코드 경로가 올바르지 않습니다.")
    for mapping in mappings:
        if not isinstance(mapping, dict):
            _fail("MAPPING_INVALID", "각 매핑은 객체여야 합니다.")
        reference = mapping.get("result_item_id")
        item = catalog.get(reference) if isinstance(reference, str) else None
        if item is None:
            _fail("ITEM_NOT_FOUND", "매핑의 결과 항목을 찾을 수 없습니다.")
        if item["kind"] in {"image", "video"}:
            _fail("FORMAT_UNSUPPORTED", "미디어는 안전한 미디어 등록 경로로 연결하세요.")
        _path(mapping.get("source"))
        if mapping.get("missing", "error") not in ("error", "skip"):
            _fail("MISSING_POLICY_INVALID", "결측 정책은 오류 또는 건너뛰기여야 합니다.")
        if mapping.get("aggregate", "none") not in ("none", "max", "min", "mean"):
            _fail("AGGREGATE_INVALID", "지원하지 않는 집계입니다.")
        dimensions = mapping.get("dimensions", {})
        if not isinstance(dimensions, dict) or set(dimensions) != set(item["dimensions"]):
            _fail("DIMENSIONS_INVALID", "항목에 정의한 모든 차원의 원본 필드를 연결하세요.")
        for path in dimensions.values():
            _path(path)
        if mapping.get("series_source"):
            _path(mapping["series_source"])
            if item["kind"] != "curve" or "series" in dimensions:
                _fail("DIMENSIONS_INVALID", "series 필드는 곡선 구분에만 사용하고 차원과 중복할 수 없습니다.")
        if item["kind"] == "curve":
            _path(mapping.get("x_source"))
            if item["data_type"] not in {"FLOAT", "INTEGER"} or mapping.get("aggregate", "none") != "none":
                _fail("TYPE_MISMATCH", "곡선에는 숫자 값과 집계하지 않는 매핑이 필요합니다.")
            convert_unit(0, mapping.get("x_unit", "s"), mapping.get("target_x_unit", mapping.get("x_unit", "s")))
        if item["data_type"] in {"FLOAT", "INTEGER"}:
            convert_unit(0, mapping.get("source_unit", item["unit"]), item["unit"])
        elif mapping.get("aggregate", "none") != "none":
            _fail("TYPE_MISMATCH", "숫자 항목만 집계할 수 있습니다.")
    return recipe


def _json_pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("DUPLICATE_FIELD", f"JSON 필드가 중복됩니다: {key[:60]}")
        result[key] = value
    return result


def _read(filename: str, content: bytes, recipe: dict) -> list[dict]:
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        _fail("FILE_SIZE_LIMIT", "빈 파일이거나 5 MB 파일 한도를 초과했습니다.")
    fmt = recipe["format"]
    if PurePath(filename).suffix.lower() != "." + fmt:
        _fail("FORMAT_MISMATCH", "파일 확장자가 레시피와 일치하지 않습니다.")
    try:
        source = content.decode(recipe.get("encoding", "utf-8-sig"))
    except (UnicodeError, LookupError):
        _fail("ENCODING_INVALID", "지정된 인코딩으로 파일을 읽을 수 없습니다.")
    if fmt == "csv":
        reader = csv.reader(io.StringIO(source, newline=""), delimiter=recipe.get("delimiter", ","), strict=True)
        try:
            for _ in range(recipe.get("header_row", 1) - 1):
                next(reader)
            fields = next(reader)
            if not fields or len(fields) > MAX_FIELDS or any(not f for f in fields) or len(set(fields)) != len(fields):
                _fail("FIELDS_INVALID", "헤더는 비어 있지 않은 고유 필드이며 최대 256개여야 합니다.")
            rows = []
            for line in reader:
                if not line:
                    continue
                if len(line) != len(fields):
                    _fail("ROW_INVALID", f"CSV {reader.line_num}행의 열 개수가 헤더와 다릅니다.")
                rows.append(dict(zip(fields, line)))
                if len(rows) > MAX_ROWS:
                    _fail("ROW_LIMIT", "최대 50,000개 레코드까지 읽을 수 있습니다.")
        except StopIteration:
            _fail("HEADER_INVALID", "지정한 헤더 행을 찾을 수 없습니다.")
        except csv.Error:
            _fail("CSV_INVALID", "올바르지 않은 CSV 형식입니다.")
    else:
        try:
            data = json.loads(source, object_pairs_hook=_json_pairs, parse_constant=lambda _: _fail("NONFINITE_VALUE", "JSON 숫자는 유한해야 합니다."))
        except (json.JSONDecodeError, RecursionError):
            _fail("JSON_INVALID", "올바르지 않거나 지나치게 중첩된 JSON입니다.")
        pending = [(data, 0)]
        while pending:
            entry, depth = pending.pop()
            if depth > 32:
                _fail("JSON_DEPTH_LIMIT", "JSON 중첩 깊이는 32까지 지원합니다.")
            if isinstance(entry, (dict, list)):
                pending.extend((v, depth + 1) for v in (entry.values() if isinstance(entry, dict) else entry))
        path = recipe.get("records_path", "")
        data = _get(data, path) if path else data
        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        if len(rows) > MAX_ROWS:
            _fail("ROW_LIMIT", "최대 50,000개 레코드까지 읽을 수 있습니다.")
        if any(not isinstance(row, dict) or len(row) > MAX_FIELDS for row in rows):
            _fail("ROW_INVALID", "JSON 레코드는 최대 256개 필드의 객체여야 합니다.")
    if not rows:
        _fail("NO_RECORDS", "읽을 레코드가 없습니다. 배열 경로 또는 헤더를 확인하세요.")
    return rows


def _fields(row: dict, prefix: str = "", depth: int = 0) -> list[str]:
    fields = []
    for key, value in row.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and depth < 5:
            fields.extend(_fields(value, path, depth + 1))
        else:
            fields.append(path)
    return fields[:MAX_FIELDS]


def inspect_sample(filename: str, content: bytes) -> dict:
    fmt = PurePath(filename).suffix.lower().lstrip(".")
    if fmt not in {"csv", "json"}:
        _fail("FORMAT_UNSUPPORTED", "CSV 또는 JSON 샘플을 선택하세요.")
    rows = _read(filename, content, {"format": fmt})
    return {"format": fmt, "fields": list(dict.fromkeys(f for row in rows[:20] for f in _fields(row)))[:MAX_FIELDS], "rows": rows[:20], "row_count": len(rows)}


def _dimension_key(dimensions: dict) -> str:
    return json.dumps(dimensions, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _typed(value: Any, item: dict, mapping: dict) -> Any:
    kind = item["data_type"]
    if kind in {"FLOAT", "INTEGER"}:
        number = convert_unit(value, mapping.get("source_unit", item["unit"]), item["unit"])
        if kind == "INTEGER" and not number.is_integer():
            _fail("TYPE_MISMATCH", "정수 결과에 소수 값이 있습니다.")
        return int(number) if kind == "INTEGER" else number
    if kind == "BOOLEAN":
        if isinstance(value, bool):
            return value
        token = str(value).strip().lower()
        if token not in {"true", "false", "1", "0"}:
            _fail("TYPE_MISMATCH", "참/거짓 값은 true, false, 1, 0만 허용합니다.")
        return token in {"true", "1"}
    if isinstance(value, (list, dict)):
        _fail("TYPE_MISMATCH", "객체나 배열은 텍스트 값으로 자동 변환하지 않습니다.")
    return str(value)


def preview_recipe(recipe: dict, items: list[dict], filename: str, content: bytes) -> dict:
    recipe = validate_recipe(recipe, items)
    catalog = _items(items)
    rows = _read(filename, content, recipe)
    for index, row in enumerate(rows, 1):
        if any(_get(row, p) is None for p in recipe.get("required_fields", [])):
            _fail("REQUIRED_FIELD_MISSING", f"{index}번째 레코드에 필수 필드가 없습니다.")
    observations = []
    seen = set()
    skipped = 0
    output_values = 0
    observation_bytes = 0
    checksum = hashlib.sha256(content).hexdigest()
    for mapping in recipe["mappings"]:
        item = catalog[mapping["result_item_id"]]
        groups: dict[str, list] = defaultdict(list)
        dims_by_key = {}
        for index, row in enumerate(rows, 1):
            raw = _get(row, mapping["source"])
            if raw is None or raw == "":
                if mapping.get("missing") == "skip":
                    skipped += 1
                    continue
                _fail("VALUE_MISSING", f"{index}번째 레코드의 {mapping['source']} 값이 없습니다.")
            dimensions = {}
            for name, path in mapping.get("dimensions", {}).items():
                dim = _get(row, path)
                if dim is None or dim == "" or isinstance(dim, (list, dict)):
                    _fail("DIMENSION_MISSING", f"{index}번째 레코드의 {name} 차원 값이 없습니다.")
                dimensions[name] = str(dim)
            if mapping.get("series_source"):
                series = _get(row, mapping["series_source"])
                if series is None or isinstance(series, (dict, list)):
                    _fail("DIMENSION_MISSING", "곡선 구분 값이 없습니다.")
                dimensions["series"] = str(series)
            group_key = _dimension_key(dimensions)
            dims_by_key[group_key] = dimensions
            value = _typed(raw, item, mapping)
            if item["kind"] == "curve":
                x = convert_unit(_get(row, mapping["x_source"]), mapping.get("x_unit", "s"), mapping.get("target_x_unit", mapping.get("x_unit", "s")))
                if groups[group_key] and x <= groups[group_key][-1]["x"]:
                    _fail("CURVE_ORDER_INVALID", "곡선 X축은 각 series에서 중복 없이 증가해야 합니다.")
                groups[group_key].append({"x": x, "y": value})
            else:
                groups[group_key].append(value)
        for key, values in groups.items():
            output_values += len(values) if item["kind"] == "curve" else 1
            if output_values > MAX_OUTPUT_VALUES:
                _fail("OUTPUT_LIMIT", "정규화 결과의 전체 값/곡선 점은 100,000개 이하여야 합니다.")
            identity = (item["id"], key)
            if identity in seen:
                _fail("DUPLICATE_RESULT", "같은 항목과 차원에 여러 매핑이 값을 제공합니다.")
            seen.add(identity)
            dimensions = dims_by_key[key]
            suffix = "_" + hashlib.sha256(key.encode()).hexdigest()[:16] if dimensions else ""
            observation = {"item_id": item["id"], "kind": item["kind"], "label": item["label"], "unit": item["unit"], "data_type": item["data_type"], "dimensions": dimensions, "variable_key": item["key"] + suffix}
            if item["kind"] == "curve":
                observation.update(points=values, x_unit=mapping.get("target_x_unit", mapping.get("x_unit", "s")))
            else:
                aggregate = mapping.get("aggregate", "none")
                if len(values) > 1 and aggregate == "none":
                    _fail("DUPLICATE_RESULT", f"{item['label']}: 여러 값이 있습니다. 측정 차원 또는 명시적 집계를 지정하세요.")
                value = max(values) if aggregate == "max" else min(values) if aggregate == "min" else math.fsum(values) / len(values) if aggregate == "mean" else values[0]
                if isinstance(value, (float, int)) and not isinstance(value, bool):
                    _number(value)
                if item["data_type"] == "INTEGER" and not float(value).is_integer():
                    _fail("TYPE_MISMATCH", "집계 결과가 정수 항목의 자료형과 다릅니다.")
                observation.update(value=value, aggregate=aggregate)
            observation_bytes += len(json.dumps(observation, ensure_ascii=False).encode("utf-8"))
            if observation_bytes > MAX_OUTPUT_BYTES:
                _fail("OUTPUT_LIMIT", "정규화 결과가 8 MB 한도를 초과했습니다.")
            observations.append(observation)
    if not observations:
        _fail("NO_RESULTS", "유효한 결과가 없습니다. 결측 정책과 필드 연결을 확인하세요.")
    scalars, curves = [], []
    for entry in observations:
        common = {"variable_key": entry["variable_key"], "display_name": entry["label"], "unit": entry["unit"], "source_file": filename, "source_checksum": checksum, "result_group": "CUSTOM"}
        if entry["kind"] == "scalar":
            scalars.append({**common, "data_type": entry["data_type"], "value": entry["value"], "threshold": None})
        else:
            curves.append({**common, "series_key": entry["dimensions"].get("series", "default"), "catalog_data_type": "TIME_SERIES", "x_label": "X", "x_unit": entry["x_unit"], "y_label": entry["label"], "y_unit": entry["unit"], "points": entry["points"]})
    result = {"schema_id": "semantic-recipe", "schema_version": 1, "solver": "Recipe", "note": "", "scalars": scalars, "curves": curves, "media": [], "observations": observations, "summary": {"row_count": len(rows), "scalar_count": len(scalars), "curve_count": len(curves), "skipped_values": skipped}, "warnings": [f"결측 값 {skipped}개를 건너뛰었습니다."] if skipped else []}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_OUTPUT_BYTES:
        _fail("OUTPUT_LIMIT", "정규화 결과가 8 MB 한도를 초과했습니다.")
    return result


def validate_template(template: dict, items: list[dict]) -> dict:
    template = _definition(template)
    catalog = _items(items)
    widgets = template.get("widgets")
    if not isinstance(widgets, list) or not 1 <= len(widgets) <= MAX_WIDGETS:
        _fail("WIDGETS_INVALID", "표시 템플릿에는 1~32개 위젯이 필요합니다.")
    ids = set()
    for widget in widgets:
        if not isinstance(widget, dict) or not isinstance(widget.get("id"), str) or not widget["id"] or widget["id"] in ids:
            _fail("WIDGET_ID_INVALID", "위젯 인스턴스 ID가 없거나 중복됩니다.")
        ids.add(widget["id"])
        kind = widget.get("type")
        if not isinstance(kind, str) or kind not in {"kpi", "gauge", "table", "bar", "line", "scatter", "image", "video"}:
            _fail("WIDGET_UNSUPPORTED", "지원하지 않는 위젯 유형입니다.")
        selected = [widget.get("x_item_id"), widget.get("y_item_id")] if kind == "scatter" else widget.get("item_ids", [])
        if not isinstance(selected, list) or not selected or len(selected) > MAX_MAPPINGS or any(not isinstance(s, str) or s not in catalog for s in selected):
            _fail("ITEM_NOT_FOUND", "위젯 입력에 유효한 결과 항목을 선택하세요.")
        if kind in {"kpi", "gauge", "image", "video"} and len(selected) != 1:
            _fail("WIDGET_INPUT_INVALID", "이 위젯에는 결과 항목 하나를 연결하세요.")
        expected = "curve" if kind == "line" else kind if kind in {"image", "video"} else "scalar"
        if any(catalog[s]["kind"] != expected for s in selected):
            _fail("INCOMPATIBLE_RESULT", "위젯 입력과 결과 항목의 유형이 다릅니다.")
        if kind in {"gauge", "bar", "scatter"} and any(catalog[s]["data_type"] not in {"FLOAT", "INTEGER"} for s in selected):
            _fail("INCOMPATIBLE_RESULT", "이 위젯에는 숫자 결과가 필요합니다.")
        if type(widget.get("decimals", 2)) is not int or not 0 <= widget.get("decimals", 2) <= 10:
            _fail("DISPLAY_INVALID", "소수 자릿수는 0~10 사이여야 합니다.")
        if not isinstance(widget.get("filters", {}), dict):
            _fail("FILTER_INVALID", "차원 필터는 객체여야 합니다.")
        if any(not isinstance(k, str) or isinstance(v, (dict, list)) or v is None for k, v in widget.get("filters", {}).items()):
            _fail("FILTER_INVALID", "차원 필터에는 단일 값이 필요합니다.")
        allowed_filters = set.intersection(*(set(catalog[s]["dimensions"]) | ({"series"} if expected == "curve" else set()) for s in selected))
        if not set(widget.get("filters", {})).issubset(allowed_filters):
            _fail("FILTER_INVALID", "연결한 항목에 정의된 측정 차원만 필터로 사용할 수 있습니다.")
        if widget.get("x_display_unit") is not None and not isinstance(widget["x_display_unit"], str):
            _fail("UNIT_MISMATCH", "X축 표시 단위는 문자열이어야 합니다.")
        if "threshold" in widget and widget["threshold"] is not None:
            _number(widget["threshold"])
        display = widget.get("display_unit")
        if kind == "scatter":
            for selected_id, role in zip(selected, ("x_display_unit", "y_display_unit")):
                axis_display = widget.get(role, display)
                if axis_display is not None:
                    convert_unit(0, catalog[selected_id]["unit"], axis_display)
        elif display is not None:
            if any(catalog[s]["data_type"] not in {"FLOAT", "INTEGER"} for s in selected):
                _fail("UNIT_MISMATCH", "숫자 항목에만 표시 단위를 지정할 수 있습니다.")
            for selected_id in selected:
                convert_unit(0, catalog[selected_id]["unit"], display)
        if kind in {"bar", "line"} and len({catalog[s]["unit"] for s in selected}) > 1 and display is None:
            _fail("UNIT_MISMATCH", "같은 축에는 같은 단위나 명시적인 표시 단위가 필요합니다.")
    return template


def resolve_widgets(template: dict, items: list[dict], payload: dict) -> list[dict]:
    template = validate_template(template, items)
    catalog = _items(items)
    observations = payload.get("observations", [])
    result = []
    for widget in template["widgets"]:
        kind = widget["type"]
        selected = [widget.get("x_item_id"), widget.get("y_item_id")] if kind == "scatter" else widget["item_ids"]
        matches = [entry for entry in observations if entry.get("item_id") in selected and all(str(entry.get("dimensions", {}).get(k)) == str(v) for k, v in widget.get("filters", {}).items())]
        output = {"id": widget["id"], "type": kind, "title": widget.get("title") or catalog[selected[0]]["label"], "status": "READY", "unit": widget.get("display_unit", catalog[selected[0]]["unit"]), "decimals": widget.get("decimals", 2), "data": []}
        if any(not any(entry.get("item_id") == key for entry in matches) for key in selected):
            output.update(status="MISSING_RESULT", message="연결한 결과 항목이 이 실행에 없습니다.")
        elif any(entry.get("kind") != catalog[entry["item_id"]]["kind"] or entry.get("data_type") != catalog[entry["item_id"]]["data_type"] for entry in matches):
            output.update(status="INCOMPATIBLE_RESULT", message="저장된 결과와 표시 템플릿의 항목 형식이 다릅니다.")
        elif kind in {"kpi", "gauge", "image", "video"} and len(matches) != 1:
            output.update(status="AMBIGUOUS_RESULT", message="여러 결과가 일치합니다. 위치 또는 series 필터를 지정하세요.")
        elif kind == "scatter":
            maps = []
            duplicate = False
            for key in selected:
                values = {}
                for entry in matches:
                    if entry["item_id"] == key:
                        dim_key = _dimension_key(entry.get("dimensions", {}))
                        duplicate |= dim_key in values
                        values[dim_key] = entry
                maps.append(values)
            if duplicate or set(maps[0]) != set(maps[1]):
                output.update(status="INCOMPATIBLE_RESULT", message="X/Y 결과의 측정 위치 키가 유일하게 일치하지 않습니다.")
            else:
                x_unit = widget.get("x_display_unit", widget.get("display_unit", catalog[selected[0]]["unit"]))
                y_unit = widget.get("y_display_unit", widget.get("display_unit", catalog[selected[1]]["unit"]))
                try:
                    output["data"] = [{"x": convert_unit(maps[0][key]["value"], maps[0][key]["unit"], x_unit), "y": convert_unit(maps[1][key]["value"], maps[1][key]["unit"], y_unit), "dimensions": maps[0][key].get("dimensions", {})} for key in maps[0]]
                except SemanticValidationError:
                    output.update(status="INCOMPATIBLE_RESULT", message="저장된 결과와 축 표시 단위가 호환되지 않습니다.", data=[])
                output.update(x_unit=x_unit, y_unit=y_unit)
        else:
            for entry in matches:
                row = dict(entry)
                target_unit = widget.get("display_unit", catalog[entry["item_id"]]["unit"])
                try:
                    if "points" in entry:
                        target_x_unit = widget.get("x_display_unit", entry["x_unit"])
                        row["points"] = [{"x": convert_unit(p["x"], entry["x_unit"], target_x_unit), "y": convert_unit(p["y"], entry["unit"], target_unit)} for p in entry["points"]]
                        row["x_unit"] = target_x_unit
                    elif "value" in entry and target_unit != entry["unit"]:
                        row["value"] = convert_unit(entry["value"], entry["unit"], target_unit)
                    row["unit"] = target_unit
                except SemanticValidationError:
                    output.update(status="INCOMPATIBLE_RESULT", message="저장된 결과와 표시 단위가 호환되지 않습니다.", data=[])
                    break
                output["data"].append(row)
            if kind == "line" and output["status"] == "READY":
                x_units = {entry["x_unit"] for entry in output["data"]}
                if len(x_units) != 1:
                    output.update(status="INCOMPATIBLE_RESULT", message="여러 곡선의 X축 단위가 다릅니다. X축 표시 단위를 지정하세요.", data=[])
                else:
                    output["x_unit"] = next(iter(x_units))
            if kind == "gauge" and widget.get("threshold") is not None and output["status"] == "READY":
                threshold = convert_unit(widget["threshold"], catalog[selected[0]]["unit"], output["unit"])
                output.update(threshold=threshold, verdict="PASS" if output["data"][0]["value"] <= threshold else "FAIL")
        result.append(output)
    return result
