"""Framework-neutral workbench commands."""

from __future__ import annotations

from collections.abc import Callable
import json
from datetime import datetime, timezone
from uuid import uuid4

from ...domains.workbench.models import (
    RequestTypeAssignmentCommand,
    RequestTypeResolutionRead,
    RequestWorkPlanImmutableError,
    DemoRunInvalidError,
    WorkItemCompleteCommand,
    WorkItemLifecycleResult,
    WorkItemNotFoundError,
    WorkItemNotInProgressError,
    WorkItemNotCurrentError,
    WorkItemNotReadyError,
    WorkItemNotStartedError,
    WorkItemPrerequisiteIncompleteError,
    WorkItemProgressCommand,
    WorkItemProgressNotMonotonicError,
    WorkItemStartCommand,
    WorkItemReassignmentCommand,
    WorkItemReassignmentFinalError,
    WorkItemReassignmentState,
    ProjectAssigneeRead,
    BatchDispatchCommand,
    BatchAttemptAlreadyRejectedError,
    BatchDispatchContext,
    BatchDispatchResult,
    BatchAttemptInsertConflictError,
    BatchDemoRunValidationError,
    BatchPreflightFailedError,
    BatchPreflightRejectedError,
    BatchProfileNotConfiguredError,
    BatchProfileTaskMismatchError,
    BatchWorkItemNotInProgressError,
)
from ...domains.workbench.ports import (
    WorkbenchRequestTypeAssignmentCommandPort,
    WorkbenchWorkItemCompleteCommandPort,
    WorkbenchWorkItemProgressCommandPort,
    WorkbenchWorkItemStartCommandPort,
    WorkbenchWorkItemReassignmentCommandPort,
    WorkbenchBatchDispatchPort,
)
def _batch_dispatch_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def assign_workbench_request_type(
    command_port: WorkbenchRequestTypeAssignmentCommandPort,
    request_id: str,
    command: RequestTypeAssignmentCommand,
) -> RequestTypeResolutionRead:
    """Guard immutable plans before delegating the legacy assignment command."""
    if command_port.has_work_plan(request_id):
        raise RequestWorkPlanImmutableError(request_id)
    return command_port.assign_request_type(request_id, command)


def update_workbench_work_item_progress(
    command_port: WorkbenchWorkItemProgressCommandPort,
    item_id: str,
    command: WorkItemProgressCommand,
    *,
    authorize: Callable[[], None],
    audit: Callable[[], None],
) -> WorkItemLifecycleResult:
    """Preserve the work-item read, authorization, audit, and sync order."""
    item = command_port.work_item(item_id)
    if item is None:
        raise WorkItemNotFoundError(item_id)

    authorize()
    audit()
    if item.status != "IN_PROGRESS":
        raise WorkItemNotInProgressError(item_id)
    if command.progress == item.progress:
        return WorkItemLifecycleResult(
            request_id=item.request_id,
            summary=command_port.request_monitoring_summary(item.request_id),
            changed=False,
        )
    if command.progress < item.progress:
        raise WorkItemProgressNotMonotonicError(
            current=item.progress,
            requested=command.progress,
        )

    command_port.update_work_item_progress(item_id, command)
    return WorkItemLifecycleResult(
        request_id=item.request_id,
        summary=command_port.sync_request_status(item.request_id),
        changed=True,
    )


def start_workbench_work_item(
    command_port: WorkbenchWorkItemStartCommandPort,
    item_id: str,
    command: WorkItemStartCommand,
    *,
    authorize: Callable[[], None],
    audit: Callable[[], None],
) -> WorkItemLifecycleResult:
    """Preserve the existing sequential ready-to-running start transition."""
    item = command_port.work_item(item_id)
    if item is None:
        raise WorkItemNotFoundError(item_id)

    authorize()
    audit()
    request_id = item.request_id
    if item.status in {"IN_PROGRESS", "COMPLETED"}:
        return WorkItemLifecycleResult(
            request_id=request_id,
            summary=command_port.request_monitoring_summary(request_id),
            changed=False,
        )

    current_item_id = command_port.current_work_item_id(request_id)
    if item.status != "READY" or current_item_id != item_id:
        raise WorkItemNotReadyError(item_id=item_id, current_item_id=current_item_id)
    if command_port.incomplete_prior_count(request_id, item.sequence_no):
        raise WorkItemPrerequisiteIncompleteError(item_id)

    command_port.start_work_item(item_id, command)
    return WorkItemLifecycleResult(
        request_id=request_id,
        summary=command_port.sync_request_status(request_id),
        changed=True,
    )


