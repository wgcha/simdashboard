"""Orchestration for importing result bundles from a trusted server folder.

This module owns trusted-root discovery, path checks, bundle fingerprinting and
per-manifest failure isolation. Persistence is delegated to the canonical result
ingestion application/UoW boundary. The versioned-folder parser remains
:func:`app.folder_import.scan_folder`; callers never supply a filesystem path. A
bundle is associated to an existing load case through IDs in ``manifest.json``
before any result rows are written:

.. code-block:: json

   {"context": {"project_id": "…", "request_id": "…", "load_case_id": "…"}}

Nested ``project.id``, ``request.id`` and ``load_case.id`` are accepted too.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ..adapters.persistence.result_ingestion import SQLResultIngestionUnitOfWorkProvider
from ..application.results.commands import ingest_result_bundle, utc_identifier
from ..domains.results.models import ResultIngestionCommand
from ..database_connection import connect
from ..folder_import import FolderImportError, scan_folder
from ..parsers.manifest_format import ManifestFormat, load_manifest
from ..services.bundle_fingerprint import FingerprintFile, calculate_bundle_fingerprint


RefreshStatus = Literal["IMPORTED", "SKIPPED", "FAILED"]
SOURCE_TYPE = "MASTER_FOLDER_REFRESH"
PARSER_VERSION = "master-folder-refresh-v1"


class MasterResultRefreshError(RuntimeError):
    """A configuration error which prevents discovery entirely."""


@dataclass(frozen=True)
class RefreshItem:
    manifest_path: str
    status: RefreshStatus
    load_case_id: str | None = None
    analysis_run_id: str | None = None
    message: str | None = None


def configured_import_root() -> Path:
    """Return the only permitted discovery root, resolving its configured link once."""
    configured = os.getenv("SIMDASH_IMPORT_ROOT", "").strip()
    if not configured:
        raise MasterResultRefreshError("SIMDASH_IMPORT_ROOT가 설정되지 않았습니다.")
    try:
        root = Path(configured).expanduser().resolve(strict=True)
    except OSError as exc:
        raise MasterResultRefreshError("SIMDASH_IMPORT_ROOT를 확인할 수 없습니다.") from exc
    if not root.is_dir():
        raise MasterResultRefreshError("SIMDASH_IMPORT_ROOT는 디렉터리여야 합니다.")
    return root


class MasterResultRefreshService:
    """Best-effort importer for all ``manifest.json`` files under one trusted root."""

    def __init__(self, root: Path | None = None):
        self.root = root or configured_import_root()

    def refresh(self) -> list[RefreshItem]:
        # os.walk never follows directory links.  We still report a linked
        # manifest as a failed item rather than silently treating it as trusted.
        manifests: list[Path] = []
        for directory, dirnames, filenames in os.walk(self.root, followlinks=False):
            directory_path = Path(directory)
            dirnames[:] = [name for name in dirnames if not (directory_path / name).is_symlink()]
            manifests.extend(directory_path / name for name in filenames if name == "manifest.json")
        return [self._refresh_manifest(path) for path in sorted(manifests)]

    def _refresh_manifest(self, manifest_path: Path) -> RefreshItem:
        relative = self._relative_manifest_path(manifest_path)
        if relative is None or manifest_path.is_symlink():
            return RefreshItem(
                relative or "manifest.json",
                "FAILED",
                message="마스터 폴더 밖 또는 심볼릭 링크 manifest는 허용되지 않습니다.",
            )
        try:
            manifest_checksum = _checksum(manifest_path)
            raw_manifest = _read_manifest(manifest_path)
            mapping_files = _validate_bundle_paths(self.root, manifest_path.parent, raw_manifest)
            bundle_fingerprint = calculate_bundle_fingerprint(
                self.root,
                [FingerprintFile("manifest.json", manifest_path), *mapping_files],
            )
            target = _manifest_target(raw_manifest)
            parsed = scan_folder(manifest_path.parent)
            outcome = ingest_result_bundle(
                _ingestion_command(relative, manifest_checksum, bundle_fingerprint, target, parsed),
                SQLResultIngestionUnitOfWorkProvider(utc_identifier),
                _now,
                utc_identifier,
            )
            if outcome["status"] == "SKIPPED":
                return RefreshItem(
                    relative,
                    "SKIPPED",
                    target[2],
                    message="동일한 결과 bundle이 이미 완료되었습니다.",
                )
            return RefreshItem(relative, "IMPORTED", target[2], outcome["analysis_run_id"])
        except Exception as exc:
            target = _safe_manifest_target(manifest_path)
            self._record_failure(target, relative or "manifest.json", str(exc))
            return RefreshItem(
                relative or "manifest.json",
                "FAILED",
                target[2] if target else None,
                message=_safe_error_message(exc),
            )

    def _relative_manifest_path(self, manifest_path: Path) -> str | None:
        try:
            resolved = manifest_path.resolve(strict=True)
            return resolved.relative_to(self.root).as_posix()
        except (OSError, ValueError):
            return None

    def _record_failure(self, target: tuple[str, str, str] | None, relative: str, message: str) -> None:
        # The legacy table requires a load case.  Unassociated malformed
        # manifests are still returned in the response (and included in audit).
        if target is None:
            return
        try:
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO folder_import_jobs
                    VALUES (?, ?, NULL, ?, ?, ?, 'FAILED', ?, ?)
                    """,
                    [
                        f"folder-refresh-{uuid4().hex[:12]}",
                        target[2],
                        "master-folder",
                        1,
                        relative,
                        json.dumps({"error": _safe_error_message(message)}, ensure_ascii=False),
                        _now(),
                    ],
                )
        except Exception:
            # A failed diagnostic record must not mask the original import
            # failure or stop refresh of other bundles.
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


