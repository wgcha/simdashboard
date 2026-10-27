"""SCX drive: 소재물성 prepared ahead of time and served from the DB (issue #55, 2026-10-27).

Production: opening 소재물성 for a request failed with ``DRIVE_READ_NOT_PREPARED: 드라이브 자료를 준비하지
못했습니다(조회 횟수 한도)``.  The catalog walked every Scene's deck folders on the drive (listing, sniffing
each ``.rad``/``.inc``) inside one interactive read session, and a request with many Scenes ran out of read
rounds on every page view.

Now materials are handled like the Case results (``capture_jobs``):

* the screen (``catalog``/``deck`` below) reads **only the DB**: the Scenes come from the registered Folder
  Schema snapshot, the deck state from ``materials_preparations`` (migration 0046).  A Scene without a
  prepared row is ``PREPARING`` (준비 중) and gets a ``PENDING`` row; a request whose folder snapshot is not
  in the DB yet answers ``preparation.status = PREPARING`` instead of an error.  No drive read happens on a
  page view (the endpoints run without a read session: a stray drive read would fail at once);
* the capture worker (one thread, BACKGROUND drive priority) runs :func:`run_pending` after its capture pass:
  per request one bounded, resumable read session lists the Scene's deck folders, downloads the decks
  (distribution: Radioss Starter decks with their ``/INCLUDE`` files, same rules and limits as before;
  usage: the OptiStruct ``.fem`` through ``optistruct_materials.run_job``, blob store and size limit as
  before), parses them and stores the result;
* a row is keyed by the drive versions it was prepared from (``fingerprint``: the deck folders' listings and
  every file read).  A folder refresh (``request_check``) or a view of a row older than ``RECHECK_SECONDS``
  marks it ``needs_check``: the next pass lists the folders again (no download) and prepares the Scene again
  only when a version changed.  Meanwhile the screen keeps showing the prepared deck;
* failures: a parse/path error is ``FAILED`` (code + Korean message, [다시 분석] sets it ``PENDING``); a
  drive hiccup leaves the row ``PENDING`` (its last error is kept) and fails it after
  ``MAX_TRANSIENT_ATTEMPTS`` passes.

Local SPDM roots keep the direct read path of ``materials_catalog`` (cheap local reads); nothing here runs.
Nothing here writes to SPDM or the drive.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Callable

from ..database_connection import ConnectionLike, connect, rows
from ..parsers.optistruct_deck_parser import OptiStructParseError
from . import materials_catalog as catalog_service
from . import optistruct_materials
from .storage import provider_for_root
from .storage.provider import SpdmStorageError

logger = logging.getLogger(__name__)

ACTOR = "system:materials"
TABLE = "materials_preparations"
# Bump to prepare every distribution Scene again (parser or rule change).
RADIOSS_PREPARATION_VERSION = "radioss-prep-1"
SESSION_SECONDS = 300.0                # drive reads of one request's session
PASS_SECONDS = 600.0                   # one pass of the worker
RECHECK_SECONDS = 600.0                # a READY row viewed after this long is listed again
EMPTY_SCOPE_SECONDS = 600.0            # a registered request without Scenes is looked at again after this long
MAX_TRANSIENT_ATTEMPTS = 5
_MESSAGE_LIMIT = 400
# Drive session ended (deadline / cancel / out of rounds / busy): the pass stops, the row waits for the next one.
_SESSION_CODES = frozenset({"DRIVE_READ_NOT_PREPARED", "DRIVE_READ_DEADLINE", "DRIVE_READ_CANCELLED", "DRIVE_BUSY"})
_TRANSIENT_CODES = frozenset({"SPDM_FILE_BUSY", "SPDM_UNAVAILABLE", "SPDM_TIMEOUT", "MATERIALS_SCAN_INCOMPLETE",
                              "MATERIALS_FILE_CHANGED", "MATERIALS_INCLUDE_UNAVAILABLE", "DRIVE_STAGING_FULL",
                              "DRIVE_SOURCE_CHANGED"})
_COLUMNS = ("id,root_key,project_id,request_id,environment,scene_key,relative_path,status,needs_check,fingerprint,"
            "parser_version,source_rel_path,files_json,warnings_json,deck_json,error_code,error_message,attempts,"
            "checked_at,prepared_at,created_at,updated_at")

_run_lock = threading.Lock()
_queued_lock = threading.Lock()
_queued: set[tuple[str, str, str]] = set()
_empty_scopes: dict[tuple[str, str, str], float] = {}


def enabled() -> bool:
    """SCX drive mode: the 소재물성 screen is served from prepared rows."""
    from .drive import reads as drive_reads

    return drive_reads.active()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)


def _scene_key(relative_path: str) -> str:
    return str(relative_path).casefold()


def _is_transient(code: str) -> bool:
    return code.startswith("DRIVE_") or code in _TRANSIENT_CODES


def reset_for_tests() -> None:
    with _queued_lock:
        _queued.clear()
        _empty_scopes.clear()


# --- rows --------------------------------------------------------------------------------------

def _rows(conn: ConnectionLike, root_key: str, request_id: str, environment: str) -> dict[str, dict[str, Any]]:
    found = rows(conn.execute(f"SELECT {_COLUMNS} FROM {TABLE} WHERE root_key=? AND request_id=? AND environment=?",
                              [root_key, request_id, environment]))
    return {str(item["scene_key"]): item for item in found}


def _insert_pending(conn: ConnectionLike, root_key: str, project_id: str, request_id: str, environment: str,
                    relative_path: str) -> None:
    now = _now()
    conn.execute(
        f"INSERT INTO {TABLE}(id,root_key,project_id,request_id,environment,scene_key,relative_path,status,"
        "needs_check,files_json,warnings_json,attempts,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (root_key, request_id, environment, scene_key) DO NOTHING",
        [f"mprep-{uuid.uuid4().hex}", root_key, project_id, request_id, environment, _scene_key(relative_path),
         relative_path, "PENDING", False, "[]", "[]", 0, now, now])


def _update(conn: ConnectionLike, row_id: str, **values: Any) -> None:
    values["updated_at"] = _now()
    names = sorted(values)
    conn.execute(f"UPDATE {TABLE} SET {','.join(f'{name}=?' for name in names)} WHERE id=?",
                 [values[name] for name in names] + [row_id])


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _loads(value: Any, default: Any) -> Any:
    try:
        loaded = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return default
    return default if loaded is None else loaded


def queue_scope(project_id: str, request_id: str, environment: str) -> None:
    """Ask the worker to look at one request scope (new Scenes, a folder snapshot) and wake it."""
    if not enabled():
        return
    with _queued_lock:
        _queued.add((str(project_id), str(request_id), str(environment).upper()))
        _empty_scopes.pop((str(project_id), str(request_id), str(environment).upper()), None)
    _wake()


def request_check(conn: ConnectionLike, project_id: str, request_id: str, environment: str) -> None:
    """A folder refresh changed the request: its prepared Scenes are checked against the drive again."""
    if not enabled():
        return
    conn.execute(f"UPDATE {TABLE} SET needs_check=? WHERE request_id=? AND environment=? AND status<>'PENDING'",
                 [True, request_id, str(environment).upper()])
    queue_scope(project_id, request_id, environment)


def _wake() -> None:
    from . import capture_jobs

    capture_jobs.wake()


# --- screen side (DB only) ----------------------------------------------------------------------

def _schema_ready(conn: ConnectionLike, project_id: str, request_id: str, environment: str) -> bool:
    """Whether the request's Folder Schema can be resolved from the DB (a current folder snapshot)."""
    from . import folder_discovery, folder_discovery_environment, folder_schema_resolver as resolver

    root = folder_discovery.configured_root(conn)
    root_key = folder_discovery_environment.root_identity(root)
    try:
        request_path = resolver._request_path(conn, root_key, project_id, request_id, environment)
        active = resolver.active_refresh_snapshot(conn, root_key, project_id, request_id, environment)
    except resolver.FolderSchemaError:
        return True   # not a preparation state: the scope resolution reports the error itself
    return bool(active) and resolver.snapshot_matches_path(active, request_path)


