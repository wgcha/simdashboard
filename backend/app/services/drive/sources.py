"""Registered drive source versions, pending changes and MISSING sources (stage D2, 05 §4, 04 §2.2).

Table ``drive_source_versions`` (migration 0037): one row per registered
version of a result-relevant drive file below the SPDM root; the current row
of a path has ``superseded_at IS NULL``.

Auto-reflect rule in scx mode (plan §9 D3, user decision 2026-10-07):

* a **new** file (no current row) is registered automatically (version 1), so
  new files and new Scenes appear with the next 60 s sync, as in local mode;
* a **changed** existing file (its drive ``version_token`` differs from the
  registered one) is not applied: the row becomes ``review_state='PENDING'``
  with the drive values in ``pending_*``.  The dashboard keeps showing the
  registered version (``reads`` overlay) until a user accepts it
  (``accept``: a new row ``version_no+1``, the old one superseded) or dismisses
  it (``dismiss``: ``IGNORED`` for that drive version; a later change asks again);
* a file **gone** from the drive is ``source_state='MISSING'``; nothing in the
  DB is deleted and the registered version stays visible.  Accepting a
  MISSING row confirms the removal (the row is superseded, the next sync drops
  the file from the folder snapshot).

``version_token`` is the drive's sha1 when the listing carries it, otherwise
size + modification time (contract §2.2; C1/C9 unresolved).  A size/time-only
difference is confirmed by downloading the file and comparing sha256 with the
registered content when that is known; equal content only refreshes the row.
All drive access here happens without a database connection; every database
step is a short connection of its own.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable

from ...database_connection import ConnectionLike, connect, connection_held, rows
from ..storage.provider import SpdmStorageError
from . import reads
from .reads import Accepted, Version, epoch_us, version_of

AUTO_ACTOR = "auto-sync"
MAX_SCOPE_FILES = 50_000
# Same set as folder_discovery_scan.RESULT_RELEVANT_EXTENSIONS (results, media, decks, reports).
RELEVANT_SUFFIXES = frozenset({".csv", ".json", ".jpg", ".jpeg", ".png", ".mp4", ".webm", ".inc", ".rad",
                               ".pdf", ".ppt", ".pptx", ".xlsx"})
_COLUMNS = ("id,root_key,rel_path,version_no,project_id,request_id,item_id,size_bytes,modified_at,sha1,sha256,"
            "version_token,content_stored,stored_path,pending_version_token,pending_size_bytes,pending_modified_at,"
            "pending_sha1,pending_item_id,pending_detected_at,review_state,reviewed_by,reviewed_at,registered_at,"
            "registered_by,superseded_at,source_state,last_checked_at")


class DriveSourceError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stamp(us: int | None) -> datetime | None:
    if us is None:
        return None
    return datetime(1970, 1, 1) + timedelta(microseconds=int(us))


def _iso(value: Any) -> str | None:
    if not isinstance(value, datetime):
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def row_version(row: dict[str, Any]) -> Version:
    size = row.get("size_bytes")
    return Version(token=str(row["version_token"]), size=int(size) if size is not None else None,
                   modified_us=epoch_us(row.get("modified_at")), sha1=row.get("sha1") or None,
                   item_id=row.get("item_id") or None)


def pending_version(row: dict[str, Any]) -> Version | None:
    if not row.get("pending_version_token"):
        return None
    size = row.get("pending_size_bytes")
    return Version(token=str(row["pending_version_token"]), size=int(size) if size is not None else None,
                   modified_us=epoch_us(row.get("pending_modified_at")), sha1=row.get("pending_sha1") or None,
                   item_id=row.get("pending_item_id") or None)


# --- reads for the provider overlay -----------------------------------------------------------

def load_overlay(conn: ConnectionLike, root_key: str) -> dict[str, Accepted]:
    found = rows(conn.execute(
        "SELECT rel_path,version_token,size_bytes,modified_at,sha1,item_id,sha256,stored_path,source_state,review_state "
        "FROM drive_source_versions WHERE root_key=? AND superseded_at IS NULL "
        "AND (review_state<>'NONE' OR source_state='MISSING')", [root_key]))
    return {str(row["rel_path"]): Accepted(rel_path=str(row["rel_path"]), version=row_version(row),
                                          sha256=row.get("sha256"), stored_path=row.get("stored_path"),
                                          source_state=str(row["source_state"]), review_state=str(row["review_state"]))
            for row in found}


def current_row(conn: ConnectionLike, root_key: str, rel_path: str) -> dict[str, Any] | None:
    found = rows(conn.execute(f"SELECT {_COLUMNS} FROM drive_source_versions WHERE root_key=? AND rel_path=? "
                              "AND superseded_at IS NULL", [root_key, rel_path]))
    return found[0] if found else None


def registered_content(conn: ConnectionLike, sha256: str) -> bytes | None:
    """Bytes of a registered version from the DB (captured asset or result registration file)."""
    for statement in ("SELECT content FROM dashboard_assets WHERE sha256=? AND content IS NOT NULL LIMIT 1",
                      "SELECT content FROM result_registration_files WHERE sha256=? AND content IS NOT NULL LIMIT 1"):
        row = conn.execute(statement, [sha256]).fetchone()
        if row and row[0] is not None:
            return bytes(row[0])
    return None


def _in_db(conn: ConnectionLike, sha256: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM dashboard_assets WHERE sha256=? AND content IS NOT NULL LIMIT 1",
                             [sha256]).fetchone())


def _insert(conn: ConnectionLike, root_key: str, rel: str, version: Version, *, version_no: int,
            scope_ids: tuple[str, str] | None, actor: str, sha256: str | None = None, stored_path: str | None = None,
            content_stored: bool = False) -> None:
    now = _now()
    project_id, request_id = scope_ids or (None, None)
    conn.execute(
        "INSERT INTO drive_source_versions(id,root_key,rel_path,version_no,project_id,request_id,item_id,size_bytes,"
        "modified_at,sha1,sha256,version_token,content_stored,stored_path,review_state,registered_at,registered_by,"
        "source_state,last_checked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'NONE',?,?,'PRESENT',?) ON CONFLICT DO NOTHING",
        [f"drive-source-{uuid.uuid4().hex}", root_key, rel, version_no, project_id, request_id, version.item_id,
         version.size, _stamp(version.modified_us), version.sha1, sha256, version.token, content_stored, stored_path,
         now, actor, now])


def record_downloads(conn: ConnectionLike, root_key: str, downloads: Iterable[Any],
                     scope_ids: tuple[str, str] | None) -> None:
    """After a successful read session: remember sha256 (and blob path) of the versions it downloaded.

    A file read for the first time (e.g. by the registration capture) gets its
    version 1 here, so a later drive change is detected against what was read.
    """
    for record in downloads:
        row = current_row(conn, root_key, record.rel_path)
        stored = _in_db(conn, record.sha256)
        if row is None:
            if PurePosixPath(record.rel_path).suffix.casefold() not in RELEVANT_SUFFIXES:
                continue
            _insert(conn, root_key, record.rel_path, record.version, version_no=1, scope_ids=scope_ids,
                    actor=AUTO_ACTOR, sha256=record.sha256, stored_path=record.stored_path, content_stored=stored)
            continue
        if not row_version(row).same_as(record.version):
            continue
        if row.get("sha256") == record.sha256 and (record.stored_path is None or row.get("stored_path") == record.stored_path) \
                and bool(row.get("content_stored")) == stored:
            continue
        conn.execute("UPDATE drive_source_versions SET sha256=?,stored_path=COALESCE(?,stored_path),content_stored=? WHERE id=?",
                     [record.sha256, record.stored_path, stored, row["id"]])


# --- sync classification (05 §4) --------------------------------------------------------------

def _walk_scope(session: reads.DriveReadSession, request_path: str) -> tuple[dict[str, Any], list[str]]:
    """Result-relevant files below the request folder (Final archive and dot folders skipped), from the drive."""
    from ..folder_discovery_environment import _skip_final_archive

    skip = _skip_final_archive(request_path)
    files: dict[str, Any] = {}
    skipped: list[str] = []
    stack: list[tuple[str, str | None]] = [(request_path, None)]
    while stack:
        folder, parent = stack.pop()
        if skip(folder, parent):
            skipped.append(folder)
            continue
        try:
            entries = reads.listing(session.spdm_root, folder)
        except SpdmStorageError as error:
            if error.code == "SPDM_NOT_FOUND":
                continue   # deleted meanwhile: its files are MISSING
            raise
        for entry in entries:
            name = str(entry.name)
            child = f"{folder}/{name}" if folder else name
            if name.startswith("."):
                skipped.append(child)
                continue
            if entry.kind == "dir":
                stack.append((child, folder))
            elif entry.kind == "file" and PurePosixPath(name).suffix.casefold() in RELEVANT_SUFFIXES:
                files[child] = entry
                if len(files) > MAX_SCOPE_FILES:
                    raise SpdmStorageError("SPDM_LIMIT", "의뢰 폴더의 결과 파일 수가 상한을 넘었습니다.")
    return files, skipped


def _scope_rows(conn: ConnectionLike, root_key: str, request_path: str) -> dict[str, dict[str, Any]]:
    prefix = request_path.rstrip("/") + "/"
    found = rows(conn.execute(
        f"SELECT {_COLUMNS} FROM drive_source_versions WHERE root_key=? AND superseded_at IS NULL "
        "AND substr(rel_path,1,?)=?", [root_key, len(prefix), prefix]))
    return {str(row["rel_path"]): row for row in found}


def _below_any(rel: str, prefixes: list[str]) -> bool:
    return any(rel == prefix or rel.startswith(prefix.rstrip("/") + "/") for prefix in prefixes)


def classify_request(session: reads.DriveReadSession, project_id: str, request_id: str, environment: str,
                     actor: str | None = None) -> dict[str, Any]:
    """Compare the request folder on the drive with the registered versions and record the outcome.

    Runs as the ``prepare`` step of a read session (no DB connection held while
    the drive is listed or a file is downloaded).  Returns counts for the UI.
    """
    from ..folder_schema_resolver import _request_path

    if connection_held():
        raise RuntimeError("classification must run without an open database connection")
    with connect() as conn:
        request_path = _request_path(conn, session.root_key, project_id, request_id, environment)
    session.scope = request_path
    session.scope_ids = (str(project_id), str(request_id))
    files, skipped = _walk_scope(session, request_path)
    with connect() as conn:
        registered = _scope_rows(conn, session.root_key, request_path)

    now = _now()
    inserts: list[tuple[str, Version]] = []
    updates: list[tuple[str, dict[str, Any]]] = []
    for rel, entry in sorted(files.items()):
        live = version_of(entry)
        row = registered.get(rel)
        if row is None:
            inserts.append((rel, live))
            continue
        accepted = row_version(row)
        same = accepted.same_as(live)
        if not same and not (live.sha1 and accepted.sha1) and row.get("sha256"):
            # size/time differ but sha1 cannot decide: compare the content (05 §4)
            try:
                _size, digest = reads.local_copy_sha256(session.spdm_root, rel, live, masked=False,
                                                        max_bytes=reads.MAX_FILE_BYTES)
            except SpdmStorageError:
                digest = None   # unreadable now: treat as changed, the user decides
            same = digest == row.get("sha256")
        if same:
            fields: dict[str, Any] = {}
            if live.token != accepted.token:   # same content, other token form or time: refresh the row in place
                fields.update(version_token=live.token, size_bytes=live.size, modified_at=_stamp(live.modified_us),
                              sha1=live.sha1 or row.get("sha1"), item_id=live.item_id or row.get("item_id"))
            if row["source_state"] != "PRESENT":
                fields.update(source_state="PRESENT", last_checked_at=now)
            if row["review_state"] != "NONE":
                fields.update(review_state="NONE", pending_version_token=None, pending_size_bytes=None,
                              pending_modified_at=None, pending_sha1=None, pending_item_id=None, pending_detected_at=None)
            if fields:
                updates.append((str(row["id"]), fields))
            continue
        pending = pending_version(row)
        if pending is not None and pending.same_as(live) and row["review_state"] in {"PENDING", "IGNORED"}:
            if row["source_state"] != "CHANGED":
                updates.append((str(row["id"]), {"source_state": "CHANGED", "last_checked_at": now}))
            continue
        updates.append((str(row["id"]), {
            "review_state": "PENDING", "source_state": "CHANGED", "pending_version_token": live.token,
            "pending_size_bytes": live.size, "pending_modified_at": _stamp(live.modified_us), "pending_sha1": live.sha1,
            "pending_item_id": live.item_id, "pending_detected_at": now, "last_checked_at": now,
        }))
    for rel, row in registered.items():
        if rel in files or _below_any(rel, skipped) or row["source_state"] == "MISSING":
            continue
        updates.append((str(row["id"]), {"source_state": "MISSING", "last_checked_at": now}))

    with connect() as conn:
        relabel = bool(conn.execute(
            "SELECT 1 FROM drive_source_versions WHERE root_key=? AND superseded_at IS NULL AND substr(rel_path,1,?)=? "
            "AND (project_id IS NULL OR request_id IS NULL OR project_id<>? OR request_id<>?) LIMIT 1",
            [session.root_key, len(request_path) + 1, request_path.rstrip("/") + "/", project_id, request_id]).fetchone())
    if inserts or updates or relabel:
        with connect() as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                for rel, live in inserts:
                    _insert(conn, session.root_key, rel, live, version_no=1, scope_ids=session.scope_ids,
                            actor=AUTO_ACTOR)
                for row_id, fields in updates:
                    assignments = ",".join(f"{name}=?" for name in fields)
                    conn.execute(f"UPDATE drive_source_versions SET {assignments} WHERE id=?", [*fields.values(), row_id])
                # Scope labels follow the request that owns the folder now (a folder registered again
                # after a project cleanup or a registration delete gets the new ids).
                conn.execute("UPDATE drive_source_versions SET project_id=?,request_id=? WHERE root_key=? "
                             "AND superseded_at IS NULL AND substr(rel_path,1,?)=? "
                             "AND (project_id IS NULL OR request_id IS NULL OR project_id<>? OR request_id<>?)",
                             [project_id, request_id, session.root_key, len(request_path) + 1, request_path.rstrip("/") + "/",
                              project_id, request_id])
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        reads.load_overlay(session)
    kinds = {version_of(entry).kind for entry in files.values()}
    return {"files": len(files), "new": len(inserts), "token_kind": (kinds.pop() if len(kinds) == 1 else ("mixed" if kinds else None))}


# --- pending changes API (05 §4) --------------------------------------------------------------

def _request_scope(conn: ConnectionLike, root_key: str, project_id: str, request_id: str) -> None:
    row = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
    if not row or str(row[0]) != str(project_id):
        raise DriveSourceError("DRIVE_SOURCE_SCOPE_MISMATCH", "프로젝트와 의뢰 문맥이 일치하지 않습니다.", 404)


def summary(conn: ConnectionLike, root_key: str, project_id: str, request_id: str) -> dict[str, int]:
    row = conn.execute(
        "SELECT sum(CASE WHEN review_state='PENDING' THEN 1 ELSE 0 END),"
        "sum(CASE WHEN source_state='MISSING' AND review_state<>'IGNORED' THEN 1 ELSE 0 END),"
        "sum(CASE WHEN review_state='IGNORED' THEN 1 ELSE 0 END) "
        "FROM drive_source_versions WHERE root_key=? AND project_id=? AND request_id=? AND superseded_at IS NULL",
        [root_key, project_id, request_id]).fetchone()
    return {"pending_changes": int(row[0] or 0) if row else 0, "missing": int(row[1] or 0) if row else 0,
            "ignored": int(row[2] or 0) if row else 0}


def _item(row: dict[str, Any]) -> dict[str, Any]:
    pending = pending_version(row)
    kind = "MISSING" if row["source_state"] == "MISSING" else "CHANGED"
    return {
        "id": str(row["id"]), "relative_path": str(row["rel_path"]), "kind": kind,
        "review_state": str(row["review_state"]), "version_no": int(row["version_no"]),
        "registered": {"size": row.get("size_bytes"), "modified_at": _iso(row.get("modified_at")),
                       "token_kind": row_version(row).kind, "registered_at": _iso(row.get("registered_at"))},
        "drive": None if pending is None or kind == "MISSING" else {
            "size": pending.size, "modified_at": _iso(_stamp(pending.modified_us)), "token_kind": pending.kind},
        "detected_at": _iso(row.get("pending_detected_at") or row.get("last_checked_at")),
    }


def list_changes(conn: ConnectionLike, root_key: str, project_id: str, request_id: str) -> dict[str, Any]:
    _request_scope(conn, root_key, project_id, request_id)
    found = rows(conn.execute(
        f"SELECT {_COLUMNS} FROM drive_source_versions WHERE root_key=? AND project_id=? AND request_id=? "
        "AND superseded_at IS NULL AND (review_state<>'NONE' OR source_state='MISSING') ORDER BY rel_path",
        [root_key, project_id, request_id]))
    items = [_item(row) for row in found]
    return {"project_id": project_id, "request_id": request_id, "items": items,
            **summary(conn, root_key, project_id, request_id)}


def _selected(conn: ConnectionLike, root_key: str, project_id: str, request_id: str, ids: list[str] | None,
              states: tuple[str, ...]) -> list[dict[str, Any]]:
    found = rows(conn.execute(
        f"SELECT {_COLUMNS} FROM drive_source_versions WHERE root_key=? AND project_id=? AND request_id=? "
        "AND superseded_at IS NULL AND (review_state<>'NONE' OR source_state='MISSING') ORDER BY rel_path",
        [root_key, project_id, request_id]))
    candidates = [row for row in found if (row["review_state"] in states or
                                           ("MISSING" in states and row["source_state"] == "MISSING"))]
    if ids is None:
        return candidates
    wanted = set(ids)
    chosen = [row for row in candidates if str(row["id"]) in wanted]
    if len(chosen) != len(wanted):
        raise DriveSourceError("DRIVE_SOURCE_CHANGE_STALE", "확인할 변경 목록이 바뀌었습니다. 다시 불러오세요.")
    return chosen


def accept(conn: ConnectionLike, root_key: str, project_id: str, request_id: str, ids: list[str] | None,
           actor: str) -> dict[str, Any]:
    """[새 버전 등록]: register the pending drive version as ``version_no+1`` (old row kept, superseded).

    The content is read and captured by the next sync of the request (the
    caller invalidates the sync memo), which downloads only the accepted files.
    A MISSING row is confirmed as removed (superseded, no new row).
    """
    _request_scope(conn, root_key, project_id, request_id)
    chosen = _selected(conn, root_key, project_id, request_id, ids, ("PENDING", "IGNORED", "MISSING"))
    now = _now()
    accepted_count = removed = 0
    conn.execute("BEGIN TRANSACTION")
    try:
        for row in chosen:
            conn.execute("UPDATE drive_source_versions SET superseded_at=?,reviewed_by=?,reviewed_at=? "
                         "WHERE id=? AND superseded_at IS NULL", [now, actor, now, row["id"]])
            pending = pending_version(row)
            if row["source_state"] == "MISSING" or pending is None:
                removed += 1
                continue
            _insert(conn, root_key, str(row["rel_path"]), pending, version_no=int(row["version_no"]) + 1,
                    scope_ids=(project_id, request_id), actor=actor)
            accepted_count += 1
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"accepted": accepted_count, "removed": removed, **summary(conn, root_key, project_id, request_id)}


def dismiss(conn: ConnectionLike, root_key: str, project_id: str, request_id: str, ids: list[str] | None,
            actor: str) -> dict[str, Any]:
    """[무시]: keep the registered version; this drive version is not asked again (a newer one is)."""
    _request_scope(conn, root_key, project_id, request_id)
    chosen = _selected(conn, root_key, project_id, request_id, ids, ("PENDING", "MISSING"))
    now = _now()
    conn.execute("BEGIN TRANSACTION")
    try:
        for row in chosen:
            conn.execute("UPDATE drive_source_versions SET review_state='IGNORED',reviewed_by=?,reviewed_at=? WHERE id=?",
                         [actor, now, row["id"]])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"dismissed": len(chosen), **summary(conn, root_key, project_id, request_id)}


__all__ = ["AUTO_ACTOR", "DriveSourceError", "accept", "classify_request", "current_row", "dismiss", "list_changes",
           "load_overlay", "record_downloads", "registered_content", "row_version", "summary"]
