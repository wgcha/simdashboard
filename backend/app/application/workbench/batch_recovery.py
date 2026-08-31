"""Framework-neutral ownership use cases for manual batch-recovery workers.

This module deliberately does not schedule, re-run, or expose recovery through
an API. It establishes an internal ownership lease and can atomically record an
already-succeeded DEMO_ONLY runner crash-window while that lease remains valid.
"""

from ...domains.workbench.models import (
    BatchRecoveryLeaseClaimCommand,
    BatchRecoveryLeaseCommandInvalidError,
    BatchRecoveryFinalizeCommand,
    BatchRecoveryFinalizeRead,
    BatchRecoveryLeaseLostError,
    BatchRecoveryLeaseRead,
    BatchRecoveryLeaseReleaseCommand,
    BatchRecoveryLeaseRenewCommand,
    BatchRecoveryLeaseUnavailableError,
)
from ...domains.workbench.ports import (
    WorkbenchBatchRecoveryFinalizationPort,
    WorkbenchBatchRecoveryLeasePort,
)


def _validate_values(
    *, owner_id: str, token: str, generation: int | None = None, ttl_seconds: int | None = None,
    allow_zero_generation: bool = False,
) -> None:
    if not owner_id or owner_id.strip() != owner_id:
        raise BatchRecoveryLeaseCommandInvalidError("owner_id")
    if not token or token.strip() != token:
        raise BatchRecoveryLeaseCommandInvalidError("token")
    if generation is not None and generation < (0 if allow_zero_generation else 1):
        raise BatchRecoveryLeaseCommandInvalidError("generation")
    if ttl_seconds is not None and not 1 <= ttl_seconds <= 86_400:
        raise BatchRecoveryLeaseCommandInvalidError("ttl_seconds")


def claim_workbench_batch_recovery_lease(
    command_port: WorkbenchBatchRecoveryLeasePort,
    attempt_id: str,
    command: BatchRecoveryLeaseClaimCommand,
) -> BatchRecoveryLeaseRead:
    """Atomically claim only the exact crash-window attempt eligible for recovery."""
    _validate_values(
        owner_id=command.owner_id,
        token=command.token,
        generation=command.expected_generation,
        ttl_seconds=command.ttl_seconds,
        allow_zero_generation=True,
    )
    command_port.begin_transaction()
    try:
        lease = command_port.claim_batch_recovery_lease(attempt_id, command)
    except Exception:
        command_port.rollback_transaction()
        raise
    if lease is None:
        command_port.rollback_transaction()
        raise BatchRecoveryLeaseUnavailableError(attempt_id)
    try:
        command_port.commit_transaction()
    except Exception:
        command_port.rollback_transaction()
        raise
    return lease


def renew_workbench_batch_recovery_lease(
    command_port: WorkbenchBatchRecoveryLeasePort,
    attempt_id: str,
    command: BatchRecoveryLeaseRenewCommand,
) -> BatchRecoveryLeaseRead:
    """Renew only the still-active exact ownership generation."""
    _validate_values(owner_id=command.owner_id, token=command.token, generation=command.generation, ttl_seconds=command.ttl_seconds)
    command_port.begin_transaction()
    try:
        lease = command_port.renew_batch_recovery_lease(attempt_id, command)
    except Exception:
        command_port.rollback_transaction()
        raise
    if lease is None:
        command_port.rollback_transaction()
        raise BatchRecoveryLeaseLostError(attempt_id)
    try:
        command_port.commit_transaction()
    except Exception:
        command_port.rollback_transaction()
        raise
    return lease


def release_workbench_batch_recovery_lease(
    command_port: WorkbenchBatchRecoveryLeasePort,
    attempt_id: str,
    command: BatchRecoveryLeaseReleaseCommand,
) -> None:
    """Release with exact generation CAS; a stale owner cannot clear a successor."""
    _validate_values(owner_id=command.owner_id, token=command.token, generation=command.generation)
    command_port.begin_transaction()
    try:
        released = command_port.release_batch_recovery_lease(attempt_id, command)
    except Exception:
        command_port.rollback_transaction()
        raise
    if not released:
        command_port.rollback_transaction()
        raise BatchRecoveryLeaseLostError(attempt_id)
    try:
        command_port.commit_transaction()
    except Exception:
        command_port.rollback_transaction()
        raise


def finalize_workbench_batch_recovery_attempt(
    command_port: WorkbenchBatchRecoveryFinalizationPort,
    attempt_id: str,
    command: BatchRecoveryFinalizeCommand,
) -> BatchRecoveryFinalizeRead:
    """Atomically record a verified runner result while the exact lease is live.

    The persistence port performs the lease-fenced status CAS and all dependent
    event, dispatch, work-item, and request updates inside this one transaction.
    It returns ``None`` whenever ownership, expiry, or crash-window identity no
    longer matches, so stale workers fail closed without mutating any record.
    """
    _validate_values(owner_id=command.owner_id, token=command.token, generation=command.generation)
    command_port.begin_transaction()
    try:
        finalized = command_port.finalize_batch_recovery_attempt(attempt_id, command)
    except Exception:
        command_port.rollback_transaction()
        raise
    if finalized is None:
        command_port.rollback_transaction()
        raise BatchRecoveryLeaseLostError(attempt_id)
    try:
        command_port.commit_transaction()
    except Exception:
        command_port.rollback_transaction()
        raise
    return finalized
