"""Authorized dashboard reads and explicit atomic capture publication."""
from __future__ import annotations
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from ..database_connection import connect
from ..modules.access_control import (PROJECT_DATA_VIEW, PROJECT_LAYOUT_EDIT, RESULT_IMPORT, SYSTEM_CATALOG_MANAGE,
                                      require_permission, require_resource_permission)
from ..security import write_audit_event
from ..services.drive import reads as drive_reads
from ..services import dashboard_capture as storage, dashboard_queries as queries, folder_request_progress
from ..services import video_representatives as representatives

router = APIRouter(prefix="/api/dashboard", tags=["result-dashboard"])
project_requests_router = APIRouter(tags=["result-dashboard"])


class ResultEnvironmentCounts(BaseModel):
    USAGE: int
    DISTRIBUTION: int


class ResultEnvironments(BaseModel):
    environments: list[Literal["USAGE", "DISTRIBUTION"]]
    case_counts: ResultEnvironmentCounts
    folder_environments: list[Literal["USAGE", "DISTRIBUTION"]] = []


class FolderProgressStep(BaseModel):
    key: Literal["REGISTERED", "MODELING", "RESULTS", "FINAL", "REPORT"]
    label: str
    status: Literal["DONE", "IN_PROGRESS", "WAITING"]
    detail: str | None


class FolderRequestProgress(BaseModel):
    applicable: bool
    environment: Literal["USAGE", "DISTRIBUTION"] | None
    completed: int
    total: int
    current_key: Literal["REGISTERED", "MODELING", "RESULTS", "FINAL", "REPORT"] | None
    next_action: str | None
    steps: list[FolderProgressStep]
    checked_at: str


class CaptureInput(BaseModel):
    project_id: str
    request_id: str
    root_relative_path: str = Field(min_length=1, max_length=2048)
    environment: Literal["USAGE", "DISTRIBUTION"]
    storage_root_id: str | None = None


class ScanInput(BaseModel):
    project_id: str
    request_id: str
    root_relative_path: str = Field(default="", max_length=2048)
    environment: Literal["USAGE", "DISTRIBUTION"]


class ComparisonMember(BaseModel):
    simulation_case_id: str
    load_case_id: str
    execution_run_id: str
    capture_id: str
    mode: str
    run_option_id: str | None = None
    component_id: str
    basis: Literal["DETAIL", "REPORTED_SUMMARY"]


class ComparisonInput(BaseModel):
    members: list[ComparisonMember] = Field(min_length=1, max_length=8)
    edge_keys: str = "LEFT,RIGHT,TOP,BOTTOM"
    line_indices: str = "1,2,3,4"


def error(exc):
    return HTTPException(422, detail={"code": exc.code, "message": str(exc)})


def capture_for_read(conn, request, capture_id):
    if str(capture_id).startswith(storage.LATEST_PREFIX):
        # Merged newest result per Scene for one Case (see merge_latest_payload).
        case_id = str(capture_id)[len(storage.LATEST_PREFIX):]
        project_id = storage.case_project_id(conn, case_id)
        if project_id is None:
            raise HTTPException(404, "수집 버전을 찾을 수 없습니다.")
        require_permission(request, PROJECT_DATA_VIEW, project_id, conn=conn)
        latest = storage.get_latest_capture(conn, case_id)
        if latest is None:
            raise HTTPException(404, "수집 버전을 찾을 수 없습니다.")
        return latest
    row = conn.execute("""SELECT dc.project_id FROM dashboard_captures c
        JOIN dashboard_cases dc ON dc.id=c.case_id WHERE c.id=?""", [capture_id]).fetchone()
    if not row:
        raise HTTPException(404, "수집 버전을 찾을 수 없습니다.")
    require_permission(request, PROJECT_DATA_VIEW, str(row[0]), conn=conn)
    return storage.get_capture(conn, capture_id)


def lines_from(value):
    try:
        lines = {int(part) for part in value.split(",") if part}
    except ValueError:
        raise HTTPException(422, "라인은 1~4입니다.")
    if not lines or not lines.issubset({1, 2, 3, 4}):
        raise HTTPException(422, "라인은 1~4 중 하나 이상 선택하세요.")
    return lines


@router.get("/catalog")
def catalog(request: Request, request_id: str,
            environment: Literal["USAGE", "DISTRIBUTION"], project_id: str | None = None):
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", request_id, conn=conn)
        return queries.catalog(conn, request_id, environment, project_id)


@project_requests_router.get("/api/projects/{project_id}/requests/{request_id}/result-environments",
                             response_model=ResultEnvironments)
