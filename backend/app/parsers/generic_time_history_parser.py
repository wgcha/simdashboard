import csv
from typing import Dict, List, Any
from pathlib import Path

class GenericTimeHistoryParser:
    def parse(self, file_path: Path) -> List[Dict[str, Any]]:
        time_series = []
        
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    time_val = float(row["time"])
                    variable_key = row["variable_key"]
                    value = float(row["value"])
                    display_name = row.get("display_name", variable_key)
                    time_unit = row.get("time_unit", "s")
                    value_unit = row.get("value_unit", "")
                except KeyError as e:
                    raise ValueError(f"Missing column in generic time history CSV: {e}")
                except ValueError:
                    raise ValueError(f"Invalid numeric value in generic time history CSV: {row}")

                time_series.append({
                    "time_value": time_val,
                    "variable_key": variable_key,
                    "value_double": value,
                    "display_name": display_name,
                    "time_unit": time_unit,
                    "value_unit": value_unit
                })
                    
        return time_series
