from __future__ import annotations

import csv
import io
import json
from typing import Any

from .radioss_csv import RadiossCsvError, parse_radioss_mesh_csv


OPEN_CELL_SCALARS = {
    "top_edge_max_stress": "상단 엣지 최대 응력",
    "bottom_edge_max_stress": "하단 엣지 최대 응력",
    "left_edge_max_stress": "좌측 엣지 최대 응력",
    "right_edge_max_stress": "우측 엣지 최대 응력",
}
OPEN_CELL_SERIES = {
    "top_edge_stress_time": "상단 엣지",
    "bottom_edge_stress_time": "하단 엣지",
    "left_edge_stress_time": "좌측 엣지",
    "right_edge_stress_time": "우측 엣지",
}
CHASSIS_SCALARS = {
    "chassis_rear_top_edge_gap_permanent_deformation": "상단 엣지 최대 이격",
    "chassis_rear_bottom_edge_gap_permanent_deformation": "하단 엣지 최대 이격",
    "chassis_rear_corner_top_left_permanent_deformation": "좌상단 모서리 영구변형",
    "chassis_rear_corner_top_right_permanent_deformation": "우상단 모서리 영구변형",
    "chassis_rear_corner_bottom_left_permanent_deformation": "좌하단 모서리 영구변형",
    "chassis_rear_corner_bottom_right_permanent_deformation": "우하단 모서리 영구변형",
}


class ResultFormatError(ValueError):
    pass


def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ResultFormatError(f"{label}은(는) 숫자여야 합니다: {value!r}") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise ResultFormatError(f"{label}에 유효하지 않은 숫자가 있습니다.")
    return result