def _project_id(conn: ConnectionLike, request_id: str) -> str:
    row = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
    if not row:
        raise catalog_service.MaterialsCatalogError("RESULT_CONTEXT_INVALID", "기존 의뢰를 확인할 수 없습니다.", 404)
    return str(row[0])


def _environment(environment: str) -> str:
    value = str(environment).upper()
    if value not in {"DISTRIBUTION", "USAGE"}:
        raise catalog_service.MaterialsCatalogError("MATERIALS_ENVIRONMENT_UNSUPPORTED",
                                                    "소재 덱은 사용환경·유통환경에서만 조회할 수 있습니다.")
    return value


def _preparing_folders(request_id: str, environment: str) -> dict[str, Any]:
    return {"request_id": request_id, "environment": environment, "scenes": [],
            "hierarchy": {"cases": [], "load_cases": [], "execution_runs": [], "run_options": []}, "conflicts": [],
            "preparation": {"status": "PREPARING", "code": "MATERIALS_FOLDERS_PREPARING",
                            "message": "의뢰 폴더 구조를 서버에 준비하고 있습니다. 준비되면 자동으로 표시됩니다.",
                            "preparing": 0, "failed": 0}}


def _scene_state(entry: dict[str, Any], row: dict[str, Any] | None) -> None:
    status = str(row.get("status")) if row else "PENDING"
    entry["materials_status"] = {"PENDING": "PREPARING"}.get(status, status)
    entry["has_deck"] = status in {"READY", "FAILED"}
    entry["materials_error_code"] = row.get("error_code") if row and status in {"FAILED", "PENDING"} else None
    entry["materials_error_message"] = row.get("error_message") if row and status in {"FAILED", "PENDING"} else None
    entry["materials_prepared_at"] = _iso(row.get("prepared_at")) if row else None


