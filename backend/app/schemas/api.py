from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from .auth_limits import PASSWORD_MAX_LENGTH, PASSWORD_USERNAME_MAX_LENGTH


WidgetType = Literal[
    "kpi",
    "verdict",
    "gauge",
    "edge_bar",
    "time_series",
    "scatter",
    "result_table",
    "contour",
    "video",
    "video_grid",
    "model3d",
    "note",
    "workflow",
    "open_cell_map",
    "open_cell_summary",
    "summary",
    "chassis_summary",
    "chassis_diagram",
    "chassis_bar",
    "chassis_table",
    "run_comparison",
]

ANALYSIS_PAGE_WIDGET_TYPES = {
    "kpi",
    "verdict",
    "gauge",
    "edge_bar",
    "time_series",
    "scatter",
    "result_table",
    "contour",
    "video",
    "video_grid",
    "note",
    "open_cell_map",
    "open_cell_summary",
    "summary",
    "chassis_summary",
    "chassis_diagram",
    "chassis_bar",
    "chassis_table",
    "run_comparison",
}


class Widget(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    type: WidgetType
    title: str = Field(min_length=1, max_length=160)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=2, le=12)
    h: int = Field(ge=2, le=12)
    settings: dict[str, Any] = Field(default_factory=dict)


class AnalysisPageMeta(BaseModel):
    kind: Literal["analysis_page"] = "analysis_page"
    analysis_key: Literal["open_cell", "chassis_rear", "run_comparison", "custom"]
    status: Literal["draft", "published", "archived"]
    display_order: int = Field(ge=0)
    is_system: bool


class DashboardDefinition(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    widgets: list[Widget]
    page: AnalysisPageMeta | None = None

    @model_validator(mode="after")
    def validate_widget_layout(self) -> "DashboardDefinition":
        widget_ids = [widget.id for widget in self.widgets]
        if len(widget_ids) != len(set(widget_ids)):
            raise ValueError("위젯 ID는 대시보드 안에서 중복될 수 없습니다.")
        if any(widget.x + widget.w > 12 for widget in self.widgets):
            raise ValueError("위젯은 12열 레이아웃을 벗어날 수 없습니다.")
        if self.page and any(widget.type not in ANALYSIS_PAGE_WIDGET_TYPES for widget in self.widgets):
            raise ValueError("분석 페이지에서 지원하지 않는 위젯 유형이 포함되어 있습니다.")
        comparison_widgets = [widget for widget in self.widgets if widget.type == "run_comparison"]
        if comparison_widgets and (not self.page or self.page.analysis_key != "run_comparison" or not self.page.is_system):
            raise ValueError("Run 비교 위젯은 시스템 Run 비교 분석 페이지에서만 사용할 수 있습니다.")
        if self.page and self.page.analysis_key == "run_comparison" and len(comparison_widgets) != 1:
            raise ValueError("시스템 Run 비교 분석 페이지에는 Run 비교 위젯이 하나 필요합니다.")
        return self


class AnalysisPageCreate(BaseModel):
    load_case_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)


class AnalysisPageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    status: Literal["draft", "published", "archived"] | None = None


class AnalysisPageOrderUpdate(BaseModel):
    load_case_id: str = Field(min_length=1, max_length=120)
    page_ids: list[str]


class AnalysisPageSummary(BaseModel):
    id: str
    project_id: str
    request_id: str | None
    load_case_id: str | None
    name: str
    description: str
    version: int
    updated_at: datetime
    page: AnalysisPageMeta


