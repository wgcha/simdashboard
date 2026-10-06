"""Folder-registered request progress computed from SPDM folders and the app DB.

Contract: docs/contracts/folder-request-progress.md (P1-P5, §2, §3).
Read-only: Scene folders are listed one level deep (names and is-file only), file
contents are never opened and nothing is written under the SPDM root. Final state
is read through ``case_finalization.latest_completed`` (signed records, no hashing).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from fastapi import HTTPException

from ..database_connection import ConnectionLike

MEMO_SECONDS = 30.0
INPUT_SUFFIXES = frozenset({".rad", ".fem"})
REPORT_SUFFIXES = frozenset({".pptx", ".html"})
# Upper bound of names inspected per Scene folder (stops at the first input file).
MAX_SCENE_ENTRIES = 10_000
STEPS = (("REGISTERED", "의뢰 등록"), ("MODELING", "해석 모델링"), ("RESULTS", "해석 결과"),
         ("FINAL", "Final 지정"), ("REPORT", "보고서"))
_STORAGE_UNAVAILABLE = "SPDM 저장소를 확인할 수 없습니다."
_SCENES_UNAVAILABLE = "Scene 폴더 구조를 확인할 수 없습니다."
_SCENES_NOT_STORED = "저장된 Scene 폴더 구조가 없습니다. 폴더 동기화 후 다시 확인됩니다."
_SCENE_UNREADABLE = "Scene 폴더를 읽을 수 없습니다."
_FINAL_UNAVAILABLE = "Final 지정 기록을 읽을 수 없습니다."
_REPORTS_UNAVAILABLE = "Final 보고서 폴더를 읽을 수 없습니다."
_NO_CASES = "등록된 Case가 없습니다."

_memo: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_memo_lock = threading.Lock()


def clear_memo() -> None:
    with _memo_lock:
        _memo.clear()


def request_in_project(conn: ConnectionLike, project_id: str, request_id: str) -> bool:
    return conn.execute("SELECT 1 FROM analysis_requests WHERE id=? AND project_id=?",
                        [request_id, project_id]).fetchone() is not None


def folder_progress(conn: ConnectionLike, project_id: str, request_id: str) -> dict[str, Any]:
    """Memoized (30 s per request) progress; the caller checks request existence and permission."""
    key = (str(project_id), str(request_id))
    current = time.monotonic()
    with _memo_lock:
        cached = _memo.get(key)
        if cached and current - cached[0] < MEMO_SECONDS:
            return cached[1]
    result = compute(conn, project_id, request_id)
    with _memo_lock:
        _memo[key] = (time.monotonic(), result)
    return result


def _checked_at() -> str:
    from .folder_discovery_environment import iso_utc
    return iso_utc(datetime.now(timezone.utc).replace(tzinfo=None))


def _step(key: str, status: str, detail: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": dict(STEPS)[key], "status": status, "detail": detail}


def _counted(key: str, done: int, total: int, word: str, error: str | None) -> dict[str, Any]:
    if error:
        return _step(key, "WAITING", error)
    if total == 0:
        return _step(key, "WAITING", _NO_CASES)
    status = "DONE" if done == total else ("IN_PROGRESS" if done > 0 else "WAITING")
    return _step(key, status, f"Case {done}/{total} {word} 있음")


def _live_environment(conn: ConnectionLike, project_id: str, request_id: str) -> str | None:
    row = conn.execute(
        "SELECT environment FROM folder_environment_registrations WHERE project_id=? AND request_id=? "
        "AND status<>'DELETED' ORDER BY created_at DESC,id DESC LIMIT 1",
        [project_id, request_id],
    ).fetchone()
    return str(row[0]).upper() if row else None


def _cases(conn: ConnectionLike, project_id: str, request_id: str, environment: str,
           root_id: str | None) -> list[dict[str, str]]:
    query = ("SELECT id,relative_path,source_name,storage_root_id FROM dashboard_cases "
             "WHERE project_id=? AND request_id=? AND environment=? ORDER BY relative_path")
    rows = conn.execute(query, [project_id, request_id, environment]).fetchall()
    return [{"id": str(r[0]), "relative_path": str(r[1]), "label": str(r[2])}
            for r in rows if root_id is None or str(r[3]) == root_id]


def _cases_with_results(conn: ConnectionLike, project_id: str, request_id: str,
                        case_ids: list[str]) -> set[str]:
    """Cases whose newest capture has result assets and is not superseded by a pending/failed job."""
    if not case_ids:
        return set()
    marks = ",".join("?" for _ in case_ids)
    latest_job: dict[str, tuple[str, Any]] = {}
    for case_id, status, updated_at in conn.execute(
            f"SELECT j.case_id,j.status,j.updated_at FROM folder_environment_capture_jobs j "
            f"JOIN folder_environment_registrations r ON r.id=j.registration_id "
            f"WHERE r.project_id=? AND r.request_id=? AND r.status<>'DELETED' AND j.case_id IN ({marks}) "
            f"ORDER BY j.updated_at DESC,j.id DESC", [project_id, request_id, *case_ids]).fetchall():
        latest_job.setdefault(str(case_id), (str(status), updated_at))
    newest: dict[str, tuple[str, Any]] = {}
    for capture_id, case_id, created_at in conn.execute(
            f"SELECT id,case_id,created_at FROM dashboard_captures WHERE case_id IN ({marks}) "
            f"ORDER BY created_at DESC,id DESC", case_ids).fetchall():
        newest.setdefault(str(case_id), (str(capture_id), created_at))
    capture_ids = [value[0] for value in newest.values()]
    with_assets: set[str] = set()
    if capture_ids:
        asset_marks = ",".join("?" for _ in capture_ids)
        with_assets = {str(row[0]) for row in conn.execute(
            f"SELECT DISTINCT capture_id FROM dashboard_assets WHERE capture_id IN ({asset_marks})",
            capture_ids).fetchall()}
    done: set[str] = set()
    for case_id, (capture_id, created_at) in newest.items():
        if capture_id not in with_assets:
            continue
        job = latest_job.get(case_id)
        if job and job[0] != "COMPLETED" and (created_at is None or job[1] is None or created_at < job[1]):
            continue  # A newer collection attempt is still pending/running or failed.
        done.add(case_id)
    return done


def _stored_scene_paths(conn: ConnectionLike, root, project_id: str, request_id: str,
                        environment: str) -> list[str] | None:
    """Scene folders from stored data only: the active refresh snapshot, else live registrations' previews.

    Never scans the SPDM tree (contract §3). ``None`` when nothing is stored.
    """
    from . import folder_discovery_environment
    from .folder_schema_resolver import active_refresh_snapshot
    active = active_refresh_snapshot(conn, folder_discovery_environment.root_identity(root),
                                     project_id, request_id, environment)
    if active is not None:
        schema = active.get("schema_json") or {}
        nodes = schema.get("nodes") if isinstance(schema, dict) else None
        if isinstance(nodes, list):
            return [str(node.get("relative_path") or "") for node in nodes
                    if isinstance(node, dict) and node.get("role_kind") == "SCENE"
                    and node.get("status") in {"CONFIRMED", "LINKED"} and node.get("relative_path")]
    found: list[str] = []
    stored = False
    for (rows_json,) in conn.execute(
            "SELECT p.rows_json FROM folder_environment_registrations r "
            "JOIN folder_environment_previews p ON p.id=r.preview_id "
            "WHERE r.project_id=? AND r.request_id=? AND r.environment=? AND r.status<>'DELETED' "
            "ORDER BY r.created_at DESC,r.id DESC", [project_id, request_id, environment]).fetchall():
        rows = folder_discovery_environment.preview_data(rows_json).get("rows")
        if not isinstance(rows, list):
            continue
        stored = True
        found.extend(str(row.get("relative_path") or "") for row in rows
                     if isinstance(row, dict) and row.get("role_kind") == "SCENE" and row.get("relative_path"))
    return list(dict.fromkeys(found)) if stored else None


def _scene_paths_by_case(scene_paths: list[str], cases: list[dict[str, str]]) -> dict[str, list[str]]:
    """Scene folders below each Case folder, by folded Case path."""
    result: dict[str, list[str]] = {}
    for case in cases:
        prefix = case["relative_path"].strip("/").casefold() + "/"
        result[case["relative_path"].casefold()] = [
            path for path in scene_paths if path.strip("/").casefold().startswith(prefix)]
    return result


def _scene_has_input(root, relative_path: str) -> bool:
    """A ``.rad``/``.fem`` file directly inside the Scene folder (names only, never recursive)."""
    from . import result_registration_paths
    from .storage.local import LocalFsProvider
    fs = LocalFsProvider(root)
    path = result_registration_paths._safe_existing(root, relative_path, allow_missing_leaf=True)
    if not fs.exists(path, follow_links=False):
        return False
    fs.assert_safe(path)
    if not fs.is_dir(path):
        return False
    for index, entry in enumerate(fs.list(path)):
        if index >= MAX_SCENE_ENTRIES:
            break
        if PurePosixPath(entry.name).suffix.casefold() in INPUT_SUFFIXES and entry.kind == "file":
            return True
    return False


def _final_state(conn: ConnectionLike, project_id: str, request_id: str,
                 environment: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """FINAL from the signed completion record; a Reports-folder failure only affects REPORT."""
    from . import case_finalization
    try:
        record, report_names = case_finalization.latest_completed(
            conn, project_id=project_id, request_id=request_id, environment=environment)
    except (ValueError, OSError, HTTPException):
        return _step("FINAL", "WAITING", _FINAL_UNAVAILABLE), _step("REPORT", "WAITING", _FINAL_UNAVAILABLE)
    if record is None:
        return _step("FINAL", "WAITING"), _step("REPORT", "WAITING")
    if report_names is None:
        return _step("FINAL", "DONE"), _step("REPORT", "WAITING", _REPORTS_UNAVAILABLE)
    has_report = any(PurePosixPath(name).suffix.casefold() in REPORT_SUFFIXES for name in report_names)
    return _step("FINAL", "DONE"), _step("REPORT", "DONE" if has_report else "WAITING")


def compute(conn: ConnectionLike, project_id: str, request_id: str) -> dict[str, Any]:
    from . import dashboard_capture
    from .storage import get_storage_provider

    environment = _live_environment(conn, project_id, request_id)
    if environment is None:
        return {"applicable": False, "environment": None, "completed": 0, "total": len(STEPS),
                "current_key": None, "next_action": None, "steps": [], "checked_at": _checked_at()}
    root = None
    root_error: str | None = None
    try:
        provider = get_storage_provider(conn)
        root = provider.root if provider is not None else None
        if root is None:
            root_error = _STORAGE_UNAVAILABLE
    except (ValueError, OSError):
        root_error = _STORAGE_UNAVAILABLE
    root_id = dashboard_capture._root_id(root) if root is not None else None
    cases = _cases(conn, project_id, request_id, environment, root_id)

    modeling_error = root_error
    inputs_done = 0
    if root is not None:
        try:
            stored = _stored_scene_paths(conn, root, project_id, request_id, environment)
        except (ValueError, OSError, HTTPException):
            stored, modeling_error = None, _SCENES_UNAVAILABLE
        if modeling_error is None and stored is None and cases:
            modeling_error = _SCENES_NOT_STORED
        scenes = _scene_paths_by_case(stored or [], cases)
        if modeling_error is None:
            try:
                for case in cases:
                    if any(_scene_has_input(root, path) for path in scenes.get(case["relative_path"].casefold(), [])):
                        inputs_done += 1
            except (ValueError, OSError):
                modeling_error = _SCENE_UNREADABLE
    results_done = len(_cases_with_results(conn, project_id, request_id, [case["id"] for case in cases]))

    if root is not None:
        final_step, report_step = _final_state(conn, project_id, request_id, environment)
    else:
        final_step, report_step = _step("FINAL", "WAITING", root_error), _step("REPORT", "WAITING", root_error)
    total_cases = len(cases)
    steps = [
        _step("REGISTERED", "DONE"),
        _counted("MODELING", inputs_done, total_cases, "입력", modeling_error),
        _counted("RESULTS", results_done, total_cases, "결과", None),
        final_step,
        report_step,
    ]
    current = next((step for step in steps if step["status"] != "DONE"), None)
    actions = {
        "MODELING": (f"입력 파일 대기 Case {total_cases - inputs_done}개" if total_cases
                     else "Working에 Case 폴더를 추가하세요"),
        "RESULTS": f"결과 대기 Case {total_cases - results_done}개",
        "FINAL": "대표 Case를 Final로 지정하세요",
        "REPORT": "Final 보고서를 저장하세요",
    }
    return {
        "applicable": True, "environment": environment,
        "completed": sum(1 for step in steps if step["status"] == "DONE"), "total": len(steps),
        "current_key": current["key"] if current else None,
        "next_action": actions.get(current["key"], "완료") if current else "완료",
        "steps": steps, "checked_at": _checked_at(),
    }
