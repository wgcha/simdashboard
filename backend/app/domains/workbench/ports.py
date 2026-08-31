from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from .models import (
    RequestTypeAssignmentCommand,
    RequestTypeResolutionRead,
    RequestTypeVersionRead,
    TaskTypeVersionRead,
    WorkItemCompleteCommand,
    WorkItemLifecycleState,
    WorkItemProgressCommand,
    WorkItemStartCommand,
    WorkItemReassignmentState,
    ProjectAssigneeRead,
    BatchDispatchCommand,
    BatchDispatchContext,
    BatchDispatchPreflight,
    BatchRecoveryLeaseClaimCommand,
    BatchRecoveryFinalizeCommand,
    BatchRecoveryFinalizeRead,
    BatchRecoveryLeaseRead,
    BatchRecoveryLeaseReleaseCommand,
    BatchRecoveryLeaseRenewCommand,
    WorkPlanMonitoringSummaryRead,
    RequestResultLayoutContext,
    ResultLayoutMaterializeCommand,
)


class WorkbenchCatalogQueryPort(Protocol):
    """Read port for immutable task and request type catalogs."""

    def list_task_types(self, *, all_versions: bool) -> list[TaskTypeVersionRead]: ...

    def list_request_types(self, *, all_versions: bool) -> list[RequestTypeVersionRead]: ...


class WorkbenchRequestTypeResolutionQueryPort(Protocol):
    """Read port for resolving one request to its immutable type version."""

    def request_type_resolution(self, request_id: str) -> RequestTypeResolutionRead: ...


class WorkbenchRequestWorkPlanQueryPort(Protocol):
    """Read port for request existence and its canonical monitoring projection."""

    def analysis_request_exists(self, request_id: str) -> bool: ...

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...


class WorkbenchRequestResultLayoutQueryPort(Protocol):
    """Same-connection reads for one immutable request result-layout snapshot."""

    def request_result_layout_context(self, request_id: str) -> RequestResultLayoutContext | None: ...

    def load_case_belongs_to_request(self, request_id: str, load_case_id: str) -> bool: ...

    def result_layout_snapshot(self, request_id: str) -> dict[str, Any] | None: ...

    def result_layout_bindings(self, request_id: str, load_case_id: str | None) -> dict[str, Any]: ...


class WorkbenchResultLayoutMaterializeCommandPort(Protocol):
    """Same-connection UoW and materialization operations for a result snapshot."""

    def request_result_layout_context(self, request_id: str) -> RequestResultLayoutContext | None: ...

    def load_case_belongs_to_request(self, request_id: str, load_case_id: str) -> bool: ...

    def begin_transaction(self) -> None: ...

    def commit_transaction(self) -> None: ...

    def rollback_transaction(self) -> None: ...

    def materialize_result_layout(
        self,
        request_id: str,
        command: ResultLayoutMaterializeCommand,
    ) -> dict[str, Any]: ...


class WorkbenchRequestTypeAssignmentCommandPort(Protocol):
    """Command port for the existing request-type assignment persistence flow."""

    def has_work_plan(self, request_id: str) -> bool: ...

    def assign_request_type(
        self,
        request_id: str,
        command: RequestTypeAssignmentCommand,
    ) -> RequestTypeResolutionRead: ...


