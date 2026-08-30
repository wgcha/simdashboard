from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..repositories.workbench import BatchAttemptWorkflowRunLinkConflictError, WorkbenchRepository
from ..schemas.workbench import DemoRunCreate, WorkflowNodeDraft


class WorkbenchValidationError(ValueError):
    pass


DEMO_MESSAGES: dict[str, tuple[str, str, str]] = {
    "CAD_PREPARE": ("등록 형상 입력을 확인했습니다.", "데모 형상 파라미터를 적용했습니다.", "CAD 데모 이미지를 준비했습니다."),
    "DOE_GENERATE": ("설계변수 계약을 확인했습니다.", "결정적 데모 설계점을 생성했습니다.", "DOE 데모 manifest를 준비했습니다."),
    "ANALYSIS_MODELING": ("모델링 입력 계약을 확인했습니다.", "데모 경계조건을 검토했습니다.", "Solver Deck 데모를 준비했습니다."),
    "HPC_SUBMIT": ("DEMO_ONLY 모드를 확인했습니다.", "가상 Scheduler 접수 이벤트를 생성했습니다.", "실제 HPC 제출 없이 완료했습니다."),
    "EXECUTION_MONITOR": ("가상 작업 상태를 연결했습니다.", "결정적 로그 이벤트를 재생했습니다.", "실제 프로세스 감시 없이 완료했습니다."),
    "RESULT_COLLECT": ("데모 결과 위치 계약을 확인했습니다.", "데모 결과 목록을 수집했습니다.", "결과 manifest 데모를 준비했습니다."),
    "POST_PROCESS": ("후처리 입력을 확인했습니다.", "데모 KPI와 contour를 구성했습니다.", "후처리 데모 이미지를 준비했습니다."),
    "ANALYSIS_DB_PUBLISH": ("발행 대상 데모를 확인했습니다.", "중복 방지 키를 검토했습니다.", "실제 결과 DB 변경 없이 완료했습니다."),
    "TRAINING_DATASET_PUBLISH": ("데이터셋 입력 계약을 확인했습니다.", "데모 split과 계보를 구성했습니다.", "실제 학습 데이터 저장 없이 완료했습니다."),
    "PHYSICSAI_TRAIN_VALIDATE": ("DEMO_ONLY 모드를 확인했습니다.", "결정적 학습/검증 지표를 구성했습니다.", "PhysicsAI 실행 없이 완료했습니다."),
    "MODEL_APPROVAL": ("데모 모델 후보를 확인했습니다.", "승인 기준 미리보기를 생성했습니다.", "실제 운영 모델 상태 변경 없이 완료했습니다."),
    "REALTIME_PREDICT": ("데모 입력 형상을 확인했습니다.", "결정적 예측 값을 구성했습니다.", "PhysicsAI 추론 실행 없이 완료했습니다."),
    "DASHBOARD_VISUALIZE": ("데모 결과 binding을 확인했습니다.", "시각화 미리보기를 구성했습니다.", "대시보드 데모 이미지를 준비했습니다."),
    "RELIABILITY_EVALUATION": ("신뢰성 평가 입력을 확인했습니다.", "오픈셀 파손 및 CHR 휨 지표를 평가했습니다.", "신뢰성 평가 데모 결과를 준비했습니다."),
    "OPTIMIZATION_ANALYSIS": ("최적화 분석 입력을 확인했습니다.", "설계 후보의 데모 목적함수를 비교했습니다.", "최적화 분석 데모 결과를 준비했습니다."),
    "PERFORMANCE_RANKING": ("설계 성능 지표를 확인했습니다.", "설계 후보의 데모 순위를 계산했습니다.", "설계 성능 순위 데모 결과를 준비했습니다."),
}

