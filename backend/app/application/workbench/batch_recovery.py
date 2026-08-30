"""Framework-neutral ownership use cases for manual batch-recovery workers.

This module deliberately does not schedule, re-run, finalize, or transition an
attempt.  It only establishes a short-lived internal ownership lease.
"""

from ...domains.workbench.models import (
    BatchRecoveryLeaseClaimCommand,
    BatchRecoveryLeaseCommandInvalidError,
    BatchRecoveryLeaseLostError,
    BatchRecoveryLeaseRead,
    BatchRecoveryLeaseReleaseCommand,
    BatchRecoveryLeaseRenewCommand,
    BatchRecoveryLeaseUnavailableError,
)
from ...domains.workbench.ports import WorkbenchBatchRecoveryLeasePort


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
