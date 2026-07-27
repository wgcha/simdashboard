from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class Widget(BaseModel):
    id: str
    type: str
    title: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=2, le=12)
    h: int = Field(ge=2, le=12)
    settings: dict[str, Any] = Field(default_factory=dict)


class DashboardDefinition(BaseModel):
    id: str
    name: str
    description: str = ""
    widgets: list[Widget]


class DashboardClone(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    created_by: str = Field(default="대시보드 사용자", min_length=2, max_length=60)


class NaturalLanguageCommand(BaseModel):
    command: str = Field(min_length=2, max_length=500)


class WorkspaceLayoutUpdate(BaseModel):
    definition: dict[str, Any]
    updated_by: str = Field(default="대시보드 사용자", min_length=2, max_length=60)


class WorkspaceLayoutResponse(BaseModel):
    layout_kind: Literal["portfolio", "workflow"]
    version: int
    definition: dict[str, Any]
    updated_by: str
    updated_at: datetime


class WorkspaceLayoutVersionResponse(BaseModel):
    layout_kind: Literal["portfolio", "workflow"]
    version: int
    created_by: str
    created_at: datetime
    is_valid: bool


class WorkflowStepUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    status: Literal["COMPLETED", "IN_PROGRESS", "WAITING", "BLOCKED", "FAILED"] | None = None
    owner: str | None = Field(default=None, min_length=1, max_length=80)
    progress: int | None = Field(default=None, ge=0, le=100)
    is_optional: bool | None = None
    note: str | None = Field(default=None, max_length=500)


class WorkflowStepDraft(BaseModel):
    id: str | None = None
    name: str = Field(min_length=2, max_length=80)
    status: Literal["COMPLETED", "IN_PROGRESS", "WAITING", "BLOCKED", "FAILED"]
    owner: str = Field(min_length=1, max_length=80)
    progress: int = Field(ge=0, le=100)
    is_optional: bool = False
    note: str = Field(default="", max_length=500)


class WorkflowStepsReplace(BaseModel):
    steps: list[WorkflowStepDraft] = Field(min_length=1, max_length=50)


class QualityThresholdUpdate(BaseModel):
    threshold_double: float = Field(gt=0, le=1000)
    updated_by: str = Field(default="관리자", min_length=2, max_length=40)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    product_name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    manufacturer: str = Field(default="", max_length=120)
    display_size_inch: float | None = Field(default=None, gt=0, le=200)


class AnalysisRequestCreate(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    owner: str = Field(min_length=2, max_length=60)
    due_in_days: int = Field(default=7, ge=1, le=365)
    overall_note: str = Field(default="", max_length=500)


class LoadCaseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    analysis_type: Literal["DROP", "SIDE_CLAMP"]
    parameters: dict[str, Any] = Field(default_factory=dict)


class ResultImportPayload(BaseModel):
    filename: str = Field(min_length=5, max_length=240)
    content: str = Field(min_length=1, max_length=5_000_000)
    author: str = Field(default="해석 담당자", min_length=2, max_length=60)
    validate_only: bool = False


class VariableCreate(BaseModel):
    variable_key: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str = Field(min_length=2, max_length=120)
    data_type: Literal["NUMBER", "TIME_SERIES", "FLOAT", "INTEGER", "TEXT", "CURVE", "IMAGE", "VIDEO", "MODEL_3D", "VERDICT", "STATUS", "BOOLEAN"]
    unit: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=500)
    filterable: bool = True
    threshold: float | None = None
    allowed_widgets: list[str] = Field(default_factory=list, max_length=12)
    allowed_aggregations: list[str] = Field(default_factory=list, max_length=8)
    result_group: Literal["OPEN_CELL", "CHASSIS_REAR", "CUSTOM"] = "CUSTOM"
    updated_by: str = Field(default="관리자", min_length=2, max_length=60)


class VariableUpdate(BaseModel):
    display_name: str = Field(min_length=2, max_length=120)
    unit: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=500)
    filterable: bool = True
    threshold: float | None = None
    allowed_widgets: list[str] = Field(default_factory=list, max_length=12)
    allowed_aggregations: list[str] = Field(default_factory=list, max_length=8)
    result_group: Literal["OPEN_CELL", "CHASSIS_REAR", "CUSTOM"] = "CUSTOM"
    updated_by: str = Field(default="관리자", min_length=2, max_length=60)


class ImportSchemaPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    definition: dict[str, Any]
    updated_by: str = Field(default="관리자", min_length=2, max_length=60)


class ReportLayoutPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    definition: dict[str, Any]
    updated_by: str = Field(default="보고서 편집자", min_length=2, max_length=60)


class ReportTemplateUploadPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    filename: str = Field(min_length=6, max_length=240)
    content_base64: str = Field(min_length=8, max_length=36_000_000)
    updated_by: str = Field(default="보고서 편집자", min_length=2, max_length=60)


class ReportTemplateRenderPayload(BaseModel):
    replacements: dict[str, str] = Field(default_factory=dict)
    filename: str = Field(default="analysis-report.pptx", min_length=6, max_length=240)


class ReviewItemCreate(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    body: str = Field(min_length=2, max_length=2000)
    variable_key: str | None = Field(default=None, max_length=120)
    time_value: float | None = None
    entity_type: Literal["NODE", "ELEMENT"] | None = None
    entity_id: str | None = Field(default=None, max_length=120)
    review_status: Literal["OPEN", "IN_REVIEW", "RESOLVED"] = "OPEN"
    created_by: str = Field(default="검토자", min_length=2, max_length=60)


class ReviewItemUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=2, max_length=2000)
    review_status: Literal["OPEN", "IN_REVIEW", "RESOLVED"]


class LoginPayload(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class UserAccessUpdate(BaseModel):
    role: Literal["viewer", "editor", "admin"]
    is_active: bool
