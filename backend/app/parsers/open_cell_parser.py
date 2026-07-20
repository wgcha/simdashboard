import csv
from typing import Dict, List, Any
from pathlib import Path

class OpenCellParser:
    def parse(self, file_path: Path) -> Dict[str, Any]:
        time_series = []
        scalars = {
            "top_edge_max_stress": 0.0,
            "bottom_edge_max_stress": 0.0,
            "left_edge_max_stress": 0.0,
            "right_edge_max_stress": 0.0,
        }
        
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    time_val = float(row["time"])
                    top = float(row["top"])
                    bottom = float(row["bottom"])
                    left = float(row["left"])
                    right = float(row["right"])
                except KeyError as e:
                    raise ValueError(f"Missing column in open cell CSV: {e}")
                except ValueError:
                    raise ValueError(f"Invalid numeric value in open cell CSV: {row}")

                time_series.extend([
                    {"time_value": time_val, "variable_key": "top_edge_stress_time", "value_double": top, "display_name": "상단 엣지"},
                    {"time_value": time_val, "variable_key": "bottom_edge_stress_time", "value_double": bottom, "display_name": "하단 엣지"},
                    {"time_value": time_val, "variable_key": "left_edge_stress_time", "value_double": left, "display_name": "좌측 엣지"},
                    {"time_value": time_val, "variable_key": "right_edge_stress_time", "value_double": right, "display_name": "우측 엣지"},
                ])
                
                scalars["top_edge_max_stress"] = max(scalars["top_edge_max_stress"], top)
                scalars["bottom_edge_max_stress"] = max(scalars["bottom_edge_max_stress"], bottom)
                scalars["left_edge_max_stress"] = max(scalars["left_edge_max_stress"], left)
                scalars["right_edge_max_stress"] = max(scalars["right_edge_max_stress"], right)
                
        display_names = {
            "top_edge_max_stress": "상단 엣지 최대 응력",
            "bottom_edge_max_stress": "하단 엣지 최대 응력",
            "left_edge_max_stress": "좌측 엣지 최대 응력",
            "right_edge_max_stress": "우측 엣지 최대 응력",
        }
        return {
            "time_series": time_series,
            "scalars": [
                {"variable_key": k, "value_double": v, "display_name": display_names[k], "unit": "MPa"} 
                for k, v in scalars.items()
            ]
        }
