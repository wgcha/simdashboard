from typing import List, Dict, Any, Optional
import uuid
from backend.app.database import connect

class ResultRepository:
    def delete_results_for_run(self, run_id: str) -> None:
        with connect() as conn:
            conn.execute("DELETE FROM scalar_results WHERE analysis_run_id = ?", [run_id])
            conn.execute("DELETE FROM time_series_results WHERE analysis_run_id = ?", [run_id])
            conn.execute("DELETE FROM media_assets WHERE analysis_run_id = ?", [run_id])

    def save_scalar_results(self, run_id: str, results: List[Dict[str, Any]]) -> None:
        if not results: return
        with connect() as conn:
            for r in results:
                result_id = f"scalar-{uuid.uuid4().hex[:12]}"
                conn.execute(
                    """
                    INSERT INTO scalar_results (id, analysis_run_id, variable_key, display_name, value_double, unit, threshold_double, verdict)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [result_id, run_id, r["variable_key"], r.get("display_name", r["variable_key"]), r["value_double"], r.get("unit"), r.get("threshold_double"), r.get("verdict")]
                )

    def save_time_series_results(self, run_id: str, results: List[Dict[str, Any]]) -> None:
        if not results: return
        with connect() as conn:
            # Simple batch insert for DuckDB MVP
            conn.executemany(
                """
                INSERT INTO time_series_results (analysis_run_id, variable_key, display_name, time_value, value, time_unit, value_unit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [[run_id, r["variable_key"], r.get("display_name", r["variable_key"]), r["time_value"], r["value_double"], r.get("time_unit", "s"), r.get("value_unit", "")] for r in results]
            )

    def get_thresholds(self, project_id: str) -> List[Dict[str, Any]]:
        from backend.app.database import rows
        with connect() as conn:
            return rows(conn.execute("SELECT * FROM quality_thresholds WHERE project_id = ?", [project_id]))
