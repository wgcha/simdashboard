from datetime import datetime, timezone
import json
from typing import Dict, Any, Optional
from backend.app.database import connect, rows

class ImportJobRepository:
    def create_job(self, job_data: Dict[str, Any]) -> None:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO result_import_jobs (
                    id, analysis_run_id, project_id, request_id, load_case_id,
                    manifest_path, source_directory, schema_version, status,
                    overwrite_policy, file_count, row_count, manifest_checksum,
                    source_checksum, error_code, error_message, started_at,
                    completed_at, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    job_data["id"], job_data.get("analysis_run_id"), job_data["project_id"],
                    job_data["request_id"], job_data["load_case_id"], job_data["manifest_path"],
                    job_data["source_directory"], job_data["schema_version"], job_data["status"],
                    job_data["overwrite_policy"], job_data.get("file_count", 0), job_data.get("row_count", 0),
                    job_data["manifest_checksum"], job_data.get("source_checksum"),
                    job_data.get("error_code"), job_data.get("error_message"),
                    job_data.get("started_at"), job_data.get("completed_at"),
                    datetime.now(timezone.utc).replace(tzinfo=None)
                ]
            )

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with connect() as conn:
            result = rows(conn.execute("SELECT * FROM result_import_jobs WHERE id = ?", [job_id]))
            return result[0] if result else None
