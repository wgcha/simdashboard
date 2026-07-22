from __future__ import annotations

import csv
import math
import sys
from pathlib import Path


OUTPUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).with_name("radioss_tv_result_example.csv")
FIELDS = [
    "record_type", "part_name", "time", "time_unit", "node_id", "element_id",
    "n1", "n2", "n3", "n4", "x", "y", "z", "dx", "dy", "dz",
    "stress_max_principal", "stress_von_mises", "length_unit", "stress_unit",
]


def empty_row(**values):
    return {name: values.get(name, "") for name in FIELDS}


rows = []

# Open Cell: 1600 x 900 mm, 5 x 5 nodes, 4 x 4 shell elements, three result frames.
open_nodes = {}
node_id = 1001
for iy in range(5):
    for ix in range(5):
        open_nodes[(ix, iy)] = node_id
        node_id += 1

open_elements = []
element_id = 5001
for iy in range(4):
    for ix in range(4):
        open_elements.append((element_id, ix, iy, [open_nodes[(ix, iy)], open_nodes[(ix + 1, iy)], open_nodes[(ix + 1, iy + 1)], open_nodes[(ix, iy + 1)]]))
        element_id += 1

edge_peaks = {"top": 63.0, "bottom": 84.0, "left": 71.0, "right": 76.5}
for time in (0.0, 10.0, 30.0):
    for (ix, iy), current_node_id in open_nodes.items():
        x, y = ix * 400.0, iy * 225.0
        rows.append(empty_row(record_type="NODE", part_name="OPEN_CELL", time=time, time_unit="ms", node_id=current_node_id, x=x, y=y, z=0.0, dx=0.0, dy=0.0, dz=0.0, length_unit="mm"))
    for current_element_id, ix, iy, connectivity in open_elements:
        edges = []
        if iy == 3: edges.append("top")
        if iy == 0: edges.append("bottom")
        if ix == 0: edges.append("left")
        if ix == 3: edges.append("right")
        peak = max((edge_peaks[edge] for edge in edges), default=38.0)
        factor = {0.0: 0.08, 10.0: 1.0, 30.0: 0.12}[time]
        principal = round((peak - (ix + iy) * 0.35) * factor, 3)
        rows.append(empty_row(record_type="ELEMENT", part_name="OPEN_CELL", time=time, time_unit="ms", element_id=current_element_id, n1=connectivity[0], n2=connectivity[1], n3=connectivity[2], n4=connectivity[3], stress_max_principal=principal, stress_von_mises=round(principal * 0.82, 3), stress_unit="MPa"))

# Chassis Rear: final frame is assumed to be the unloaded permanent-deformation state.
chassis_nodes = {}
node_id = 2001
for edge, y in (("bottom", -60.0), ("top", 960.0)):
    for ix in range(9):
        chassis_nodes[(edge, ix)] = node_id
        node_id += 1

for time in (0.0, 30.0):
    for (edge, ix), current_node_id in chassis_nodes.items():
        x = ix * 200.0
        y = 960.0 if edge == "top" else -60.0
        if time == 0.0:
            dx = dy = dz = 0.0
        else:
            dz = (6.4 if edge == "top" else 4.3) * math.sin(math.pi * ix / 8)
            dy = 0.15 * math.sin(math.pi * ix / 8)
            dx = 0.0
            if edge == "top" and ix == 0: dx = -5.2
            if edge == "top" and ix == 8: dx = 3.1
            if edge == "bottom" and ix == 0: dx = -2.4
            if edge == "bottom" and ix == 8: dx = 5.4
        rows.append(empty_row(record_type="NODE", part_name="CHASSIS_REAR", time=time, time_unit="ms", node_id=current_node_id, x=x, y=y, z=-35.0, dx=round(dx, 4), dy=round(dy, 4), dz=round(dz, 4), length_unit="mm"))

    for ix in range(8):
        connectivity = [chassis_nodes[("bottom", ix)], chassis_nodes[("bottom", ix + 1)], chassis_nodes[("top", ix + 1)], chassis_nodes[("top", ix)]]
        vm = 12.0 if time == 0.0 else round(112.0 + 9.0 * math.sin(math.pi * (ix + 0.5) / 8), 3)
        rows.append(empty_row(record_type="ELEMENT", part_name="CHASSIS_REAR", time=time, time_unit="ms", element_id=7001 + ix, n1=connectivity[0], n2=connectivity[1], n3=connectivity[2], n4=connectivity[3], stress_von_mises=vm, stress_unit="MPa"))

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
with OUTPUT.open("w", newline="", encoding="utf-8-sig") as handle:
    writer = csv.DictWriter(handle, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)

print(f"created: {OUTPUT} ({len(rows)} rows)")