def _safe_manifest_target(manifest_path: Path) -> tuple[str, str, str] | None:
    try:
        return _manifest_target(_read_manifest(manifest_path))
    except Exception:
        return None


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        return load_manifest(path, expected_format=ManifestFormat.CANONICAL_MAPPINGS).data
    except ValueError as exc:
        raise FolderImportError(str(exc)) from exc


def _validate_bundle_paths(master_root: Path, bundle_root: Path, manifest: dict[str, Any]) -> list[FingerprintFile]:
    """Reject every mapping that can traverse, or hide traversal behind, a link."""
    mappings = manifest.get("mappings")
    if not isinstance(mappings, list):
        raise FolderImportError("manifest.json에는 mappings 배열이 필요합니다.")
    if bundle_root.is_symlink():
        raise FolderImportError("심볼릭 링크 결과 폴더는 허용되지 않습니다.")
    validated: list[FingerprintFile] = []
    for mapping in mappings:
        if not isinstance(mapping, dict) or not isinstance(mapping.get("path"), str):
            raise FolderImportError("mappings 항목에 안전한 path가 필요합니다.")
        relative = Path(mapping["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise FolderImportError("결과 파일 경로는 결과 폴더 내부의 상대 경로여야 합니다.")
        candidate = bundle_root / relative
        if any(part.is_symlink() for part in _path_parts(bundle_root, candidate)):
            raise FolderImportError("결과 파일 심볼릭 링크는 허용되지 않습니다.")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(bundle_root.resolve(strict=True))
            resolved.relative_to(master_root)
        except (OSError, ValueError) as exc:
            raise FolderImportError("결과 파일이 허용된 마스터 폴더 밖에 있습니다.") from exc
        if not resolved.is_file():
            raise FolderImportError("결과 파일을 찾을 수 없습니다.")
        bundle_relative = resolved.relative_to(bundle_root.resolve(strict=True)).as_posix()
        validated.append(FingerprintFile(bundle_relative, resolved))
    return validated


def _path_parts(root: Path, candidate: Path) -> list[Path]:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return [candidate]
    current = root
    parts: list[Path] = []
    for part in relative.parts:
        current = current / part
        parts.append(current)
    return parts


def _ingestion_command(
    relative: str,
    manifest_checksum: str,
    bundle_fingerprint: str,
    target: tuple[str, str, str],
    parsed: dict[str, Any],
) -> ResultIngestionCommand:
    project_id, request_id, load_case_id = target
    return {
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


def _checksum(path: Path) -> str:
    """Return the manifest checksum retained separately from the bundle fingerprint."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _safe_error_message(error: Exception | str) -> str:
    # Do not expose server absolute paths in an administrative API response.
    return str(error).replace(str(Path.cwd()), "[workspace]")[:500]
