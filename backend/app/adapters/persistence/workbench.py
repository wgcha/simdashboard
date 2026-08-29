"""SQL adapters for isolated workbench query and command boundaries."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

from ...database_connection import ConnectionLike, rows
from ...domains.workbench.models import (
    RequestTypeAssignmentCommand,
    RequestTypeAssignmentLockedError,
    RequestTypeAssignmentTargetNotFoundError,
    RequestTypeResolution,
    RequestTypeResolutionRead,
    RequestTypeVersionRead,
    TaskTypeVersionRead,
    ProjectAssigneeRead,
    BatchDispatchCommand,
    BatchDispatchContext,
    BatchDispatchPreflight,
    BatchDemoRunValidationError,
    BatchPreflightFailedError,
    WorkItemCompleteCommand,
    WorkItemAssigneeAccountNotActiveError,
    WorkItemAssigneeMembershipRequiredError,
    WorkItemLifecycleState,
    WorkItemProgressCommand,
    WorkItemReassignmentState,
    WorkItemStartCommand,
    WorkPlanMonitoringSummaryRead,
)
from ...modules.access_control import resolve_project_assignee
from ...repositories.workbench import WorkbenchRepository
from ...services.request_monitoring import request_monitoring_summary, sync_request_status
from ...services.batch_execution import BatchPreflightError, preflight_batch_profile
from ...services.demo_runner import DemoRunnerService, WorkbenchValidationError
from ...schemas.workbench import DemoRunCreate


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _task_type_read(item: dict[str, Any]) -> TaskTypeVersionRead:
    return {
        "id": str(item["id"]),
        "version": int(item["version"]),
        "kind": str(item["kind"]),
        "display_name": str(item["display_name"]),
        "description": str(item["description"]),
        "supports_standalone": bool(item["supports_standalone"]),
        "input_artifact_types": list(item["input_artifact_types"]),
        "output_artifact_types": list(item["output_artifact_types"]),
        "parameter_schema": dict(item["parameter_schema"]),
        "demo_artifact_url": str(item["demo_artifact_url"]),
        "is_active": bool(item["is_active"]),
        "created_at": item["created_at"],
    }


def _request_type_read(item: dict[str, Any]) -> RequestTypeVersionRead:
    return {
        "id": str(item["id"]),
        "version": int(item["version"]),
        "display_name": str(item["display_name"]),
        "description": str(item["description"]),
        "allowed_task_types": list(item["allowed_task_types"]),
        "default_workflow": dict(item["default_workflow"]),
        "match_rules": dict(item["match_rules"]),
        "is_active": bool(item["is_active"]),
        "created_at": item["created_at"],
    }


class SQLWorkbenchCatalogQuery:
    """Adapt the established workbench repository catalog facade to a read port."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._repository = WorkbenchRepository(connection)

    def list_task_types(self, *, all_versions: bool) -> list[TaskTypeVersionRead]:
        return [_task_type_read(item) for item in self._repository.list_task_types(all_versions=all_versions)]

    def list_request_types(self, *, all_versions: bool) -> list[RequestTypeVersionRead]:
        return [_request_type_read(item) for item in self._repository.list_request_types(all_versions=all_versions)]


