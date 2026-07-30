from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TaskKind = Literal[
    "CAD_PREPARE",
    "DOE_GENERATE",
    "ANALYSIS_MODELING",
    "HPC_SUBMIT",
    "EXECUTION_MONITOR",
    "RESULT_COLLECT",
    "POST_PROCESS",
    "ANALYSIS_DB_PUBLISH",
    "TRAINING_DATASET_PUBLISH",
    "PHYSICSAI_TRAIN_VALIDATE",
    "MODEL_APPROVAL",
    "REALTIME_PREDICT",
    "DASHBOARD_VISUALIZE",
    "RELIABILITY_EVALUATION",
    "OPTIMIZATION_ANALYSIS",
    "PERFORMANCE_RANKING",
]


class StrictModel(BaseModel):
    """Workbench payloads reject undeclared execution-like fields by design."""

    model_config = ConfigDict(extra="forbid")


class TaskTypeRef(StrictModel):
    id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    version: int = Field(default=1, ge=1)


class WorkflowNodeDraft(StrictModel):
    node_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    task_type_id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    task_type_version: int = Field(default=1, ge=1)
    depends_on: list[str] = Field(default_factory=list, max_length=32)


class WorkflowDefinitionDraft(StrictModel):
    nodes: list[WorkflowNodeDraft] = Field(min_length=1, max_length=32)


class TaskTypeVersionCreate(StrictModel):
    id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    kind: TaskKind
    display_name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    supports_standalone: bool = True
    input_artifact_types: list[str] = Field(default_factory=list, max_length=24)
    output_artifact_types: list[str] = Field(default_factory=list, max_length=24)
    parameter_schema: dict[str, Any] = Field(default_factory=dict)
    demo_artifact_url: str = Field(default="/assets/demo-workbench.svg", pattern=r"^/assets/[a-zA-Z0-9._/-]+$")
    is_active: bool = True

    @field_validator("demo_artifact_url")
    @classmethod
    def validate_demo_artifact_url(cls, value: str) -> str:
        if ".." in value.split("/") or "\\" in value or not value.startswith("/assets/"):
            raise ValueError("demo_artifact_url은 등록된 assets 상대 경로만 사용할 수 있습니다.")
        return value


class RequestTypeVersionCreate(StrictModel):
    id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    allowed_task_types: list[TaskTypeRef] = Field(min_length=1, max_length=32)
    default_workflow: WorkflowDefinitionDraft
    match_rules: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class RequestTypeAssignmentInput(StrictModel):
    request_type_id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    request_type_version: int = Field(ge=1)


class DemoRunCreate(StrictModel):
    name: str = Field(min_length=2, max_length=160)
    request_id: str | None = Field(default=None, min_length=3, max_length=100)
    request_type_id: str | None = Field(default=None, min_length=3, max_length=80)
    request_type_version: int | None = Field(default=None, ge=1)
    execution_mode: Literal["DEMO_ONLY"] = "DEMO_ONLY"
    nodes: list[WorkflowNodeDraft] = Field(min_length=1, max_length=32)
    created_by: str = Field(default="데모 사용자", min_length=2, max_length=80)


class WorkItemComplete(StrictModel):
    completed_by: str = Field(min_length=2, max_length=80)
    demo_run_id: str | None = Field(default=None, min_length=3, max_length=100)


class WorkItemStart(StrictModel):
    started_by: str = Field(min_length=2, max_length=80)
