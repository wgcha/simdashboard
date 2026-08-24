"""Orchestration for importing result bundles from a trusted server folder.

This module deliberately owns discovery and persistence orchestration only.  The
versioned-folder parser remains :func:`app.folder_import.scan_folder`; callers
never supply a filesystem path.  A bundle is associated to an existing load case
through IDs in ``manifest.json`` before any result rows are written:

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

from ..database_connection import ConnectionLike, connect
from ..folder_import import FolderImportError, scan_folder
from ..media_policy import validate_media_metadata
from ..parsers.manifest_format import ManifestFormat, load_manifest
from ..repositories.variable_catalog import VariableCatalogRepository
from ..services.media_storage_service import attach_stored_media, store_file
from ..services.request_monitoring import sync_request_status


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
            return RefreshItem(relative or "manifest.json", "FAILED", message="마스터 폴더 밖 또는 심볼릭 링크 manifest는 허용되지 않습니다.")
        try:
            manifest_checksum = _checksum(manifest_path)
            raw_manifest = _read_manifest(manifest_path)
            _validate_bundle_paths(self.root, manifest_path.parent, raw_manifest)
            target = _manifest_target(raw_manifest)
            if self._already_imported(relative, manifest_checksum):
                self._record_skip(target, relative, manifest_checksum)
                return RefreshItem(relative, "SKIPPED", target[2], message="동일한 manifest가 이미 완료되었습니다.")
            parsed = scan_folder(manifest_path.parent)
            return self._persist_import(relative, manifest_checksum, target, parsed, manifest_path.parent)
        except Exception as exc:
            target = _safe_manifest_target(manifest_path)
            self._record_failure(target, relative or "manifest.json", str(exc))
            return RefreshItem(relative or "manifest.json", "FAILED", target[2] if target else None, message=_safe_error_message(exc))

    def _relative_manifest_path(self, manifest_path: Path) -> str | None:
        try:
            resolved = manifest_path.resolve(strict=True)
            return resolved.relative_to(self.root).as_posix()
        except (OSError, ValueError):
            return None

    def _already_imported(self, relative_path: str, checksum: str) -> bool:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM analysis_run_metadata
                WHERE source_type=? AND source_name=? AND source_checksum=?
                LIMIT 1
                """,
                [SOURCE_TYPE, relative_path, checksum],
            ).fetchone()
            return row is not None

    def _record_skip(self, target: tuple[str, str, str], relative: str, checksum: str) -> None:
        with connect() as conn:
            now = _now()
            conn.execute(
                """INSERT INTO folder_import_jobs VALUES (?, ?, NULL, ?, ?, ?, 'SKIPPED', ?, ?)""",
                [
                    f"folder-refresh-{uuid4().hex[:12]}", target[2], "master-folder", 1,
                    relative, json.dumps({"manifest_checksum": checksum, "reason": "IDENTICAL_COMPLETED"}, ensure_ascii=False), now,
                ],
            )

    def _record_failure(self, target: tuple[str, str, str] | None, relative: str, message: str) -> None:
        # The legacy table requires a load case.  Unassociated malformed
        # manifests are still returned in the response (and included in audit).
        if target is None:
            return
        try:
            with connect() as conn:
                conn.execute(
                    """INSERT INTO folder_import_jobs VALUES (?, ?, NULL, ?, ?, ?, 'FAILED', ?, ?)""",
                    [
                        f"folder-refresh-{uuid4().hex[:12]}", target[2], "master-folder", 1,
                        relative, json.dumps({"error": _safe_error_message(message)}, ensure_ascii=False), _now(),
                    ],
                )
        except Exception:
            # A failed diagnostic record must not mask the original import
            # failure or stop refresh of other bundles.
            return

    def _persist_import(
        self,
        relative: str,
        manifest_checksum: str,
        target: tuple[str, str, str],
        parsed: dict[str, Any],
        bundle_root: Path,
    ) -> RefreshItem:
        project_id, request_id, load_case_id = target
        job_id = f"folder-refresh-{uuid4().hex[:12]}"
        run_id = f"run-{uuid4().hex[:12]}"
        now = _now()
        with connect() as conn:
            actual = conn.execute(
                """
                SELECT ar.project_id, lc.request_id
                FROM load_cases lc JOIN analysis_requests ar ON ar.id=lc.request_id
                WHERE lc.id=?
                """,
                [load_case_id],
            ).fetchone()
            if actual is None or str(actual[0]) != project_id or str(actual[1]) != request_id:
                raise FolderImportError("manifest의 project_id, request_id, load_case_id 연결이 존재하지 않거나 일치하지 않습니다.")
            next_run_no = conn.execute(
                "SELECT coalesce(max(run_no), 0) + 1 FROM analysis_runs WHERE load_case_id=?", [load_case_id]
            ).fetchone()[0]
            catalog = VariableCatalogRepository(conn)
            conn.execute("BEGIN TRANSACTION")
            try:
                conn.execute(
                    "INSERT INTO folder_import_jobs VALUES (?, ?, ?, ?, ?, ?, 'RUNNING', NULL, ?)",
                    [job_id, load_case_id, run_id, parsed["schema_id"], parsed["schema_version"], relative, now],
                )
                _ensure_catalog_definitions(catalog, load_case_id, parsed)
                conn.execute(
                    "INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [run_id, load_case_id, None, next_run_no, parsed["solver"], "COMPLETED", now, now],
                )
                conn.execute(
                    """
                    INSERT INTO analysis_run_metadata
                        (analysis_run_id, source_type, source_name, source_checksum, schema_id, schema_version,
                         parser_version, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        run_id, SOURCE_TYPE, relative, manifest_checksum, parsed["schema_id"], parsed["schema_version"],
                        PARSER_VERSION, json.dumps({"job_id": job_id, "project_id": project_id, "request_id": request_id}, ensure_ascii=False), now,
                    ],
                )
                _persist_results(conn, run_id, parsed)
                summary = {
                    "scalar_count": len(parsed["scalars"]), "curve_count": len(parsed["curves"]),
                    "media_count": len(parsed["media"]), "manifest_checksum": manifest_checksum,
                }
                conn.execute(
                    "UPDATE folder_import_jobs SET status='COMPLETED', summary_json=?, analysis_run_id=? WHERE id=?",
                    [json.dumps(summary, ensure_ascii=False), run_id, job_id],
                )
                _sync_import_status(conn, request_id, load_case_id, now)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return RefreshItem(relative, "IMPORTED", load_case_id, run_id)


def _manifest_target(raw_manifest: dict[str, Any]) -> tuple[str, str, str]:
    context = raw_manifest.get("context")
    if not isinstance(context, dict):
        raise FolderImportError("manifest.context에 project_id, request_id, load_case_id가 필요합니다.")

    def value(name: str) -> str:
        direct = context.get(name)
        nested = context.get(name.removesuffix("_id"))
        candidate = direct if isinstance(direct, str) else nested.get("id") if isinstance(nested, dict) else None
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


def _validate_bundle_paths(master_root: Path, bundle_root: Path, manifest: dict[str, Any]) -> None:
    """Reject every mapping that can traverse, or hide traversal behind, a link."""
    mappings = manifest.get("mappings")
    if not isinstance(mappings, list):
        raise FolderImportError("manifest.json에는 mappings 배열이 필요합니다.")
    if bundle_root.is_symlink():
        raise FolderImportError("심볼릭 링크 결과 폴더는 허용되지 않습니다.")
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


def _ensure_catalog_definitions(catalog: VariableCatalogRepository, load_case_id: str, parsed: dict[str, Any]) -> None:
    for item in parsed["scalars"]:
        if not catalog.get(load_case_id, item["variable_key"], include_inactive=True):
            catalog.create(load_case_id, {
                "variable_key": item["variable_key"], "display_name": item["display_name"], "data_type": item["data_type"],
                "unit": item["unit"] or "-", "description": f"마스터 폴더 적재: {item['source_file']}",
                "threshold": item["threshold"], "result_group": item["result_group"], "updated_by": "마스터 폴더 새로고침",
            })
    for item in parsed["curves"]:
        if not catalog.get(load_case_id, item["variable_key"], include_inactive=True):
            catalog.create(load_case_id, {
                "variable_key": item["variable_key"], "display_name": item["display_name"], "data_type": "CURVE",
                "unit": item["y_unit"] or "-", "description": f"마스터 폴더 커브: {item['source_file']}",
                "threshold": None, "result_group": item["result_group"], "updated_by": "마스터 폴더 새로고침",
            })
    for item in parsed["media"]:
        if not catalog.get(load_case_id, item["variable_key"], include_inactive=True):
            catalog.create(load_case_id, {
                "variable_key": item["variable_key"], "display_name": item["display_name"], "data_type": item["asset_type"],
                "unit": "-", "description": f"마스터 폴더 미디어: {item['source_file']}",
                "threshold": None, "result_group": item["result_group"], "updated_by": "마스터 폴더 새로고침",
            })


def _persist_results(conn: ConnectionLike, run_id: str, parsed: dict[str, Any]) -> None:
    for item in parsed["scalars"]:
        value_double = item["value"] if item["data_type"] == "FLOAT" else None
        value_integer = item["value"] if item["data_type"] == "INTEGER" else None
        value_text = str(item["value"]) if item["data_type"] not in {"FLOAT", "INTEGER"} else None
        threshold = float(item["threshold"]) if item["threshold"] is not None else None
        verdict = (
            "FAIL" if float(item["value"]) >= threshold else "PASS"
        ) if threshold is not None and item["data_type"] in {"FLOAT", "INTEGER"} else (str(item["value"]) if item["data_type"] == "VERDICT" else None)
        conn.execute(
            "INSERT INTO scalar_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [f"scalar-{uuid4().hex[:12]}", run_id, item["variable_key"], item["display_name"], value_double, value_integer, value_text, item["unit"], threshold, verdict],
        )
    for item in parsed["curves"]:
        curve_id = f"curve-{uuid4().hex[:12]}"
        now = _now()
        conn.execute(
            "INSERT INTO curve_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [curve_id, run_id, item["variable_key"], item["display_name"], item["series_key"], item["x_label"], item["x_unit"], item["y_label"], item["y_unit"], len(item["points"]), item["source_file"], item["source_checksum"], now],
        )
        for index, point in enumerate(item["points"]):
            conn.execute("INSERT INTO curve_points VALUES (?, ?, ?, ?)", [curve_id, index, point["x"], point["y"]])
            conn.execute(
                "INSERT INTO time_series_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                [run_id, item["variable_key"], item["display_name"], point["x"], point["y"], item["x_unit"], item["y_unit"]],
            )
    for item in parsed["media"]:
        validate_media_metadata("CONTOUR_IMAGE" if item["asset_type"] == "IMAGE" else item["asset_type"], item["path"].name, item["path"].stat().st_size)
        asset_id = f"media-{uuid4().hex[:12]}"
        conn.execute(
            """
            INSERT INTO media_assets (id, analysis_run_id, asset_type, title, file_path, mime_type, file_size, checksum, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                asset_id, run_id, item["asset_type"], item["display_name"], f"imports/{run_id}/{item['path'].name}", item["mime_type"],
                item["path"].stat().st_size, item["source_checksum"],
                json.dumps({"variable_key": item["variable_key"], "source_file": item["source_file"]}),
            ],
        )
        attach_stored_media(conn, asset_id, store_file(conn, item["path"], filename=item["path"].name, mime_type=item["mime_type"], asset_type=item["asset_type"]))
    if parsed["note"]:
        conn.execute("INSERT INTO qualitative_notes VALUES (?, ?, ?, ?, ?)", [f"note-{uuid4().hex[:12]}", run_id, "마스터 폴더 새로고침", parsed["note"], _now()])


def _sync_import_status(conn: ConnectionLike, request_id: str, load_case_id: str, now: datetime) -> None:
    conn.execute("UPDATE load_cases SET status='COMPLETED' WHERE id=?", [load_case_id])
    conn.execute("UPDATE analysis_requests SET status='IN_PROGRESS' WHERE id=?", [request_id])
    conn.execute(
        "UPDATE request_steps SET status='COMPLETED', progress=100, actual_end=? WHERE request_id=? AND name IN ('해석 실행', '후처리 작업')",
        [now, request_id],
    )
    conn.execute(
        "UPDATE request_steps SET status='IN_PROGRESS', progress=greatest(progress, 20), actual_start=coalesce(actual_start, ?) WHERE request_id=? AND name='결과 검토'",
        [now, request_id],
    )
    sync_request_status(conn, request_id)


def _checksum(path: Path) -> str:
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