class SQLWorkbenchRequestTypeResolutionQuery:
    """Adapt the repository's existing context/assignment/rule query sequence."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._repository = WorkbenchRepository(connection)

    def request_type_resolution(self, request_id: str) -> RequestTypeResolutionRead:
        return _request_type_resolution_read(self._repository.request_type_resolution(request_id))


def _request_type_resolution_read(stored: dict[str, Any]) -> RequestTypeResolutionRead:
    request_type = stored["request_type"]
    candidates = stored["candidates"]
    return {
        "resolution": cast(RequestTypeResolution, stored["resolution"]),
        "request_id": str(stored["request_id"]),
        "source": str(stored["source"]) if stored["source"] is not None else None,
        "reason": str(stored["reason"]),
        "request_type": _request_type_read(request_type) if request_type is not None else None,
        "candidates": [_request_type_read(item) for item in candidates],
        "decided_by": str(stored["decided_by"]) if stored["decided_by"] is not None else None,
        "decided_at": stored["decided_at"],
    }


def _work_plan_monitoring_summary_read(stored: dict[str, Any]) -> WorkPlanMonitoringSummaryRead:
    return {
        "status": str(stored["status"]),
        "progress": int(stored["progress"]),
        "current_step": str(stored["current_step"]) if stored["current_step"] is not None else None,
        "current_step_id": str(stored["current_step_id"]) if stored["current_step_id"] is not None else None,
        "completed_count": int(stored["completed_count"]) if stored["completed_count"] is not None else None,
        "total_count": int(stored["total_count"]) if stored["total_count"] is not None else None,
        "work_plan": dict(stored["work_plan"]) if stored["work_plan"] is not None else None,
        "steps": [dict(item) for item in stored["steps"]],
        "latest_demo_run": dict(stored["latest_demo_run"]) if stored["latest_demo_run"] is not None else None,
        "request_type_assignment": (
            dict(stored["request_type_assignment"])
            if stored["request_type_assignment"] is not None
            else None
        ),
    }


class SQLWorkbenchRequestWorkPlanQuery:
    """Adapt the existing request check and canonical monitoring projection."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection
        self._repository = WorkbenchRepository(connection)

    def analysis_request_exists(self, request_id: str) -> bool:
        return self._repository.analysis_request_exists(request_id)

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead:
        return _work_plan_monitoring_summary_read(request_monitoring_summary(self._connection, request_id))


class SQLWorkbenchRequestTypeAssignmentCommand:
    """Delegate the existing assignment SQL and resolution flow on one connection."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._repository = WorkbenchRepository(connection)

    def has_work_plan(self, request_id: str) -> bool:
        return bool(self._repository.work_plan(request_id))

    def assign_request_type(
        self,
        request_id: str,
        command: RequestTypeAssignmentCommand,
    ) -> RequestTypeResolutionRead:
        try:
            stored = self._repository.assign_request_type(
                request_id,
                command.request_type_id,
                command.request_type_version,
                command.source,
                command.decided_by,
            )
        except PermissionError as exc:
            raise RequestTypeAssignmentLockedError() from exc
        except LookupError as exc:
            raise RequestTypeAssignmentTargetNotFoundError() from exc
        return _request_type_resolution_read(stored)


class _SQLWorkbenchWorkItemLifecycleAdapter:
    """Shared same-connection repository UoW and monitoring facade for lifecycle commands."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection
        self._repository = WorkbenchRepository(connection)

    def begin_transaction(self) -> None:
        self._repository.begin_transaction()

    def commit_transaction(self) -> None:
        self._repository.commit_transaction()

    def rollback_transaction(self) -> None:
        self._repository.rollback_transaction()

    def work_item(self, item_id: str) -> WorkItemLifecycleState | None:
        stored = self._repository.work_item(item_id)
        if stored is None:
            return None
        return WorkItemLifecycleState(
            id=str(stored["id"]),
            request_id=str(stored["request_id"]),
            status=str(stored["status"]),
            progress=int(stored.get("progress") or 0),
            sequence_no=int(stored["sequence_no"]),
        )

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead:
        return _work_plan_monitoring_summary_read(request_monitoring_summary(self._connection, request_id))

    def sync_request_status(self, request_id: str) -> WorkPlanMonitoringSummaryRead:
        return _work_plan_monitoring_summary_read(sync_request_status(self._connection, request_id))

    def current_work_item_id(self, request_id: str) -> str | None:
        row = self._connection.execute(
            """
            SELECT id FROM request_work_items
            WHERE request_id = ? AND status IN ('IN_PROGRESS', 'READY')
            ORDER BY CASE WHEN status = 'IN_PROGRESS' THEN 0 ELSE 1 END, sequence_no
            LIMIT 1
            """,
            [request_id],
        ).fetchone()
        return str(row[0]) if row else None

    def incomplete_prior_count(self, request_id: str, sequence_no: int) -> int:
        return int(
            self._connection.execute(
                """
                SELECT count(*) FROM request_work_items
                WHERE request_id = ? AND sequence_no < ? AND status <> 'COMPLETED'
                """,
                [request_id, sequence_no],
            ).fetchone()[0]
        )


class SQLWorkbenchWorkItemProgressCommand(_SQLWorkbenchWorkItemLifecycleAdapter):
    """Persist a monotonic progress update on the lifecycle connection."""

    def update_work_item_progress(self, item_id: str, command: WorkItemProgressCommand) -> None:
        self._connection.execute(
            "UPDATE request_work_items SET progress=?, progress_updated_by=?, progress_updated_at=? WHERE id=?",
            [command.progress, command.updated_by, _utcnow_naive(), item_id],
        )