DEMO_TEXT_ARTIFACTS: tuple[dict[str, str], ...] = (
    {
        "id": "execution-log",
        "kind": "LOG",
        "label": "실행 로그",
        "url": "/assets/demo-execution.log",
        "media_type": "text/plain",
    },
    {
        "id": "validation-report",
        "kind": "VALIDATION",
        "label": "검증 리포트",
        "url": "/assets/demo-validation-report.txt",
        "media_type": "text/plain",
    },
    {
        "id": "result-summary",
        "kind": "RESULT",
        "label": "결과 요약",
        "url": "/assets/demo-result-summary.txt",
        "media_type": "text/plain",
    },
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
    return value


_RUN_IDENTITY_CONSTRAINTS = {
    "workflow_runs_pkey",
    "ux_workflow_runs_batch_attempt_id",
}


def _is_deterministic_run_identity_conflict(exc: Exception) -> bool:
    """Recognize only the two unique conflicts that a safe retry can replay."""
    errors = (exc, getattr(exc, "orig", None))
    codes = {
        str(code)
        for error in errors
        if error is not None
        for code in (getattr(error, "sqlstate", None), getattr(error, "pgcode", None))
        if code is not None
    }
    constraints = {
        str(name).lower()
        for error in errors
        if error is not None
        for name in (
            getattr(error, "constraint_name", None),
            getattr(getattr(error, "diag", None), "constraint_name", None),
        )
        if name is not None
    }
    if "23505" in codes and constraints & _RUN_IDENTITY_CONSTRAINTS:
        return True

    message = " ".join(str(error).lower() for error in errors if error is not None)
    is_run_id_duplicate = 'duplicate key "id:' in message and (
        "workflow_runs_pkey" in message or "primary key" in message
    )
    is_attempt_id_duplicate = 'duplicate key "batch_attempt_id:' in message and (
        "ux_workflow_runs_batch_attempt_id" in message or "unique constraint" in message
    )
    return is_run_id_duplicate or is_attempt_id_duplicate


def _topological_nodes(nodes: list[WorkflowNodeDraft]) -> list[WorkflowNodeDraft]:
    by_key = {node.node_key: node for node in nodes}
    if len(by_key) != len(nodes):
        raise WorkbenchValidationError("Workflow node_key가 중복되었습니다.")
    for node in nodes:
        if len(set(node.depends_on)) != len(node.depends_on):
            raise WorkbenchValidationError(f"{node.node_key}의 dependency가 중복되었습니다.")
        if node.node_key in node.depends_on:
            raise WorkbenchValidationError(f"{node.node_key}는 자기 자신에 의존할 수 없습니다.")
        unknown = [key for key in node.depends_on if key not in by_key]
        if unknown:
            raise WorkbenchValidationError(f"{node.node_key}의 dependency를 찾을 수 없습니다: {', '.join(unknown)}")

    remaining = {key: set(node.depends_on) for key, node in by_key.items()}
    ordered: list[WorkflowNodeDraft] = []
    while remaining:
        ready = [node.node_key for node in nodes if node.node_key in remaining and not remaining[node.node_key]]
        if not ready:
            raise WorkbenchValidationError("Workflow dependency에 순환이 있습니다.")
        for key in ready:
            ordered.append(by_key[key])
            remaining.pop(key)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return ordered


class DemoRunnerService:
    """Persists deterministic demo events and never starts an external process."""

    def __init__(self, repository: WorkbenchRepository):
        self.repository = repository

    def _validated_plan(self, payload: DemoRunCreate) -> tuple[list[WorkflowNodeDraft], dict[str, Any] | None, dict[str, dict[str, Any]]]:
        ordered = _topological_nodes(payload.nodes)
        if payload.request_type_version is not None and payload.request_type_id is None:
            raise WorkbenchValidationError("request_type_version에는 request_type_id가 필요합니다.")

        request_type = None
        if payload.request_type_id:
            request_type = (
                self.repository.get_request_type(payload.request_type_id, payload.request_type_version)
                if payload.request_type_version is not None
                else self.repository.latest_request_type(payload.request_type_id)
            )
            if not request_type or not request_type["is_active"]:
                raise WorkbenchValidationError("사용 가능한 Request Type 버전을 찾을 수 없습니다.")

        if payload.request_id and not self.repository.analysis_request_exists(payload.request_id):
            raise WorkbenchValidationError("연결할 해석 의뢰를 찾을 수 없습니다.")

        if payload.request_id:
            work_plan = self.repository.work_plan(payload.request_id)
            if work_plan:
                if payload.request_type_id and (
                    payload.request_type_id != work_plan["request_type_id"]
                    or (payload.request_type_version is not None and payload.request_type_version != work_plan["request_type_version"])
                ):
                    raise WorkbenchValidationError("의뢰에 고정된 작업 시나리오와 Request Type이 다릅니다.")
                request_type = self.repository.get_request_type(
                    work_plan["request_type_id"],
                    int(work_plan["request_type_version"]),
                )
                current = next(
                    (item for item in self.repository.work_items(payload.request_id) if item["status"] == "IN_PROGRESS"),
                    None,
                )
                if not current:
                    raise WorkbenchValidationError("현재 실행할 수 있는 작업이 없습니다.")
                if len(ordered) != 1:
                    raise WorkbenchValidationError("작업 시나리오 의뢰는 현재 작업 하나만 실행할 수 있습니다.")
                node = ordered[0]
                if (
                    node.node_key != current["node_key"]
                    or node.task_type_id != current["task_type_id"]
                    or node.task_type_version != current["task_type_version"]
                ):
                    raise WorkbenchValidationError(f"현재 실행 가능한 작업이 아닙니다: {current['display_name']}")

        allowed = None
        if request_type:
            allowed = {(item["id"], int(item["version"])) for item in request_type["allowed_task_types"]}

        task_types: dict[str, dict[str, Any]] = {}
        for node in ordered:
            task_type = self.repository.get_task_type(node.task_type_id, node.task_type_version)
            if not task_type or not task_type["is_active"]:
                raise WorkbenchValidationError(f"사용 가능한 Task Type이 아닙니다: {node.task_type_id} v{node.task_type_version}")
            if allowed is not None and (node.task_type_id, node.task_type_version) not in allowed:
                raise WorkbenchValidationError(f"Request Type에서 허용하지 않은 Task입니다: {node.task_type_id}")
            if len(ordered) == 1 and not task_type["supports_standalone"]:
                raise WorkbenchValidationError(f"단독 실행을 지원하지 않는 Task입니다: {node.task_type_id}")
            task_types[node.node_key] = task_type
        return ordered, request_type, task_types

    def create_run(
        self,
        payload: DemoRunCreate,
        *,
        run_id: str | None = None,
        batch_attempt_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a demo run, optionally under a caller-owned stable identity.

        The stable identity is intentionally an internal service concern.  Batch
        dispatch uses it to make a retry after a partial commit return the same
        workflow run instead of attempting to insert a second one.
        """
        if (run_id is None) != (batch_attempt_id is None):
            raise WorkbenchValidationError("run_id와 batch_attempt_id는 함께 지정해야 합니다.")
        if run_id is not None:
            run_id = run_id.strip()
            batch_attempt_id = batch_attempt_id.strip()  # type: ignore[union-attr]
            if not run_id or not batch_attempt_id:
                raise WorkbenchValidationError("run_id와 batch_attempt_id는 비어 있을 수 없습니다.")
            attempt = self.repository.batch_attempt(batch_attempt_id)
            work_item = self.repository.work_item(str(attempt["work_item_id"])) if attempt else None
            node = payload.nodes[0] if len(payload.nodes) == 1 else None
            if (
                not attempt
                or not work_item
                or work_item.get("request_id") != payload.request_id
                or attempt.get("execution_mode") != "DEMO_ONLY"
                or attempt.get("created_by") != payload.created_by.strip()
                or attempt.get("status") not in {"QUEUED", "SUCCEEDED"}
                or node is None
                or node.node_key != work_item.get("node_key")
                or node.task_type_id != work_item.get("task_type_id")
                or node.task_type_version != work_item.get("task_type_version")
                or (attempt.get("status") == "QUEUED" and attempt.get("workflow_run_id") not in {None, run_id})
                or (attempt.get("status") == "SUCCEEDED" and attempt.get("workflow_run_id") != run_id)
            ):
                raise WorkbenchValidationError("batch attempt의 요청·DEMO_ONLY mode·소유권이 실행 요청과 일치하지 않습니다.")

            expected_definition = {"nodes": [node.model_dump() for node in payload.nodes]}

            def matches_existing(existing: dict[str, Any]) -> bool:
                return (
                    existing.get("batch_attempt_id") == batch_attempt_id
                    and existing.get("request_id") == payload.request_id
                    and existing.get("execution_mode") == "DEMO_ONLY"
                    and existing.get("created_by") == payload.created_by.strip()
                    and existing.get("status") == "SUCCEEDED"
                    and existing.get("name") == payload.name.strip()
                    and _json(existing.get("definition_json")) == expected_definition
                )

            # Check the attempt-owned record first.  This catches a unique
            # batch_attempt_id collision with a different run ID and permits
            # recovery even after the work item's mutable lifecycle advances.
            owned_run = self.repository.workflow_run_by_batch_attempt(batch_attempt_id)
            if owned_run:
                if owned_run.get("id") != run_id or not matches_existing(owned_run):
                    raise WorkbenchValidationError("batch_attempt_id가 다른 deterministic run에 이미 연결되어 있습니다.")
                return self.get_run(run_id)  # type: ignore[return-value]
            existing = self.repository.get_workflow_run(run_id)
            if existing:
                raise WorkbenchValidationError("run_id가 이 batch attempt의 DEMO_ONLY 실행 소유권과 일치하지 않습니다.")
        ordered, request_type, task_types = self._validated_plan(payload)
        run_id = run_id or f"demo-{uuid4().hex[:12]}"
        base_time = _utcnow()
        definition = {"nodes": [node.model_dump() for node in payload.nodes]}
        run = {
            "id": run_id,
            "name": payload.name.strip(),
            "request_id": payload.request_id,
            "request_type_id": request_type["id"] if request_type else None,
            "request_type_version": request_type["version"] if request_type else None,
            "definition_json": json.dumps(definition, ensure_ascii=False),
            "execution_mode": "DEMO_ONLY",
            "status": "SUCCEEDED",
            "progress": 100,
            "created_by": payload.created_by.strip(),
            "created_at": base_time,
            "started_at": base_time,
            "completed_at": base_time + timedelta(seconds=max(1, len(ordered) * 3)),
            "batch_attempt_id": batch_attempt_id,
        }

        self.repository.conn.execute("BEGIN TRANSACTION")
        try:
            self.repository.insert_workflow_run(run)
            if batch_attempt_id is not None:
                # This is intentionally not a status transition.  It commits
                # with the runner record so a crash before finalization still
                # leaves the QUEUED attempt and run mutually identifiable.
                self.repository.link_batch_attempt_workflow_run(batch_attempt_id, run_id)
            for task_index, node in enumerate(ordered):
                task_type = task_types[node.node_key]
                task_run_id = f"task-{uuid4().hex[:12]}"
                started_at = base_time + timedelta(seconds=task_index * 3)
                task = {
                    "id": task_run_id,
                    "workflow_run_id": run_id,
                    "node_key": node.node_key,
                    "task_type_id": node.task_type_id,
                    "task_type_version": node.task_type_version,
                    "status": "SUCCEEDED",
                    "progress": 100,
                    "depends_on_json": json.dumps(node.depends_on, ensure_ascii=False),
                    "demo_artifact_url": task_type["demo_artifact_url"],
                    "started_at": started_at,
                    "completed_at": started_at + timedelta(seconds=2),
                }
                self.repository.insert_task_run(task)
                messages = DEMO_MESSAGES[task_type["kind"]]
                for event_index, (event_type, level, message, progress) in enumerate(
                    (
                        ("TASK_STARTED", "INFO", messages[0], 0),
                        ("DEMO_PROGRESS", "INFO", messages[1], 50),
                        ("TASK_SUCCEEDED", "INFO", messages[2], 100),
                    )
                ):
                    self.repository.insert_task_event(
                        {
                            "id": f"event-{uuid4().hex[:12]}",
                            "task_run_id": task_run_id,
                            "event_index": event_index,
                            "event_type": event_type,
                            "level": level,
                            "message": message,
                            "progress": progress,
                            "occurred_at": started_at + timedelta(seconds=event_index),
                        }
                    )
            self.repository.conn.execute("COMMIT")
        except Exception as exc:
            self.repository.conn.execute("ROLLBACK")
            if isinstance(exc, BatchAttemptWorkflowRunLinkConflictError):
                raise WorkbenchValidationError(
                    "batch attempt 상태가 바뀌어 deterministic runner identity를 연결할 수 없습니다."
                ) from exc
            if run_id is not None and batch_attempt_id is not None and _is_deterministic_run_identity_conflict(exc):
                # Concurrent deterministic creates may collide after both
                # callers performed the absence check.  Re-read only the
                # immutable attempt-owned identity after rollback; do not
                # adopt a run merely because its deterministic ID exists.
                recovered = self.repository.workflow_run_by_batch_attempt(batch_attempt_id)
                recovered_attempt = self.repository.batch_attempt(batch_attempt_id)
                if recovered and (
                    recovered_attempt is not None
                    and recovered_attempt.get("execution_mode") == "DEMO_ONLY"
                    and recovered_attempt.get("created_by") == payload.created_by.strip()
                    and (
                        (recovered_attempt.get("status") == "QUEUED" and recovered_attempt.get("workflow_run_id") == run_id)
                        or (recovered_attempt.get("status") == "SUCCEEDED" and recovered_attempt.get("workflow_run_id") == run_id)
                    )
                    and recovered.get("id") == run_id
                    and recovered.get("request_id") == payload.request_id
                    and recovered.get("execution_mode") == "DEMO_ONLY"
                    and recovered.get("created_by") == payload.created_by.strip()
                    and recovered.get("status") == "SUCCEEDED"
                    and recovered.get("name") == payload.name.strip()
                    and _json(recovered.get("definition_json")) == definition
                ):
                    return self.get_run(run_id)  # type: ignore[return-value]
                if recovered or self.repository.get_workflow_run(run_id):
                    raise WorkbenchValidationError("deterministic batch run identity conflicts with an existing workflow run.") from exc
            raise
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.repository.get_workflow_run(run_id)
        if not run:
            return None
        # It is a persistence-only recovery identity, never part of the
        # public WorkflowRun API projection.
        run.pop("batch_attempt_id", None)
        run["definition"] = _json(run.pop("definition_json")) or {"nodes": []}
        tasks = self.repository.task_runs(run_id)
        for task in tasks:
            task["depends_on"] = _json(task.pop("depends_on_json")) or []
            task["events"] = self.repository.task_events(task["id"])
            task["demo_text_artifacts"] = [dict(item) for item in DEMO_TEXT_ARTIFACTS]
        run["tasks"] = tasks
        run["batch_dispatch"] = self.repository.batch_dispatch_for_run(run_id)
        run["batch_attempt"] = self.repository.batch_attempt_for_run(run_id)
        return run

    def list_runs(self, request_id: str | None = None) -> list[dict[str, Any]]:
        return [self.get_run(item["id"]) for item in self.repository.list_workflow_runs(request_id)]  # type: ignore[misc]