class WorkbenchWorkItemProgressCommandPort(Protocol):
    """Same-connection persistence operations for progress updates."""

    def work_item(self, item_id: str) -> WorkItemLifecycleState | None: ...

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...

    def update_work_item_progress(
        self,
        item_id: str,
        command: WorkItemProgressCommand,
    ) -> None: ...

    def sync_request_status(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...


class WorkbenchWorkItemStartCommandPort(Protocol):
    """Same-connection persistence operations for the start transition."""

    def work_item(self, item_id: str) -> WorkItemLifecycleState | None: ...

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...

    def current_work_item_id(self, request_id: str) -> str | None: ...

    def incomplete_prior_count(self, request_id: str, sequence_no: int) -> int: ...

    def start_work_item(self, item_id: str, command: WorkItemStartCommand) -> None: ...

    def sync_request_status(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...


class WorkbenchWorkItemCompleteCommandPort(Protocol):
    """Same-connection persistence operations for the complete transition."""

    def work_item(self, item_id: str) -> WorkItemLifecycleState | None: ...

    def request_monitoring_summary(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...

    def current_work_item_id(self, request_id: str) -> str | None: ...

    def incomplete_prior_count(self, request_id: str, sequence_no: int) -> int: ...

    def demo_run_is_succeeded_for_request(self, demo_run_id: str, request_id: str) -> bool: ...

    def complete_work_item(self, item_id: str, command: WorkItemCompleteCommand) -> None: ...

    def next_waiting_work_item_id(self, request_id: str, sequence_no: int) -> str | None: ...

    def mark_work_item_ready(self, item_id: str) -> None: ...

    def sync_request_status(self, request_id: str) -> WorkPlanMonitoringSummaryRead: ...


class WorkbenchWorkItemReassignmentCommandPort(Protocol):
    """Same-connection persistence operations for reassignment and its response projection."""

    def reassignment_state(self, item_id: str) -> WorkItemReassignmentState | None: ...

    def resolve_project_assignee(self, project_id: str, owner_user_id: str) -> ProjectAssigneeRead: ...

    def update_work_item_assignee(self, item_id: str, assignee: ProjectAssigneeRead) -> None: ...

    def reassigned_work_item(self, item_id: str) -> dict[str, object]: ...


class WorkbenchBatchDispatchPort(Protocol):
    """Discrete persistence and runner operations for a three-phase batch dispatch."""

    def work_item(self, item_id: str) -> dict[str, Any] | None: ...

    def existing_attempt(self, item_id: str, idempotency_key: str) -> dict[str, Any] | None: ...

    def batch_profile(self, task_type_id: str, task_type_version: int) -> dict[str, Any] | None: ...

    def load_run(self, run_id: str) -> dict[str, object] | None: ...

    def begin_transaction(self) -> None: ...

    def commit_transaction(self) -> None: ...

    def rollback_transaction(self) -> None: ...

    def insert_preflight_attempt(self, context: BatchDispatchContext, command: BatchDispatchCommand) -> None: ...

    def insert_preflight_event(self, context: BatchDispatchContext) -> None: ...

    def preflight(self, context: BatchDispatchContext) -> BatchDispatchPreflight: ...

    def reject_attempt(self, context: BatchDispatchContext, *, message: str, completed_at: datetime) -> None: ...

    def insert_rejected_event(self, context: BatchDispatchContext, *, message: str, occurred_at: datetime) -> None: ...

    def queue_attempt(self, context: BatchDispatchContext) -> None: ...

    def insert_queued_event(self, context: BatchDispatchContext, *, occurred_at: datetime) -> None: ...

    def create_demo_run(self, context: BatchDispatchContext, command: BatchDispatchCommand) -> dict[str, object]: ...

    def fail_attempt(self, context: BatchDispatchContext, *, message: str, completed_at: datetime) -> None: ...

    def insert_failed_event(self, context: BatchDispatchContext, *, message: str, occurred_at: datetime) -> None: ...

    def succeed_attempt(self, context: BatchDispatchContext, *, workflow_run_id: str, completed_at: datetime) -> None: ...

    def insert_succeeded_event(self, context: BatchDispatchContext, *, occurred_at: datetime) -> None: ...

    def insert_batch_dispatch(
        self,
        context: BatchDispatchContext,
        preflight: BatchDispatchPreflight,
        command: BatchDispatchCommand,
        *,
        workflow_run_id: str,
        created_at: datetime,
    ) -> None: ...

    def update_work_item_progress(self, context: BatchDispatchContext, command: BatchDispatchCommand, *, updated_at: datetime) -> None: ...

    def sync_request_status(self, request_id: str) -> None: ...


class WorkbenchBatchRecoveryLeasePort(Protocol):
    """Internal-only atomic ownership operations for recoverable batch attempts."""

    def begin_transaction(self) -> None: ...

    def commit_transaction(self) -> None: ...

    def rollback_transaction(self) -> None: ...

    def claim_batch_recovery_lease(
        self,
        attempt_id: str,
        command: BatchRecoveryLeaseClaimCommand,
    ) -> BatchRecoveryLeaseRead | None: ...

    def renew_batch_recovery_lease(
        self,
        attempt_id: str,
        command: BatchRecoveryLeaseRenewCommand,
    ) -> BatchRecoveryLeaseRead | None: ...

    def release_batch_recovery_lease(
        self,
        attempt_id: str,
        command: BatchRecoveryLeaseReleaseCommand,
    ) -> bool: ...


class WorkbenchBatchRecoveryFinalizationPort(Protocol):
    """Internal-only UoW for the single fenced crash-window finalization."""

    def begin_transaction(self) -> None: ...

    def commit_transaction(self) -> None: ...

    def rollback_transaction(self) -> None: ...

    def finalize_batch_recovery_attempt(
        self,
        attempt_id: str,
        command: BatchRecoveryFinalizeCommand,
    ) -> BatchRecoveryFinalizeRead | None: ...