class SQLWorkbenchWorkItemStartCommand(_SQLWorkbenchWorkItemLifecycleAdapter):
    """Persist the existing sequential READY-to-IN_PROGRESS start transition."""

    def start_work_item(self, item_id: str, command: WorkItemStartCommand) -> None:
        now = _utcnow_naive()
        self._connection.execute(
            """
            UPDATE request_work_items
            SET status = 'IN_PROGRESS', progress = 1, progress_updated_by = ?, progress_updated_at = ?, started_by = ?, started_at = ?
            WHERE id = ? AND status = 'READY'
            """,
            [command.started_by, now, command.started_by, now, item_id],
        )


class SQLWorkbenchWorkItemCompleteCommand(_SQLWorkbenchWorkItemLifecycleAdapter):
    """Persist completion, optional demo-run link, and the next READY transition."""

    def demo_run_is_succeeded_for_request(self, demo_run_id: str, request_id: str) -> bool:
        run = self._connection.execute(
            "SELECT request_id, status FROM workflow_runs WHERE id = ?",
            [demo_run_id],
        ).fetchone()
        return bool(run and run[0] == request_id and run[1] == "SUCCEEDED")

    def complete_work_item(self, item_id: str, command: WorkItemCompleteCommand) -> None:
        now = _utcnow_naive()
        self._connection.execute(
            """
            UPDATE request_work_items
            SET status = 'COMPLETED', progress = 100, progress_updated_by = ?, progress_updated_at = ?, completed_by = ?, completed_at = ?, demo_run_id = ?
            WHERE id = ? AND status = 'IN_PROGRESS'
            """,
            [command.completed_by, now, command.completed_by, now, command.demo_run_id, item_id],
        )

    def next_waiting_work_item_id(self, request_id: str, sequence_no: int) -> str | None:
        row = self._connection.execute(
            """
            SELECT id FROM request_work_items
            WHERE request_id = ? AND sequence_no > ? AND status = 'WAITING'
            ORDER BY sequence_no LIMIT 1
            """,
            [request_id, sequence_no],
        ).fetchone()
        return str(row[0]) if row else None

    def mark_work_item_ready(self, item_id: str) -> None:
        self._connection.execute(
            "UPDATE request_work_items SET status = 'READY' WHERE id = ? AND status = 'WAITING'",
            [item_id],
        )


