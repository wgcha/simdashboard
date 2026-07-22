from __future__ import annotations

import csv
import io
import math
from collections import defaultdict
from typing import Any


class RadiossCsvError(ValueError):
    pass


EDGE_LABELS = {"top": "상단", "bottom": "하단", "left": "좌측", "right": "우측"}


def _float(value: Any, label: str, default: float | None = None) -> float:
    if (value in (None, "")) and default is not None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RadiossCsvError(f"{label}은(는) 숫자여야 합니다: {value!r}") from exc
    if not math.isfinite(result):
        raise RadiossCsvError(f"{label}에 유효하지 않은 숫자가 있습니다.")
    return result


def _part(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "_").replace("-", "_")


def _distance_to_line(point: tuple[float, float, float], start: tuple[float, float, float], end: tuple[float, float, float]) -> float:
    ab = tuple(end[i] - start[i] for i in range(3))
    ap = tuple(point[i] - start[i] for i in range(3))
    length = math.sqrt(sum(value * value for value in ab))
    if length <= 1e-12:
        raise RadiossCsvError("Chassis Rear 엣지 양 끝점이 동일한 위치입니다.")
    cross = (
        ap[1] * ab[2] - ap[2] * ab[1],
        ap[2] * ab[0] - ap[0] * ab[2],
        ap[0] * ab[1] - ap[1] * ab[0],
    )
    return math.sqrt(sum(value * value for value in cross)) / length