def result_environments(project_id: str, request_id: str, request: Request):
    """Environments of the request's registered Cases (case-results-environment.md §2)."""
    with connect() as conn:
        # Same check as /catalog; an unknown request is 404 there as well.
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", request_id, conn=conn)
        found = queries.result_environments(conn, project_id, request_id)
    if found is None:
        raise HTTPException(404, {"code": "REQUEST_NOT_FOUND", "message": "의뢰를 찾을 수 없습니다."})
    return found


@project_requests_router.get("/api/projects/{project_id}/requests/{request_id}/folder-progress",
                             response_model=FolderRequestProgress)
@drive_reads.read_session()
def folder_progress(project_id: str, request_id: str, request: Request):
    """Progress of a folder-registered request from SPDM folders and the app DB (folder-request-progress.md §3)."""
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", request_id, conn=conn)
        if not folder_request_progress.request_in_project(conn, project_id, request_id):
            raise HTTPException(404, {"code": "REQUEST_NOT_FOUND", "message": "의뢰를 찾을 수 없습니다."})
        return folder_request_progress.folder_progress(conn, project_id, request_id)


@router.post("/scans")
@drive_reads.read_session()
def scan(payload: ScanInput, request: Request):
    with connect() as conn:
        # Unbound discovery follows the existing storage administration policy.
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            storage._verify_context(conn, payload.project_id, payload.request_id)
            return storage.discover_cases(conn, payload.root_relative_path, payload.environment)
        except storage.DashboardCaptureError as exc:
            raise error(exc) from exc


@router.post("/captures")
@drive_reads.read_session()
def publish(payload: CaptureInput, request: Request):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            root_id = storage._root_id(storage._provider(conn).root)
            if payload.storage_root_id and payload.storage_root_id != root_id:
                raise storage.DashboardCaptureError("DASHBOARD_ROOT_ID_INVALID", "저장소가 변경되었습니다. 다시 조사하세요.")
            relative = storage._relative(payload.root_relative_path)
            if getattr(conn, "backend", None) == "postgresql":
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", [root_id + relative])
            bound = conn.execute("SELECT project_id,request_id FROM dashboard_cases WHERE storage_root_id=? AND relative_path=?", [root_id, relative]).fetchone()
            if bound is None:
                require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
            elif (str(bound[0]), str(bound[1])) != (payload.project_id, payload.request_id):
                raise HTTPException(403, "다른 업무에 연결된 폴더입니다.")
            data = {**payload.model_dump(), "storage_root_id": root_id, "root_relative_path": relative,
                    "simulation_case_id": storage._case_id(root_id, relative), "recipe_version": "dashboard-v1"}
            is_duck = getattr(conn, "backend", None) != "postgresql"
            if is_duck:
                conn.execute("BEGIN TRANSACTION")
            try:
                result = storage.create_capture(conn, data, actor=request.state.principal.user_id)
                write_audit_event(request=request, principal=request.state.principal, status_code=200,
                    action="DASHBOARD_CAPTURE_PUBLISHED", detail={"capture_id": result["id"], "case_id": result["case_id"]}, connection=conn)
                if is_duck:
                    conn.execute("COMMIT")
                return result
            except BaseException:
                if is_duck:
                    conn.execute("ROLLBACK")
                raise
        except storage.DashboardCaptureError as exc:
            raise error(exc) from exc


@router.get("/usage/cases/{case_id}")
def usage(case_id: str, capture_id: str, request: Request, reference_case_id: str | None = None, reference_capture_id: str | None = None):
    with connect() as conn:
        capture = capture_for_read(conn, request, capture_id)
        try:
            result = queries.usage(capture, case_id)
            if bool(reference_case_id) != bool(reference_capture_id):
                raise HTTPException(422, "Reference Case와 수집 버전을 함께 선택하세요.")
            if reference_capture_id:
                reference = capture_for_read(conn, request, reference_capture_id)
                return queries.usage_reference(result, queries.usage(reference, reference_case_id))
            return result
        except storage.DashboardCaptureError as exc:
            raise error(exc) from exc


@router.get("/distribution/runs/{run_id}")
def distribution(run_id: str, request: Request, capture_id: str, mode: str, component_id: str,
                 basis: Literal["DETAIL", "REPORTED_SUMMARY"], run_option_id: str | None = None,
                 edge_keys: str = "LEFT,RIGHT,TOP,BOTTOM", line_indices: str = "1,2,3,4"):
    edges = set(filter(None, edge_keys.upper().split(",")))
    if not edges.issubset(queries.EDGES):
        raise HTTPException(422, "엣지 선택이 올바르지 않습니다.")
    with connect() as conn:
        capture = storage.attach_scene_values(conn, capture_for_read(conn, request, capture_id), run_id=run_id)
        try:
            return queries.distribution(capture, run_id, mode, component_id, basis, edges, lines_from(line_indices), run_option_id)
        except storage.DashboardCaptureError as exc:
            raise error(exc) from exc