class SQLWorkbenchWorkItemReassignmentCommand:
    """Same-connection SQL adapter for a work-item ownership change and response projection."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection
        self._repository = WorkbenchRepository(connection)

    def begin_transaction(self) -> None:
        self._repository.begin_transaction()

    def commit_transaction(self) -> None:
        self._repository.commit_transaction()

    def rollback_transaction(self) -> None:
        self._repository.rollback_transaction()

    def reassignment_state(self, item_id: str) -> WorkItemReassignmentState | None:
        stored = rows(
            self._connection.execute(
                """
                SELECT items.*, requests.project_id
                FROM request_work_items items
                JOIN analysis_requests requests ON requests.id=items.request_id
                WHERE items.id=?
                """,
                [item_id],
            )
        )
        if not stored:
            return None
        item = stored[0]
        return WorkItemReassignmentState(
            id=str(item["id"]),
            request_id=str(item["request_id"]),
            project_id=str(item["project_id"]),
            status=str(item["status"]),
            owner_user_id=str(item["owner_user_id"]) if item["owner_user_id"] is not None else None,
        )

    def resolve_project_assignee(self, project_id: str, owner_user_id: str) -> ProjectAssigneeRead:
        try:
            assignee = resolve_project_assignee(self._connection, project_id, owner_user_id)
        except Exception as exc:
            detail = getattr(exc, "detail", None)
            if isinstance(detail, dict) and detail.get("code") == "ASSIGNEE_PROJECT_MEMBERSHIP_REQUIRED":
                raise WorkItemAssigneeMembershipRequiredError(
                    project_id=project_id,
                    owner_user_id=owner_user_id,
                ) from exc
            if isinstance(detail, dict) and detail.get("code") == "ASSIGNEE_ACCOUNT_NOT_ACTIVE":
                raise WorkItemAssigneeAccountNotActiveError(
                    project_id=project_id,
                    owner_user_id=owner_user_id,
                ) from exc
            raise
        return ProjectAssigneeRead(user_id=assignee.user_id, display_name=assignee.display_name)

    def update_work_item_assignee(self, item_id: str, assignee: ProjectAssigneeRead) -> None:
        self._connection.execute(
            "UPDATE request_work_items SET owner=?, owner_user_id=? WHERE id=?",
            [assignee.display_name, assignee.user_id, item_id],
        )

    def reassigned_work_item(self, item_id: str) -> dict[str, object]:
        return dict(rows(self._connection.execute("SELECT * FROM request_work_items WHERE id=?", [item_id]))[0])


class SQLWorkbenchBatchDispatchCommand:
    """SQL implementation of the discrete three-phase batch-dispatch port."""

    def __init__(self, connection: ConnectionLike) -> None:
        self._connection = connection
        self._repository = WorkbenchRepository(connection)

    def work_item(self, item_id: str) -> dict[str, Any] | None:
        items = rows(self._connection.execute("SELECT * FROM request_work_items WHERE id=?", [item_id]))
        return items[0] if items else None

    def existing_attempt(self, item_id: str, idempotency_key: str) -> dict[str, Any] | None:
        return self._repository.batch_attempt_by_key(item_id, idempotency_key)

    def batch_profile(self, task_type_id: str, task_type_version: int) -> dict[str, Any] | None:
        return self._repository.get_batch_profile_for_task(task_type_id, task_type_version)

    def load_run(self, run_id: str) -> dict[str, object] | None:
        return DemoRunnerService(self._repository).get_run(run_id)

    def begin_transaction(self) -> None:
        self._repository.begin_transaction()

    def commit_transaction(self) -> None:
        self._repository.commit_transaction()

    def rollback_transaction(self) -> None:
        self._repository.rollback_transaction()

    def insert_preflight_attempt(self, context: BatchDispatchContext, command: BatchDispatchCommand) -> None:
        self._repository.insert_batch_attempt({
            "id": context.attempt_id, "work_item_id": context.work_item["id"], "workflow_run_id": None,
            "batch_profile_id": context.profile["id"], "batch_profile_version": int(context.profile["version"]),
            "profile_snapshot_json": context.profile_snapshot_json, "command_preview": context.initial_command_preview,
            "idempotency_key": command.idempotency_key, "execution_mode": "DEMO_ONLY", "status": "PREFLIGHT",
            "progress": 0, "last_message": "배치 프로필과 작업 호환성을 검증 중입니다.",
            "created_by": command.created_by, "created_at": context.started_at, "started_at": context.started_at,
            "completed_at": None,
        })

    def insert_preflight_event(self, context: BatchDispatchContext) -> None:
        self._repository.insert_batch_attempt_event({
            "id": f"batch-event-{uuid4().hex[:12]}", "attempt_id": context.attempt_id, "event_index": 0,
            "event_type": "PREFLIGHT", "level": "INFO", "message": "배치 프로필 snapshot을 고정했습니다.",
            "progress": 0, "occurred_at": context.started_at,
        })

    def preflight(self, context: BatchDispatchContext) -> BatchDispatchPreflight:
        try:
            result = preflight_batch_profile(context.profile, context.work_item)
        except BatchPreflightError as exc:
            raise BatchPreflightFailedError(exc.code, str(exc)) from exc
        return BatchDispatchPreflight(
            command_preview=result.command_preview,
            working_directory_preview=result.working_directory_preview,
        )

    def reject_attempt(self, context: BatchDispatchContext, *, message: str, completed_at: datetime) -> None:
        self._repository.update_batch_attempt(context.attempt_id, workflow_run_id=None, status="REJECTED", progress=0, message=message, started_at=context.started_at, completed_at=completed_at)

    def insert_rejected_event(self, context: BatchDispatchContext, *, message: str, occurred_at: datetime) -> None:
        self._repository.insert_batch_attempt_event({
            "id": f"batch-event-{uuid4().hex[:12]}", "attempt_id": context.attempt_id, "event_index": 1,
            "event_type": "REJECTED", "level": "ERROR", "message": message, "progress": 0, "occurred_at": occurred_at,
        })

    def queue_attempt(self, context: BatchDispatchContext) -> None:
        self._repository.update_batch_attempt(context.attempt_id, workflow_run_id=None, status="QUEUED", progress=10, message="DEMO_ONLY 실행 기록을 생성합니다.", started_at=context.started_at)

    def insert_queued_event(self, context: BatchDispatchContext, *, occurred_at: datetime) -> None:
        self._repository.insert_batch_attempt_event({
            "id": f"batch-event-{uuid4().hex[:12]}", "attempt_id": context.attempt_id, "event_index": 1,
            "event_type": "QUEUED", "level": "INFO", "message": "DEMO_ONLY 제어 plane에 등록했습니다.",
            "progress": 10, "occurred_at": occurred_at,
        })

    def create_demo_run(self, context: BatchDispatchContext, command: BatchDispatchCommand) -> dict[str, object]:
        work_item = context.work_item
        payload = DemoRunCreate(
            name=f"{work_item['display_name']} 배치 기록", request_id=work_item["request_id"],
            execution_mode="DEMO_ONLY", nodes=[{
                "node_key": work_item["node_key"], "task_type_id": work_item["task_type_id"],
                "task_type_version": int(work_item["task_type_version"]), "depends_on": [],
            }], created_by=command.created_by,
        )
        try:
            return DemoRunnerService(self._repository).create_run(payload)
        except WorkbenchValidationError as exc:
            raise BatchDemoRunValidationError(str(exc)) from exc

    def fail_attempt(self, context: BatchDispatchContext, *, message: str, completed_at: datetime) -> None:
        self._repository.update_batch_attempt(context.attempt_id, workflow_run_id=None, status="FAILED", progress=10, message=message, started_at=context.started_at, completed_at=completed_at)

    def insert_failed_event(self, context: BatchDispatchContext, *, message: str, occurred_at: datetime) -> None:
        self._repository.insert_batch_attempt_event({
            "id": f"batch-event-{uuid4().hex[:12]}", "attempt_id": context.attempt_id, "event_index": 2,
            "event_type": "FAILED", "level": "ERROR", "message": message, "progress": 10, "occurred_at": occurred_at,
        })

    def succeed_attempt(self, context: BatchDispatchContext, *, workflow_run_id: str, completed_at: datetime) -> None:
        self._repository.update_batch_attempt(context.attempt_id, workflow_run_id=workflow_run_id, status="SUCCEEDED", progress=100, message="DEMO_ONLY 배치 실행 기록이 완료되었습니다.", started_at=context.started_at, completed_at=completed_at)

    def insert_succeeded_event(self, context: BatchDispatchContext, *, occurred_at: datetime) -> None:
        self._repository.insert_batch_attempt_event({
            "id": f"batch-event-{uuid4().hex[:12]}", "attempt_id": context.attempt_id, "event_index": 2,
            "event_type": "SUCCEEDED", "level": "INFO", "message": "외부 solver 호출 없이 DEMO_ONLY 실행 기록을 완료했습니다.",
            "progress": 100, "occurred_at": occurred_at,
        })

    def insert_batch_dispatch(self, context: BatchDispatchContext, preflight: BatchDispatchPreflight, command: BatchDispatchCommand, *, workflow_run_id: str, created_at: datetime) -> None:
        self._repository.insert_batch_dispatch({
            "id": f"dispatch-{uuid4().hex[:12]}", "work_item_id": context.work_item["id"],
            "workflow_run_id": workflow_run_id, "batch_profile_id": context.profile["id"],
            "profile_snapshot_json": context.profile_snapshot_json, "command_preview": preflight.command_preview,
            "status": "RECORDED_DEMO", "created_by": command.created_by, "created_at": created_at,
        })

    def update_work_item_progress(self, context: BatchDispatchContext, command: BatchDispatchCommand, *, updated_at: datetime) -> None:
        self._connection.execute(
            """
            UPDATE request_work_items
            SET progress=CASE WHEN progress < 90 THEN 90 ELSE progress END,
                progress_updated_by=?, progress_updated_at=?
            WHERE id=? AND status='IN_PROGRESS'
            """,
            [command.created_by, updated_at, context.work_item["id"]],
        )

    def sync_request_status(self, request_id: str) -> None:
        sync_request_status(self._connection, request_id)
