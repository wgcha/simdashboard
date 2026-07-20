from typing import Optional, Dict, Any
import json
from datetime import datetime, timezone
from backend.app.database import connect, rows

class AnalysisRepository:
    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        with connect() as conn:
            result = rows(conn.execute("SELECT * FROM projects WHERE id = ?", [project_id]))
            return result[0] if result else None

    def get_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        with connect() as conn:
            result = rows(conn.execute("SELECT * FROM analysis_requests WHERE id = ?", [request_id]))
            return result[0] if result else None

    def get_load_case(self, load_case_id: str) -> Optional[Dict[str, Any]]:
        with connect() as conn:
            result = rows(conn.execute("SELECT * FROM load_cases WHERE id = ?", [load_case_id]))
            return result[0] if result else None

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with connect() as conn:
            result = rows(conn.execute("SELECT * FROM analysis_runs WHERE id = ?", [run_id]))
            return result[0] if result else None

    def create_run(self, run_id: str, load_case_id: str, run_no: int, solver: Optional[str], started_at: Optional[datetime], completed_at: Optional[datetime], source_program: str, source_program_version: Optional[str]) -> None:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO analysis_runs (id, load_case_id, run_no, solver, status, started_at, completed_at, source_program, source_program_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [run_id, load_case_id, run_no, solver, "COMPLETED", started_at, completed_at, source_program, source_program_version]
            )

    def update_run_import_status(self, run_id: str, status: str, overall_verdict: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with connect() as conn:
            if overall_verdict:
                conn.execute("UPDATE analysis_runs SET result_import_status = ?, last_imported_at = ?, overall_verdict = ? WHERE id = ?", [status, now, overall_verdict, run_id])
            else:
                conn.execute("UPDATE analysis_runs SET result_import_status = ?, last_imported_at = ? WHERE id = ?", [status, now, run_id])

