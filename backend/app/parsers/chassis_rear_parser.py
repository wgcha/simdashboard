import csv
from typing import Dict, List, Any
from pathlib import Path

class ChassisRearParser:
    def parse(self, file_path: Path) -> List[Dict[str, Any]]:
        scalars = []
        allowed_locations = {
            "top_edge_gap": "상단 엣지 갭",
            "bottom_edge_gap": "하단 엣지 갭",
            "corner_top_left": "좌상단 코너",
            "corner_top_right": "우상단 코너",
            "corner_bottom_left": "좌하단 코너",
            "corner_bottom_right": "우하단 코너"
        }
        
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    location = row["location"]
                    deformation = float(row["permanent_deformation"])
                except KeyError as e:
                    raise ValueError(f"Missing column in chassis rear CSV: {e}")
                except ValueError:
                    raise ValueError(f"Invalid numeric value in chassis rear CSV: {row}")

                if location in allowed_locations:
                    scalars.append({
                        "variable_key": f"chassis_rear_{location}_permanent_deformation",
                        "value_double": deformation,
                        "display_name": f"Chassis Rear {allowed_locations[location]} 영구변형",
                        "unit": "mm"
                    })
                    
        return scalars