def _can(request, permission, project_id, conn) -> bool:
    try:
        require_permission(request, permission, project_id, conn=conn)
        return True
    except HTTPException:
        return False


def _video_selection(conn, request, capture) -> tuple[dict, dict]:
    """Rule + the user's choices of the capture's Case, and the viewer's rights on them."""
    case_id = str(capture["case_id"])
    project_id = storage.case_project_id(conn, case_id)
    rule = representatives.load_rule(conn, project_id) if project_id else {"keywords": [], "updated_by": None, "updated_at": None}
    choices = representatives.load_choices(conn, case_id)
    names = representatives.user_names(conn, [item.get("chosen_by") for item in choices.values()] + [rule.get("updated_by")])
    selection = {"keywords": rule["keywords"], "choices": choices, "names": names}
    rights = {"can_choose": bool(project_id) and _can(request, RESULT_IMPORT, project_id, conn),
              "can_edit_rule": bool(project_id) and _can(request, PROJECT_LAYOUT_EDIT, project_id, conn)}
    return selection, {"project_id": project_id, "keywords": rule["keywords"], "updated_by": rule.get("updated_by"),
                       "updated_by_name": names.get(str(rule.get("updated_by"))), "updated_at": rule.get("updated_at"), **rights}


@router.get("/distribution/runs/{run_id}/videos")
def run_videos(run_id: str, request: Request, capture_id: str, run_option_id: str | None = None, mode: str | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(queries.VIDEO_PAGE_SIZE_MAX, ge=1, le=queries.VIDEO_PAGE_SIZE_MAX),
               scene_id: str | None = None, scene_label: str | None = None, representative_only: bool = False):
    """Videos of a Run option (every video, one Scene, or one representative tile per Scene)."""
    with connect() as conn:
        capture = capture_for_read(conn, request, capture_id)
        if capture is None:
            raise HTTPException(404, "수집 버전을 찾을 수 없습니다.")
        selection, rule = _video_selection(conn, request, capture)
        try:
            result = queries.run_videos(capture, run_id, mode=mode, run_option_id=run_option_id, page=page, page_size=page_size,
                                        scene_id=scene_id, scene_label=scene_label, representative_only=representative_only,
                                        selection=selection, probe=lambda assets: representatives.probe_assets(conn, assets))
        except storage.DashboardCaptureError as exc:
            raise error(exc) from exc
    result["video_rule"] = rule
    return result


@router.get("/distribution/runs/{run_id}/video-scenes")
def run_video_scenes(run_id: str, request: Request, capture_id: str, run_option_id: str | None = None, mode: str | None = None):
    """Scenes of a Run option with their video counts and representative (no video content is read)."""
    with connect() as conn:
        capture = capture_for_read(conn, request, capture_id)
        if capture is None:
            raise HTTPException(404, "수집 버전을 찾을 수 없습니다.")
        selection, rule = _video_selection(conn, request, capture)
        try:
            result = queries.video_scenes(capture, run_id, mode=mode, run_option_id=run_option_id, selection=selection)
        except storage.DashboardCaptureError as exc:
            raise error(exc) from exc
    result["video_rule"] = rule
    return result


class VideoRepresentativeInput(BaseModel):
    case_id: str = Field(min_length=1, max_length=200)
    scene_key: str = Field(min_length=1, max_length=2048)
    relative_path: str = Field(min_length=1, max_length=2048)


def _representative_context(conn, request, case_id: str, permission) -> tuple[str, dict]:
    project_id = storage.case_project_id(conn, case_id)
    if project_id is None:
        raise HTTPException(404, "Case를 찾을 수 없습니다.")
    require_permission(request, permission, project_id, conn=conn)
    capture = storage.get_latest_capture(conn, case_id)
    if capture is None or capture.get("environment") != "DISTRIBUTION":
        raise HTTPException(404, "유통환경 수집 결과를 찾을 수 없습니다.")
    return project_id, capture


def _write(conn, work):
    """Runs ``work`` in one transaction (DuckDB needs it explicitly; PostgreSQL's connection scope commits)."""
    is_duck = getattr(conn, "backend", None) != "postgresql"
    if is_duck:
        conn.execute("BEGIN TRANSACTION")
    try:
        result = work()
        if is_duck:
            conn.execute("COMMIT")
        return result
    except BaseException:
        if is_duck:
            conn.execute("ROLLBACK")
        raise


