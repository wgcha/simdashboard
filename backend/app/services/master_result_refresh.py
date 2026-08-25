"""Orchestration for importing result bundles from a trusted server folder.

This module owns trusted-root discovery, private bundle snapshots and
per-manifest failure isolation. Persistence is delegated to the canonical
result-ingestion application/UoW boundary. The versioned-folder parser reads
only the captured private snapshot; callers never supply a filesystem path. A
bundle is associated to an existing load case through IDs in its captured
``manifest.json`` before any result rows are written:

.. code-block:: json

   {"context": {"project_id": "…", "request_id": "…", "load_case_id": "…"}}

Nested ``project.id``, ``request.id`` and ``load_case.id`` are accepted too.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from ..adapters.persistence.result_ingestion import SQLResultIngestionUnitOfWorkProvider
from ..application.results.commands import ingest_result_bundle, utc_identifier
from ..config import import_bundle_limits
from ..domains.results.models import ResultIngestionCommand
from ..database_connection import connect
from ..folder_import import FolderImportError, scan_folder
from ..services.bundle_snapshot import BundleSnapshotError, capture_bundle


RefreshStatus = Literal["IMPORTED", "SKIPPED", "FAILED"]
ResultTarget = tuple[str, str, str]
SOURCE_TYPE = "MASTER_FOLDER_REFRESH"
PARSER_VERSION = "master-folder-refresh-v1"
RETRY_TARGET_MISMATCH = "RESULT_IMPORT_RETRY_TARGET_MISMATCH"
_refresh_lock = Lock()
logger = logging.getLogger(__name__)


class MasterResultRefreshError(RuntimeError):
    """A configuration error which prevents discovery entirely."""


@dataclass(frozen=True)
class RefreshItem:
    manifest_path: str
    status: RefreshStatus
    load_case_id: str | None = None
    analysis_run_id: str | None = None
    message: str | None = None
    operation: Literal["CREATED", "NOOP", "REPLACED", "REJECTED"] | None = None
    reason_code: str | None = None
    existing_analysis_run_id: str | None = None
    replaced_analysis_run_id: str | None = None
    source_revision: int | None = None


def configured_import_root() -> Path:
    """Return the only permitted discovery root, normalized once."""
    configured = os.getenv("SIMDASH_IMPORT_ROOT", "").strip()
    if not configured:
        raise MasterResultRefreshError("SIMDASH_IMPORT_ROOT가 설정되지 않았습니다.")
    return _normalize_import_root(Path(configured))


def _normalize_import_root(root: Path) -> Path:
    """Resolve both configured and explicit roots before discovery/capture."""
    try:
        normalized = root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise MasterResultRefreshError("SIMDASH_IMPORT_ROOT를 확인할 수 없습니다.") from exc
    if not normalized.is_dir():
        raise MasterResultRefreshError("SIMDASH_IMPORT_ROOT는 디렉터리여야 합니다.")
    return normalized


class MasterResultRefreshService:
    """Best-effort importer for all ``manifest.json`` files under one trusted root."""

    def __init__(self, root: Path | None = None):
        self.root = configured_import_root() if root is None else _normalize_import_root(root)
        try:
            self.limits = import_bundle_limits()
        except RuntimeError as exc:
            raise MasterResultRefreshError(f"결과 bundle 제한 설정이 올바르지 않습니다: {exc}") from exc

    def refresh(self) -> list[RefreshItem]:
        # A snapshot can consume the complete per-bundle temporary-storage
        # allowance. Serialize refreshes within this worker process so two
        # requests never allocate independent snapshots concurrently.
        if not _refresh_lock.acquire(blocking=False):
            raise MasterResultRefreshError("다른 마스터 결과 폴더 새로고침이 진행 중입니다.")
        try:
            return self._refresh_locked()
        finally:
            _refresh_lock.release()

    def retry_manifest(
        self,
        relative: str,
        *,
        expected_target: ResultTarget | None = None,
    ) -> RefreshItem:
        """Retry one previously recorded manifest under the process-local refresh lock.

        The caller supplies only a database-stored discovery-relative path.
        This method still validates it before handing it to the descriptor-
        relative snapshot boundary, so a corrupt historical record cannot
        become an arbitrary filesystem read.
        """
        normalized = _stored_manifest_relative(relative)
        expected_target = _validated_expected_target(expected_target)
        if not _refresh_lock.acquire(blocking=False):
            raise MasterResultRefreshError("다른 마스터 결과 폴더 새로고침이 진행 중입니다.")
        try:
            return self._refresh_manifest(normalized, expected_target=expected_target)
        finally:
            _refresh_lock.release()

    def _refresh_locked(self) -> list[RefreshItem]:
        # os.walk never follows directory links. A final manifest link is
        # retained as a discovery-relative item and rejected by snapshot
        # capture, while linked directories are intentionally not explored.
        manifests: list[str] = []
        for directory, dirnames, filenames in os.walk(self.root, followlinks=False):
            directory_path = Path(directory)
            dirnames[:] = [name for name in dirnames if not (directory_path / name).is_symlink()]
            directory_relative = directory_path.relative_to(self.root)
            manifests.extend(
                (directory_relative / name).as_posix()
                for name in filenames
                if name == "manifest.json"
            )
        return [self._refresh_manifest(relative) for relative in sorted(manifests)]

    def _refresh_manifest(
        self,
        relative: str,
        *,
        expected_target: ResultTarget | None = None,
    ) -> RefreshItem:
        captured_target: ResultTarget | None = None
        captured_manifest: dict[str, Any] | None = None
        captured_source_checksum: str | None = None
        try:
            with capture_bundle(self.root, relative, self.limits) as captured:
                captured_manifest = captured.manifest
                captured_source_checksum = captured.bundle_fingerprint
                captured_target = _manifest_target(captured.manifest)
                if expected_target is not None and captured_target != expected_target:
                    raise FolderImportError(
                        "재시도 대상 결과 manifest가 원 가져오기 대상과 일치하지 않습니다.",
                        code=RETRY_TARGET_MISMATCH,
                    )
                parsed = scan_folder(captured.bundle_root, limits=self.limits)
                outcome = ingest_result_bundle(
                    _ingestion_command(
                        relative,
                        captured.manifest_checksum,
                        captured.bundle_fingerprint,
                        captured_target,
                        parsed,
                        captured.manifest,
                    ),
                    SQLResultIngestionUnitOfWorkProvider(utc_identifier),
                    _now,
                    utc_identifier,
                )
                if outcome["status"] == "SKIPPED":
                    return RefreshItem(
                        relative,
                        "SKIPPED",
                        captured_target[2],
                        outcome["analysis_run_id"],
                        message="동일한 결과 bundle이 이미 완료되었습니다.",
                        operation=outcome["operation"],
                        reason_code=outcome["reason_code"],
                        existing_analysis_run_id=outcome["existing_analysis_run_id"],
                        replaced_analysis_run_id=outcome["replaced_analysis_run_id"],
                        source_revision=outcome["source_revision"],
                    )
                if outcome["status"] == "REJECTED":
                    # A rejected job is already committed by the canonical
                    # UoW.  Master refresh keeps its historical status enum
                    # and exposes the stable adapter reason code.
                    return RefreshItem(
                        relative,
                        "FAILED",
                        captured_target[2],
                        outcome["analysis_run_id"],
                        message="동일한 source run이 이미 존재하여 결과 bundle을 거부했습니다.",
                        operation="REJECTED",
                        reason_code="SOURCE_RUN_CONFLICT",
                        existing_analysis_run_id=outcome["existing_analysis_run_id"],
                        replaced_analysis_run_id=outcome["replaced_analysis_run_id"],
                        source_revision=outcome["source_revision"],
                    )
                return RefreshItem(
                    relative,
                    "IMPORTED",
                    captured_target[2],
                    outcome["analysis_run_id"],
                    operation=outcome["operation"],
                    reason_code=outcome["reason_code"],
                    existing_analysis_run_id=outcome["existing_analysis_run_id"],
                    replaced_analysis_run_id=outcome["replaced_analysis_run_id"],
                    source_revision=outcome["source_revision"],
                )
        except Exception as exc:
            is_controlled_error = isinstance(exc, (BundleSnapshotError, FolderImportError))
            retry_target_mismatch = False
            if captured_target is None and isinstance(exc, BundleSnapshotError) and exc.manifest is not None:
                try:
                    captured_target = _manifest_target(exc.manifest)
                except Exception:
                    captured_target = None
            # A capture can fail after its manifest was decoded but before it
            # returned a CapturedBundle.  Preserve the retry target as the
            # audit destination and turn a detectable target drift into the
            # same fixed controlled outcome as the normal capture path.
            if expected_target is not None:
                manifest_for_target = captured_manifest or getattr(exc, "manifest", None)
                if isinstance(manifest_for_target, dict):
                    try:
                        retry_target_mismatch = _manifest_target(manifest_for_target) != expected_target
                    except FolderImportError:
                        pass
            if not is_controlled_error:
                logger.exception("Unexpected master result refresh failure for %s", relative)
            error_code = getattr(exc, "code", None) if is_controlled_error else None
            if retry_target_mismatch:
                error_code = RETRY_TARGET_MISMATCH
                safe_message = "재시도 대상 결과 manifest가 원 가져오기 대상과 일치하지 않습니다."
            else:
                safe_message = _safe_error_message(exc)
            failure_target = expected_target if expected_target is not None else captured_target
            self._record_failure(
                failure_target,
                relative,
                safe_message,
                error_code or "BUNDLE_IMPORT_FAILED",
                manifest=captured_manifest if captured_manifest is not None else getattr(exc, "manifest", None),
                source_checksum=captured_source_checksum,
            )
            return RefreshItem(
                relative,
                "FAILED",
                failure_target[2] if failure_target else None,
                message=safe_message,
                operation="REJECTED" if error_code == "SOURCE_RUN_CONFLICT" else None,
                reason_code=error_code or "BUNDLE_IMPORT_FAILED",
            )

    def _record_failure(
        self,
        target: tuple[str, str, str] | None,
        relative: str,
        message: str,
        error_code: str,
        *,
        manifest: dict[str, Any] | None = None,
        source_checksum: str | None = None,
    ) -> None:
        # folder_import_jobs requires a load case. Do not record a failure
        # against an arbitrary case when captured context IDs do not form the
        # same project/request/load-case relationship in the current database.
        # Unassociated malformed manifests remain visible in the response.
        if target is None:
            return
        try:
            recorded_at = _now()
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO folder_import_jobs (
                        id,
                        load_case_id,
                        analysis_run_id,
                        schema_id,
                        schema_version,
                        source_folder,
                        status,
                        summary_json,
                        created_at,
                        source_type,
                        source_checksum,
                        source_run_id,
                        conflict_policy,
                        outcome_reason,
                        completed_at
                    )
                    SELECT ?, lc.id, NULL, ?, ?, ?, 'FAILED', ?, ?, ?, ?, ?, ?, ?, ?
                    FROM load_cases lc
                    JOIN analysis_requests ar ON ar.id=lc.request_id
                    WHERE lc.id=?
                      AND lc.request_id=?
                      AND ar.project_id=?
                    """,
                    [
                        f"folder-refresh-{uuid4().hex[:12]}",
                        "master-folder",
                        1,
                        relative,
                        json.dumps(
                            {"error_code": error_code, "error": message},
                            ensure_ascii=False,
                        ),
                        recorded_at,
                        SOURCE_TYPE,
                        source_checksum,
                        _failure_source_run_id(manifest),
                        _failure_conflict_policy(manifest),
                        error_code,
                        recorded_at,
                        target[2],
                        target[1],
                        target[0],
                    ],
                )
        except Exception:
            # A failed diagnostic record must not mask the original import
            # failure or stop refresh of other bundles.
            logger.exception("Could not record master result refresh failure")
            return


