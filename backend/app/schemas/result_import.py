from datetime import datetime
from enum import Enum
from typing import Any, List, Optional
from pydantic import BaseModel, Field

class ResultType(str, Enum):
    OPEN_CELL_STRESS = "OPEN_CELL_STRESS"
    CHASSIS_REAR_DEFORMATION = "CHASSIS_REAR_DEFORMATION"
    GENERIC_TIME_HISTORY = "GENERIC_TIME_HISTORY"
    SCALAR_RESULTS = "SCALAR_RESULTS"
    MEDIA_ASSET = "MEDIA_ASSET"

class FileFormat(str, Enum):
    CSV = "CSV"
    JSON = "JSON"

class OverwritePolicy(str, Enum):
    REJECT = "REJECT"
    REPLACE = "REPLACE"
    APPEND = "APPEND"

class ResultFile(BaseModel):
    type: ResultType
    path: str
    format: Optional[FileFormat] = None
    time_unit: Optional[str] = None
    value_unit: Optional[str] = None
    required: bool = False
    metadata: Optional[dict[str, Any]] = None

class Manifest(BaseModel):
    schema_version: str
    project_id: str
    request_id: str
    load_case_id: str
    run_id: str
    run_no: int
    solver: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    source_program: str
    source_program_version: Optional[str] = None
    overwrite_policy: OverwritePolicy
    result_files: List[ResultFile]
    metadata: Optional[dict[str, Any]] = None

class ImportRequest(BaseModel):
    manifest_path: str