def _scope_with_rows(conn: ConnectionLike, request_id: str, environment: str):
    """(scope parts, catalog items with their prepared state, rows, conflicts) or None while preparing."""
    project_id = _project_id(conn, request_id)
    if not _schema_ready(conn, project_id, request_id, environment):
        return None
    try:
        _, scope, root, root_id, root_key, schema = catalog_service._request_scope(conn, request_id, environment)
    except catalog_service.MaterialsCatalogError as exc:
        if exc.code in {"FOLDER_SCHEMA_SCAN_UNAVAILABLE", "FOLDER_SCHEMA_SCAN_INCOMPLETE"} or _is_transient(exc.code):
            return None   # the request tree is not in the DB yet (the scope would need the drive)
        raise
    except SpdmStorageError as exc:
        if _is_transient(str(exc.code)):
            return None
        raise
    found = _rows(conn, root_key, request_id, environment)
    conflicts: list[dict[str, str]] = []
    items = catalog_service._catalog_items(
        conn, root, root_id, root_key, scope, schema, conflicts,
        deck_lookup=lambda entry: _scene_state(entry, found.get(_scene_key(entry["relative_path"]))))
    return (project_id, scope, schema, root_key), items, found, conflicts


def _current_version(environment: str) -> str:
    return optistruct_materials.PARSER_VERSION if environment == "USAGE" else RADIOSS_PREPARATION_VERSION


def _outdated(row: dict[str, Any]) -> bool:
    """A prepared row the next pass must look at again: a parser upgrade, or not checked for ``RECHECK_SECONDS``."""
    if row.get("status") not in {"READY", "NO_DECK"} or row.get("needs_check"):
        return False
    if row.get("parser_version") != _current_version(str(row.get("environment"))):
        return True
    checked = row.get("checked_at")
    return isinstance(checked, datetime) and checked < _now() - timedelta(seconds=RECHECK_SECONDS)


def _note_views(conn: ConnectionLike, parts: tuple, items: list[dict[str, Any]], found: dict[str, dict[str, Any]]) -> None:
    """New Scenes get a PENDING row; outdated prepared rows (:func:`_outdated`) are checked again."""
    project_id, scope, _schema, root_key = parts
    wake = False
    for entry in items:
        row = found.get(_scene_key(entry["relative_path"]))
        if row is None:
            _insert_pending(conn, root_key, project_id, scope["request_id"], scope["environment"], entry["relative_path"])
            wake = True
        elif _outdated(row):
            _update(conn, str(row["id"]), needs_check=True)
            wake = True
        elif row.get("status") == "PENDING" or row.get("needs_check"):
            wake = True
    if wake:
        queue_scope(project_id, scope["request_id"], scope["environment"])


def catalog(conn: ConnectionLike, request_id: str, environment: str) -> dict[str, Any]:
    """The 소재물성 catalog from the DB (no drive read); see the module docstring."""
    environment = _environment(environment)
    resolved = _scope_with_rows(conn, request_id, environment)
    if resolved is None:
        queue_scope(_project_id(conn, request_id), request_id, environment)
        return _preparing_folders(request_id, environment)
    parts, items, found, conflicts = resolved
    _note_views(conn, parts, items, found)
    _, scope, schema, root_key = parts
    payload = catalog_service.catalog_payload(request_id, scope, schema, root_key, items, conflicts)
    preparing = sum(1 for item in items if item.get("materials_status") == "PREPARING")
    failed = sum(1 for item in items if item.get("materials_status") == "FAILED")
    payload["preparation"] = {
        "status": "PREPARING" if preparing else "READY", "code": "MATERIALS_PREPARING" if preparing else None,
        "message": f"Scene {preparing}개의 소재 자료를 서버에 준비하고 있습니다." if preparing else None,
        "preparing": preparing, "failed": failed,
    }
    return payload


def _pending_analysis(solver: str, row: dict[str, Any] | None, rel: str, status: str = "QUEUED") -> dict[str, Any]:
    analysis = optistruct_materials._analysis(status, rel_path=rel, size=0, bytes_total=0)
    analysis.update(solver=solver, size_bytes=None, max_bytes=None,
                    error_code=row.get("error_code") if row else None,
                    error_message=row.get("error_message") if row else None,
                    parsed_at=_iso(row.get("prepared_at")) if row else None)
    return analysis


