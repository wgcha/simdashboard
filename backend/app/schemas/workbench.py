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


class WorkItemProgress(StrictModel):
    progress: int = Field(ge=1, le=99)
    updated_by: str = Field(min_length=2, max_length=80)


class WorkItemAssigneeUpdate(StrictModel):
    owner_user_id: str = Field(min_length=3, max_length=120)


class BatchProfileInput(StrictModel):
    id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=2, max_length=120)
    solver_path: str = Field(min_length=2, max_length=500)
    working_directory: str = Field(min_length=1, max_length=500)
    arguments_template: str = Field(default="{input}", max_length=1000)
    environment: dict[str, str] = Field(default_factory=dict)
    task_type_ids: list[str] = Field(min_length=1, max_length=32)
    is_active: bool = True
    updated_by: str = Field(default="관리자", min_length=2, max_length=80)

    @field_validator("solver_path", "working_directory", "arguments_template")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in "\t" for character in value):
            raise ValueError("배치 경로와 인수에는 제어 문자를 사용할 수 없습니다.")
        return value.strip()

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 40:
            raise ValueError("환경 변수는 최대 40개까지 저장할 수 있습니다.")
        for key, item in value.items():
            if not key or len(key) > 80 or not key.replace("_", "A").isalnum():
                raise ValueError(f"환경 변수 이름이 올바르지 않습니다: {key}")
            if len(item) > 500 or any(ord(character) < 32 and character not in "\t" for character in item):
                raise ValueError(f"환경 변수 값이 올바르지 않습니다: {key}")
        return value

    @field_validator("task_type_ids")
    @classmethod
    def validate_task_type_ids(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value))
        if any(not item or len(item) > 80 or not item[0].isalpha() or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for character in item) for item in normalized):
            raise ValueError("작업 유형 ID 형식이 올바르지 않습니다.")
        return normalized


class BatchDispatchCreate(StrictModel):
    batch_profile_id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    created_by: str = Field(default="실행 담당자", min_length=2, max_length=80)
