"""결과 등록 "폴더 구조 만들기": the empty request skeleton per environment, down to the Case level.

In the SCX environment SPDM creates only the project and request folders. The dashboard reads a
request folder only once it has ``Working`` (depth schema §5, ``WORKING_MISSING``), so this
creates, for one environment of the selected request, the folders

    <project>/<request folder>            (only when none exists and the user confirmed the exact name)
    <project>/<request folder>/Working
    <project>/<request folder>/Working/<Case>   (one per Case name; deeper levels are left to analysts)

Contract: docs/features/result-registration.md §2a.

Rules
  * Request folder discovery (``overview``): the folder linked to the request for that environment,
    else request-level folders below the request's project folder(s) whose WR key matches and whose
    name carries that environment's keyword (D4). Other folders are listed so the user can choose
    explicitly (a folder without any keyword is allowed with a confirmation; nothing is renamed).
    When no folder exists, a sibling name is proposed (``[WR-0001]_[유통_환경]`` →
    ``[WR-0001]_[사용_환경]``) and created only when the client sends exactly that proposal.
  * Writes stay inside the SPDM root: the request folder and ``Working`` in the provider zone
    ``SKELETON`` and the Case folders in ``WORKING`` (``mkdir_pinned`` only, parents pinned).
    ``Final`` is never touched. Existing folders are reported (``existing``), never renamed,
    deleted or overwritten; a re-run is idempotent.
  * Case names: the W8 "새 폴더 만들기" name rules and the depth checks of the Case level.
  * Ownership: a folder linked to another request/environment is refused
    (``result_registration_paths._owner_conflict``).
  * SCX drive mode: the folders are ``MKDIR`` items of one drive upload batch (``mkdirs`` is
    idempotent); requires drive writes (router gate).
  * A request folder not yet linked to this request is reserved for it in the folder auto-discovery
    (``folder_auto_discovery.reserve_link``), so the discovery registers it as a LINK to the selected
    request instead of a new request: right after the write (local) or after the batch (drive).
"""
from __future__ import annotations

import logging
import re
import threading
import unicodedata
from pathlib import PurePosixPath
from typing import Any

from fastapi import HTTPException

from ..database_connection import ConnectionLike
from . import environment_folder_profiles as depth_profiles
from . import folder_auto_discovery, folder_auto_sync
from . import result_drop_upload as drop
from . import result_registration_paths as paths
from .drive import upload_queue
from .storage import provider_for_root
from .storage.provider import SKELETON, WORKING, StorageError, skeleton_zone_allows, working_zone_allows

ORIGIN = "result_structure"
MAX_CASES = 100
CANDIDATE_LIMIT = 200
ACTIVE_STATUSES = folder_auto_discovery.ACTIVE_STATUSES
KEYWORDS = depth_profiles.ENVIRONMENT_KEYWORDS
_OTHER = {"USAGE": "DISTRIBUTION", "DISTRIBUTION": "USAGE"}
_WR_TOKEN = re.compile(r"\[?WR[-_]?[0-9A-Za-z]+\]?", re.I)
_WR_KEY = re.compile(r"\[?WR[-_]?(\w+)", re.I)
_NOTE_PREFIX = "폴더 의뢰번호:"
_logger = logging.getLogger(__name__)


def _error(code: str, message: str, status: int = 422, **extra: Any) -> drop.DropUploadError:
    return drop.DropUploadError(code, message, status, **extra)


def _wr_key(value: str | None) -> str | None:
    """Casefolded WR key of a request folder name or title (``[WR-0001]_[유통_환경]`` → ``0001``)."""
    match = _WR_KEY.match(str(value or "").strip())
    return match.group(1).casefold() if match else None


def _keyword(name: str) -> tuple[str | None, str | None]:
    return depth_profiles.keyword_environment(name)


# ---------------------------------------------------------------------------
# Context: links, project folders and request-level candidates
# ---------------------------------------------------------------------------

