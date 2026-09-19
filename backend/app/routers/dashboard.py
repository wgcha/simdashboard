"""Authorized dashboard reads and explicit atomic capture publication."""
from __future__ import annotations
from typing import Literal
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from ..database_connection import connect
from ..modules.access_control import PROJECT_DATA_VIEW, RESULT_IMPORT, SYSTEM_CATALOG_MANAGE, require_permission, require_resource_permission
from ..security import write_audit_event
from ..services import dashboard_capture as storage, dashboard_queries as queries

router = APIRouter(prefix="/api/dashboard", tags=["result-dashboard"])


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
def catalog(request: Request, request_id: str, environment: Literal["USAGE", "DISTRIBUTION"]):
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "request", request_id, conn=conn)
        return queries.catalog(conn, request_id, environment)


@router.post("/scans")
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
def publish(payload: CaptureInput, request: Request):
    with connect() as conn:
        require_resource_permission(request, RESULT_IMPORT, "request", payload.request_id, conn=conn)
        try:
            root_id = storage._root_id(storage._root(conn))
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
        capture = capture_for_read(conn, request, capture_id)
        try:
            return queries.distribution(capture, run_id, mode, component_id, basis, edges, lines_from(line_indices), run_option_id)
        except storage.DashboardCaptureError as exc:
            raise error(exc) from exc


@router.get("/distribution/scenes/{scene_id}")
def scene_detail(scene_id: str, request: Request, capture_id: str, run_id: str, mode: str, component_id: str,
                 basis: Literal["DETAIL", "REPORTED_SUMMARY"], line_indices: str = "1,2,3,4",
                 position: Literal["TOP", "BOT", "LH", "RH"] = "TOP", run_option_id: str | None = None):
    with connect() as conn:
        capture = capture_for_read(conn, request, capture_id)
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
            capture = capture_for_read(conn, request, member.capture_id)
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