def _manifest_target(raw_manifest: dict[str, Any]) -> tuple[str, str, str]:
    context = raw_manifest.get("context")
    if not isinstance(context, dict):
        raise FolderImportError("manifest.context에 project_id, request_id, load_case_id가 필요합니다.")

    def value(name: str) -> str:
        direct = context.get(name)
        nested = context.get(name.removesuffix("_id"))
        candidate = (
            direct
            if isinstance(direct, str)
            else nested.get("id")
            if isinstance(nested, dict)
            else None
        )
        if not isinstance(candidate, str) or not candidate.strip():
            raise FolderImportError(f"manifest.context.{name}가 필요합니다.")
        return candidate.strip()

    return value("project_id"), value("request_id"), value("load_case_id")


def _ingestion_command(
    relative: str,
    manifest_checksum: str,
    bundle_fingerprint: str,
    target: tuple[str, str, str],
    parsed: dict[str, Any],
    manifest: dict[str, Any] | None = None,
) -> ResultIngestionCommand:
    project_id, request_id, load_case_id = target
    command: ResultIngestionCommand = {
        "project_id": project_id,
        "request_id": request_id,
        "load_case_id": load_case_id,
        "source_type": SOURCE_TYPE,
        "source_name": relative,
        "source_checksum": bundle_fingerprint,
        "parser_version": PARSER_VERSION,
        "parsed": parsed,
        "actor": "마스터 폴더 새로고침",
        "metadata": {
            "manifest_checksum": manifest_checksum,
            "bundle_fingerprint": bundle_fingerprint,
        },
    }
    source_run_id, conflict_policy = _manifest_source_identity(manifest or {})
    if source_run_id is not None:
        command["source_run_id"] = source_run_id
        command["conflict_policy"] = conflict_policy
    return command