class _Context:
    def __init__(self, conn: ConnectionLike, project_id: str, request_id: str) -> None:
        self.conn, self.project_id, self.request_id = conn, project_id, request_id
        row = conn.execute("SELECT r.title, r.overall_note FROM analysis_requests r WHERE r.id=? AND r.project_id=?",
                           [request_id, project_id]).fetchone()
        if not row:
            raise _error("RESULT_CONTEXT_INVALID", "기존 프로젝트와 의뢰의 연결을 확인할 수 없습니다.", 404)
        self.title, self.note = str(row[0] or ""), str(row[1] or "")
        self.root, self.root_id, self.root_key = paths.storage_context(conn)
        self.fs = provider_for_root(self.root)
        try:
            self.schema = depth_profiles.get_depth_schema(conn)
        except HTTPException as exc:
            raise _error("DEPTH_SCHEMA_MISSING", "현재 깊이 스키마가 없습니다. 관리자에게 문의하세요.", 409) from exc
        roles = [item["role"] for item in self.schema["upper"]["levels"]]
        self.project_level, self.request_level = roles.index("PROJECT") + 1, len(roles)
        self.linked = self._linked_requests()
        self.project_folders = self._project_folders()
        self.lister = folder_auto_discovery._Lister(self.root)
        self.candidates = self._candidates()
        self.key = self._request_key()

    # -- links ------------------------------------------------------------------------------
    def _linked_requests(self) -> dict[str, str]:
        """environment -> request folder linked to this request (registry, then SPDM request parent)."""
        found: dict[str, str] = {}
        marks = ",".join("?" for _ in ACTIVE_STATUSES)
        for path, environment in self.conn.execute(
                "SELECT g.relative_path, r.environment FROM folder_environment_registry g "
                "JOIN folder_environment_registrations r ON r.id=g.registration_id "
                f"WHERE g.root_key=? AND g.role_kind='REQUEST' AND r.project_id=? AND r.request_id=? AND r.status IN ({marks}) "
                "ORDER BY r.created_at", [self.root_key, self.project_id, self.request_id, *ACTIVE_STATUSES]).fetchall():
            found.setdefault(str(environment), str(path))
        for (path,) in self.conn.execute("SELECT request_folder FROM spdm_storage_request_parents WHERE project_id=? AND request_id=?",
                                         [self.project_id, self.request_id]).fetchall():
            environment, _code = _keyword(PurePosixPath(str(path)).name)
            if environment:
                found.setdefault(environment, str(path))
        return found

    def _project_folders(self) -> list[str]:
        found: list[str] = []
        marks = ",".join("?" for _ in ACTIVE_STATUSES)
        rows = self.conn.execute(
            "SELECT g.relative_path FROM folder_environment_registry g JOIN folder_environment_registrations r ON r.id=g.registration_id "
            f"WHERE g.root_key=? AND g.role_kind='PROJECT' AND g.target_id=? AND r.status IN ({marks})",
            [self.root_key, self.project_id, *ACTIVE_STATUSES]).fetchall()
        rows += self.conn.execute("SELECT project_folder FROM spdm_storage_project_parents WHERE project_id=?",
                                  [self.project_id]).fetchall()
        try:
            rows += self.conn.execute("SELECT relative_path FROM folder_discovery_registry WHERE root_key=? "
                                      "AND role_kind='PROJECT' AND target_id=?", [self.root_key, self.project_id]).fetchall()
        except Exception:  # noqa: BLE001 - legacy table missing on a minimal schema
            pass
        for path in self.linked.values():
            parts = PurePosixPath(path).parts
            if len(parts) >= self.request_level:
                rows.append(("/".join(parts[:self.project_level]),))
        for (path,) in rows:
            value = str(path)
            if value and len(PurePosixPath(value).parts) == self.project_level \
                    and value.casefold() not in {item.casefold() for item in found}:
                found.append(value)
        return sorted(found, key=str.casefold)

    def _candidates(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for project in self.project_folders:
            for name, path in folder_auto_discovery._request_candidates(self.lister, self.schema, project):
                if path.casefold() in seen or len(out) >= CANDIDATE_LIMIT:
                    continue
                seen.add(path.casefold())
                environment, code = _keyword(name)
                linked_env = next((env for env, linked in self.linked.items() if linked.casefold() == path.casefold()), None)
                owner = "THIS" if linked_env else (
                    "OTHER" if paths._has_owner_conflict(self.conn, self.root_id, self.root_key, path, self.project_id,
                                                         self.request_id, environment or "USAGE") else None)
                out.append({"relative_path": path, "name": name, "parent_relative_path": str(PurePosixPath(path).parent),
                            "environment": environment, "keyword_code": code, "owner": owner,
                            "linked_environment": linked_env, "wr_key": _wr_key(name)})
        return out

    def _request_key(self) -> str | None:
        for path in self.linked.values():
            key = _wr_key(PurePosixPath(path).name)
            if key:
                return key
        note = self.note[len(_NOTE_PREFIX):].strip() if self.note.startswith(_NOTE_PREFIX) else ""
        return _wr_key(self.title) or _wr_key(note)

    # -- per environment ----------------------------------------------------------------------
    def display(self, relative: str) -> str:
        return drop.display_path(drop.display_root(self.root), relative)

    def candidate(self, relative: str) -> dict[str, Any] | None:
        return next((item for item in self.candidates if item["relative_path"].casefold() == relative.casefold()), None)

    def matches(self, environment: str) -> list[dict[str, Any]]:
        return [item for item in self.candidates if item["environment"] == environment and item["owner"] != "OTHER"
                and self.key and item["wr_key"] == self.key and not item["linked_environment"]]

    def proposal(self, environment: str) -> dict[str, Any] | None:
        """Name and parent of a new request folder for ``environment`` (sibling naming), or ``None``."""
        keyword, other = KEYWORDS[environment], KEYWORDS[_OTHER[environment]]
        sibling = next((item for item in self.candidates if self.key and item["wr_key"] == self.key
                        and item["environment"] == _OTHER[environment]), None)
        name, parent = None, None
        if sibling:
            name, parent = sibling["name"].replace(other, keyword), sibling["parent_relative_path"]
        elif self.request_level - self.project_level == 1 and len(self.project_folders) == 1:
            parent = self.project_folders[0]
            if other in self.title and keyword not in self.title and _wr_key(self.title):
                name = self.title.strip().replace(other, keyword)
            else:
                token = _WR_TOKEN.match(self.title.strip())
                if token and self.key:
                    value = token.group(0)
                    if value.startswith("[") and not value.endswith("]"):
                        value += "]"
                    name = f"{value}_[{keyword}_환경]"
        if not name or not parent:
            return None
        name = unicodedata.normalize("NFC", name)
        relative = f"{parent}/{name}"
        if (drop.name_problem(name) or _keyword(name)[0] != environment or not skeleton_zone_allows(relative)
                or any(item["name"].casefold() == name.casefold() for item in self.candidates)):
            return None
        return {"parent_relative_path": parent, "name": name, "relative_path": relative, "display_path": self.display(relative)}


# ---------------------------------------------------------------------------
# Reading a request folder: Working, existing Case folders, suggestions
# ---------------------------------------------------------------------------

def _child_dirs(fs, relative: str) -> list[str]:
    try:
        return drop._children(fs, relative)
    except (FileNotFoundError, NotADirectoryError):
        return []
    except (OSError, StorageError) as exc:
        if getattr(exc, "code", None) in {"NOT_FOUND", "SPDM_FOLDER_MISSING"}:
            return []
        raise _error("SPDM_FOLDER_UNAVAILABLE", "의뢰 폴더에 접근할 수 없습니다.", 409) from exc


def _working_name(fs, request_path: str) -> str | None:
    return next((name for name in _child_dirs(fs, request_path) if name.casefold() == "working"), None)


def _registered_cases(conn: ConnectionLike, ctx: _Context, environment: str) -> list[str]:
    names = [str(row[0]) for row in conn.execute(
        "SELECT source_name FROM dashboard_cases WHERE project_id=? AND request_id=? AND environment=? ORDER BY source_name",
        [ctx.project_id, ctx.request_id, environment]).fetchall()]
    marks = ",".join("?" for _ in ACTIVE_STATUSES)
    names += [PurePosixPath(str(row[0])).name for row in conn.execute(
        "SELECT g.relative_path FROM folder_environment_registry g JOIN folder_environment_registrations r ON r.id=g.registration_id "
        f"WHERE g.root_key=? AND g.role_kind='SIMULATION_CASE' AND r.project_id=? AND r.request_id=? AND r.environment=? "
        f"AND r.status IN ({marks})", [ctx.root_key, ctx.project_id, ctx.request_id, environment, *ACTIVE_STATUSES]).fetchall()]
    unique: dict[str, str] = {}
    for name in names:
        if name and not drop._ignored(name):
            unique.setdefault(name.casefold(), name)
    return sorted(unique.values(), key=str.casefold)


def _folder_view(ctx: _Context, environment: str, relative: str) -> dict[str, Any]:
    item = ctx.candidate(relative) or {}
    working = _working_name(ctx.fs, relative)
    cases = _child_dirs(ctx.fs, f"{relative}/{working}") if working else []
    return {"relative_path": relative, "name": PurePosixPath(relative).name, "display_path": ctx.display(relative),
            "environment": _keyword(PurePosixPath(relative).name)[0], "keyword_code": _keyword(PurePosixPath(relative).name)[1],
            "linked": ctx.linked.get(environment, "").casefold() == relative.casefold(),
            "owner": item.get("owner") or ("THIS" if ctx.linked.get(environment, "").casefold() == relative.casefold() else None),
            "working_exists": bool(working), "working_name": working or "Working", "existing_cases": cases}


def _suggestions(conn: ConnectionLike, ctx: _Context, environment: str, existing: list[str]) -> list[dict[str, Any]]:
    folded = {name.casefold() for name in existing}
    out = [{"name": name, "source": "REGISTERED", "exists": name.casefold() in folded}
           for name in _registered_cases(conn, ctx, environment)]
    known = {item["name"].casefold() for item in out}
    out += [{"name": name, "source": "FOLDER", "exists": True} for name in existing if name.casefold() not in known]
    return out


def overview(conn: ConnectionLike, project_id: str, request_id: str, *, environment: str | None = None,
             request_relative_path: str | None = None) -> dict[str, Any]:
    """What "폴더 구조 만들기" would work on, per environment (reads only)."""
    ctx = _Context(conn, project_id, request_id)
    environments = []
    for env in ("USAGE", "DISTRIBUTION"):
        entry: dict[str, Any] = {"environment": env, "keyword": KEYWORDS[env], "folder": None, "choices": [], "proposal": None,
                                 "case_suggestions": []}
        chosen = request_relative_path if environment == env and request_relative_path else None
        if ctx.linked.get(env) and not chosen:
            entry["status"], folder = "LINKED", ctx.linked[env]
        elif chosen:
            item = ctx.candidate(paths._relative(chosen))
            if item is None and (ctx.linked.get(env) or "").casefold() != chosen.casefold():
                raise _error("RESULT_STRUCTURE_TARGET_INVALID", "선택한 폴더가 이 의뢰의 프로젝트 폴더 아래 의뢰 폴더가 아닙니다.", 422)
            entry["status"], folder = "SELECTED", item["relative_path"] if item else ctx.linked[env]
        else:
            matches = ctx.matches(env)
            entry["choices"] = [item["relative_path"] for item in matches]
            if len(matches) == 1:
                entry["status"], folder = "FOUND", matches[0]["relative_path"]
            else:
                entry["status"], folder = ("AMBIGUOUS" if matches else "MISSING"), None
                if not matches:
                    entry["proposal"] = ctx.proposal(env)
        if folder:
            entry["folder"] = _folder_view(ctx, env, folder)
            entry["case_suggestions"] = _suggestions(conn, ctx, env, entry["folder"]["existing_cases"])
        else:
            entry["case_suggestions"] = _suggestions(conn, ctx, env, [])
        environments.append(entry)
    return {
        "project_id": project_id, "request_id": request_id, "wr_key": ctx.key,
        "project_folders": [{"relative_path": path, "display_path": ctx.display(path)} for path in ctx.project_folders],
        "candidates": [{key: item[key] for key in ("relative_path", "name", "environment", "keyword_code", "owner", "linked_environment")}
                       | {"display_path": ctx.display(item["relative_path"]), "wr_match": bool(ctx.key and item["wr_key"] == ctx.key)}
                       for item in ctx.candidates],
        "environments": environments, "max_cases": MAX_CASES, "drive": drop._drive(),
    }


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def _case_names(raw: list[str]) -> list[str]:
    names = [unicodedata.normalize("NFC", str(value or "").strip()) for value in raw]
    names = [name for name in names if name]
    if len(names) > MAX_CASES:
        raise _error("RESULT_STRUCTURE_TOO_MANY", f"Case 폴더는 한 번에 {MAX_CASES}개까지 만들 수 있습니다.", 422)
    return names


def _check_names(ctx: _Context, environment: str, request_path: str, working: str, names: list[str],
                 existing: list[str]) -> tuple[list[dict[str, str]], list[str]]:
    """(problems, warnings) of the Case names (W8 new folder rules + depth checks of the Case level)."""
    roles = drop._lower_roles(ctx.conn, environment)
    scope = drop.Scope(root=ctx.root, root_id=ctx.root_id, root_key=ctx.root_key, project_id=ctx.project_id,
                       request_id=ctx.request_id, environment=environment, request_relative_path=request_path,
                       working_relative_path=f"{request_path}/{working}", roles=roles)
    known = drop._known_names(scope) if existing else {role: set(values) for role, values in drop._DEFAULT_KNOWN.items()}
    folded_existing = {name.casefold(): name for name in existing}
    problems: list[dict[str, str]] = []
    warnings: list[str] = []
    seen: set[str] = set()
    base = drop.display_root(ctx.root)
    for name in names:
        def bad(message: str) -> None:
            problems.append({"name": name, "message": message})
        if drop.name_problem(name) or drop._ignored(name):
            bad("쓸 수 없는 문자나 이름입니다(\\ / : * ? \" < > |, 끝 마침표·공백, CON 등, . $ ~ 시작).")
            continue
        if name.casefold() in seen:
            bad("목록에 같은 이름(대소문자 무시)이 두 번 있습니다.")
            continue
        seen.add(name.casefold())
        if name.casefold() in folded_existing:
            continue  # already there: reported as "이미 있음"
        relative = f"{request_path}/{working}/{name}"
        if not working_zone_allows(relative):
            bad("이 위치에는 폴더를 만들 수 없습니다(Working 폴더 밖).")
            continue
        if len(str(ctx.fs.path(relative))) > drop.MAX_PATH_CHARS or len(drop.display_path(base, relative)) > drop.MAX_PATH_CHARS:
            bad("경로가 Windows 길이 한도를 넘습니다. 이름을 줄이세요.")
            continue
        findings = drop._depth_findings(scope, known, name, working, 2)
        errors = [message for _code, severity, message in findings if severity == "error"]
        if errors:
            bad(errors[0])
            continue
        similar = drop._similar_names(name, existing)
        duplicate = next((sibling for sibling, kind in similar if kind == "SCENE_NAME_CASE"), None)
        if duplicate:
            bad(f"'{duplicate}'와 대소문자·구분 기호만 다른 이름입니다. 기존 폴더를 쓰세요.")
            continue
        warnings += [message for _code, severity, message in findings if severity == "warning"]
        warnings += [f"'{name}'은(는) 같은 위치의 '{sibling}'와 거의 같은 이름입니다. 결과가 다른 Case로 나뉩니다."
                     for sibling, _kind in similar]
    return problems, warnings


def create(conn: ConnectionLike, project_id: str, request_id: str, environment: str, *,
           request_relative_path: str | None, new_request_folder: dict[str, str] | None, case_names: list[str],
           confirm: bool, user_id: str) -> dict[str, Any]:
    """Create (local) or queue (drive) the skeleton of one environment; never renames or overwrites."""
    environment = paths._env(environment)
    ctx = _Context(conn, project_id, request_id)
    names = _case_names(case_names)
    warnings: list[str] = []
    create_request = False
    if new_request_folder:
        if ctx.linked.get(environment) or ctx.matches(environment):
            raise _error("RESULT_STRUCTURE_PROPOSAL_CHANGED", "이 환경의 의뢰 폴더가 이미 있습니다. 다시 확인하세요.", 409)
        proposal = ctx.proposal(environment)
        sent = (str(new_request_folder.get("parent_relative_path") or ""), unicodedata.normalize("NFC", str(new_request_folder.get("name") or "")))
        if proposal is None or (proposal["parent_relative_path"], proposal["name"]) != sent:
            raise _error("RESULT_STRUCTURE_PROPOSAL_CHANGED", "만들 의뢰 폴더 이름이 바뀌었습니다. 다시 확인하세요.", 409,
                         proposal=proposal)
        request_path, create_request = proposal["relative_path"], True
        paths._safe_existing(ctx.root, proposal["parent_relative_path"])
    elif request_relative_path:
        request_path = paths._relative(request_relative_path)
        linked = ctx.linked.get(environment)
        item = ctx.candidate(request_path)
        if linked and linked.casefold() == request_path.casefold():
            request_path = linked
        elif item is None:
            raise _error("RESULT_STRUCTURE_TARGET_INVALID", "선택한 폴더가 이 의뢰의 프로젝트 폴더 아래 의뢰 폴더가 아닙니다.", 422)
        else:
            request_path = item["relative_path"]
            if item["keyword_code"] == "ENV_KEYWORD_BOTH" or (item["environment"] and item["environment"] != environment):
                raise _error("RESULT_STRUCTURE_TARGET_INVALID",
                             f"'{item['name']}'은(는) 다른 환경의 의뢰 폴더입니다. 이름에 '{KEYWORDS[environment]}'가 있는 폴더를 고르세요.", 422)
            if item["linked_environment"] or item["owner"] == "OTHER" or (linked and linked.casefold() != request_path.casefold()):
                raise _error("RESULT_PATH_OWNERSHIP_CONFLICT", "선택한 폴더는 다른 의뢰나 환경에 연결되어 있습니다.", 409)
            if item["environment"] is None:
                warnings.append(f"'{item['name']}'에는 환경 키워드('{KEYWORDS[environment]}')가 없어 대시보드가 자동으로 읽지 못합니다. "
                                "SPDM에서 이름을 바꾸거나 관리자에게 연결을 요청하세요.")
        paths._safe_existing(ctx.root, request_path)
    else:
        raise _error("RESULT_STRUCTURE_TARGET_INVALID", "의뢰 폴더를 고르세요.", 422)
    paths._owner_conflict(conn, ctx.root_id, ctx.root_key, request_path, project_id, request_id, environment)
    working = None if create_request else _working_name(ctx.fs, request_path)
    working = working or "Working"
    existing = [] if create_request else _child_dirs(ctx.fs, f"{request_path}/{working}")
    problems, name_warnings = _check_names(ctx, environment, request_path, working, names, existing)
    if problems:
        raise _error("RESULT_STRUCTURE_NAME_INVALID", f"쓸 수 없는 Case 이름이 {len(problems)}개 있습니다.", 422, problems=problems)
    warnings += name_warnings
    if warnings and not confirm:
        raise _error("RESULT_STRUCTURE_NAME_WARNING", warnings[0], 409, warnings=warnings)
    working_path = f"{request_path}/{working}"
    if not skeleton_zone_allows(working_path) or (create_request and not skeleton_zone_allows(request_path)):
        raise _error("RESULT_STRUCTURE_TARGET_INVALID", "이 위치에는 폴더를 만들 수 없습니다.", 422)
    folded_existing = {name.casefold(): name for name in existing}
    plan: list[tuple[str, str, str]] = []  # (relative_path, role, zone)
    report_existing: list[dict[str, str]] = []
    if create_request:
        plan.append((request_path, "REQUEST", SKELETON))
    if create_request or not _working_name(ctx.fs, request_path):
        plan.append((working_path, "WORKING", SKELETON))
    else:
        report_existing.append({"relative_path": working_path, "role": "WORKING", "name": working})
    for name in names:
        if name.casefold() in folded_existing:
            report_existing.append({"relative_path": f"{working_path}/{folded_existing[name.casefold()]}",
                                    "role": "SIMULATION_CASE", "name": folded_existing[name.casefold()]})
        else:
            plan.append((f"{working_path}/{name}", "SIMULATION_CASE", WORKING))
    # The folder auto-discovery must register this folder as the selected request (not a new one).
    link_needed = (not ctx.linked.get(environment) and _keyword(PurePosixPath(request_path).name)[0] == environment)
    if link_needed and plan:
        folder_auto_discovery.reserve_link(ctx.root_key, request_path, project_id, request_id, environment)
    result: dict[str, Any] = {
        "project_id": project_id, "request_id": request_id, "environment": environment,
        "request_relative_path": request_path, "request_display_path": ctx.display(request_path),
        "working_relative_path": working_path, "created": [], "existing": report_existing, "warnings": warnings,
        "drive": None, "link": {"status": "LINKED" if ctx.linked.get(environment) else ("PENDING" if link_needed else "NONE")},
        "sync": None,
    }
    if not plan:
        return result
    if drop._drive():
        batch = upload_queue.enqueue(conn, origin=ORIGIN, origin_ref=None, requested_by=str(user_id), project_id=project_id,
                                     request_id=request_id, environment=environment,
                                     items=[{"kind": "MKDIR", "dst_rel_dir": relative, "halt_on_error": role != "SIMULATION_CASE"}
                                            for relative, role, _zone in plan])
        result["queued"] = [{"relative_path": relative, "role": role, "name": PurePosixPath(relative).name}
                            for relative, role, _zone in plan]
        result["drive"] = upload_queue.batch_summary(conn, batch)
        return result
    for relative, role, zone in plan:
        try:
            made = ctx.fs.mkdir_pinned(relative, zone=zone)
        except (OSError, StorageError) as exc:
            result["failed"] = {"relative_path": relative, "role": role}
            raise _error("RESULT_STRUCTURE_FAILED", f"폴더를 만들지 못했습니다: {PurePosixPath(relative).name}", 409,
                         created=result["created"], failed=relative) from exc
        (result["created"] if made else result["existing"]).append(
            {"relative_path": relative, "role": role, "name": PurePosixPath(relative).name})
    if ctx.linked.get(environment):
        folder_auto_sync.invalidate(ctx.root_key, project_id, request_id, environment)
        try:
            sync = folder_auto_sync.sync(conn, project_id, request_id, environment, str(user_id), force=True)
        except Exception:  # noqa: BLE001 - the 60 s auto-sync retries
            sync = {"status": "FAILED"}
        result["sync"] = {key: sync.get(key) for key in ("status", "changed", "code", "message")}
    return result


def link_now(result: dict[str, Any], *, connection_factory=None) -> dict[str, Any]:
    """Local mode, after the caller's connection closed: let the discovery register the reserved folder."""
    if result.get("link", {}).get("status") != "PENDING" or result.get("drive") is not None:
        return result
    try:
        found = folder_auto_discovery.discover(force=True, connection_factory=connection_factory)
    except Exception:  # noqa: BLE001 - the screens' periodic discovery retries
        _logger.warning("folder discovery after the structure creation failed", exc_info=True)
        return result
    path = str(result["request_relative_path"]).casefold()
    review = next((item for item in found.get("needs_review") or [] if str(item.get("relative_path") or "").casefold() == path), None)
    from ..database_connection import connect

    with (connection_factory or connect)() as conn:
        linked = conn.execute(
            "SELECT 1 FROM folder_environment_registry g JOIN folder_environment_registrations r ON r.id=g.registration_id "
            "WHERE g.role_kind='REQUEST' AND lower(g.relative_path)=? AND r.project_id=? AND r.request_id=? AND r.environment=?",
            [path, result["project_id"], result["request_id"], result["environment"]]).fetchone()
    if linked:
        result["link"] = {"status": "LINKED"}
    elif review:
        result["link"] = {"status": "REVIEW", "code": review.get("code"), "message": review.get("reason")}
    return result


def on_drive_batch_finished(origin: str, batch_id: str, summary: dict[str, Any]) -> None:
    """Queue hook: the skeleton is on the drive; re-read the request and let the discovery link it."""
    from ..database_connection import connect

    with connect() as conn:
        row = conn.execute("SELECT root_key, project_id, request_id, environment FROM drive_upload_queue "
                           "WHERE batch_id=? ORDER BY seq LIMIT 1", [batch_id]).fetchone()
    if row and row[1] and row[2] and row[3]:
        folder_auto_sync.invalidate(str(row[0]), str(row[1]), str(row[2]), str(row[3]))
    _kick_discovery()


def _kick_discovery() -> None:
    """Run one forced folder auto-discovery in the background (never on the queue worker thread)."""
    def run() -> None:
        try:
            folder_auto_discovery.discover(force=True)
        except Exception:  # noqa: BLE001 - the screens' periodic discovery retries
            _logger.warning("folder discovery after a drive skeleton batch failed", exc_info=True)
    threading.Thread(target=run, name="result-structure-discovery", daemon=True).start()
