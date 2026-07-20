import csv
from typing import Dict, List, Any
from pathlib import Path

class ScalarResultParser:
    def parse(self, file_path: Path) -> List[Dict[str, Any]]:
        scalars = []
        
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    variable_key = row["variable_key"]
                    display_name = row.get("display_name", variable_key)
                    value = float(row["value"])
                    unit = row.get("unit", "")
                    threshold = float(row["threshold"]) if row.get("threshold") else None
                    criterion_key = row.get("criterion_key")
                except KeyError as e:
                    raise ValueError(f"Missing column in scalar result CSV: {e}")
                except ValueError:
                    raise ValueError(f"Invalid numeric value in scalar result CSV: {row}")

                scalars.append({
                    "variable_key": variable_key,
                    "display_name": display_name,
                    "value_double": value,
                    "unit": unit,
                    "threshold_double": threshold,
                    "criterion_key": criterion_key
                })
                    
        return scalars