def _raw_records(filename: str, content: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "json":
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ResultFormatError(f"JSON 형식 오류: {exc.msg} (줄 {exc.lineno})") from exc
        if not isinstance(payload, dict):
            raise ResultFormatError("JSON 최상위 값은 객체여야 합니다.")
        scalar = payload.get("scalar_results", [])
        series = payload.get("time_series", [])
        if not isinstance(scalar, list) or not isinstance(series, list):
            raise ResultFormatError("scalar_results와 time_series는 배열이어야 합니다.")
        return payload, scalar, series
    if suffix == "csv":
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
        if not reader.fieldnames or not {"record_type", "variable_key", "value"}.issubset(reader.fieldnames):
            raise ResultFormatError("CSV에는 record_type, variable_key, value 열이 필요합니다.")
        scalar: list[dict[str, Any]] = []
        series: list[dict[str, Any]] = []
        for line_no, row in enumerate(reader, start=2):
            record_type = (row.get("record_type") or "").strip().lower()
            row["_line"] = line_no
            if record_type == "scalar":
                scalar.append(row)
            elif record_type in {"time_series", "series"}:
                series.append(row)
            elif any((value or "").strip() for key, value in row.items() if key != "_line"):
                raise ResultFormatError(f"CSV {line_no}행 record_type은 scalar 또는 time_series여야 합니다.")
        return {}, scalar, series
    raise ResultFormatError("CSV 또는 JSON 파일만 지원합니다.")


def parse_result_file(filename: str, content: str, chassis_threshold: float = 5.0) -> dict[str, Any]:
    if not content.strip():
        raise ResultFormatError("파일 내용이 비어 있습니다.")
    if filename.lower().endswith(".csv"):
        header = next(csv.reader(io.StringIO(content.lstrip("\ufeff"))), [])
        if {"node_id", "element_id", "part_name"}.issubset(set(header)):
            try:
                return parse_radioss_mesh_csv(content, chassis_threshold)
            except RadiossCsvError as exc:
                raise ResultFormatError(str(exc)) from exc
    metadata, raw_scalars, raw_series = _raw_records(filename, content)
    scalars: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []
    scalar_keys: set[str] = set()
    series_points: set[tuple[str, float]] = set()

    for index, item in enumerate(raw_scalars, start=1):
        if not isinstance(item, dict):
            raise ResultFormatError(f"scalar_results {index}번째 항목은 객체여야 합니다.")
        key = str(item.get("variable_key", "")).strip()
        if key in OPEN_CELL_SCALARS:
            display_name, unit, default_threshold, analysis = OPEN_CELL_SCALARS[key], "MPa", 75.0, "OPEN_CELL"
        elif key in CHASSIS_SCALARS:
            display_name, unit, default_threshold, analysis = CHASSIS_SCALARS[key], "mm", chassis_threshold, "CHASSIS_REAR"
        else:
            raise ResultFormatError(f"지원하지 않는 실수형 변수입니다: {key or '(비어 있음)'}")
        if key in scalar_keys:
            raise ResultFormatError(f"실수형 변수 {key}가 중복되었습니다.")
        scalar_keys.add(key)
        value = _number(item.get("value", item.get("value_double")), f"{key} value")
        threshold = _number(item.get("threshold") or item.get("threshold_double") or default_threshold, f"{key} threshold")
        scalars.append({
            "variable_key": key,
            "display_name": str(item.get("display_name") or display_name),
            "value": value,
            "unit": str(item.get("unit") or unit),
            "threshold": threshold,
            "verdict": "FAIL" if value >= threshold else "PASS",
            "analysis": analysis,
        })

    for index, item in enumerate(raw_series, start=1):
        if not isinstance(item, dict):
            raise ResultFormatError(f"time_series {index}번째 항목은 객체여야 합니다.")
        key = str(item.get("variable_key", "")).strip()
        if key not in OPEN_CELL_SERIES:
            raise ResultFormatError(f"지원하지 않는 시간 이력 변수입니다: {key or '(비어 있음)'}")
        time_value = _number(item.get("time", item.get("time_value")), f"{key} time")
        value = _number(item.get("value"), f"{key} value")
        point = (key, time_value)
        if point in series_points:
            raise ResultFormatError(f"시간 이력 {key}의 {time_value} 시점이 중복되었습니다.")
        series_points.add(point)
        series.append({
            "variable_key": key,
            "display_name": str(item.get("display_name") or OPEN_CELL_SERIES[key]),
            "time": time_value,
            "value": value,
            "time_unit": str(item.get("time_unit") or "ms"),
            "value_unit": str(item.get("value_unit") or item.get("unit") or "MPa"),
        })

    if not scalars:
        raise ResultFormatError("판정에 사용할 scalar 결과가 하나 이상 필요합니다.")
    return {
        "solver": str(metadata.get("solver") or "Imported Result"),
        "note": str(metadata.get("note") or ""),
        "scalars": scalars,
        "time_series": series,
        "locations": [],
        "warnings": [],
        "summary": {
            "source_format": "SUMMARY_RESULT",
            "node_count": 0,
            "element_count": 0,
            "frame_count": 0,
            "final_time": None,
            "scalar_count": len(scalars),
            "time_series_count": len(series),
            "open_cell_count": sum(item["analysis"] == "OPEN_CELL" for item in scalars),
            "chassis_rear_count": sum(item["analysis"] == "CHASSIS_REAR" for item in scalars),
            "fail_count": sum(item["verdict"] == "FAIL" for item in scalars),
            "overall_verdict": "FAIL" if any(item["verdict"] == "FAIL" for item in scalars) else "PASS",
        },
    }


CSV_TEMPLATE = """record_type,variable_key,display_name,value,unit,threshold,time,time_unit,value_unit
scalar,top_edge_max_stress,상단 엣지 최대 응력,61.8,MPa,75,,,
scalar,bottom_edge_max_stress,하단 엣지 최대 응력,82.4,MPa,75,,,
scalar,chassis_rear_top_edge_gap_permanent_deformation,상단 엣지 최대 이격,5.3,mm,5,,,
time_series,top_edge_stress_time,상단 엣지,12.5,,,,12.5,ms,MPa
"""

JSON_TEMPLATE = {
    "solver": "Explicit Solver",
    "note": "해석 수행자의 정성적 의견",
    "scalar_results": [
        {"variable_key": "top_edge_max_stress", "value": 61.8, "unit": "MPa", "threshold": 75},
        {"variable_key": "chassis_rear_top_edge_gap_permanent_deformation", "value": 5.3, "unit": "mm", "threshold": 5},
    ],
    "time_series": [
        {"variable_key": "top_edge_stress_time", "time": 0.0, "value": 0.0, "time_unit": "ms", "value_unit": "MPa"},
        {"variable_key": "top_edge_stress_time", "time": 12.5, "value": 61.8, "time_unit": "ms", "value_unit": "MPa"},
    ],
}