def parse_radioss_mesh_csv(content: str, chassis_threshold: float, open_cell_threshold: float = 75.0, edge_band_ratio: float = 0.18) -> dict[str, Any]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    required = {"record_type", "part_name", "time", "time_unit"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise RadiossCsvError("Radioss CSV에는 record_type, part_name, time, time_unit 열이 필요합니다.")

    nodes: dict[tuple[float, int], dict[str, Any]] = {}
    elements: dict[tuple[float, int], dict[str, Any]] = {}
    time_units: set[str] = set()
    length_units: set[str] = set()
    stress_units: set[str] = set()

    for line_no, row in enumerate(reader, start=2):
        record_type = str(row.get("record_type") or "").strip().upper()
        if not record_type:
            continue
        part = _part(row.get("part_name"))
        if part not in {"OPEN_CELL", "CHASSIS_REAR"}:
            raise RadiossCsvError(f"CSV {line_no}행 part_name은 OPEN_CELL 또는 CHASSIS_REAR여야 합니다.")
        time = _float(row.get("time"), f"CSV {line_no}행 time", 0.0)
        time_unit = str(row.get("time_unit") or "ms").strip()
        time_units.add(time_unit)
        if record_type == "NODE":
            try:
                node_id = int(str(row.get("node_id") or ""))
            except ValueError as exc:
                raise RadiossCsvError(f"CSV {line_no}행 node_id는 정수여야 합니다.") from exc
            key = (time, node_id)
            if key in nodes:
                raise RadiossCsvError(f"노드 {node_id}의 time={time} 행이 중복되었습니다.")
            length_unit = str(row.get("length_unit") or "mm").strip()
            length_units.add(length_unit)
            position = tuple(_float(row.get(axis), f"노드 {node_id} {axis}") for axis in ("x", "y", "z"))
            displacement = tuple(_float(row.get(axis), f"노드 {node_id} {axis}", 0.0) for axis in ("dx", "dy", "dz"))
            nodes[key] = {"id": node_id, "part": part, "position": position, "displacement": displacement, "time": time, "time_unit": time_unit}
        elif record_type == "ELEMENT":
            try:
                element_id = int(str(row.get("element_id") or ""))
            except ValueError as exc:
                raise RadiossCsvError(f"CSV {line_no}행 element_id는 정수여야 합니다.") from exc
            key = (time, element_id)
            if key in elements:
                raise RadiossCsvError(f"요소 {element_id}의 time={time} 행이 중복되었습니다.")
            connectivity: list[int] = []
            for name in ("n1", "n2", "n3", "n4"):
                raw = str(row.get(name) or "").strip()
                if raw:
                    try:
                        connectivity.append(int(raw))
                    except ValueError as exc:
                        raise RadiossCsvError(f"CSV {line_no}행 {name}은 정수여야 합니다.") from exc
            if len(connectivity) < 3:
                raise RadiossCsvError(f"요소 {element_id}에는 최소 3개 연결 노드가 필요합니다.")
            stress_unit = str(row.get("stress_unit") or "MPa").strip()
            stress_units.add(stress_unit)
            principal = _float(row.get("stress_max_principal"), f"요소 {element_id} 최대주응력") if part == "OPEN_CELL" else None
            von_mises = _float(row.get("stress_von_mises"), f"요소 {element_id} von Mises", 0.0)
            elements[key] = {"id": element_id, "part": part, "nodes": connectivity, "principal": principal, "von_mises": von_mises, "time": time, "time_unit": time_unit}
        else:
            raise RadiossCsvError(f"CSV {line_no}행 record_type은 NODE 또는 ELEMENT여야 합니다.")

    if not nodes:
        raise RadiossCsvError("NODE 행이 없습니다.")
    if len(time_units) != 1:
        raise RadiossCsvError(f"time_unit이 혼합되어 있습니다: {sorted(time_units)}")
    if length_units and length_units != {"mm"}:
        raise RadiossCsvError("현재 길이 단위는 mm만 지원합니다.")
    if stress_units and stress_units != {"MPa"}:
        raise RadiossCsvError("현재 응력 단위는 MPa만 지원합니다.")

    times = sorted({time for time, _ in nodes} | {time for time, _ in elements})
    base_time = min(time for time, _ in nodes)
    final_time = max(time for time, _ in nodes)
    base_nodes = {node_id: node for (time, node_id), node in nodes.items() if time == base_time}
    scalars: list[dict[str, Any]] = []
    series: list[dict[str, Any]] = []
    locations: list[dict[str, Any]] = []
    warnings: list[str] = []

    open_nodes = [node for node in base_nodes.values() if node["part"] == "OPEN_CELL"]
    open_elements = [element for element in elements.values() if element["part"] == "OPEN_CELL"]
    if open_nodes or open_elements:
        if len(open_nodes) < 4 or not open_elements:
            raise RadiossCsvError("Open Cell 분석에는 기준 시점 노드 4개 이상과 요소 응력 행이 필요합니다.")
        min_x, max_x = min(node["position"][0] for node in open_nodes), max(node["position"][0] for node in open_nodes)
        min_y, max_y = min(node["position"][1] for node in open_nodes), max(node["position"][1] for node in open_nodes)
        width, height = max_x - min_x, max_y - min_y
        if width <= 0 or height <= 0:
            raise RadiossCsvError("Open Cell 노드의 X/Y 영역이 유효하지 않습니다.")
        element_edges: dict[int, list[str]] = {}
        element_centroids: dict[int, tuple[float, float, float]] = {}
        for element in open_elements:
            connected = [base_nodes.get(node_id) for node_id in element["nodes"]]
            if any(node is None or node["part"] != "OPEN_CELL" for node in connected):
                raise RadiossCsvError(f"Open Cell 요소 {element['id']}의 연결 노드가 기준 시점 NODE 행과 일치하지 않습니다.")
            positions = [node["position"] for node in connected if node]
            centroid = tuple(sum(point[i] for point in positions) / len(positions) for i in range(3))
            element_centroids[element["id"]] = centroid
            distances = {
                "top": (max_y - centroid[1]) / height,
                "bottom": (centroid[1] - min_y) / height,
                "left": (centroid[0] - min_x) / width,
                "right": (max_x - centroid[0]) / width,
            }
            element_edges[element["id"]] = [edge for edge, distance in distances.items() if distance <= edge_band_ratio]

        grouped: dict[tuple[str, float], list[dict[str, Any]]] = defaultdict(list)
        for element in open_elements:
            centroid = element_centroids[element["id"]]
            for edge in element_edges[element["id"]]:
                grouped[(edge, element["time"])].append({**element, "centroid": centroid})
        for edge in EDGE_LABELS:
            edge_points: list[dict[str, Any]] = []
            for time in sorted({time for current_edge, time in grouped if current_edge == edge}):
                candidates = grouped[(edge, time)]
                if candidates:
                    maximum = max(candidates, key=lambda item: item["principal"])
                    edge_points.append(maximum)
                    series.append({"variable_key": f"{edge}_edge_stress_time", "display_name": f"{EDGE_LABELS[edge]} 엣지", "time": time, "value": maximum["principal"], "time_unit": maximum["time_unit"], "value_unit": "MPa"})
            if not edge_points:
                raise RadiossCsvError(f"Open Cell {EDGE_LABELS[edge]} 엣지 밴드에 요소가 없습니다. 메시 또는 edge_band_ratio를 확인하세요.")
            peak = max(edge_points, key=lambda item: item["principal"])
            scalars.append({"variable_key": f"{edge}_edge_max_stress", "display_name": f"{EDGE_LABELS[edge]} 엣지 최대 응력", "value": peak["principal"], "unit": "MPa", "threshold": open_cell_threshold, "verdict": "FAIL" if peak["principal"] >= open_cell_threshold else "PASS", "analysis": "OPEN_CELL"})
            locations.append({"variable_key": f"{edge}_edge_max_stress", "entity_type": "ELEMENT", "entity_id": str(peak["id"]), "x": peak["centroid"][0], "y": peak["centroid"][1], "z": peak["centroid"][2], "time": peak["time"], "time_unit": peak["time_unit"], "method": f"MAX_PRINCIPAL / {int(edge_band_ratio * 100)}% EDGE BAND"})
        warnings.append("Open Cell은 취성 유리 평가를 위해 요소 최대주응력(stress_max_principal)을 사용했습니다.")

    final_chassis = [node for (time, _), node in nodes.items() if time == final_time and node["part"] == "CHASSIS_REAR"]
    if final_chassis:
        if len(final_chassis) < 6:
            raise RadiossCsvError("Chassis Rear 분석에는 최종 시점 노드가 6개 이상 필요합니다.")
        min_x, max_x = min(node["position"][0] for node in final_chassis), max(node["position"][0] for node in final_chassis)
        min_y, max_y = min(node["position"][1] for node in final_chassis), max(node["position"][1] for node in final_chassis)
        tolerance = max(max_y - min_y, 1.0) * 1e-6
        deformed = lambda node: tuple(node["position"][i] + node["displacement"][i] for i in range(3))
        for edge, target_y in (("top", max_y), ("bottom", min_y)):
            edge_nodes = sorted((node for node in final_chassis if abs(node["position"][1] - target_y) <= tolerance), key=lambda node: node["position"][0])
            if len(edge_nodes) < 3:
                raise RadiossCsvError(f"Chassis Rear {EDGE_LABELS[edge]} 엣지에 최소 3개 노드가 필요합니다.")
            start, end = deformed(edge_nodes[0]), deformed(edge_nodes[-1])
            measured = [(node, _distance_to_line(deformed(node), start, end)) for node in edge_nodes]
            peak_node, peak_value = max(measured, key=lambda item: item[1])
            key = f"chassis_rear_{edge}_edge_gap_permanent_deformation"
            scalars.append({"variable_key": key, "display_name": f"{EDGE_LABELS[edge]} 엣지 최대 이격", "value": peak_value, "unit": "mm", "threshold": chassis_threshold, "verdict": "FAIL" if peak_value >= chassis_threshold else "PASS", "analysis": "CHASSIS_REAR"})
            point = deformed(peak_node)
            locations.append({"variable_key": key, "entity_type": "NODE", "entity_id": str(peak_node["id"]), "x": point[0], "y": point[1], "z": point[2], "time": final_time, "time_unit": peak_node["time_unit"], "method": "MAX 3D DISTANCE TO DEFORMED ENDPOINT CHORD"})

        corners = {
            "top_left": (min_x, max_y), "top_right": (max_x, max_y),
            "bottom_left": (min_x, min_y), "bottom_right": (max_x, min_y),
        }
        x_span, y_span = max(max_x - min_x, 1.0), max(max_y - min_y, 1.0)
        for corner, target in corners.items():
            node = min(final_chassis, key=lambda item: ((item["position"][0] - target[0]) / x_span) ** 2 + ((item["position"][1] - target[1]) / y_span) ** 2)
            value = math.sqrt(sum(component * component for component in node["displacement"]))
            key = f"chassis_rear_corner_{corner}_permanent_deformation"
            label = {"top_left": "좌상단", "top_right": "우상단", "bottom_left": "좌하단", "bottom_right": "우하단"}[corner]
            scalars.append({"variable_key": key, "display_name": f"{label} 모서리 영구변형", "value": value, "unit": "mm", "threshold": chassis_threshold, "verdict": "FAIL" if value >= chassis_threshold else "PASS", "analysis": "CHASSIS_REAR"})
            point = deformed(node)
            locations.append({"variable_key": key, "entity_type": "NODE", "entity_id": str(node["id"]), "x": point[0], "y": point[1], "z": point[2], "time": final_time, "time_unit": node["time_unit"], "method": "FINAL DISPLACEMENT MAGNITUDE"})
        warnings.append(f"Chassis Rear는 최종 프레임 {final_time:g} {next(iter(time_units))}를 하중 제거 후 영구변형 상태로 가정했습니다.")

    if not scalars:
        raise RadiossCsvError("OPEN_CELL 요소 또는 CHASSIS_REAR 노드 데이터가 없어 파생 결과를 만들 수 없습니다.")
    return {
        "solver": "Altair Radioss CSV",
        "note": "Radioss 노드/요소 CSV에서 자동 산출",
        "scalars": scalars,
        "time_series": series,
        "locations": locations,
        "warnings": warnings,
        "summary": {
            "source_format": "RADIOSS_MESH_CSV",
            "node_count": len(nodes),
            "element_count": len(elements),
            "frame_count": len(times),
            "final_time": final_time,
            "scalar_count": len(scalars),
            "time_series_count": len(series),
            "open_cell_count": sum(item["analysis"] == "OPEN_CELL" for item in scalars),
            "chassis_rear_count": sum(item["analysis"] == "CHASSIS_REAR" for item in scalars),
            "fail_count": sum(item["verdict"] == "FAIL" for item in scalars),
            "overall_verdict": "FAIL" if any(item["verdict"] == "FAIL" for item in scalars) else "PASS",
        },
    }
