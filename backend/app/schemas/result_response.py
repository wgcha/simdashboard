from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel

class ImportJobResponse(BaseModel):
    id: str
    analysis_run_id: Optional[str] = None
    project_id: str
    request_id: str
    load_case_id: str
    manifest_path: str
    source_directory: str
    schema_version: str
    status: str
    overwrite_policy: str
    file_count: int
    row_count: int
    manifest_checksum: str
    source_checksum: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    imported_at: datetime