def deck(conn: ConnectionLike, request_id: str, environment: str, scene_id: str | None = None,
         relative_path: str | None = None, *, retry: bool = False) -> dict[str, Any]:
    """The prepared deck of one Scene from the DB (no drive read).

    Not prepared yet: ``analysis.status`` ``QUEUED`` (준비 중, the view polls); failed: ``FAILED`` with the
    code and message (``retry`` queues it again); no deck: ``MATERIALS_DECK_NOT_FOUND`` (404) as before.
    """
    environment = _environment(environment)
    if not scene_id and not relative_path:
        raise catalog_service.MaterialsCatalogError("MATERIALS_SCENE_REQUIRED", "scene_id 또는 relative_path를 선택하세요.")
    resolved = _scope_with_rows(conn, request_id, environment)
    if resolved is None:
        queue_scope(_project_id(conn, request_id), request_id, environment)
        raise catalog_service.MaterialsCatalogError("MATERIALS_PREPARING",
                                                    "의뢰 폴더 구조를 서버에 준비하고 있습니다. 잠시 후 다시 여세요.", 409)
    parts, items, found, _ = resolved
    project_id, scope, _schema, root_key = parts
    selected = catalog_service.select_scene(items, scene_id, relative_path)
    scene = selected
    row = found.get(_scene_key(selected["relative_path"]))
    solver = "OPTISTRUCT" if environment == "USAGE" else "RADIOSS"
    base = {"request_id": request_id, "environment": environment, "scene": scene,
            "files": [{"relative_path": str(item.get("relative_path")), "size_bytes": item.get("size_bytes")}
                      for item in _loads(row.get("files_json"), []) if isinstance(item, dict)] if row else [],
            "candidate_warnings": _loads(row.get("warnings_json"), []) if row else []}

    def pending() -> dict[str, Any]:
        queue_scope(project_id, request_id, environment)
        rel = str((row or {}).get("source_rel_path") or selected["relative_path"])
        return {**base, "analysis": _pending_analysis(solver, row, rel), "deck": None,
                "preparation": {"status": "PREPARING"}}

    if row is None:
        _insert_pending(conn, root_key, project_id, request_id, environment, selected["relative_path"])
        return pending()
    status = str(row.get("status"))
    if retry and status == "FAILED":
        _update(conn, str(row["id"]), status="PENDING", attempts=0, needs_check=False)
        row = {**row, "status": "PENDING", "error_code": None, "error_message": None}
        return pending()
    if status == "PENDING":
        return pending()
    if status == "NO_DECK":
        message = ("Scene 또는 Case 폴더 바로 아래에서 OptiStruct 입력 파일(.fem)을 찾을 수 없습니다."
                   if environment == "USAGE" else "씬 우선순위 경로에서 Parts와 Materials 덱을 모두 찾을 수 없습니다.")
        raise catalog_service.MaterialsCatalogError("MATERIALS_DECK_NOT_FOUND", message, 404)
    rel = str(row.get("source_rel_path") or selected["relative_path"])
    if status == "FAILED":
        analysis = _pending_analysis(solver, row, rel, "FAILED")
        return {**base, "analysis": analysis, "deck": None, "preparation": {"status": "FAILED"}}
    if _outdated(row):
        _update(conn, str(row["id"]), needs_check=True)
        row = {**row, "needs_check": True}
        queue_scope(project_id, request_id, environment)
    preparation = {"status": "READY", "prepared_at": _iso(row.get("prepared_at")), "checking": bool(row.get("needs_check"))}
    if environment == "DISTRIBUTION":
        return {**base, "analysis": None, "deck": _loads(row.get("deck_json"), None), "preparation": preparation}
    files = _loads(row.get("files_json"), [])
    main = files[0] if files and isinstance(files[0], dict) else {}
    cache = optistruct_materials.load_row(conn, root_key, rel)
    if (not cache or cache.get("status") != "READY" or not cache.get("deck_json")
            or cache.get("fingerprint") != main.get("version") or cache.get("parser_version") != row.get("parser_version")):
        # The parse cache row is gone or newer than this preparation: prepare the Scene again.
        _update(conn, str(row["id"]), status="PENDING", needs_check=False)
        return pending()
    parsed = {key: value for key, value in json.loads(cache["deck_json"]).items() if key != "includes"}
    size = int(main.get("size_bytes") or 0)
    analysis = optistruct_materials._analysis(
        "READY", rel_path=rel, size=size, bytes_done=size, cached=True, parse_seconds=cache.get("parse_seconds"),
        parsed_at=_iso(cache.get("updated_at")))
    return {**base, "analysis": analysis, "deck": parsed, "preparation": preparation}