def _manifest_source_identity(raw_manifest: dict[str, Any]) -> tuple[str | None, Literal["SKIP", "REJECT"] | None]:
    """Validate the producer identity subset accepted by master refresh.

    The master adapter deliberately does not allow producer ``REPLACE``.  A
    replacement must be an explicit API operation so a filesystem producer
    cannot silently append a new immutable run over an existing source.
    """
    source_value = raw_manifest.get("source_run_id")
    if source_value is None:
        return None, None
    if not isinstance(source_value, str):
        raise FolderImportError(
            "manifest.source_run_id는 문자열이어야 합니다.",
            code="SOURCE_RUN_ID_INVALID",
        )
    source_run_id = source_value.strip()
    if not source_run_id or len(source_run_id) > 120 or not source_run_id.isprintable():
        raise FolderImportError(
            "manifest.source_run_id 형식이 올바르지 않습니다.",
            code="SOURCE_RUN_ID_INVALID",
        )
    policy = raw_manifest.get("conflict_policy", "SKIP")
    if not isinstance(policy, str) or policy not in {"SKIP", "REJECT"}:
        raise FolderImportError(
            "마스터 manifest에서는 conflict_policy REPLACE를 사용할 수 없습니다.",
            code="SOURCE_CONFLICT_POLICY_UNSUPPORTED",
        )
    return source_run_id, policy


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_error_message(error: Exception) -> str:
    """Return only messages explicitly controlled by the import boundaries."""
    if isinstance(error, (BundleSnapshotError, FolderImportError)):
        return str(error)[:500]
    return "결과 bundle 가져오기 중 내부 오류가 발생했습니다. 관리자에게 문의하세요."