def _representative_answer(conn, capture, key: str) -> dict:
    run, scene = queries.find_scene_by_key(capture, key)
    case_id = str(capture["case_id"])
    project_id = storage.case_project_id(conn, case_id)
    rule = representatives.load_rule(conn, project_id) if project_id else {"keywords": []}
    choice = representatives.load_choices(conn, case_id).get(key)
    stored = [m for m in scene.get("media", []) if m.get("kind") == "VIDEO" and m.get("asset_id")] if scene else []
    video, source, missing = representatives.pick_representative(stored, rule["keywords"], choice["relative_path"] if choice else None)
    names = representatives.user_names(conn, [choice.get("chosen_by")] if choice else [])
    return {"case_id": case_id, "scene_key": key, "relative_path": video["relative_path"] if video else None,
            "asset_id": video["asset_id"] if video else None, "source": source if video else None,
            "chosen_by": choice.get("chosen_by") if choice and source == "USER" else None,
            "chosen_by_name": names.get(str(choice.get("chosen_by"))) if choice and source == "USER" else None,
            "chosen_at": choice.get("chosen_at") if choice and source == "USER" else None, "choice_missing": missing}


@router.put("/distribution/video-representatives")
def set_video_representative(payload: VideoRepresentativeInput, request: Request):
    """Saves the user's representative video of one (Case, Scene); same permission as result import."""
    with connect() as conn:
        project_id, capture = _representative_context(conn, request, payload.case_id, RESULT_IMPORT)
        _run, scene = queries.find_scene_by_key(capture, payload.scene_key)
        if scene is None:
            raise HTTPException(422, {"code": "VIDEO_SCENE_NOT_FOUND", "message": "Scene을 찾을 수 없습니다. 목록을 새로 고치세요."})
        stored = {str(m.get("relative_path")) for m in scene.get("media", []) if m.get("kind") == "VIDEO" and m.get("asset_id")}
        if payload.relative_path not in stored:
            raise HTTPException(422, {"code": "VIDEO_NOT_IN_SCENE", "message": "이 Scene에 없는 영상입니다. 재생할 수 있게 저장된 영상만 대표로 지정할 수 있습니다."})
        principal = request.state.principal

        def work():
            representatives.save_choice(conn, payload.case_id, payload.scene_key, payload.relative_path, principal.user_id)
            write_audit_event(request=request, principal=principal, status_code=200, action="DASHBOARD_VIDEO_REPRESENTATIVE_SET",
                              detail={"project_id": project_id, "case_id": payload.case_id, "scene_key": payload.scene_key,
                                      "relative_path": payload.relative_path}, connection=conn)
        _write(conn, work)
        return _representative_answer(conn, capture, payload.scene_key)


@router.delete("/distribution/video-representatives")
def reset_video_representative(request: Request, case_id: str, scene_key: str):
    """Removes the user's choice of one (Case, Scene); the rule / first file name decides again."""
    with connect() as conn:
        project_id, capture = _representative_context(conn, request, case_id, RESULT_IMPORT)
        principal = request.state.principal

        def work():
            removed = representatives.clear_choice(conn, case_id, scene_key)
            if removed:
                write_audit_event(request=request, principal=principal, status_code=200, action="DASHBOARD_VIDEO_REPRESENTATIVE_RESET",
                                  detail={"project_id": project_id, "case_id": case_id, "scene_key": scene_key}, connection=conn)
        _write(conn, work)
        return _representative_answer(conn, capture, scene_key)


class VideoRuleInput(BaseModel):
    keywords: list[str] = Field(default_factory=list, max_length=representatives.MAX_KEYWORDS * 4)


@project_requests_router.get("/api/projects/{project_id}/video-rule")
def get_video_rule(project_id: str, request: Request):
    """The project's ordered file-name keywords that pick the default representative video."""
    with connect() as conn:
        require_permission(request, PROJECT_DATA_VIEW, project_id, conn=conn)
        rule = representatives.load_rule(conn, project_id)
        names = representatives.user_names(conn, [rule.get("updated_by")])
        return {**rule, "updated_by_name": names.get(str(rule.get("updated_by"))),
                "can_edit_rule": _can(request, PROJECT_LAYOUT_EDIT, project_id, conn)}