# --- worker side --------------------------------------------------------------------------------

def _fingerprint(listing: list[Any], files: list[Any]) -> str:
    payload = json.dumps({"dirs": listing, "files": files}, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _listing_part(fs: Any, directories: list[str], suffixes: frozenset[str] | set[str]) -> list[Any]:
    """(folder, deck files with their versions) of every folder the deck rule looks at; None when missing."""
    part: list[Any] = []
    for directory in directories:
        entry = fs.stat(directory)
        if entry is None or entry.kind != "dir":
            part.append([directory.casefold(), None])
            continue
        files = sorted([str(item.name), optistruct_materials.fingerprint(item)] for item in fs.list(directory)
                       if item.kind == "file" and PurePosixPath(str(item.name)).suffix.casefold() in suffixes)
        part.append([directory.casefold(), files])
    return part


def _files_part(fs: Any, relative_paths: list[str]) -> list[Any]:
    part = []
    for rel in sorted(set(relative_paths), key=str.casefold):
        entry = fs.stat(rel)
        part.append([rel.casefold(), optistruct_materials.fingerprint(entry) if entry is not None and entry.kind == "file" else None])
    return part


def _usage_directories(entry: dict[str, Any]) -> list[str]:
    directories = [str(entry["relative_path"])]
    case = (entry.get("hierarchy") or {}).get("simulation_case")
    if isinstance(case, dict) and case.get("relative_path") and str(case["relative_path"]).casefold() != directories[0].casefold():
        directories.append(str(case["relative_path"]))
    return directories


def _prepare_distribution(conn: ConnectionLike, row: dict[str, Any], entry: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    fs = provider_for_root(ctx["root"])
    listing = _listing_part(fs, catalog_service._candidate_directories(entry, ctx["scope"]), catalog_service._DECK_EXTENSIONS)
    old_files = [str(item.get("relative_path")) for item in _loads(row.get("files_json"), []) if isinstance(item, dict)]
    if (row.get("status") in {"READY", "NO_DECK"} and row.get("needs_check")
            and row.get("parser_version") == RADIOSS_PREPARATION_VERSION
            and _fingerprint(listing, _files_part(fs, old_files)) == row.get("fingerprint")):
        return {"status": "UNCHANGED"}
    try:
        parsed, warnings, files = catalog_service._parse_scene(
            entry, None, ctx["root"], ctx["scope"], conn, ctx["root_id"], ctx["root_key"])
    except catalog_service.MaterialsCatalogError as exc:
        if exc.code != "MATERIALS_DECK_NOT_FOUND":
            raise
        return {"status": "NO_DECK", "fingerprint": _fingerprint(listing, []), "files": [], "warnings": [],
                "parser_version": RADIOSS_PREPARATION_VERSION}
    files_part = _files_part(fs, [item["relative_path"] for item in files])
    versions = {rel: version for rel, version in files_part}
    directory = PurePosixPath(files[0]["relative_path"]).parent.as_posix() if files else None
    return {"status": "READY", "fingerprint": _fingerprint(listing, files_part), "deck": parsed, "warnings": warnings,
            "files": [{**item, "version": versions.get(item["relative_path"].casefold())} for item in files],
            "source_rel_path": directory, "parser_version": RADIOSS_PREPARATION_VERSION}


def _prepare_usage(conn: ConnectionLike, row: dict[str, Any], entry: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    fs = provider_for_root(ctx["root"])
    listing = _listing_part(fs, _usage_directories(entry), optistruct_materials.INPUT_SUFFIXES)
    old_files = [str(item.get("relative_path")) for item in _loads(row.get("files_json"), []) if isinstance(item, dict)]
    if (row.get("status") in {"READY", "NO_DECK"} and row.get("needs_check")
            and row.get("parser_version") == optistruct_materials.PARSER_VERSION
            and _fingerprint(listing, _files_part(fs, old_files)) == row.get("fingerprint")):
        return {"status": "UNCHANGED"}
    _, files, warnings = catalog_service._usage_candidates(
        entry, ctx["root"], ctx["scope"], conn=conn, root_id=ctx["root_id"], root_key=ctx["root_key"])
    if not files:
        return {"status": "NO_DECK", "fingerprint": _fingerprint(listing, []), "files": [], "warnings": [],
                "parser_version": optistruct_materials.PARSER_VERSION}
    rel = files[0]
    stat = fs.stat(rel)
    if stat is None or stat.kind != "file":
        raise catalog_service.MaterialsCatalogError("MATERIALS_SCAN_INCOMPLETE", "입력 파일 정보를 읽을 수 없습니다.")
    size, version = int(stat.size or 0), optistruct_materials.fingerprint(stat)
    limit = optistruct_materials.max_input_bytes()
    if size > limit:
        raise OptiStructParseError("MATERIALS_FILE_SIZE_LIMIT", optistruct_materials._size_message(limit), 413)
    cache = optistruct_materials.load_row(conn, ctx["root_key"], rel)
    if cache and cache.get("fingerprint") == version and cache.get("parser_version") == optistruct_materials.PARSER_VERSION:
        dependencies = _loads(cache.get("dependencies_json"), None)
        if isinstance(dependencies, list) and optistruct_materials._dependencies_fresh(fs, dependencies):
            deps = [{"relative_path": str(item.get("rel_path")), "size_bytes": item.get("size_bytes"),
                     "version": item.get("fingerprint")} for item in dependencies if not item.get("missing")]
            files_part = _files_part(fs, [rel, *[item["relative_path"] for item in deps]])
            result = {"fingerprint": _fingerprint(listing, files_part), "warnings": warnings, "source_rel_path": rel,
                      "files": [{"relative_path": rel, "size_bytes": size, "version": version}, *deps],
                      "parser_version": optistruct_materials.PARSER_VERSION}
            if cache.get("status") == "READY":
                return {"status": "READY", **result}
            return {"status": "FAILED", **result, "error_code": cache.get("error_code") or "MATERIALS_PARSE_FAILED",
                    "error_message": cache.get("error_message") or "입력 파일을 분석하지 못했습니다."}
    blob = cache.get("blob_sha256") if cache and cache.get("fingerprint") == version else None
    spec = optistruct_materials.JobSpec(
        key=optistruct_materials.job_key(ctx["root_key"], rel, version), root=ctx["root"], root_key=ctx["root_key"],
        rel_path=rel, fingerprint=version, size=size, request_relative_path=ctx["scope"]["request_relative_path"],
        blob_sha256=blob, resolve_include=catalog_service._resolve_optistruct_include)
    return {"status": "PARSE", "spec": spec}


def _store(conn: ConnectionLike, row: dict[str, Any], result: dict[str, Any]) -> None:
    now = _now()
    status = result["status"]
    if status == "UNCHANGED":
        _update(conn, str(row["id"]), needs_check=False, checked_at=now)
        return
    values: dict[str, Any] = {
        "status": status, "needs_check": False, "checked_at": now, "attempts": 0,
        "fingerprint": result.get("fingerprint"), "parser_version": result.get("parser_version"),
        "source_rel_path": result.get("source_rel_path"), "files_json": _dump(result.get("files") or []),
        "warnings_json": _dump(result.get("warnings") or []), "error_code": result.get("error_code"),
        "error_message": (str(result.get("error_message") or "")[:_MESSAGE_LIMIT] or None),
        "deck_json": _dump(result["deck"]) if result.get("deck") is not None else None,
    }
    if status in {"READY", "NO_DECK"}:
        values["prepared_at"] = now
    _update(conn, str(row["id"]), **values)


def _failure(conn: ConnectionLike, row: dict[str, Any], code: str, message: str, *, transient: bool) -> None:
    """A transient failure keeps the row PENDING (or its prepared state) until ``MAX_TRANSIENT_ATTEMPTS``."""
    attempts = int(row.get("attempts") or 0) + 1
    message = message[:_MESSAGE_LIMIT]
    if transient and attempts < MAX_TRANSIENT_ATTEMPTS:
        _update(conn, str(row["id"]), attempts=attempts, error_code=code, error_message=message)
        return
    if row.get("status") in {"READY", "NO_DECK"} and transient:
        # The prepared deck stays visible; the next view after RECHECK_SECONDS checks again.
        _update(conn, str(row["id"]), attempts=0, needs_check=False, checked_at=_now(), error_code=code,
                error_message=message)
        return
    _update(conn, str(row["id"]), status="FAILED", attempts=attempts, needs_check=False, checked_at=_now(),
            error_code=code, error_message=message, deck_json=None)


def _scope_entries(conn: ConnectionLike, request_id: str, environment: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    _, scope, root, root_id, root_key, schema = catalog_service._request_scope(conn, request_id, environment)
    items = catalog_service._catalog_items(conn, root, root_id, root_key, scope, schema, deck_lookup=lambda entry: None)
    ctx = {"scope": scope, "root": root, "root_id": root_id, "root_key": root_key}
    return ctx, {_scene_key(item["relative_path"]): item for item in items}


def _ensure_snapshot(project_id: str, request_id: str, environment: str, deadline: float) -> bool:
    from . import folder_auto_sync
    from .drive import reads as drive_reads

    with connect() as conn:
        if _schema_ready(conn, project_id, request_id, environment):
            return True
    with drive_reads.bounded(deadline=deadline):
        folder_auto_sync.sync_scope(project_id, request_id, environment, ACTOR, force=True)
    with connect() as conn:
        return _schema_ready(conn, project_id, request_id, environment)


def _run_scope(scope_key: tuple[str, str, str], deadline: float, tally: dict[str, int],
               should_stop: Callable[[], bool] | None) -> None:
    """The waiting Scenes of one request: one bounded, resumable BACKGROUND read session (+ OptiStruct parses)."""
    from .drive import reads as drive_reads

    project_id, request_id, environment = scope_key
    with _queued_lock:
        _queued.discard(scope_key)
    if not _ensure_snapshot(project_id, request_id, environment, deadline):
        return
    with connect() as conn:
        ctx, entries = _scope_entries(conn, request_id, environment)
        if not entries:
            with _queued_lock:
                _empty_scopes[scope_key] = time.monotonic()
            return
        found = _rows(conn, ctx["root_key"], request_id, environment)
        for key, entry in entries.items():
            if key not in found:
                _insert_pending(conn, ctx["root_key"], project_id, request_id, environment, entry["relative_path"])
        work = int(conn.execute(
            f"SELECT count(*) FROM {TABLE} WHERE root_key=? AND request_id=? AND environment=? "
            "AND (status='PENDING' OR needs_check=?)", [ctx["root_key"], request_id, environment, True]).fetchone()[0])
    if not work:
        return

    parsed_now: set[str] = set()
    for _attempt in range(2):     # the second session stores the OptiStruct parses the first one asked for
        stopped: set[str] = set()
        parses: dict[str, Any] = {}
        ended = {"value": False}

        def body() -> None:
            with connect() as conn:
                ctx, entries = _scope_entries(conn, request_id, environment)
                while True:
                    if should_stop is not None and should_stop():
                        ended["value"] = True
                        return
                    candidates = [row for key, row in _rows(conn, ctx["root_key"], request_id, environment).items()
                                  if key in entries and key not in stopped and key not in parses
                                  and (row.get("status") == "PENDING" or row.get("needs_check"))]
                    if not candidates:
                        return
                    row = min(candidates, key=lambda item: (str(item.get("updated_at")), str(item["id"])))
                    key = str(row["scene_key"])
                    entry = entries[key]
                    try:
                        prepare = _prepare_usage if environment == "USAGE" else _prepare_distribution
                        result = prepare(conn, row, entry, ctx)
                    except (catalog_service.MaterialsCatalogError, OptiStructParseError, SpdmStorageError, OSError) as exc:
                        code = str(getattr(exc, "code", None) or "MATERIALS_SCAN_INCOMPLETE")
                        transient = _is_transient(code) or (isinstance(exc, OSError)
                                                            and not isinstance(exc, SpdmStorageError))
                        if code in _SESSION_CODES or (transient and (drive_reads.stop_reason() is not None
                                                                     or drive_reads.final_round())):
                            ended["value"] = True
                            return   # the session ended (deadline, rounds): this Scene waits for the next pass
                        message = str(exc) if not isinstance(exc, OSError) or isinstance(exc, SpdmStorageError) \
                            else f"덱 파일을 읽을 수 없습니다: {exc.__class__.__name__}"
                        _failure(conn, row, code, message, transient=transient)
                        stopped.add(key)
                        tally["failed"] += 1
                        continue
                    if result["status"] == "PARSE":
                        if key in parsed_now:
                            info = optistruct_materials.transient_error(ctx["root_key"], result["spec"].rel_path) or {}
                            _failure(conn, row, str(info.get("error_code") or "MATERIALS_SCAN_INCOMPLETE"),
                                     str(info.get("error_message") or "입력 파일을 아직 분석하지 못했습니다. 다음 확인에서 다시 분석합니다."),
                                     transient=True)
                            stopped.add(key)
                            continue
                        parses[key] = result["spec"]
                        continue
                    try:
                        _store(conn, row, result)
                    except ValueError:   # not JSON-serialisable (a non-finite number)
                        _failure(conn, row, "MATERIALS_PARSE_FAILED", "분석 결과를 저장할 수 없습니다(잘못된 수치).",
                                 transient=False)
                    tally[result["status"].lower()] = tally.get(result["status"].lower(), 0) + 1

        rounds = drive_reads.MAX_ROUNDS + 4 * work
        with drive_reads.bounded(deadline=deadline):
            drive_reads.run(body, priority="BACKGROUND", rounds=rounds, resumable=True)
        if ended["value"]:
            tally["interrupted"] += 1
        if not parses:
            return
        # OptiStruct inputs (500-1000 MB): parsed one after another with their own read session each,
        # cached in materials_deck_cache (blob reuse, size and time limits of optistruct_materials).
        for key, spec in parses.items():
            if (should_stop is not None and should_stop()) or time.monotonic() >= deadline:
                return
            optistruct_materials.run_job(optistruct_materials.JobState(spec=spec))
            parsed_now.add(key)


def _work_scopes(root_key: str) -> list[tuple[str, str, str]]:
    from . import capture_jobs

    marks = ",".join("?" for _ in capture_jobs.LIVE_STATES)
    with connect() as conn:
        waiting = [(str(a), str(b), str(c)) for a, b, c in conn.execute(
            f"SELECT DISTINCT project_id,request_id,environment FROM {TABLE} WHERE root_key=? "
            "AND (status='PENDING' OR needs_check=?) ORDER BY request_id,environment", [root_key, True]).fetchall()]
        unseen = [(str(a), str(b), str(c).upper()) for a, b, c in conn.execute(
            "SELECT DISTINCT r.project_id,r.request_id,r.environment FROM folder_environment_registrations r "
            "JOIN folder_environment_previews p ON p.id=r.preview_id JOIN folder_environment_scans s ON s.id=p.scan_id "
            f"WHERE r.status IN ({marks}) AND s.root_key=? AND r.project_id IS NOT NULL AND r.request_id IS NOT NULL "
            f"AND upper(r.environment) IN ('DISTRIBUTION','USAGE') AND NOT EXISTS (SELECT 1 FROM {TABLE} m "
            "WHERE m.root_key=? AND m.request_id=r.request_id AND m.environment=upper(r.environment)) "
            "ORDER BY r.request_id", [*capture_jobs.LIVE_STATES, root_key, root_key]).fetchall()]
    now = time.monotonic()
    with _queued_lock:
        queued = sorted(_queued)
        recent_empty = {key for key, at in _empty_scopes.items() if now - at < EMPTY_SCOPE_SECONDS}
    ordered: list[tuple[str, str, str]] = []
    for item in [*waiting, *queued, *[scope for scope in unseen if scope not in recent_empty]]:
        if item not in ordered:
            ordered.append(item)
    return ordered


def run_pending(*, budget_seconds: float = PASS_SECONDS, session_seconds: float = SESSION_SECONDS,
                cancel: threading.Event | None = None, should_stop: Callable[[], bool] | None = None,
                scopes: list[tuple[str, str, str]] | None = None) -> dict[str, Any]:
    """Prepare the waiting Scenes until none is left or ``budget_seconds`` is used (never raises)."""
    from . import folder_discovery, folder_discovery_environment
    from .drive import reads as drive_reads

    tally = {"ready": 0, "no_deck": 0, "failed": 0, "unchanged": 0, "interrupted": 0}
    if not drive_reads.active():
        return {"status": "DISABLED", **tally}
    if not _run_lock.acquire(blocking=False):
        return {"status": "BUSY", **tally}
    status = "DONE"
    try:
        with connect() as conn:
            root_key = folder_discovery_environment.root_identity(folder_discovery.configured_root(conn))
        deadline = time.monotonic() + max(0.0, float(budget_seconds))
        tried: set[tuple[str, str, str]] = set()
        while True:
            if (cancel is not None and cancel.is_set()) or (should_stop is not None and should_stop()):
                status = "STOPPED"
                break
            if time.monotonic() >= deadline - 1.0:
                status = "TIME_LIMIT"
                break
            waiting = [item for item in (scopes if scopes is not None else _work_scopes(root_key)) if item not in tried]
            if not waiting:
                break
            scope_key = waiting[0]
            tried.add(scope_key)
            try:
                with drive_reads.bounded(cancel=cancel):
                    _run_scope(scope_key, min(deadline, time.monotonic() + session_seconds), tally, should_stop)
            except Exception as exc:  # noqa: BLE001 - one request never stops the others
                if getattr(exc, "code", None) == "DRIVE_BUSY":
                    logger.info("Materials preparation of request %s deferred: the drive is busy", scope_key[1])
                    status = "BUSY"
                    break
                logger.warning("Materials preparation of request %s (%s) failed", scope_key[1], scope_key[2],
                               exc_info=True)
    except Exception:  # noqa: BLE001 - the worker never dies
        logger.warning("Materials preparation pass failed", exc_info=True)
        status = "FAILED"
    finally:
        _run_lock.release()
    if any(tally.values()):
        logger.info("Materials preparation: %(ready)d ready, %(no_deck)d without deck, %(failed)d failed, "
                    "%(unchanged)d unchanged, %(interrupted)d interrupted", tally)
    return {"status": status, **tally}


__all__ = ["catalog", "deck", "enabled", "queue_scope", "request_check", "reset_for_tests", "run_pending"]