def complete_workbench_work_item(
    command_port: WorkbenchWorkItemCompleteCommandPort,
    item_id: str,
    command: WorkItemCompleteCommand,
    *,
    authorize: Callable[[], None],
    audit: Callable[[], None],
) -> WorkItemLifecycleResult:
    """Preserve the current-item, demo-run, completion, and next-ready sequence."""
    item = command_port.work_item(item_id)
    if item is None:
        raise WorkItemNotFoundError(item_id)

    authorize()
    audit()
    request_id = item.request_id
    if item.status == "COMPLETED":
        return WorkItemLifecycleResult(
            request_id=request_id,
            summary=command_port.request_monitoring_summary(request_id),
            changed=False,
        )

    current_item_id = command_port.current_work_item_id(request_id)
    if item.status == "READY" and current_item_id == item_id:
        raise WorkItemNotStartedError(item_id=item_id, current_item_id=current_item_id)
    if current_item_id != item_id:
        raise WorkItemNotCurrentError(item_id=item_id, current_item_id=current_item_id)
    if command_port.incomplete_prior_count(request_id, item.sequence_no):
        raise WorkItemPrerequisiteIncompleteError(item_id)
    if command.demo_run_id and not command_port.demo_run_is_succeeded_for_request(command.demo_run_id, request_id):
        raise DemoRunInvalidError(item_id=item_id, demo_run_id=command.demo_run_id)

    command_port.complete_work_item(item_id, command)
    next_item_id = command_port.next_waiting_work_item_id(request_id, item.sequence_no)
    if next_item_id:
        command_port.mark_work_item_ready(next_item_id)
    return WorkItemLifecycleResult(
        request_id=request_id,
        summary=command_port.sync_request_status(request_id),
        changed=True,
    )


def reassign_workbench_work_item(
    command_port: WorkbenchWorkItemReassignmentCommandPort,
    item_id: str,
    command: WorkItemReassignmentCommand,
    *,
    authorize: Callable[[WorkItemReassignmentState], None],
    audit: Callable[[WorkItemReassignmentState, ProjectAssigneeRead], None],
) -> dict[str, object]:
    """Preserve reassignment read, permission, owner-resolution, audit, and response order."""
    item = command_port.reassignment_state(item_id)
    if item is None:
        raise WorkItemNotFoundError(item_id)

    authorize(item)
    if item.status == "COMPLETED":
        raise WorkItemReassignmentFinalError(item_id)
    assignee = command_port.resolve_project_assignee(item.project_id, command.owner_user_id)
    command_port.update_work_item_assignee(item_id, assignee)
    audit(item, assignee)
    return command_port.reassigned_work_item(item_id)


