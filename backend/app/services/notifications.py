"""Per-user notifications: the top bar bell and the notifications page.

Contract: ``docs/features/notifications.md``.  Table ``notifications`` (migration
0040, DuckDB equivalent ``database.ensure_notifications_schema``).

* **Writing**: event code calls :func:`emit` (through one of the helpers below)
  with the connection that records the event, before that transaction commits,
  so a notification exists exactly when its event does.  Writing is DB-only (no
  drive or file access) and never raises: a failure is logged, and on
  PostgreSQL a savepoint keeps the caller's transaction usable.
* **Recipients** (:func:`recipients`): ACTIVE users only.  Project events go to
  the request's project members (and explicitly named users such as the user
  who started the work) that hold the event's permission in that project
  (``access_policy.permissions_for``: membership role, global admin = admin).
  Global admins are recipients of a project event only when named explicitly
  (they started the work) or the event is administrative (queue paused).
* **Dedupe**: while an unread row with the same ``(user_id, dedupe_key)``
  exists, a repeat is skipped, or with ``coalesce`` updates that row in place
  (title, body, time) so one unread notification summarises the repeats.
* **Retention**: rows older than :data:`RETENTION_DAYS` are hidden on read and
  deleted on the next insert for that user; at most :data:`MAX_PER_USER` rows
  are kept per user (oldest deleted first).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlencode

from ..access_policy import PROJECT_DATA_VIEW, RESULT_IMPORT, Permission, permissions_for
from ..config import security_settings

logger = logging.getLogger("app.services.notifications")

RETENTION_DAYS = 90
MAX_PER_USER = 1000
MAX_RECIPIENTS = 500
SEVERITIES = ("INFO", "SUCCESS", "WARNING", "ERROR")

# type -> Korean label (filters on the notifications page)
TYPES: dict[str, str] = {
    "DRIVE_QUEUE_PAUSED": "드라이브 대기열",
    "DRIVE_UPLOAD": "드라이브 업로드",
    "FINAL": "Final 지정",
    "FINAL_SUMMARY": "Final 요약 파일",
    "DRIVE_SOURCE_CHANGED": "원본 변경 확인",
    "DRIVE_SOURCE_MISSING": "원본 없음",
    "NEW_RESULTS": "새 결과 반영",
    "CAPTURE_FAILED": "결과 캡처 실패",
}

_COLUMNS = "id,user_id,type,severity,title,body,link,project_id,request_id,dedupe_key,created_at,read_at"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return stamp.isoformat().replace("+00:00", "Z")
    return str(value)


def request_link(project_id: str | None, request_id: str | None) -> str:
    """Case results screen of a request (``/workspace/requests?...&view=case_results``)."""
    if not project_id or not request_id:
        return "/workspace/requests"
    return "/workspace/requests?" + urlencode({"project": project_id, "request": request_id, "view": "case_results"})


ADMIN_DRIVE_LINK = "/workspace/overview"


# --- recipients -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class _Principal:
    user_id: str
    account_status: str
    is_global_admin: bool


def _local_admin() -> bool:
    try:
        return security_settings().auth_mode == "disabled"
    except Exception:  # noqa: BLE001 - settings unavailable: no implicit local administrator
        return False


def _active_users(conn: Any, ids: Iterable[str]) -> dict[str, _Principal]:
    wanted = sorted({str(value) for value in ids if value})
    found: dict[str, _Principal] = {}
    for start in range(0, len(wanted), 200):
        chunk = wanted[start:start + 200]
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(f"SELECT id, is_global_admin FROM users WHERE id IN ({marks}) "
                                "AND account_status='ACTIVE' AND is_active", chunk).fetchall():
            found[str(row[0])] = _Principal(str(row[0]), "ACTIVE", bool(row[1]))
    if "local-admin" in wanted and _local_admin():
        found["local-admin"] = _Principal("local-admin", "ACTIVE", True)
    return found


def global_admins(conn: Any) -> list[str]:
    ids = [str(row[0]) for row in conn.execute(
        "SELECT id FROM users WHERE is_global_admin AND account_status='ACTIVE' AND is_active ORDER BY id").fetchall()]
    if _local_admin():
        ids.append("local-admin")
    return ids


def recipients(conn: Any, *, project_id: str | None = None, permission: Permission | None = PROJECT_DATA_VIEW,
               members: bool = True, include: Iterable[str | None] = (), admins: bool = False) -> list[str]:
    """ACTIVE users allowed to see a project event (see the module docstring)."""
    candidates: set[str] = {str(value) for value in include if value}
    if members and project_id:
        candidates |= {str(row[0]) for row in conn.execute(
            "SELECT user_id FROM project_memberships WHERE project_id=?", [project_id]).fetchall()}
    if admins:
        candidates |= set(global_admins(conn))
    active = _active_users(conn, candidates)
    roles: dict[str, str] = {}
    if project_id:
        roles = {str(row[0]): str(row[1]) for row in conn.execute(
            "SELECT user_id, role FROM project_memberships WHERE project_id=?", [project_id]).fetchall()}
    allowed = []
    for user_id, principal in sorted(active.items()):
        if permission is not None:
            role = "admin" if principal.is_global_admin else roles.get(user_id)
            granted = permissions_for(principal, role if role in {"general", "power", "admin"} else None)  # type: ignore[arg-type]
            if permission not in granted:
                continue
        allowed.append(user_id)
    return allowed[:MAX_RECIPIENTS]


# --- writing --------------------------------------------------------------------------------------

def emit(conn: Any, user_ids: Iterable[str], *, type: str, title: str, body: str = "", severity: str = "INFO",
         link: str | None = None, project_id: str | None = None, request_id: str | None = None,
         dedupe_key: str | None = None, coalesce: bool = False) -> int:
    """Write one notification per user in the caller's transaction; returns the rows written.

    Never raises (a notification must not undo the event it describes)."""
    users = sorted({str(value) for value in user_ids if value})
    if not users:
        return 0
    postgres = getattr(conn, "backend", "") == "postgresql"
    if postgres:
        try:
            conn.execute("SAVEPOINT simdash_notify")
        except Exception:  # noqa: BLE001
            logger.warning("notification savepoint failed; notification skipped", exc_info=True)
            return 0
    try:
        written = _emit(conn, users, type=type, title=title, body=body, severity=severity, link=link,
                        project_id=project_id, request_id=request_id, dedupe_key=dedupe_key, coalesce=coalesce)
        if postgres:
            conn.execute("RELEASE SAVEPOINT simdash_notify")
        return written
    except Exception:  # noqa: BLE001 - logged; the event itself stays recorded
        logger.warning("notification %s could not be written", type, exc_info=True)
        if postgres:
            try:
                conn.execute("ROLLBACK TO SAVEPOINT simdash_notify")
            except Exception:  # noqa: BLE001
                logger.warning("notification savepoint rollback failed", exc_info=True)
        return 0


def _emit(conn: Any, users: list[str], *, type: str, title: str, body: str, severity: str, link: str | None,
          project_id: str | None, request_id: str | None, dedupe_key: str | None, coalesce: bool) -> int:
    if type not in TYPES:
        raise ValueError(f"unknown notification type {type}")
    if severity not in SEVERITIES:
        raise ValueError(f"unknown notification severity {severity}")
    now = _now()
    title, body = str(title)[:200], (str(body)[:1000] or None)
    written = 0
    for user_id in users:
        if dedupe_key:
            existing = conn.execute("SELECT id FROM notifications WHERE user_id=? AND dedupe_key=? AND read_at IS NULL "
                                    "ORDER BY created_at DESC LIMIT 1", [user_id, dedupe_key]).fetchone()
            if existing is not None:
                if coalesce:
                    conn.execute("UPDATE notifications SET title=?, body=?, severity=?, link=?, created_at=? WHERE id=?",
                                 [title, body, severity, link, now, existing[0]])
                    written += 1
                continue
        conn.execute(f"INSERT INTO notifications ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     [f"nt-{uuid.uuid4().hex}", user_id, type, severity, title, body, link, project_id, request_id,
                      dedupe_key[:300] if dedupe_key else None, now, None])
        written += 1
        _prune(conn, user_id, now)
    return written


def _prune(conn: Any, user_id: str, now: datetime) -> None:
    conn.execute("DELETE FROM notifications WHERE user_id=? AND created_at<?", [user_id, now - timedelta(days=RETENTION_DAYS)])
    count = int(conn.execute("SELECT count(*) FROM notifications WHERE user_id=?", [user_id]).fetchone()[0])
    if count <= MAX_PER_USER:
        return
    old = [str(row[0]) for row in conn.execute(
        "SELECT id FROM notifications WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        [user_id, count - MAX_PER_USER, MAX_PER_USER]).fetchall()]
    for start in range(0, len(old), 200):
        chunk = old[start:start + 200]
        conn.execute(f"DELETE FROM notifications WHERE id IN ({','.join('?' for _ in chunk)})", chunk)


# --- reading --------------------------------------------------------------------------------------

def _view(row: Any) -> dict[str, Any]:
    return {"id": str(row[0]), "type": str(row[2]), "type_label": TYPES.get(str(row[2]), str(row[2])),
            "severity": str(row[3]), "title": str(row[4]), "body": row[5], "link": row[6], "project_id": row[7],
            "request_id": row[8], "created_at": _iso(row[10]), "read_at": _iso(row[11]), "read": row[11] is not None}


def unread_count(conn: Any, user_id: str) -> int:
    cutoff = _now() - timedelta(days=RETENTION_DAYS)
    return int(conn.execute("SELECT count(*) FROM notifications WHERE user_id=? AND read_at IS NULL AND created_at>=?",
                            [user_id, cutoff]).fetchone()[0])


def list_for_user(conn: Any, user_id: str, *, unread_only: bool = False, type: str | None = None,
                  limit: int = 50, offset: int = 0) -> dict[str, Any]:
    where, params = "user_id=? AND created_at>=?", [user_id, _now() - timedelta(days=RETENTION_DAYS)]
    if unread_only:
        where += " AND read_at IS NULL"
    if type:
        where += " AND type=?"
        params.append(type)
    total = int(conn.execute(f"SELECT count(*) FROM notifications WHERE {where}", params).fetchone()[0])
    found = conn.execute(f"SELECT {_COLUMNS} FROM notifications WHERE {where} ORDER BY created_at DESC, id DESC "
                         "LIMIT ? OFFSET ?", [*params, int(limit), int(offset)]).fetchall()
    return {"items": [_view(row) for row in found], "total": total, "unread_count": unread_count(conn, user_id),
            "limit": int(limit), "offset": int(offset),
            "types": [{"type": key, "label": label} for key, label in TYPES.items()]}


def mark_read(conn: Any, user_id: str, *, ids: list[str] | None = None, all: bool = False) -> int:
    """Mark the user's own notifications read (``all`` or ``ids``); other users' rows are never touched."""
    now = _now()
    if all:
        count = int(conn.execute("SELECT count(*) FROM notifications WHERE user_id=? AND read_at IS NULL",
                                 [user_id]).fetchone()[0])
        conn.execute("UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL", [now, user_id])
        return count
    wanted = sorted({str(value) for value in ids or [] if value})
    if not wanted:
        return 0
    marks = ",".join("?" for _ in wanted)
    count = int(conn.execute(f"SELECT count(*) FROM notifications WHERE user_id=? AND read_at IS NULL AND id IN ({marks})",
                             [user_id, *wanted]).fetchone()[0])
    conn.execute(f"UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL AND id IN ({marks})",
                 [now, user_id, *wanted])
    return count


# --- event helpers (called inside the transaction that records the event) ----------------------------

def _request_title(conn: Any, request_id: str | None) -> str:
    if not request_id:
        return ""
    row = conn.execute("SELECT title FROM analysis_requests WHERE id=?", [request_id]).fetchone()
    return str(row[0]) if row and row[0] else str(request_id)


def _project_of(conn: Any, request_id: str | None) -> str | None:
    if not request_id:
        return None
    row = conn.execute("SELECT project_id FROM analysis_requests WHERE id=?", [request_id]).fetchone()
    return str(row[0]) if row and row[0] else None


def drive_queue_paused(conn: Any) -> int:
    """AUTH_REQUIRED stopped the drive upload queue (global admins)."""
    return emit(conn, recipients(conn, permission=None, members=False, admins=True), type="DRIVE_QUEUE_PAUSED",
                severity="WARNING", title="드라이브 업로드 대기열이 멈췄습니다",
                body="드라이브 인증이 필요합니다. 공용 계정 토큰을 다시 등록하면 대기열이 이어서 진행됩니다.",
                link=ADMIN_DRIVE_LINK, dedupe_key="drive-queue-paused")


_UPLOAD_TEXT = {
    "DONE": ("SUCCESS", "드라이브 업로드 완료", "파일 {done}/{total}개를 드라이브에 올렸습니다. 다음 자동 반영에서 결과에 나타납니다."),
    "PARTIAL": ("WARNING", "드라이브 업로드 일부 완료", "파일 {done}/{total}개만 올렸습니다. 나머지는 충돌·실패로 멈췄습니다."),
    "CONFLICT": ("WARNING", "드라이브 업로드 충돌", "같은 이름의 다른 파일이 드라이브에 있어 올리지 않았습니다(덮어쓰지 않음)."),
    "FAILED": ("ERROR", "드라이브 업로드 실패", "파일 {done}/{total}개를 올린 뒤 멈췄습니다. 관리자에게 문의하거나 다시 올리세요."),
    "CANCELLED": ("WARNING", "드라이브 업로드 취소", "관리자가 업로드를 취소했습니다(드라이브 내용은 그대로입니다)."),
}


def drive_upload_finished(conn: Any, summary: dict[str, Any]) -> int:
    """A result drop upload batch finished (the user who started it)."""
    state = str(summary.get("state") or "")
    if state not in _UPLOAD_TEXT:
        return 0
    total, done = int(summary.get("files_total") or 0), int(summary.get("files_done") or 0)
    if state == "DONE" and total == 0:
        return 0      # a new-folder batch: the screen already waited for it
    project_id, request_id = summary.get("project_id"), summary.get("request_id")
    severity, title, body = _UPLOAD_TEXT[state]
    name = _request_title(conn, request_id)
    users = recipients(conn, project_id=project_id, members=False, include=[summary.get("requested_by")])
    return emit(conn, users, type="DRIVE_UPLOAD", severity=severity, title=title + (f" · {name}" if name else ""),
                body=body.format(done=done, total=total), link=request_link(project_id, request_id),
                project_id=project_id, request_id=request_id,
                dedupe_key=f"drive-upload:{summary.get('batch_id')}:{state}")


def final_finished(conn: Any, operation: dict[str, Any], *, completed: bool, error_code: str | None = None) -> int:
    """A drive Final completed or failed (the designating user + request members)."""
    project_id, request_id = operation.get("project_id"), operation.get("request_id")
    actor = operation.get("confirmed_by") or operation.get("created_by")
    name = _request_title(conn, request_id)
    plan = operation.get("plan_json") if isinstance(operation.get("plan_json"), dict) else {}
    case = str(plan.get("case_label") or plan.get("case_relative_path") or operation.get("case_id") or "")
    users = recipients(conn, project_id=project_id, include=[actor])
    if completed:
        return emit(conn, users, type="FINAL", severity="SUCCESS", title=f"Final 지정 완료 · {name}",
                    body=(f"{case} 결과를 Final로 지정했습니다." if case else "Final 지정이 드라이브에 반영되었습니다."),
                    link=request_link(project_id, request_id), project_id=project_id, request_id=request_id,
                    dedupe_key=f"final:{operation.get('operation_id')}:COMPLETE")
    return emit(conn, users, type="FINAL", severity="ERROR", title=f"Final 지정 실패 · {name}",
                body=f"Final 드라이브 반영이 멈췄습니다({error_code or '오류'}). 다시 시도하거나 관리자에게 문의하세요.",
                link=request_link(project_id, request_id), project_id=project_id, request_id=request_id,
                dedupe_key=f"final:{operation.get('operation_id')}:FAILED")


def final_summary_repair_needed(conn: Any, operation: dict[str, Any], state: str) -> int:
    """The current-Final summary file upload stopped: someone with result import must run "요약 파일 갱신"."""
    project_id, request_id = operation.get("project_id"), operation.get("request_id")
    actor = operation.get("confirmed_by") or operation.get("created_by")
    users = recipients(conn, project_id=project_id, permission=RESULT_IMPORT, include=[actor])
    return emit(conn, users, type="FINAL_SUMMARY", severity="WARNING",
                title=f"Final 요약 파일 갱신 필요 · {_request_title(conn, request_id)}",
                body=f"현재 Final 요약 파일을 드라이브에 올리지 못했습니다({state}). 결과 화면에서 '요약 파일 갱신'을 실행하세요.",
                link=request_link(project_id, request_id), project_id=project_id, request_id=request_id,
                dedupe_key=f"final-summary:{request_id}", coalesce=True)


def drive_sources_changed(conn: Any, project_id: str, request_id: str, *, changed: int, missing: int) -> int:
    """A sync recorded drive changes waiting for confirmation and/or MISSING sources (users with result import)."""
    written = 0
    users = recipients(conn, project_id=project_id, permission=RESULT_IMPORT) if (changed or missing) else []
    name = _request_title(conn, request_id)
    if changed:
        written += emit(conn, users, type="DRIVE_SOURCE_CHANGED", severity="WARNING",
                        title=f"원본 변경 확인 필요 · {name}",
                        body=f"드라이브에서 바뀐 결과 파일 {changed}개가 확인을 기다립니다. 확인 전까지 등록된 버전을 표시합니다.",
                        link=request_link(project_id, request_id), project_id=project_id, request_id=request_id,
                        dedupe_key=f"drive-source-changed:{request_id}", coalesce=True)
    if missing:
        written += emit(conn, users, type="DRIVE_SOURCE_MISSING", severity="WARNING",
                        title=f"원본 파일 없음 · {name}",
                        body=f"드라이브에서 결과 파일 {missing}개를 찾을 수 없습니다. 등록된 버전은 그대로 보입니다.",
                        link=request_link(project_id, request_id), project_id=project_id, request_id=request_id,
                        dedupe_key=f"drive-source-missing:{request_id}", coalesce=True)
    return written


def new_results(conn: Any, project_id: str, request_id: str, environment: str, *, cases: int, scenes: int,
                snapshot_id: str) -> int:
    """The auto-sync reflected new Cases/Scenes of a request (one notification per request per sync)."""
    if not cases and not scenes:
        return 0
    parts = [f"Case {cases}개" if cases else "", f"Scene {scenes}개" if scenes else ""]
    environment_label = {"USAGE": "사용환경", "DISTRIBUTION": "유통환경"}.get(str(environment).upper(), str(environment))
    return emit(conn, recipients(conn, project_id=project_id), type="NEW_RESULTS", severity="INFO",
                title=f"새 결과 반영 · {_request_title(conn, request_id)}",
                body=f"{environment_label}: 새 {', '.join(part for part in parts if part)}가 결과 화면에 반영되었습니다.",
                link=request_link(project_id, request_id), project_id=project_id, request_id=request_id,
                dedupe_key=f"new-results:{snapshot_id}")


def capture_failed(conn: Any, registration_id: str, actor: str | None = None) -> int:
    """Capture jobs of a folder registration failed (the registering user + users with result import)."""
    row = conn.execute("SELECT project_id, request_id, created_by FROM folder_environment_registrations WHERE id=?",
                       [registration_id]).fetchone()
    if not row:
        return 0
    project_id, request_id = (str(row[0]) if row[0] else None), (str(row[1]) if row[1] else None)
    failed = int(conn.execute("SELECT count(*) FROM folder_environment_capture_jobs WHERE registration_id=? "
                              "AND status='FAILED'", [registration_id]).fetchone()[0])
    if not failed:
        return 0
    users = recipients(conn, project_id=project_id, permission=RESULT_IMPORT, include=[actor, row[2]])
    return emit(conn, users, type="CAPTURE_FAILED", severity="ERROR",
                title=f"결과 캡처 실패 · {_request_title(conn, request_id)}",
                body=f"등록한 폴더의 Case {failed}개 결과를 읽지 못했습니다. 결과 등록 화면에서 다시 캡처하세요.",
                link=request_link(project_id, request_id), project_id=project_id, request_id=request_id,
                dedupe_key=f"capture-failed:{registration_id}", coalesce=True)