@project_requests_router.put("/api/projects/{project_id}/video-rule")
def put_video_rule(project_id: str, payload: VideoRuleInput, request: Request):
    """Replaces the rule (admin / project manager permission, like other project settings)."""
    with connect() as conn:
        require_permission(request, PROJECT_LAYOUT_EDIT, project_id, conn=conn)
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone():
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        try:
            keywords = representatives.clean_keywords(payload.keywords)
        except representatives.VideoRepresentativeError as exc:
            raise HTTPException(422, {"code": exc.code, "message": str(exc)}) from exc
        principal = request.state.principal

        def work():
            before = representatives.load_rule(conn, project_id)["keywords"]
            saved = representatives.save_rule(conn, project_id, keywords, principal.user_id)
            write_audit_event(request=request, principal=principal, status_code=200, action="PROJECT_VIDEO_RULE_UPDATED",
                              detail={"project_id": project_id, "before": before, "after": keywords}, connection=conn)
            return saved
        saved = _write(conn, work)
        names = representatives.user_names(conn, [saved.get("updated_by")])
        return {**saved, "updated_by_name": names.get(str(saved.get("updated_by"))), "can_edit_rule": True}


@router.get("/distribution/scenes/{scene_id}")
def scene_detail(scene_id: str, request: Request, capture_id: str, run_id: str, mode: str, component_id: str,
                 basis: Literal["DETAIL", "REPORTED_SUMMARY"], line_indices: str = "1,2,3,4",
                 position: Literal["TOP", "BOT", "LH", "RH"] = "TOP", run_option_id: str | None = None):
    with connect() as conn:
        # Detail values are parsed on demand from this Scene's original csv files (issue #55).
        capture = storage.attach_scene_values(conn, capture_for_read(conn, request, capture_id), run_id=run_id,
                                              scene_id=scene_id, detail=True, component_id=component_id)
        try:
            return queries.scene_detail(capture, scene_id, run_id, mode, component_id, basis, lines_from(line_indices), position, run_option_id)
        except storage.DashboardCaptureError as exc:
            raise error(exc) from exc


@router.post("/distribution/comparison")
def comparison(payload: ComparisonInput, request: Request):
    edges = set(filter(None, payload.edge_keys.upper().split(",")))
    if not edges.issubset(queries.EDGES):
        raise HTTPException(422, "엣지 선택이 올바르지 않습니다.")
    parts, ids = [], set()
    with connect() as conn:
        for member in payload.members:
            capture = storage.attach_scene_values(conn, capture_for_read(conn, request, member.capture_id),
                                                  run_id=member.execution_run_id)
            if capture["case_id"] != member.simulation_case_id or member.simulation_case_id in ids:
                raise HTTPException(422, "Case별로 정확한 수집 버전을 하나씩 선택하세요.")
            ids.add(member.simulation_case_id)
            try:
                part = queries.distribution(capture, member.execution_run_id, member.mode, member.component_id, member.basis, edges, lines_from(payload.line_indices), member.run_option_id)
                if part["context"]["load_case_id"] != member.load_case_id:
                    raise HTTPException(422, "하중경우가 선택 Run과 다릅니다.")
                parts.append(part)
            except storage.DashboardCaptureError as exc:
                raise error(exc) from exc
    return queries.comparison(parts)


@router.get("/assets/{asset_id}")
def asset(asset_id: str, request: Request):
    with connect() as conn:
        row = conn.execute("""SELECT dc.project_id FROM dashboard_assets a JOIN dashboard_captures c ON c.id=a.capture_id
            JOIN dashboard_cases dc ON dc.id=c.case_id WHERE a.id=?""", [asset_id]).fetchone()
        if not row:
            raise HTTPException(404, "원본 자산이 없습니다.")
        require_permission(request, PROJECT_DATA_VIEW, str(row[0]), conn=conn)
        item = storage.find_asset(conn, asset_id)
    content = bytes(item["content"])
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    requested = request.headers.get("range")
    if requested:
        try:
            if not requested.startswith("bytes=") or "," in requested:
                raise ValueError()
            start, end = requested[6:].split("-", 1)
            if start:
                first = int(start)
                last = min(int(end), len(content) - 1) if end else len(content) - 1
            else:
                count = int(end)
                if count <= 0:
                    raise ValueError()
                first, last = max(0, len(content) - count), len(content) - 1
            if first < 0 or first > last or first >= len(content):
                raise ValueError()
        except ValueError:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{len(content)}"})
        headers["Content-Range"] = f"bytes {first}-{last}/{len(content)}"
        return Response(content[first:last + 1], status_code=206, media_type=item["media_type"], headers=headers)
    return Response(content, media_type=item["media_type"], headers=headers)