class DashboardClone(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    created_by: str | None = Field(default=None, min_length=2, max_length=60)


class NaturalLanguageCommand(BaseModel):
    command: str = Field(min_length=2, max_length=500)


class WorkspaceLayoutUpdate(BaseModel):
    definition: dict[str, Any]
    updated_by: str | None = Field(default=None, min_length=2, max_length=60)


class WorkspaceLayoutResponse(BaseModel):
    project_id: str
    layout_kind: Literal["portfolio", "workflow"]
    version: int
    definition: dict[str, Any]
    updated_by: str
    updated_at: datetime


class WorkspaceLayoutVersionResponse(BaseModel):
    project_id: str
    layout_kind: Literal["portfolio", "workflow"]
    version: int
    created_by: str
    created_at: datetime
    is_valid: bool


class WorkflowStepUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    status: Literal["READY", "COMPLETED", "IN_PROGRESS", "WAITING", "BLOCKED", "FAILED"] | None = None
    owner_user_id: str | None = Field(default=None, min_length=3, max_length=120)
    # Deprecated display snapshot input. The server ignores it and resolves
    # owner from owner_user_id.
    owner: str | None = Field(default=None, min_length=1, max_length=80)
    progress: int | None = Field(default=None, ge=0, le=100)
    is_optional: bool | None = None
    note: str | None = Field(default=None, max_length=500)


class WorkflowStepDraft(BaseModel):
    id: str | None = None
    name: str = Field(min_length=2, max_length=80)
    status: Literal["READY", "COMPLETED", "IN_PROGRESS", "WAITING", "BLOCKED", "FAILED"]
    owner_user_id: str = Field(min_length=3, max_length=120)
    owner: str | None = Field(default=None, min_length=1, max_length=80)
    progress: int = Field(ge=0, le=100)
    is_optional: bool = False
    note: str = Field(default="", max_length=500)


class WorkflowStepsReplace(BaseModel):
    steps: list[WorkflowStepDraft] = Field(min_length=1, max_length=50)


class QualityThresholdUpdate(BaseModel):
    threshold_double: float = Field(gt=0, le=1000)
    updated_by: str | None = Field(default=None, min_length=2, max_length=40)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    product_name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    manufacturer: str = Field(default="", max_length=120)
    display_size_inch: float | None = Field(default=None, gt=0, le=200)


class AnalysisRequestCreate(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    owner_user_id: str = Field(min_length=3, max_length=120)
    # Compatibility-only input; the canonical snapshot comes from users.
    owner: str | None = Field(default=None, min_length=2, max_length=60)
    due_in_days: int = Field(default=7, ge=1, le=365)
    overall_note: str = Field(default="", max_length=500)
    source_type: Literal["EXTERNAL_SYSTEM", "DEPARTMENT_HEAD"]
    source_reference: str = Field(min_length=2, max_length=160)
    requested_by: str | None = Field(default=None, min_length=2, max_length=80)
    request_type_id: str = Field(
        default="design-reliability-validation",
        min_length=3,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
    request_type_version: int = Field(default=1, ge=1)
    assigned_by: str | None = Field(default=None, min_length=2, max_length=80)


class AssigneeUpdate(BaseModel):
    owner_user_id: str = Field(min_length=3, max_length=120)


class LoadCaseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    analysis_type: Literal["DROP", "SIDE_CLAMP"]
    parameters: dict[str, Any] = Field(default_factory=dict)


class DropVideoSubsystemEvaluation(BaseModel):
    critical_value: float = Field(ge=0)
    threshold: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=20)
    verdict: Literal["PASS", "FAIL"]
    metrics: dict[str, float] = Field(default_factory=dict, max_length=8)


class DropVideoEvaluation(BaseModel):
    overall_verdict: Literal["PASS", "FAIL"]
    open_cell: DropVideoSubsystemEvaluation
    chassis_rear: DropVideoSubsystemEvaluation


class DropVideoItemResponse(BaseModel):
    video_id: str
    scene_id: str
    scene_name: str
    video_url: str
    download_url: str | None = None
    thumbnail_url: str | None
    duration: float | None = Field(default=None, ge=0)
    file_size: int = Field(ge=0)
    format: Literal["mp4", "webm"]
    codec: str | None
    fast_start: bool | None
    sort_order: int = Field(ge=1)
    drop_direction: str | None
    drop_condition: str | None
    analysis_version: str | None
    evaluation: DropVideoEvaluation


class DropVideoLoadCaseResponse(BaseModel):
    load_case_id: str
    load_case_name: str
    analysis_type: str
    request_id: str
    request_name: str


class DropVideoPaginationResponse(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=20)
    total_items: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    has_previous: bool
    has_next: bool


class DropVideoSubsystemSummary(BaseModel):
    pass_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    threshold: float = Field(gt=0)
    unit: str = Field(min_length=1, max_length=20)


class DropVideoEvaluationSummary(BaseModel):
    total_scenes: int = Field(ge=0)
    pass_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    open_cell: DropVideoSubsystemSummary
    chassis_rear: DropVideoSubsystemSummary


class DropVideoPageResponse(BaseModel):
    load_case: DropVideoLoadCaseResponse
    source: Literal["DATABASE", "EXAMPLE_ADAPTER"]
    demo_only: bool
    evaluation_source: Literal["SYNTHETIC_DEMO"]
    contract_version: Literal[1]
    summary: DropVideoEvaluationSummary
    pagination: DropVideoPaginationResponse
    videos: list[DropVideoItemResponse]


class ResultImportPayload(BaseModel):
    filename: str = Field(min_length=5, max_length=240)
    content: str = Field(min_length=1, max_length=5_000_000)
    author: str | None = Field(default=None, min_length=2, max_length=60)
    validate_only: bool = False
    source_run_id: str | None = None
    conflict_policy: Literal["SKIP", "REJECT", "REPLACE"] = "SKIP"

    @field_validator("source_run_id", mode="before")
    @classmethod
    def normalize_source_run_id(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("source_run_id는 문자열이어야 합니다.")
        normalized = value.strip()
        if not normalized or len(normalized) > 120 or not normalized.isprintable():
            raise ValueError("source_run_id 형식이 올바르지 않습니다.")
        return normalized


class ResultImportResponse(BaseModel):
    """Stable response contract for manual result imports."""

    status: Literal["VALID", "IMPORTED", "SKIPPED", "REJECTED"]
    run_id: str | None = None
    run_no: int | None = None
    filename: str
    # ``summary`` is additive for clients that prefer a grouped shape.  The
    # flattened counters/source_format below remain for existing consumers.
    summary: dict[str, Any] | None = None
    scalar_count: int | None = None
    node_count: int | None = None
    element_count: int | None = None
    frame_count: int | None = None
    final_time: float | None = None
    time_series_count: int | None = None
    open_cell_count: int | None = None
    chassis_rear_count: int | None = None
    fail_count: int | None = None
    overall_verdict: Literal["PASS", "FAIL"] | None = None
    source_format: str | None = None
    results: list[Any] = Field(default_factory=list)
    warnings: list[Any] = Field(default_factory=list)
    operation: Literal["CREATED", "NOOP", "REPLACED", "REJECTED"] | None = None
    reason_code: str | None = None
    existing_run_id: str | None = None
    replaced_run_id: str | None = None
    source_revision: int | None = None


class TypedResultExampleResponse(BaseModel):
    """Named response contract for the checked-in typed example importer."""

    status: Literal["IMPORTED", "SKIPPED", "REJECTED"]
    job_id: str
    run_id: str | None = None
    run_no: int | None = None
    schema_id: str
    summary: dict[str, Any]
    operation: Literal["CREATED", "NOOP", "REPLACED", "REJECTED"] | None = None
    reason_code: str | None = None
    existing_run_id: str | None = None
    replaced_run_id: str | None = None
    source_revision: int | None = None


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
    updated_by: str | None = Field(default=None, min_length=2, max_length=60)


class VariableUpdate(BaseModel):
    display_name: str = Field(min_length=2, max_length=120)
    unit: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=500)
    filterable: bool = True
    threshold: float | None = None
    allowed_widgets: list[str] = Field(default_factory=list, max_length=12)
    allowed_aggregations: list[str] = Field(default_factory=list, max_length=8)
    result_group: Literal["OPEN_CELL", "CHASSIS_REAR", "CUSTOM"] = "CUSTOM"
    updated_by: str | None = Field(default=None, min_length=2, max_length=60)


class ImportSchemaPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    definition: dict[str, Any]
    updated_by: str | None = Field(default=None, min_length=2, max_length=60)


class ReportLayoutPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    definition: dict[str, Any]
    updated_by: str | None = Field(default=None, min_length=2, max_length=60)


class ReportTemplateUploadPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    filename: str = Field(min_length=6, max_length=240)
    content_base64: str = Field(min_length=8, max_length=36_000_000)
    updated_by: str | None = Field(default=None, min_length=2, max_length=60)


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
    created_by: str | None = Field(default=None, min_length=2, max_length=60)


class ReviewItemUpdate(BaseModel):
    body: str | None = Field(default=None, min_length=2, max_length=2000)
    review_status: Literal["OPEN", "IN_REVIEW", "RESOLVED"]


class LoginPayload(BaseModel):
    username: str = Field(min_length=2, max_length=PASSWORD_USERNAME_MAX_LENGTH)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