def _stored_manifest_relative(relative: str) -> str:
    """Accept only the canonical persisted relative path shape for retry."""
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise MasterResultRefreshError("저장된 결과 manifest 경로가 올바르지 않습니다.")
    parts = relative.split("/")
    if parts[-1] != "manifest.json" or any(part in {"", ".", ".."} for part in parts):
        raise MasterResultRefreshError("저장된 결과 manifest 경로가 올바르지 않습니다.")
    return "/".join(parts)


def _validated_expected_target(target: ResultTarget | None) -> ResultTarget | None:
    """Validate the server-derived retry target before it becomes a write key."""
    if target is None:
        return None
    if not isinstance(target, tuple) or len(target) != 3 or any(
        not isinstance(value, str) or not value.strip() for value in target
    ):
        raise MasterResultRefreshError("저장된 결과 가져오기 대상이 올바르지 않습니다.")
    project_id, request_id, load_case_id = target
    return project_id.strip(), request_id.strip(), load_case_id.strip()


def _failure_source_run_id(manifest: dict[str, Any] | None) -> str | None:
    if not isinstance(manifest, dict):
        return None
    candidate = manifest.get("source_run_id")
    if not isinstance(candidate, str):
        return None
    normalized = candidate.strip()
    return normalized if normalized and len(normalized) <= 120 and normalized.isprintable() else None


def _failure_conflict_policy(manifest: dict[str, Any] | None) -> str | None:
    if not isinstance(manifest, dict):
        return None
    if _failure_source_run_id(manifest) is None:
        return None
    candidate = manifest.get("conflict_policy", "SKIP")
    return candidate if isinstance(candidate, str) and candidate in {"SKIP", "REJECT"} else None