def dispatch_workbench_batch(
    command_port: WorkbenchBatchDispatchPort,
    item_id: str,
    command: BatchDispatchCommand,
    *,
    authorize: Callable[[], None],
    audit: Callable[[], None],
) -> BatchDispatchResult:
    """Orchestrate the preserved preflight, DEMO runner, and finalization phases."""
    work_item = command_port.work_item(item_id)
    if work_item is None:
        raise WorkItemNotFoundError(item_id)
    authorize()
    existing_attempt = command_port.existing_attempt(item_id, command.idempotency_key)
    if existing_attempt:
        workflow_run_id = existing_attempt.get("workflow_run_id")
        if workflow_run_id:
            existing_run = command_port.load_run(str(workflow_run_id))
            if existing_run:
                return BatchDispatchResult(run=existing_run)
        raise BatchAttemptAlreadyRejectedError(existing_attempt)

    if work_item["status"] != "IN_PROGRESS":
        raise BatchWorkItemNotInProgressError(item_id)
    task_type_id = str(work_item["task_type_id"])
    task_type_version = int(work_item["task_type_version"])
    profile = command_port.batch_profile(task_type_id, task_type_version)
    if command.batch_profile_id and (not profile or command.batch_profile_id != profile["id"]):
        raise BatchProfileTaskMismatchError(task_type_id, task_type_version)
    if not profile or not profile["is_active"]:
        raise BatchProfileNotConfiguredError(task_type_id, task_type_version)

    started_at = _batch_dispatch_now()
    context = BatchDispatchContext(
        work_item=work_item,
        profile=profile,
        attempt_id=f"attempt-{uuid4().hex[:12]}",
        started_at=started_at,
        profile_snapshot_json=json.dumps(profile, ensure_ascii=False, default=str),
        initial_command_preview=f'"{profile["solver_path"]}" {profile["arguments_template"]}'.strip(),
    )

    preflight_insert_conflict_rolled_back = False
    command_port.begin_transaction()
    try:
        audit()
        try:
            command_port.insert_preflight_attempt(context, command)
        except BatchAttemptInsertConflictError:
            command_port.rollback_transaction()
            preflight_insert_conflict_rolled_back = True
            existing_attempt = command_port.existing_attempt(item_id, command.idempotency_key)
            if existing_attempt and existing_attempt.get("workflow_run_id"):
                existing_run = command_port.load_run(str(existing_attempt["workflow_run_id"]))
                if existing_run:
                    return BatchDispatchResult(run=existing_run)
            raise BatchAttemptAlreadyRejectedError(existing_attempt or {})
        command_port.insert_preflight_event(context)
        try:
            preflight = command_port.preflight(context)
        except BatchPreflightFailedError as exc:
            rejected_at = _batch_dispatch_now()
            command_port.reject_attempt(context, message=str(exc), completed_at=rejected_at)
            command_port.insert_rejected_event(context, message=str(exc), occurred_at=rejected_at)
            command_port.commit_transaction()
            raise BatchPreflightRejectedError(exc.code, str(exc), context.attempt_id) from exc
        command_port.queue_attempt(context)
        command_port.insert_queued_event(context, occurred_at=_batch_dispatch_now())
        command_port.commit_transaction()
    except (BatchPreflightRejectedError, BatchAttemptAlreadyRejectedError):
        raise
    except Exception:
        if not preflight_insert_conflict_rolled_back:
            command_port.rollback_transaction()
        raise

    try:
        run = command_port.create_demo_run(context, command)
    except BatchDemoRunValidationError as exc:
        failed_at = _batch_dispatch_now()
        command_port.begin_transaction()
        command_port.fail_attempt(context, message=str(exc), completed_at=failed_at)
        command_port.insert_failed_event(context, message=str(exc), occurred_at=failed_at)
        command_port.commit_transaction()
        raise

    completed_at = _batch_dispatch_now()
    workflow_run_id = str(run["id"])
    command_port.begin_transaction()
    command_port.succeed_attempt(context, workflow_run_id=workflow_run_id, completed_at=completed_at)
    command_port.insert_succeeded_event(context, occurred_at=completed_at)
    command_port.insert_batch_dispatch(
        context,
        preflight,
        command,
        workflow_run_id=workflow_run_id,
        created_at=completed_at,
    )
    command_port.update_work_item_progress(context, command, updated_at=completed_at)
    command_port.sync_request_status(str(work_item["request_id"]))
    command_port.commit_transaction()
    loaded_run = command_port.load_run(workflow_run_id)
    if loaded_run is None:
        raise RuntimeError(f"Demo run was not found after batch dispatch: {workflow_run_id}")
    return BatchDispatchResult(run=loaded_run)
