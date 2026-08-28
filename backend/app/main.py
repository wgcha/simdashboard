from __future__ import annotations

import json
import base64
import io
import re
import shutil
import zipfile
from xml.etree import ElementTree as ET
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from . import config as app_config
from .modules.access_control import (
    DASHBOARD_EDIT,
    PROJECT_DATA_VIEW,
    PROJECT_LAYOUT_EDIT,
    PROJECT_THRESHOLD_MANAGE,
    PROJECT_VARIABLE_MANAGE,
    REPORT_EXPORT,
    REQUEST_CREATE,
    REQUEST_EDIT,
    RESULT_IMPORT,
    RESULT_REVIEW,
    SYSTEM_CATALOG_MANAGE,
    WORKFLOW_EDIT,
    has_permission,
    require_permission,
    require_resource_permission,
    resolve_project_assignee,
)
from .database import connect, initialize_database, json_value, rows
from .config import database_settings, security_settings
from .repositories.media_repository import get_blob, get_drop_video, get_media_asset, list_drop_videos
from .services.media_http import build_media_response
from .repositories.portfolio import PortfolioRepository
from .repositories.variable_catalog import VariableCatalogRepository
from .repositories.workbench import WorkbenchRepository
from .services.request_monitoring import request_monitoring_summary, sync_request_status
from .schemas.api import (
    AnalysisPageCreate,
    AnalysisPageOrderUpdate,
    AnalysisPageSummary,
    AnalysisPageUpdate,
    AnalysisRequestCreate,
    DashboardClone,
    DashboardDefinition,
    DropVideoPageResponse,
    ImportSchemaPayload,
    LoadCaseCreate,
    NaturalLanguageCommand,
    QualityThresholdUpdate,
    ReportLayoutPayload,
    ReportTemplateRenderPayload,
    ReportTemplateUploadPayload,
    ReviewItemCreate,
    ReviewItemUpdate,
    VariableCreate,
    VariableUpdate,
    WorkspaceLayoutResponse,
    WorkspaceLayoutUpdate,
    WorkspaceLayoutVersionResponse,
    WorkflowStepUpdate,
    WorkflowStepsReplace,
)
from .schemas.reports import (
    ReportLayoutDeactivationResponse,
    ReportLayoutSavedResponse,
    ReportLayoutVersionDetailResponse,
    ReportLayoutVersionSummaryResponse,
)
from .security import SecurityMiddleware, write_audit_event
from .routers.security import router as security_router
from .routers.access_control import router as access_control_router
from .routers.workbench import router as workbench_router
from .routers.modeling_catalog import router as modeling_catalog_router
from .routers.result_folder_refresh import router as result_folder_refresh_router
from .routers.result_ingestion import router as result_ingestion_router
from .adapters.http.routers.projects import router as projects_router
from .adapters.http.routers.reports import router as reports_router
from .adapters.http.routers.requests import router as requests_router
from .adapters.persistence.products import SQLProductInformationRepositoryProvider
from .application.products.queries import list_product_information
from .adapters.persistence.results import SQLAnalysisRunSummaryRepositoryProvider
from .application.results.queries import list_analysis_runs as list_analysis_runs_query
from .services.drop_video_demo import (
    DEMO_DROP_VIDEO_LOAD_CASE_IDS,
    DROP_VIDEO_DEMO_BY_ID,
    DROP_VIDEO_DEMO_SCENES,
    DROP_VIDEO_SOURCE_DIR,
    build_demo_evaluation,
    probe_mp4,
    summarize_demo_evaluations,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Analysis Canvas API", version="0.1.0", lifespan=lifespan)
app.add_middleware(SecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(security_settings().cors_allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/assets", StaticFiles(directory=str(__import__("pathlib").Path(__file__).resolve().parents[1] / "public_assets")), name="assets")
app.include_router(security_router)
app.include_router(access_control_router)
app.include_router(workbench_router)
app.include_router(modeling_catalog_router)
app.include_router(result_folder_refresh_router)


def media_storage_mode() -> app_config.MediaStorageMode:
    """Read media cutover policy at request time and keep it patchable."""

    return app_config.media_storage_mode()


WORKSPACE_LAYOUT_KINDS = {"portfolio", "workflow"}


def _validated_workspace_layout(kind: str, definition: dict[str, Any]) -> dict[str, Any]:
    if kind not in WORKSPACE_LAYOUT_KINDS:
        raise HTTPException(404, "지원하지 않는 레이아웃 종류입니다.")
    font_size = definition.get("fontSize")
    if not isinstance(font_size, int) or not 8 <= font_size <= 18:
        raise HTTPException(422, "레이아웃 글자 크기는 8~18 사이의 정수여야 합니다.")
    if kind == "portfolio":
        chart_order = definition.get("chartOrder")
        expected = {"trend", "status", "quality", "type"}
        if not isinstance(chart_order, list) or len(chart_order) != len(expected) or set(chart_order) != expected:
            raise HTTPException(422, "운영 대시보드 차트 순서가 올바르지 않습니다.")
        return {"fontSize": font_size, "chartOrder": chart_order}
    accent = definition.get("accentColor")
    if not isinstance(accent, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        raise HTTPException(422, "워크플로 강조 색상은 #을 포함한 6자리 HEX여야 합니다.")
    items = definition.get("items")
    if not isinstance(items, list):
        raise HTTPException(422, "워크플로 레이아웃 items 배열이 필요합니다.")
    normalized_items = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("requestId"), str):
            raise HTTPException(422, "워크플로 레이아웃 항목에는 requestId가 필요합니다.")
        coordinates = {key: item.get(key) for key in ("x", "y", "w", "h")}
        if any(not isinstance(value, int) for value in coordinates.values()):
            raise HTTPException(422, "워크플로 레이아웃 위치와 크기는 정수여야 합니다.")
        normalized_items.append({"requestId": item["requestId"], **coordinates})
    return {"fontSize": font_size, "accentColor": accent, "items": normalized_items}


def _validated_report_layout(definition: dict[str, Any]) -> dict[str, Any]:
    if definition.get("coverVariant") not in {"balanced", "executive", "evidence"}:
        raise HTTPException(422, "지원하지 않는 표지 형식입니다.")
    sections = definition.get("sectionOrder")
    if not isinstance(sections, list) or len(sections) != 3 or set(sections) != {"series", "scalar", "media"}:
        raise HTTPException(422, "근거 페이지 순서는 series, scalar, media를 한 번씩 포함해야 합니다.")
    accent = str(definition.get("accentColor") or "")
    if len(accent) != 6 or any(character not in "0123456789abcdefABCDEF" for character in accent):
        raise HTTPException(422, "강조색은 6자리 HEX 색상이어야 합니다.")
    slide_master = definition.get("slideMaster")
    if slide_master is not None:
        if not isinstance(slide_master, dict) or slide_master.get("design") not in {"plain", "frame", "header-band", "split"}:
            raise HTTPException(422, "슬라이드 마스터에는 올바른 디자인이 필요합니다.")
        for color_key in ("backgroundColor", "accentColor"):
            color = str(slide_master.get(color_key) or "")
            if len(color) != 6 or any(character not in "0123456789abcdefABCDEF" for character in color):
                raise HTTPException(422, f"슬라이드 마스터 {color_key}은 6자리 HEX 색상이어야 합니다.")
    placements = definition.get("variablePlacements")
    if not isinstance(placements, list):
        raise HTTPException(422, "variablePlacements 배열이 필요합니다.")
    for placement in placements:
        if not isinstance(placement, dict) or not placement.get("variableKey") or placement.get("presentation") not in {"chart", "table", "both"}:
            raise HTTPException(422, "변수 배치에는 variableKey와 올바른 presentation이 필요합니다.")
    slides = definition.get("slides")
    if slides is not None:
        if not isinstance(slides, list) or not slides:
            raise HTTPException(422, "slides는 1개 이상의 배열이어야 합니다.")
        slide_ids: set[str] = set()
        for slide in slides:
            if not isinstance(slide, dict) or not slide.get("id") or slide.get("kind") not in {"cover", "series", "scalar", "media", "custom"}:
                raise HTTPException(422, "각 슬라이드에는 고유 id와 올바른 kind가 필요합니다.")
            if slide["id"] in slide_ids:
                raise HTTPException(422, "슬라이드 id는 중복될 수 없습니다.")
            slide_ids.add(slide["id"])
            slide_style = slide.get("style")
            if slide_style is not None:
                if not isinstance(slide_style, dict) or not isinstance(slide_style.get("useMaster"), bool):
                    raise HTTPException(422, "슬라이드 스타일에는 useMaster 불리언 값이 필요합니다.")
                if slide_style.get("design") is not None and slide_style["design"] not in {"plain", "frame", "header-band", "split"}:
                    raise HTTPException(422, "지원하지 않는 슬라이드 디자인입니다.")
                for color_key in ("backgroundColor", "accentColor"):
                    if slide_style.get(color_key) is None:
                        continue
                    color = str(slide_style[color_key])
                    if len(color) != 6 or any(character not in "0123456789abcdefABCDEF" for character in color):
                        raise HTTPException(422, f"슬라이드 {color_key}은 6자리 HEX 색상이어야 합니다.")
            elements = slide.get("elements")
            if not isinstance(elements, list) or len(elements) > 80:
                raise HTTPException(422, "슬라이드 elements는 80개 이하의 배열이어야 합니다.")
            element_ids: set[str] = set()
            for element in elements:
                if not isinstance(element, dict) or element.get("type") not in {"title", "text", "verdict", "scalar-card", "chart", "table", "image"}:
                    raise HTTPException(422, "지원하지 않는 보고서 위젯 형식입니다.")
                if not element.get("id") or element["id"] in element_ids:
                    raise HTTPException(422, "슬라이드 안의 위젯 id는 고유해야 합니다.")
                element_ids.add(element["id"])
                if element.get("text") is not None and not isinstance(element["text"], str):
                    raise HTTPException(422, "텍스트 상자 내용은 문자열이어야 합니다.")
                for key, limit in (("x", 32), ("w", 32), ("y", 18), ("h", 18)):
                    if not isinstance(element.get(key), (int, float)) or element[key] < 0 or element[key] > limit:
                        raise HTTPException(422, f"위젯 {key} 좌표가 캔버스 범위를 벗어났습니다.")
                if element["w"] <= 0 or element["h"] <= 0 or element["x"] + element["w"] > 32 or element["y"] + element["h"] > 18:
                    raise HTTPException(422, "위젯 영역이 슬라이드 경계를 벗어났습니다.")
    if definition.get("templateSource", "native") not in {"native", "pptx_upload"}:
        raise HTTPException(422, "지원하지 않는 템플릿 원본 형식입니다.")
    if definition.get("templateSource") == "pptx_upload" and not definition.get("templateAssetId"):
        raise HTTPException(422, "업로드 PPTX 템플릿 ID가 필요합니다.")
    if not isinstance(definition.get("templateBindings", {}), dict):
        raise HTTPException(422, "templateBindings는 객체여야 합니다.")
    return definition


@app.get("/api/health")
def health() -> dict[str, str]:
    with connect() as conn:
        conn.execute("SELECT 1").fetchone()
    return {"status": "ok", "database_backend": database_settings().backend}


@app.get("/api/feature-examples")
def feature_examples() -> list[dict[str, Any]]:
    """Return curated, stable entry points for exercising product capabilities."""
    items = [
        {"id": "run-comparison", "order": 1, "category": "분석 판단", "title": "Run 비교: 회귀와 개선", "summary": "세 설계 Run을 비교해 회귀·개선·유지 판정과 시계열 오버레이를 확인합니다.", "badge": "READY", "workspace_page": "dashboard", "preferred_view": "compare", "project_id": "project-feature-showcase", "request_id": "request-showcase-compare", "load_case_id": "loadcase-showcase-compare", "features": ["Run A/B/C", "회귀 1건", "개선 1건", "시계열 비교"], "checks": ["기준 Run과 대상 Run을 바꿉니다.", "REGRESSION·IMPROVED 필터를 확인합니다.", "상단 응력 시계열을 겹쳐 봅니다."]},
        {"id": "trust-ready", "order": 2, "category": "신뢰·추적", "title": "신뢰도: 추적 가능한 폴더 Import", "summary": "원본·체크섬·스키마·파서·Validation·카탈로그 매핑이 갖춰진 TRUSTED 결과입니다.", "badge": "TRUSTED", "workspace_page": "dashboard", "preferred_view": "compare", "project_id": "project-feature-showcase", "request_id": "request-showcase-trust", "load_case_id": "loadcase-showcase-trust", "features": ["폴더 Import", "체크섬", "Validation PASS", "다중 결과형"], "checks": ["신뢰도 패널의 모든 점검을 펼칩니다.", "원본 폴더와 스키마 버전을 확인합니다.", "결과 변수의 카탈로그 매핑을 확인합니다."]},
        {"id": "trust-warning", "order": 3, "category": "신뢰·추적", "title": "신뢰도: 의도적인 경고", "summary": "미등록 hotspot 위치와 Validation 부재를 넣어 WARN의 원인과 해소 방향을 보여줍니다.", "badge": "WARN", "workspace_page": "dashboard", "preferred_view": "compare", "project_id": "project-feature-showcase", "request_id": "request-showcase-warning", "load_case_id": "loadcase-showcase-warning", "features": ["미매핑 변수", "Validation 없음", "WARN 설명"], "checks": ["카탈로그 매핑 경고를 찾습니다.", "unmapped_hotspot 키를 확인합니다.", "Validation 경고와 FAIL의 차이를 봅니다."]},
        {"id": "review-flow", "order": 4, "category": "협업", "title": "협업 검토: 상태별 코멘트", "summary": "OPEN·IN_REVIEW·RESOLVED 검토 항목을 실제 결과 변수와 위치에 연결한 예제입니다.", "badge": "3 ITEMS", "workspace_page": "dashboard", "preferred_view": "compare", "project_id": "project-feature-showcase", "request_id": "request-showcase-review", "load_case_id": "loadcase-showcase-review", "features": ["결과 북마크", "검토 코멘트", "상태 전환", "요소 위치"], "checks": ["세 가지 검토 상태를 필터링합니다.", "코멘트를 IN_REVIEW 또는 RESOLVED로 바꿉니다.", "새 검토 항목을 추가합니다."]},
        {"id": "multi-type", "order": 5, "category": "데이터", "title": "다중 결과형: 수치·곡선·이미지", "summary": "한 Run에서 수치, 시간 이력, 하중-변위 곡선, hotspot 위치, 컨투어 이미지를 함께 확인합니다.", "badge": "5 TYPES", "workspace_page": "dashboard", "preferred_view": "open_cell", "project_id": "project-feature-showcase", "request_id": "request-showcase-multitype", "load_case_id": "loadcase-showcase-multitype", "features": ["NUMBER", "TIME_SERIES", "CURVE", "IMAGE", "LOCATION"], "checks": ["상세 분석의 수치·차트를 확인합니다.", "변수 카탈로그에서 데이터형을 비교합니다.", "보고서 편집기에서 변수 배치를 시도합니다."]},
        {"id": "data-waiting", "order": 6, "category": "데이터", "title": "변수 카탈로그: 데이터 대기", "summary": "SQL/폴더 결과가 오기 전에 NUMBER·TIME_SERIES·IMAGE·VIDEO·MODEL_3D를 먼저 선언한 상태입니다.", "badge": "NO DATA", "workspace_page": "variables", "preferred_view": "open_cell", "project_id": "project-feature-showcase", "request_id": "request-showcase-waiting", "load_case_id": "loadcase-showcase-waiting", "features": ["사전 변수 선언", "데이터 대기", "5개 데이터형"], "checks": ["결과 데이터 대기 표시를 확인합니다.", "허용 위젯과 집계를 비교합니다.", "새 변수를 추가하고 수정합니다."]},
        {"id": "workflow-states", "order": 7, "category": "운영", "title": "워크플로: 진행·차단·대기", "summary": "10단계 업무 흐름에 완료·진행·차단·대기 상태를 섞어 운영 화면을 재현합니다.", "badge": "BLOCKED", "workspace_page": "dashboard", "preferred_view": "workflow", "project_id": "project-feature-showcase", "request_id": "request-showcase-workflow", "load_case_id": "loadcase-showcase-workflow", "features": ["10단계", "진행률", "차단 사유", "담당자"], "checks": ["차단된 해석 실행 단계를 찾습니다.", "단계명을 편집해 봅니다.", "운영 대시보드 집계와 연결해 봅니다."]},
        {"id": "folder-schema", "order": 8, "category": "데이터", "title": "폴더 스키마: 3단계 매핑", "summary": "project/request/loadcase 폴더 계층과 수치·곡선·미디어 규칙을 편집하는 예제입니다.", "badge": "SCHEMA", "workspace_page": "schemas", "features": ["폴더 계층", "파일 패턴", "버전 관리"], "checks": ["다중 결과형 폴더 예제를 선택합니다.", "context_mapping 3단계를 확인합니다.", "복제 후 패턴을 수정합니다."]},
        {"id": "ppt-layout", "order": 9, "category": "보고서", "title": "PPT 시각적 레이아웃 편집", "summary": "실제 결과를 보고서로 열어 슬라이드 캔버스에서 요소 이동·크기·변수 배치·버전을 확인합니다.", "badge": "EDITOR", "workspace_page": "dashboard", "preferred_view": "open_cell", "project_id": "project-feature-showcase", "request_id": "request-showcase-multitype", "load_case_id": "loadcase-showcase-multitype", "features": ["슬라이드 캔버스", "드래그·리사이즈", "변수 바인딩", "레이아웃 버전"], "checks": ["상세 분석의 보고서 내보내기를 누릅니다.", "커스텀 슬라이드와 요소를 추가합니다.", "다른 이름으로 저장 후 버전을 비교합니다."], "action_hint": "상세 분석에서 ‘보고서 내보내기’를 누르세요."},
        {"id": "automation", "order": 10, "category": "자동화", "title": "모델링 자동화 실행 이력", "summary": "템플릿 버전, 입력 파라미터, 생성 모델과 실행 상태를 카드별로 확인합니다.", "badge": "HISTORY", "workspace_page": "templates", "features": ["템플릿 버전", "입력 파라미터", "생성 모델", "실행 상태"], "checks": ["서로 다른 해석 유형을 비교합니다.", "입력과 생성 모델 메타데이터를 확인합니다."]},
        {"id": "data-registration", "order": 11, "category": "데이터", "title": "수동·Radioss·폴더 결과 등록", "summary": "프로젝트부터 하중 경우까지 만들고 미리보기 검증 후 결과를 등록하는 전체 흐름입니다.", "badge": "IMPORT", "workspace_page": "data", "features": ["JSON/CSV", "Radioss", "검증 미리보기", "폴더 Import"], "checks": ["샘플 파일을 내려받습니다.", "검증만 실행해 오류를 먼저 확인합니다.", "등록 후 분석 열기로 이동합니다."]},
        {"id": "help", "order": 12, "category": "안내", "title": "사용 시나리오 도움말", "summary": "처음 사용하는 사람이 업무 목적별로 필요한 화면과 순서를 찾아가는 웹 도움말입니다.", "badge": "GUIDE", "workspace_page": "help", "features": ["시나리오", "단계 안내", "화면 바로가기"], "checks": ["목적에 맞는 시나리오를 고릅니다.", "단계별 설명과 바로가기를 사용합니다."]},
    ]
    load_case_ids = [item.get("load_case_id") for item in items if item.get("load_case_id")]
    if load_case_ids:
        with connect() as conn:
            for item in items:
                load_case_id = item.get("load_case_id")
                if not load_case_id:
                    item["data_profile"] = {"runs": 0, "scalars": 0, "series": 0, "curves": 0, "media": 0, "reviews": 0}
                    continue
                counts = conn.execute(
                    """
                    SELECT count(DISTINCT r.id), count(DISTINCT s.id), count(DISTINCT ts.variable_key),
                           count(DISTINCT c.id), count(DISTINCT m.id), count(DISTINCT a.id)
                    FROM load_cases lc
                    LEFT JOIN analysis_runs r ON r.load_case_id=lc.id
                    LEFT JOIN scalar_results s ON s.analysis_run_id=r.id
                    LEFT JOIN time_series_results ts ON ts.analysis_run_id=r.id
                    LEFT JOIN curve_results c ON c.analysis_run_id=r.id
                    LEFT JOIN media_assets m ON m.analysis_run_id=r.id
                    LEFT JOIN review_annotations a ON a.analysis_run_id=r.id
                    WHERE lc.id=?
                    """,
                    [load_case_id],
                ).fetchone()
                item["data_profile"] = dict(zip(["runs", "scalars", "series", "curves", "media", "reviews"], counts))
    return items


app.include_router(projects_router)


@app.get("/api/import-schemas")
def list_import_schemas() -> list[dict[str, Any]]:
    with connect() as conn:
        items = rows(conn.execute("SELECT * FROM import_schemas WHERE is_active=true ORDER BY updated_at DESC"))
    for item in items:
        item["definition"] = json_value(item.pop("definition_json"))
    return items


@app.post("/api/import-schemas", status_code=201)
def create_import_schema(payload: ImportSchemaPayload, request: Request) -> dict[str, Any]:
    if not isinstance(payload.definition.get("mappings"), list):
        raise HTTPException(422, "스키마 정의에는 mappings 배열이 필요합니다.")
    schema_id, now = f"import-schema-{uuid4().hex[:12]}", datetime.now(timezone.utc).replace(tzinfo=None)
    definition = {**payload.definition, "schema_id": payload.definition.get("schema_id") or schema_id, "version": 1}
    encoded = json.dumps(definition, ensure_ascii=False)
    principal = request.state.principal
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
            conn.execute(
                """
                INSERT INTO import_schemas
                    (id, name, description, definition_json, is_active, created_at, updated_at, updated_by)
                VALUES (?, ?, ?, ?, true, ?, ?, ?)
                """,
                [schema_id, payload.name.strip(), payload.description.strip(), encoded, now, now, principal.display_name],
            )
            conn.execute(
                """
                INSERT INTO import_schema_versions
                    (schema_id, version, definition_json, created_at, updated_by)
                VALUES (?, 1, ?, ?, ?)
                """,
                [schema_id, encoded, now, principal.display_name],
            )
            write_audit_event(request=request, principal=principal, status_code=201, action="IMPORT_SCHEMA_CREATED", detail={"schema_id": schema_id}, connection=conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": schema_id, "name": payload.name.strip(), "description": payload.description.strip(), "definition": definition, "created_at": now, "updated_at": now, "updated_by": principal.display_name}


@app.put("/api/import-schemas/{schema_id}")
def update_import_schema(schema_id: str, payload: ImportSchemaPayload, request: Request) -> dict[str, Any]:
    if not isinstance(payload.definition.get("mappings"), list):
        raise HTTPException(422, "스키마 정의에는 mappings 배열이 필요합니다.")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    principal = request.state.principal
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
            existing = conn.execute("SELECT definition_json, created_at FROM import_schemas WHERE id=? AND is_active=true", [schema_id]).fetchone()
            if not existing:
                raise HTTPException(404, "폴더 스키마를 찾을 수 없습니다.")
            previous = json_value(existing[0]) or {}
            version = int(previous.get("version") or 1) + 1
            definition = {**payload.definition, "schema_id": previous.get("schema_id") or schema_id, "version": version}
            encoded = json.dumps(definition, ensure_ascii=False)
            conn.execute("UPDATE import_schemas SET name=?, description=?, definition_json=?, updated_at=?, updated_by=? WHERE id=?", [payload.name.strip(), payload.description.strip(), encoded, now, principal.display_name, schema_id])
            conn.execute(
                "INSERT INTO import_schema_versions (schema_id, version, definition_json, created_at, updated_by) VALUES (?, ?, ?, ?, ?)",
                [schema_id, version, encoded, now, principal.display_name],
            )
            write_audit_event(request=request, principal=principal, status_code=200, action="IMPORT_SCHEMA_UPDATED", detail={"schema_id": schema_id, "version": version}, connection=conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": schema_id, "name": payload.name.strip(), "description": payload.description.strip(), "definition": definition, "created_at": existing[1], "updated_at": now, "updated_by": principal.display_name}


@app.delete("/api/import-schemas/{schema_id}")
def delete_import_schema(schema_id: str, request: Request) -> dict[str, str]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    with connect() as conn:
        existing = conn.execute("SELECT id FROM import_schemas WHERE id=? AND is_active=true", [schema_id]).fetchone()
        if not existing:
            raise HTTPException(404, "폴더 스키마를 찾을 수 없습니다.")
        in_use = conn.execute("SELECT count(*) FROM folder_import_jobs WHERE schema_id=? AND status IN ('RUNNING','COMPLETED')", [schema_id]).fetchone()[0]
        if in_use:
            raise HTTPException(409, "적재 이력이 있는 스키마는 삭제할 수 없습니다. 비활성화 정책이 필요합니다.")
        conn.execute("UPDATE import_schemas SET is_active=false, updated_at=? WHERE id=?", [datetime.now(timezone.utc).replace(tzinfo=None), schema_id])
    return {"status": "DEACTIVATED", "id": schema_id}


@app.get("/api/portfolio/overview")
def get_portfolio_overview(
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: str | None = None,
    analysis_type: str | None = None,
    status: str | None = None,
    search: str | None = Query(default=None, max_length=120),
) -> dict[str, Any]:
    with connect() as conn:
        return PortfolioRepository(conn).overview(
            date_from=date_from,
            date_to=date_to,
            project_id=project_id,
            analysis_type=analysis_type,
            status=status,
            search=search,
        )


@app.get("/api/portfolio/export.csv")
def export_portfolio_csv(
    date_from: date | None = None,
    date_to: date | None = None,
    project_id: str | None = None,
    analysis_type: str | None = None,
    status: str | None = None,
    search: str | None = Query(default=None, max_length=120),
) -> Response:
    with connect() as conn:
        payload = PortfolioRepository(conn).overview(date_from=date_from, date_to=date_to, project_id=project_id, analysis_type=analysis_type, status=status, search=search)
    return Response(PortfolioRepository.to_csv(payload["records"]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="analysis-portfolio.csv"'})


@app.get("/api/projects/{project_id}/requests")
def get_requests(project_id: str, request: Request) -> list[dict[str, Any]]:
    with connect() as conn:
        context = require_permission(request, PROJECT_DATA_VIEW, project_id, conn=conn)
        if context.project_role is None:
            detail = {
                "code": "PROJECT_MEMBERSHIP_REQUIRED",
                "message": "이 작업을 수행할 권한이 없습니다.",
                "required_permission": PROJECT_DATA_VIEW,
                "project_id": project_id,
            }
            request.state.authorization_detail = detail
            raise HTTPException(403, detail)
        return rows(
            conn.execute(
                "SELECT * FROM analysis_requests WHERE project_id = ? ORDER BY requested_at DESC",
                [project_id],
            )
        )


@app.post("/api/projects/{project_id}/requests", status_code=201)
def create_request(project_id: str, payload: AnalysisRequestCreate, request: Request) -> dict[str, Any]:
    request_id = f"request-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    due_at = now + timedelta(days=payload.due_in_days)
    principal = request.state.principal
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            if conn.execute("SELECT id FROM projects WHERE id = ?", [project_id]).fetchone() is None:
                raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
            require_permission(request, REQUEST_CREATE, project_id, conn=conn)
            assignee = resolve_project_assignee(conn, project_id, payload.owner_user_id)
            repository = WorkbenchRepository(conn)
            request_type = repository.get_request_type(payload.request_type_id, payload.request_type_version)
            if not request_type or not request_type["is_active"]:
                raise HTTPException(
                    404,
                    detail={
                        "code": "REQUEST_TYPE_NOT_FOUND",
                        "request_type_id": payload.request_type_id,
                        "request_type_version": payload.request_type_version,
                    },
                )
            assigned_by = principal.display_name
            conn.execute(
                """
                INSERT INTO analysis_requests
                    (id, project_id, title, status, owner, owner_user_id,
                     requested_at, due_at, overall_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [request_id, project_id, payload.title.strip(), "READY", assignee.display_name,
                 assignee.user_id, now, due_at, payload.overall_note.strip()],
            )
            repository.assign_request_type(
                request_id,
                payload.request_type_id,
                payload.request_type_version,
                "ADMIN",
                assigned_by,
            )
            repository.create_work_plan(
                request_id,
                request_type,
                assignee.display_name,
                assigned_by,
                owner_user_id=assignee.user_id,
                source_type=payload.source_type,
                source_reference=payload.source_reference.strip(),
                requested_by=principal.display_name,
            )
            repository.create_result_layout_snapshot(request_id, request_type, principal.display_name)
            write_audit_event(
                request=request,
                principal=principal,
                status_code=201,
                action="ANALYSIS_REQUEST_CREATED",
                detail={
                    "project_id": project_id,
                    "request_id": request_id,
                    "owner_user_id": assignee.user_id,
                },
                connection=conn,
            )
            conn.execute("COMMIT")
        except (FileExistsError, PermissionError) as exc:
            conn.execute("ROLLBACK")
            raise HTTPException(409, detail={"code": str(exc), "request_id": request_id}) from exc
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {
        "id": request_id,
        "project_id": project_id,
        "title": payload.title.strip(),
        "status": "READY",
        "owner": assignee.display_name,
        "owner_user_id": assignee.user_id,
        "requested_at": now,
        "due_at": due_at,
        "overall_note": payload.overall_note.strip(),
        "request_type_id": payload.request_type_id,
        "request_type_version": payload.request_type_version,
        "scenario_name": request_type["display_name"],
        "source_type": payload.source_type,
        "source_reference": payload.source_reference.strip(),
        "requested_by": principal.display_name,
    }


app.include_router(requests_router)


@app.get("/api/requests/{request_id}/load-cases")
def get_load_cases(request_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        result = rows(
            conn.execute(
                "SELECT * FROM load_cases WHERE request_id = ? ORDER BY created_at",
                [request_id],
            )
        )
    for item in result:
        item["parameters"] = json_value(item.pop("parameters_json"))
    return result


@app.get("/api/load-cases/{load_case_id}/drop-videos", response_model=DropVideoPageResponse)
def get_drop_videos(
    load_case_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=20),
) -> dict[str, Any]:
    with connect() as conn:
        context = conn.execute(
            """
            SELECT lc.id, lc.name, lc.analysis_type, ar.id, ar.title
            FROM load_cases lc
            JOIN analysis_requests ar ON ar.id = lc.request_id
            WHERE lc.id = ?
            """,
            [load_case_id],
        ).fetchone()
        require_resource_permission(request, PROJECT_DATA_VIEW, "load_case", load_case_id, conn=conn)
        stored_videos = list_drop_videos(conn, load_case_id)
    if context is None:
        raise HTTPException(404, "하중 경우를 찾을 수 없습니다.")

    videos: list[dict[str, Any]] = []
    database_only = media_storage_mode() == "database-only"
    storage_source = "DATABASE" if stored_videos or database_only else "EXAMPLE_ADAPTER"
    if stored_videos:
        for item in stored_videos:
            scene = DROP_VIDEO_DEMO_BY_ID.get(item["video_id"])
            metadata = json_value(item.get("metadata_json")) or {}
            videos.append(
                {
                    "video_id": item["video_id"],
                    "scene_id": item["video_id"],
                    "scene_name": item["scene_name"],
                    "video_url": f"/api/drop-videos/{item['video_id']}/content",
                    "download_url": f"/api/drop-videos/{item['video_id']}/download",
                    "thumbnail_url": None,
                    "duration": None,
                    "file_size": int(item["file_size"]),
                    "format": "mp4" if str(item["mime_type"]) == "video/mp4" else "webm",
                    "codec": metadata.get("codec"),
                    "fast_start": metadata.get("fast_start"),
                    "sort_order": int(item["sort_order"]),
                    "drop_direction": metadata.get("drop_direction"),
                    "drop_condition": metadata.get("drop_condition"),
                    "analysis_version": metadata.get("analysis_version"),
                    "evaluation": build_demo_evaluation(scene) if scene else {
                        "overall_verdict": "PASS",
                        "open_cell": {"critical_value": 0, "threshold": 75, "unit": "MPa", "verdict": "PASS", "metrics": {}},
                        "chassis_rear": {"critical_value": 0, "threshold": 5, "unit": "mm", "verdict": "PASS", "metrics": {}},
                    },
                }
            )
    elif not database_only and load_case_id in DEMO_DROP_VIDEO_LOAD_CASE_IDS:
        for scene in DROP_VIDEO_DEMO_SCENES:
            path = DROP_VIDEO_SOURCE_DIR / scene.filename
            if not path.is_file():
                continue
            media = probe_mp4(path)
            videos.append(
                {
                    "video_id": scene.video_id,
                    "scene_id": scene.video_id,
                    "scene_name": scene.scene_name,
                    "video_url": f"/api/drop-videos/{scene.video_id}/content",
                    "download_url": f"/api/drop-videos/{scene.video_id}/download",
                    "thumbnail_url": None,
                    "duration": None,
                    "file_size": path.stat().st_size,
                    "format": "mp4",
                    "codec": media.codec,
                    "fast_start": media.fast_start,
                    "sort_order": scene.sort_order,
                    "drop_direction": None,
                    "drop_condition": None,
                    "analysis_version": None,
                    "evaluation": build_demo_evaluation(scene),
                }
            )
    videos.sort(key=lambda item: (item["sort_order"], item["scene_id"]))
    summary = summarize_demo_evaluations(videos)
    total_items = len(videos)
    total_pages = (total_items + page_size - 1) // page_size if total_items else 0
    start = (page - 1) * page_size
    page_videos = videos[start:start + page_size] if start < total_items else []
    return {
        "load_case": {
            "load_case_id": context[0],
            "load_case_name": context[1],
            "analysis_type": context[2],
            "request_id": context[3],
            "request_name": context[4],
        },
        "source": storage_source,
        "demo_only": True,
        "evaluation_source": "SYNTHETIC_DEMO",
        "contract_version": 1,
        "summary": summary,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_previous": page > 1 and total_pages > 0,
            "has_next": page < total_pages,
        },
        "videos": page_videos,
    }


def _media_audit_callback(request: Request, action: str, bytes_yielded: int, status_code: int) -> None:
    try:
        with connect() as audit_connection:
            write_audit_event(
                request=request,
                principal=getattr(request.state, "principal", None),
                status_code=status_code,
                action=f"MEDIA_STREAM_{action}",
                detail={"bytes_yielded_to_asgi": bytes_yielded},
                connection=audit_connection,
            )
    except Exception:
        # Observability must not turn a successful media response into a 5xx.
        return


@app.get("/api/drop-videos/{video_id}/content", operation_id="get_drop_video_content")
@app.head("/api/drop-videos/{video_id}/content", include_in_schema=False)
def get_drop_video_content(video_id: str, request: Request) -> Response:
    with connect() as conn:
        stored = get_drop_video(conn, video_id)
        if stored:
            require_resource_permission(request, PROJECT_DATA_VIEW, "drop_video", video_id, conn=conn)
            blob = get_blob(conn, str(stored["blob_id"]))
            if blob is None:
                raise HTTPException(404, "예제 영상 blob을 찾을 수 없습니다.")
            return build_media_response(
                request,
                blob=blob,
                mime_type=str(stored["mime_type"]),
                filename=str(stored["original_filename"]),
                audit=lambda action, yielded, status: _media_audit_callback(request, action, yielded, status),
            )
    if media_storage_mode() == "database-only":
        raise HTTPException(404, "예제 영상 blob을 찾을 수 없습니다.")
    scene = DROP_VIDEO_DEMO_BY_ID.get(video_id)
    if scene is None:
        raise HTTPException(404, "허용된 예제 영상을 찾을 수 없습니다.")
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "load_case", "loadcase-drop-bottom-001", conn=conn)
    source_root = DROP_VIDEO_SOURCE_DIR.resolve()
    path = (source_root / scene.filename).resolve()
    if path.parent != source_root or not path.is_file():
        raise HTTPException(404, "예제 영상 파일을 찾을 수 없습니다.")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/drop-videos/{video_id}/download", operation_id="download_drop_video")
@app.head("/api/drop-videos/{video_id}/download", include_in_schema=False)
def download_drop_video(video_id: str, request: Request) -> Response:
    with connect() as conn:
        stored = get_drop_video(conn, video_id)
        if stored:
            require_resource_permission(request, PROJECT_DATA_VIEW, "drop_video", video_id, conn=conn)
            blob = get_blob(conn, str(stored["blob_id"]))
            if blob is None:
                raise HTTPException(404, "예제 영상 blob을 찾을 수 없습니다.")
            return build_media_response(
                request,
                blob=blob,
                mime_type=str(stored["mime_type"]),
                filename=str(stored["original_filename"]),
                download=True,
                audit=lambda action, yielded, status: _media_audit_callback(request, action, yielded, status),
            )
    if media_storage_mode() == "database-only":
        raise HTTPException(404, "예제 영상 blob을 찾을 수 없습니다.")
    scene = DROP_VIDEO_DEMO_BY_ID.get(video_id)
    if scene is None:
        raise HTTPException(404, "허용된 예제 영상을 찾을 수 없습니다.")
    with connect() as conn:
        require_resource_permission(request, PROJECT_DATA_VIEW, "load_case", "loadcase-drop-bottom-001", conn=conn)
    source_root = DROP_VIDEO_SOURCE_DIR.resolve()
    path = (source_root / scene.filename).resolve()
    if path.parent != source_root or not path.is_file():
        raise HTTPException(404, "예제 영상 파일을 찾을 수 없습니다.")
    return FileResponse(path, media_type="video/mp4", filename=scene.filename)


@app.post("/api/requests/{request_id}/load-cases", status_code=201)
def create_load_case(request_id: str, payload: LoadCaseCreate, request: Request) -> dict[str, Any]:
    load_case_id = f"loadcase-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        require_resource_permission(request, REQUEST_EDIT, "request", request_id, conn=conn)
        if conn.execute("SELECT id FROM analysis_requests WHERE id = ?", [request_id]).fetchone() is None:
            raise HTTPException(404, "해석 의뢰를 찾을 수 없습니다.")
        conn.execute(
            "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            [load_case_id, request_id, payload.name.strip(), payload.analysis_type, "READY", json.dumps(payload.parameters, ensure_ascii=False), now],
        )
    return {"id": load_case_id, "request_id": request_id, "name": payload.name.strip(), "analysis_type": payload.analysis_type, "status": "READY", "parameters": payload.parameters, "created_at": now}


app.include_router(result_ingestion_router)


def _legacy_asset_path(file_path: str) -> Path:
    assets_root = (Path(__file__).resolve().parents[1] / "assets").resolve()
    relative_path = Path(file_path)
    if relative_path.parts and relative_path.parts[0].lower() == "assets":
        relative_path = Path(*relative_path.parts[1:])
    path = (assets_root / relative_path).resolve()
    if assets_root not in path.parents or not path.is_file():
        raise HTTPException(404, "결과 미디어 파일을 찾을 수 없습니다.")
    return path


def _result_asset_response(asset_id: str, request: Request, *, download: bool) -> Response:
    with connect() as conn:
        item = get_media_asset(conn, asset_id)
        if not item:
            raise HTTPException(404, "결과 미디어를 찾을 수 없습니다.")
        require_resource_permission(request, PROJECT_DATA_VIEW, "media_asset", asset_id, conn=conn)
        if item.get("blob_id"):
            blob = get_blob(conn, str(item["blob_id"]))
            if blob is None:
                raise HTTPException(404, "결과 미디어 blob을 찾을 수 없습니다.")
            filename = item.get("original_filename") or Path(str(item.get("file_path") or "download")).name
            return build_media_response(
                request,
                blob=blob,
                mime_type=str(item.get("mime_type") or "application/octet-stream"),
                filename=str(filename),
                download=download,
                audit=lambda action, yielded, status: _media_audit_callback(request, action, yielded, status),
            )
        if media_storage_mode() == "database-only":
            raise HTTPException(404, "결과 미디어 blob을 찾을 수 없습니다.")
        path = _legacy_asset_path(str(item.get("file_path") or ""))
        return FileResponse(path, media_type=str(item.get("mime_type") or "application/octet-stream"), filename=path.name if download else None)


@app.get("/api/assets/{asset_id}", operation_id="get_result_asset")
@app.head("/api/assets/{asset_id}", include_in_schema=False)
def get_result_asset(asset_id: str, request: Request) -> Response:
    return _result_asset_response(asset_id, request, download=False)


@app.get("/api/assets/{asset_id}/download", operation_id="download_result_asset")
@app.head("/api/assets/{asset_id}/download", include_in_schema=False)
def download_result_asset(asset_id: str, request: Request) -> Response:
    return _result_asset_response(asset_id, request, download=True)


@app.get("/api/load-cases/{load_case_id}/overview")
def get_load_case_overview(
    load_case_id: str,
    request: Request,
    run_id: str | None = Query(default=None, min_length=3, max_length=120),
) -> dict[str, Any]:
    def authorize_product_information() -> object:
        try:
            return require_resource_permission(
                request,
                PROJECT_DATA_VIEW,
                "load_case",
                load_case_id,
            )
        except HTTPException as error:
            if error.status_code == 404:
                raise HTTPException(404, "하중 경우를 찾을 수 없습니다.") from error
            raise

    product_information = list_product_information(
        load_case_id,
        authorize_product_information,
        SQLProductInformationRepositoryProvider(),
    )
    with connect() as conn:
        load_case_data = rows(
            conn.execute(
                """
                SELECT lc.*, ar.id AS request_id, ar.title AS request_title,
                       p.id AS project_id, p.name AS project_name, p.product_name
                FROM load_cases lc
                JOIN analysis_requests ar ON ar.id = lc.request_id
                JOIN projects p ON p.id = ar.project_id
                WHERE lc.id = ?
                """,
                [load_case_id],
            )
        )
        if not load_case_data:
            raise HTTPException(404, "하중 경우를 찾을 수 없습니다.")
        load_case = load_case_data[0]
        run = conn.execute(
            "SELECT id FROM analysis_runs WHERE load_case_id = ? AND (? IS NULL OR id = ?) ORDER BY run_no DESC LIMIT 1",
            [load_case_id, run_id, run_id],
        ).fetchone()
        if run_id and run is None:
            raise HTTPException(404, "선택한 Run이 이 하중 경우에 존재하지 않습니다.")
        if run is None:
            load_case["parameters"] = json_value(load_case.pop("parameters_json"))
            return {
                "load_case": load_case,
                "run": None,
                "template_execution": None,
                "overall_verdict": "NO_DATA",
                "analysis_verdicts": {"open_cell": "NO_DATA", "chassis_rear": "NO_DATA"},
                "threshold": None,
                "product_information": product_information,
                "scalar_results": [],
                "time_series": [],
                "curves": [],
                "result_locations": [],
                "notes": [],
                "media": [],
            }
        run_id = run[0]
        scalar_results = rows(
            conn.execute(
                """
                SELECT sr.*, COALESCE(vd.result_group, 'CUSTOM') AS result_group
                FROM scalar_results sr
                LEFT JOIN variable_definitions vd
                  ON vd.load_case_id = ? AND vd.variable_key = sr.variable_key
                WHERE sr.analysis_run_id = ? ORDER BY sr.display_name
                """,
                [load_case_id, run_id],
            )
        )
        time_series = rows(
            conn.execute(
                """
                SELECT ts.*, COALESCE(vd.result_group, 'CUSTOM') AS result_group
                FROM time_series_results ts
                LEFT JOIN variable_definitions vd
                  ON vd.load_case_id = ? AND vd.variable_key = ts.variable_key
                WHERE ts.analysis_run_id = ? ORDER BY ts.time_value, ts.variable_key
                """,
                [load_case_id, run_id],
            )
        )
        curves = rows(conn.execute(
            """
            SELECT cr.*, COALESCE(vd.result_group, 'CUSTOM') AS result_group
            FROM curve_results cr
            LEFT JOIN variable_definitions vd
              ON vd.load_case_id = ? AND vd.variable_key = cr.variable_key
            WHERE cr.analysis_run_id = ? ORDER BY cr.display_name, cr.series_key
            """,
            [load_case_id, run_id],
        ))
        result_locations = rows(conn.execute("SELECT * FROM result_locations WHERE analysis_run_id = ? ORDER BY variable_key", [run_id]))
        notes = rows(conn.execute("SELECT * FROM qualitative_notes WHERE analysis_run_id = ? ORDER BY created_at DESC", [run_id]))
        media = rows(conn.execute("SELECT * FROM media_assets WHERE analysis_run_id = ?", [run_id]))
        template = rows(
            conn.execute(
                """
                SELECT te.* FROM template_executions te
                JOIN analysis_runs run ON run.template_execution_id = te.id
                WHERE run.id = ?
                """,
                [run_id],
            )
        )

    load_case["parameters"] = json_value(load_case.pop("parameters_json"))
    for item in media:
        item["metadata"] = json_value(item.pop("metadata_json"))
        item["asset_url"] = f"/api/assets/{item['id']}"
        item["download_url"] = f"/api/assets/{item['id']}/download"
    for item in template:
        item["input"] = json_value(item.pop("input_json"))
        item["generated_model"] = json_value(item.pop("generated_model_json"))
    open_cell_results = [item for item in scalar_results if item.get("result_group") == "OPEN_CELL" and str(item.get("unit", "")).casefold() == "mpa" and "stress" in str(item.get("variable_key", "")).casefold()]
    chassis_results = [item for item in scalar_results if item.get("result_group") == "CHASSIS_REAR" and str(item.get("unit", "")).casefold() == "mm" and "permanent_deformation" in str(item.get("variable_key", "")).casefold()]
    threshold = next((item["threshold_double"] for item in open_cell_results if item["threshold_double"] is not None), None)
    if threshold is None:
        threshold = next((item["threshold_double"] for item in chassis_results if item["threshold_double"] is not None), None)
    overall_verdict: Literal["PASS", "FAIL", "NO_DATA"] = "NO_DATA"
    if scalar_results:
        overall_verdict = "FAIL" if any(item["verdict"] == "FAIL" for item in scalar_results) else "PASS"
    return {
        "load_case": load_case,
        "run": run_id,
        "template_execution": template[0] if template else None,
        "overall_verdict": overall_verdict,
        "analysis_verdicts": {
            "open_cell": "FAIL" if any(item["verdict"] == "FAIL" for item in open_cell_results) else "PASS" if open_cell_results else "NO_DATA",
            "chassis_rear": "FAIL" if any(item["verdict"] == "FAIL" for item in chassis_results) else "PASS" if chassis_results else "NO_DATA",
        },
        "threshold": threshold,
        "product_information": product_information,
        "scalar_results": scalar_results,
        "time_series": time_series,
        "curves": curves,
        "result_locations": result_locations,
        "notes": notes,
        "media": media,
    }


def _run_result_keys(conn: Any, run_id: str) -> set[str]:
    keys = {
        row[0]
        for row in conn.execute(
            """
            SELECT variable_key FROM scalar_results WHERE analysis_run_id=?
            UNION SELECT variable_key FROM time_series_results WHERE analysis_run_id=?
            UNION SELECT variable_key FROM curve_results WHERE analysis_run_id=?
            UNION SELECT variable_key FROM result_locations WHERE analysis_run_id=?
            """,
            [run_id, run_id, run_id, run_id],
        ).fetchall()
    }
    for (metadata_json,) in conn.execute("SELECT metadata_json FROM media_assets WHERE analysis_run_id=?", [run_id]).fetchall():
        metadata = json_value(metadata_json) or {}
        if isinstance(metadata, dict) and metadata.get("variable_key"):
            keys.add(str(metadata["variable_key"]))
    return keys


def _run_trust_payload(conn: Any, run_id: str, expected_load_case_id: str | None = None) -> dict[str, Any]:
    run_rows = rows(conn.execute("SELECT * FROM analysis_runs WHERE id=?", [run_id]))
    if not run_rows or (expected_load_case_id and run_rows[0]["load_case_id"] != expected_load_case_id):
        raise HTTPException(404, "해석 Run을 찾을 수 없습니다.")
    run = run_rows[0]
    latest = conn.execute("SELECT id FROM analysis_runs WHERE load_case_id=? ORDER BY run_no DESC LIMIT 1", [run["load_case_id"]]).fetchone()
    metadata_rows = rows(conn.execute("SELECT * FROM analysis_run_metadata WHERE analysis_run_id=?", [run_id]))
    metadata = metadata_rows[0] if metadata_rows else None
    if metadata:
        metadata["metadata"] = json_value(metadata.pop("metadata_json"))
    import_rows = rows(conn.execute("SELECT * FROM folder_import_jobs WHERE analysis_run_id=? ORDER BY created_at DESC LIMIT 1", [run_id]))
    import_job = import_rows[0] if import_rows else None
    if import_job:
        import_job["summary"] = json_value(import_job.pop("summary_json"))

    counts = {
        "scalar": conn.execute("SELECT count(*) FROM scalar_results WHERE analysis_run_id=?", [run_id]).fetchone()[0],
        "time_series": conn.execute("SELECT count(*) FROM time_series_results WHERE analysis_run_id=?", [run_id]).fetchone()[0],
        "curve": conn.execute("SELECT count(*) FROM curve_results WHERE analysis_run_id=?", [run_id]).fetchone()[0],
        "media": conn.execute("SELECT count(*) FROM media_assets WHERE analysis_run_id=?", [run_id]).fetchone()[0],
        "location": conn.execute("SELECT count(*) FROM result_locations WHERE analysis_run_id=?", [run_id]).fetchone()[0],
    }
    result_keys = _run_result_keys(conn, run_id)
    catalog_rows = rows(conn.execute("SELECT variable_key, display_name, unit FROM variable_definitions WHERE load_case_id=? AND is_active=true", [run["load_case_id"]]))
    catalog = {item["variable_key"]: item for item in catalog_rows}
    unmapped = sorted(result_keys - set(catalog))
    missing = sorted(set(catalog) - result_keys)

    unit_rows = conn.execute(
        """
        SELECT variable_key, unit FROM scalar_results WHERE analysis_run_id=?
        UNION SELECT variable_key, value_unit FROM time_series_results WHERE analysis_run_id=?
        UNION SELECT variable_key, y_unit FROM curve_results WHERE analysis_run_id=?
        """,
        [run_id, run_id, run_id],
    ).fetchall()
    unit_mismatches: list[dict[str, str]] = []
    for variable_key, actual_unit in unit_rows:
        expected_unit = catalog.get(variable_key, {}).get("unit")
        if expected_unit and expected_unit != "-" and actual_unit and expected_unit != actual_unit:
            mismatch = {"variable_key": variable_key, "expected": expected_unit, "actual": actual_unit}
            if mismatch not in unit_mismatches:
                unit_mismatches.append(mismatch)

    validations = rows(conn.execute("SELECT validation_type, verdict, created_at FROM validations WHERE analysis_run_id=? ORDER BY created_at DESC", [run_id]))
    checks = [
        {"code": "run_status", "label": "Run 완료 상태", "status": "PASS" if run["status"] == "COMPLETED" else "FAIL", "detail": run["status"]},
        {"code": "source_trace", "label": "적재 출처 추적", "status": "PASS" if metadata or import_job else "WARN", "detail": metadata["source_name"] if metadata else import_job["source_folder"] if import_job else "출처 메타데이터 없음"},
        {"code": "catalog_mapping", "label": "변수 카탈로그 연결", "status": "WARN" if unmapped else "PASS", "detail": f"미연결 {len(unmapped)}개" if unmapped else f"결과 변수 {len(result_keys)}개 연결"},
        {"code": "catalog_coverage", "label": "선언 변수 커버리지", "status": "WARN" if missing else "PASS", "detail": f"이 Run에 없는 선언 변수 {len(missing)}개" if missing else "선언 변수 모두 존재"},
        {"code": "unit_consistency", "label": "단위 일관성", "status": "WARN" if unit_mismatches else "PASS", "detail": f"불일치 {len(unit_mismatches)}개" if unit_mismatches else "불일치 없음"},
        {"code": "validation", "label": "Validation", "status": "FAIL" if any(item["verdict"] == "FAIL" for item in validations) else "PASS" if validations else "WARN", "detail": f"검증 {len(validations)}건" if validations else "연결된 검증 없음"},
    ]
    trust_status = "FAIL" if any(item["status"] == "FAIL" for item in checks) else "WARN" if any(item["status"] == "WARN" for item in checks) else "TRUSTED"
    completed_at = run["completed_at"]
    age_days = None
    if completed_at:
        age_days = max(0, (datetime.now(timezone.utc).replace(tzinfo=None) - completed_at).days)
    return {
        "run": run,
        "trust_status": trust_status,
        "is_latest": bool(latest and latest[0] == run_id),
        "age_days": age_days,
        "metadata": metadata,
        "import_job": import_job,
        "counts": counts,
        "coverage": {"result_variables": len(result_keys), "catalog_variables": len(catalog), "unmapped": unmapped, "missing": missing},
        "unit_mismatches": unit_mismatches,
        "validations": validations,
        "checks": checks,
    }


@app.get("/api/load-cases/{load_case_id}/runs")
def list_analysis_runs(load_case_id: str, request: Request) -> list[dict[str, Any]]:
    return list_analysis_runs_query(
        load_case_id,
        lambda: require_permission(request, PROJECT_DATA_VIEW),
        SQLAnalysisRunSummaryRepositoryProvider(),
    )


@app.get("/api/load-cases/{load_case_id}/run-comparison")
def compare_analysis_runs(
    load_case_id: str,
    baseline_run_id: str = Query(min_length=3, max_length=120),
    target_run_id: str = Query(min_length=3, max_length=120),
    variable_key: str | None = Query(default=None, max_length=120),
) -> dict[str, Any]:
    if baseline_run_id == target_run_id:
        raise HTTPException(422, "기준 Run과 대상 Run은 달라야 합니다.")
    with connect() as conn:
        run_rows = rows(conn.execute("SELECT * FROM analysis_runs WHERE load_case_id=? AND id IN (?, ?) ORDER BY run_no", [load_case_id, baseline_run_id, target_run_id]))
        if len(run_rows) != 2:
            raise HTTPException(404, "선택한 Run을 하중 경우에서 찾을 수 없습니다.")
        run_map = {item["id"]: item for item in run_rows}
        scalar_rows = rows(conn.execute("SELECT * FROM scalar_results WHERE analysis_run_id IN (?, ?)", [baseline_run_id, target_run_id]))
        scalars = {run_id: {item["variable_key"]: item for item in scalar_rows if item["analysis_run_id"] == run_id} for run_id in (baseline_run_id, target_run_id)}
        comparison: list[dict[str, Any]] = []
        for key in sorted(set(scalars[baseline_run_id]) | set(scalars[target_run_id])):
            baseline = scalars[baseline_run_id].get(key)
            target = scalars[target_run_id].get(key)
            baseline_value = (baseline or {}).get("value_double")
            if baseline_value is None:
                baseline_value = (baseline or {}).get("value_integer")
            target_value = (target or {}).get("value_double")
            if target_value is None:
                target_value = (target or {}).get("value_integer")
            comparable = bool(baseline and target and baseline_value is not None and target_value is not None and baseline.get("unit") == target.get("unit"))
            delta = float(target_value - baseline_value) if comparable else None
            delta_percent = (delta / abs(float(baseline_value)) * 100) if comparable and baseline_value not in (None, 0) else None
            if baseline is None:
                change = "ADDED"
            elif target is None:
                change = "REMOVED"
            elif not comparable:
                change = "NOT_COMPARABLE"
            elif baseline.get("verdict") == "PASS" and target.get("verdict") == "FAIL":
                change = "REGRESSION"
            elif baseline.get("verdict") == "FAIL" and target.get("verdict") == "PASS":
                change = "IMPROVED"
            else:
                change = "UNCHANGED"
            comparison.append({
                "variable_key": key,
                "display_name": (target or baseline or {}).get("display_name", key),
                "unit": (target or baseline or {}).get("unit"),
                "baseline_value": baseline_value,
                "target_value": target_value,
                "baseline_verdict": (baseline or {}).get("verdict"),
                "target_verdict": (target or {}).get("verdict"),
                "delta": delta,
                "delta_percent": delta_percent,
                "change": change,
                "comparable": comparable,
            })

        series_rows = rows(conn.execute(
            """
            SELECT variable_key, min(display_name) AS display_name, min(value_unit) AS value_unit,
                   count(DISTINCT analysis_run_id) AS run_count
            FROM time_series_results WHERE analysis_run_id IN (?, ?)
            GROUP BY variable_key HAVING count(DISTINCT analysis_run_id)=2 ORDER BY variable_key
            """,
            [baseline_run_id, target_run_id],
        ))
        available_series = [{"variable_key": item["variable_key"], "display_name": item["display_name"], "unit": item["value_unit"]} for item in series_rows]
        selected_key = variable_key if any(item["variable_key"] == variable_key for item in available_series) else available_series[0]["variable_key"] if available_series else None
        series_payload = None
        if selected_key:
            points = rows(conn.execute("SELECT analysis_run_id, time_value, value, time_unit, value_unit FROM time_series_results WHERE analysis_run_id IN (?, ?) AND variable_key=? ORDER BY time_value", [baseline_run_id, target_run_id, selected_key]))
            merged: dict[float, dict[str, Any]] = {}
            for point in points:
                item = merged.setdefault(float(point["time_value"]), {"time_value": point["time_value"], "time_unit": point["time_unit"], "baseline_value": None, "target_value": None})
                item["baseline_value" if point["analysis_run_id"] == baseline_run_id else "target_value"] = point["value"]
            descriptor = next(item for item in available_series if item["variable_key"] == selected_key)
            series_payload = {**descriptor, "points": list(merged.values())}

        summary = {status.lower(): sum(1 for item in comparison if item["change"] == status) for status in ("REGRESSION", "IMPROVED", "UNCHANGED")}
        summary["comparable"] = sum(1 for item in comparison if item["comparable"])
        return {
            "baseline_run": run_map[baseline_run_id],
            "target_run": run_map[target_run_id],
            "summary": summary,
            "scalar_comparison": comparison,
            "available_series": available_series,
            "time_series": series_payload,
        }


@app.get("/api/analysis-runs/{run_id}/trust")
def get_analysis_run_trust(run_id: str) -> dict[str, Any]:
    with connect() as conn:
        return _run_trust_payload(conn, run_id)


def _review_items(conn: Any, run_id: str, annotation_id: str | None = None) -> list[dict[str, Any]]:
    conditions = "a.analysis_run_id=?"
    parameters: list[Any] = [run_id]
    if annotation_id:
        conditions += " AND a.id=?"
        parameters.append(annotation_id)
    return rows(conn.execute(
        f"""
        SELECT a.id, a.bookmark_id, a.analysis_run_id, a.variable_key, b.title, b.time_value,
               b.entity_type, b.entity_id, a.body, a.review_status, a.created_by, a.created_at, a.updated_at
        FROM review_annotations a JOIN result_bookmarks b ON b.id=a.bookmark_id
        WHERE {conditions} ORDER BY a.updated_at DESC
        """,
        parameters,
    ))


@app.get("/api/analysis-runs/{run_id}/review-items")
def list_review_items(run_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM analysis_runs WHERE id=?", [run_id]).fetchone():
            raise HTTPException(404, "해석 Run을 찾을 수 없습니다.")
        return _review_items(conn, run_id)


@app.post("/api/analysis-runs/{run_id}/review-items", status_code=201)
def create_review_item(run_id: str, payload: ReviewItemCreate, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    with connect() as conn:
        bookmark_id, annotation_id = f"bookmark-{uuid4().hex[:12]}", f"review-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute("BEGIN TRANSACTION")
        try:
            context = require_resource_permission(request, RESULT_REVIEW, "run", run_id, conn=conn)
            if not conn.execute("SELECT 1 FROM analysis_runs WHERE id=?", [run_id]).fetchone():
                raise HTTPException(404, "해석 Run을 찾을 수 없습니다.")
            if payload.variable_key and payload.variable_key not in _run_result_keys(conn, run_id):
                raise HTTPException(422, "선택한 변수는 이 Run의 결과에 없습니다.")
            if payload.entity_type and not payload.entity_id:
                raise HTTPException(422, "엔티티 유형을 지정하면 엔티티 ID도 필요합니다.")
            conn.execute(
                """
                INSERT INTO result_bookmarks
                    (id, analysis_run_id, variable_key, time_value, entity_type, entity_id, title, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [bookmark_id, run_id, payload.variable_key, payload.time_value, payload.entity_type, payload.entity_id, payload.title.strip(), principal.display_name, now],
            )
            conn.execute(
                """
                INSERT INTO review_annotations
                    (id, bookmark_id, analysis_run_id, variable_key, body, review_status, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [annotation_id, bookmark_id, run_id, payload.variable_key, payload.body.strip(), payload.review_status, principal.display_name, now, now],
            )
            write_audit_event(request=request, principal=principal, status_code=201, action="RESULT_REVIEW_CREATED", detail={"project_id": context.project_id, "run_id": run_id, "review_item_id": annotation_id}, connection=conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return _review_items(conn, run_id, annotation_id)[0]


@app.patch("/api/review-items/{annotation_id}")
def update_review_item(annotation_id: str, payload: ReviewItemUpdate, request: Request) -> dict[str, Any]:
    principal = request.state.principal
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            context = require_resource_permission(request, RESULT_REVIEW, "review_item", annotation_id, conn=conn)
            current = conn.execute("SELECT analysis_run_id, body, review_status FROM review_annotations WHERE id=?", [annotation_id]).fetchone()
            if not current:
                raise HTTPException(404, "검토 의견을 찾을 수 없습니다.")
            conn.execute(
                "UPDATE review_annotations SET body=?, review_status=?, updated_at=? WHERE id=?",
                [payload.body.strip() if payload.body else current[1], payload.review_status, datetime.now(timezone.utc).replace(tzinfo=None), annotation_id],
            )
            write_audit_event(request=request, principal=principal, status_code=200, action="RESULT_REVIEW_UPDATED", detail={"project_id": context.project_id, "run_id": current[0], "review_item_id": annotation_id, "old_status": current[2], "new_status": payload.review_status}, connection=conn)
            result = _review_items(conn, current[0], annotation_id)[0]
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise


@app.get("/api/projects/{project_id}/quality-thresholds")
def get_quality_thresholds(project_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(
            conn.execute(
                "SELECT * FROM quality_thresholds WHERE project_id = ? ORDER BY analysis_key, criterion_key",
                [project_id],
            )
        )


def _update_quality_threshold(
    criterion_key: str,
    payload: QualityThresholdUpdate,
    request: Request,
    expected_project_id: str | None = None,
) -> dict[str, Any]:
    with connect() as conn:
        if expected_project_id is not None:
            criteria = conn.execute(
                "SELECT project_id, unit, threshold_double FROM quality_thresholds WHERE project_id = ? AND criterion_key = ?",
                [expected_project_id, criterion_key],
            ).fetchall()
        else:
            criteria = conn.execute(
                "SELECT project_id, unit, threshold_double FROM quality_thresholds WHERE criterion_key = ? ORDER BY project_id",
                [criterion_key],
            ).fetchall()
        if not criteria:
            raise HTTPException(404, "품질 판정 기준을 찾을 수 없습니다.")
        if expected_project_id is None and len(criteria) > 1:
            raise HTTPException(409, "프로젝트 범위 품질 기준 URL을 사용해야 합니다.")
        project_id, unit, old_threshold = criteria[0]
        require_permission(request, PROJECT_THRESHOLD_MANAGE, project_id, conn=conn)
        principal = request.state.principal
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                """
                UPDATE quality_thresholds
                SET threshold_double = ?, updated_by = ?, updated_at = ?
                WHERE project_id = ? AND criterion_key = ?
                """,
                [payload.threshold_double, principal.display_name, now, project_id, criterion_key],
            )
            if criterion_key == "chassis_rear_permanent_deformation_mm":
                conn.execute(
                    """
                    UPDATE scalar_results
                    SET threshold_double = ?,
                        verdict = CASE WHEN value_double >= ? THEN 'FAIL' ELSE 'PASS' END
                    WHERE lower(unit) = 'mm'
                      AND variable_key LIKE '%permanent_deformation%'
                      AND analysis_run_id IN (
                          SELECT run.id
                          FROM analysis_runs run
                          JOIN load_cases lc ON lc.id = run.load_case_id
                          JOIN analysis_requests ar ON ar.id = lc.request_id
                          JOIN variable_definitions vd
                            ON vd.load_case_id = lc.id
                           AND vd.variable_key = scalar_results.variable_key
                          WHERE ar.project_id = ? AND vd.result_group = 'CHASSIS_REAR'
                      )
                    """,
                    [payload.threshold_double, payload.threshold_double, project_id],
                )
            elif criterion_key == "open_cell_stress_mpa":
                conn.execute(
                    """
                    UPDATE scalar_results
                    SET threshold_double = ?,
                        verdict = CASE WHEN value_double >= ? THEN 'FAIL' ELSE 'PASS' END
                    WHERE lower(unit) = 'mpa'
                      AND lower(variable_key) LIKE '%stress%'
                      AND analysis_run_id IN (
                          SELECT run.id
                          FROM analysis_runs run
                          JOIN load_cases lc ON lc.id = run.load_case_id
                          JOIN analysis_requests ar ON ar.id = lc.request_id
                          JOIN variable_definitions vd
                            ON vd.load_case_id = lc.id
                           AND vd.variable_key = scalar_results.variable_key
                          WHERE ar.project_id = ? AND vd.result_group = 'OPEN_CELL'
                      )
                    """,
                    [payload.threshold_double, payload.threshold_double, project_id],
                )
            write_audit_event(
                request=request,
                principal=principal,
                status_code=200,
                action="PROJECT_THRESHOLD_CHANGED",
                detail={"project_id": project_id, "criterion_key": criterion_key, "old_value": old_threshold, "new_value": payload.threshold_double, "unit": unit},
                connection=conn,
            )
            conn.execute("COMMIT")
            updated_threshold = rows(
                conn.execute(
                    "SELECT * FROM quality_thresholds WHERE project_id=? AND criterion_key=?",
                    [project_id, criterion_key],
                )
            )[0]
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return updated_threshold


@app.put("/api/projects/{project_id}/quality-thresholds/{criterion_key}")
def update_project_quality_threshold(project_id: str, criterion_key: str, payload: QualityThresholdUpdate, request: Request) -> dict[str, Any]:
    return _update_quality_threshold(criterion_key, payload, request, expected_project_id=project_id)


@app.put("/api/quality-thresholds/{criterion_key}", deprecated=True)
def update_quality_threshold(criterion_key: str, payload: QualityThresholdUpdate, request: Request) -> dict[str, Any]:
    return _update_quality_threshold(criterion_key, payload, request)


@app.get("/api/requests/{request_id}/workflow")
def get_workflow(request_id: str) -> dict[str, Any]:
    with connect() as conn:
        request_data = rows(conn.execute("SELECT * FROM analysis_requests WHERE id = ?", [request_id]))
        if not request_data:
            raise HTTPException(404, "해석 의뢰를 찾을 수 없습니다.")
        monitoring = request_monitoring_summary(conn, request_id)
    request_data[0]["status"] = monitoring["status"]
    return {
        "request": request_data[0],
        "steps": monitoring["steps"],
        "progress": monitoring["progress"],
        "current_step": monitoring["current_step"],
        "current_step_id": monitoring["current_step_id"],
        "completed_count": monitoring["completed_count"],
        "total_count": monitoring["total_count"],
        "work_plan": monitoring["work_plan"],
        "latest_demo_run": monitoring["latest_demo_run"],
        "request_type_assignment": monitoring["request_type_assignment"],
    }


@app.get("/api/workflows")
def get_workflows() -> list[dict[str, Any]]:
    with connect() as conn:
        requests = rows(
            conn.execute(
                """
                SELECT ar.*, p.name AS project_name, p.product_name,
                       COALESCE(min(lc.analysis_type), 'UNASSIGNED') AS category,
                       COALESCE(min(lc.name), '하중 경우 미지정') AS load_case_name
                FROM analysis_requests ar
                JOIN projects p ON p.id = ar.project_id
                LEFT JOIN load_cases lc ON lc.request_id = ar.id
                GROUP BY ar.id, ar.project_id, ar.title, ar.status, ar.owner, ar.owner_user_id, ar.requested_at,
                         ar.due_at, ar.overall_note, p.name, p.product_name
                ORDER BY ar.requested_at DESC
                """
            )
        )
        result = []
        for request in requests:
            monitoring = request_monitoring_summary(conn, request["id"])
            request["status"] = monitoring["status"]
            result.append(
                {
                    "request": request,
                    "steps": monitoring["steps"],
                    "progress": monitoring["progress"],
                    "current_step": monitoring["current_step"],
                    "current_step_id": monitoring["current_step_id"],
                    "completed_count": monitoring["completed_count"],
                    "total_count": monitoring["total_count"],
                    "work_plan": monitoring["work_plan"],
                    "latest_demo_run": monitoring["latest_demo_run"],
                    "request_type_assignment": monitoring["request_type_assignment"],
                }
            )
    return result


@app.put("/api/requests/{request_id}/workflow-steps")
def replace_workflow_steps(request_id: str, payload: WorkflowStepsReplace, request: Request) -> list[dict[str, Any]]:
    with connect() as conn:
        require_resource_permission(request, WORKFLOW_EDIT, "request", request_id, conn=conn)
        request_row = conn.execute("SELECT project_id FROM analysis_requests WHERE id = ?", [request_id]).fetchone()
        if not request_row:
            raise HTTPException(404, "해석 의뢰를 찾을 수 없습니다.")
        project_id = str(request_row[0])
        if conn.execute("SELECT 1 FROM request_work_plans WHERE request_id = ?", [request_id]).fetchone():
            raise HTTPException(
                409,
                detail={"code": "WORK_PLAN_IMMUTABLE", "request_id": request_id},
            )
        existing = rows(conn.execute("SELECT * FROM request_steps WHERE request_id = ?", [request_id]))
        existing_by_id = {item["id"]: item for item in existing}
        submitted_ids = [item.id for item in payload.steps if item.id]
        if len(submitted_ids) != len(set(submitted_ids)):
            raise HTTPException(400, "단계 ID가 중복되었습니다.")
        unknown = [step_id for step_id in submitted_ids if step_id not in existing_by_id]
        if unknown:
            raise HTTPException(400, "다른 의뢰의 단계이거나 존재하지 않는 단계가 포함되어 있습니다.")
        now = datetime.now(timezone.utc)
        try:
            conn.execute("BEGIN TRANSACTION")
            if submitted_ids:
                placeholders = ",".join("?" for _ in submitted_ids)
                conn.execute(
                    f"DELETE FROM request_steps WHERE request_id = ? AND id NOT IN ({placeholders})",
                    [request_id, *submitted_ids],
                )
            else:
                conn.execute("DELETE FROM request_steps WHERE request_id = ?", [request_id])
            for sequence_no, step in enumerate(payload.steps, start=1):
                name = step.name.strip()
                assignee = resolve_project_assignee(conn, project_id, step.owner_user_id)
                if len(name) < 2:
                    raise HTTPException(400, "단계 이름과 담당자를 확인해 주세요.")
                if step.id:
                    conn.execute(
                        """
                        UPDATE request_steps
                        SET sequence_no = ?, name = ?, status = ?, owner = ?, owner_user_id = ?,
                            progress = ?, is_optional = ?, note = ?
                        WHERE id = ? AND request_id = ?
                        """,
                        [sequence_no, name, step.status, assignee.display_name, assignee.user_id,
                         step.progress, step.is_optional, step.note.strip(), step.id, request_id],
                    )
                else:
                    step_id = f"step-{uuid4().hex[:12]}"
                    conn.execute(
                        """
                        INSERT INTO request_steps (
                            id, request_id, sequence_no, name, status, owner, owner_user_id, planned_start, planned_end,
                            actual_start, actual_end, progress, blocked_reason, note, is_optional
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, ?, ?)
                        """,
                        [step_id, request_id, sequence_no, name, step.status, assignee.display_name,
                         assignee.user_id, now, now + timedelta(days=7), step.progress,
                         step.note.strip(), step.is_optional],
                    )
            updated_steps = rows(conn.execute("SELECT * FROM request_steps WHERE request_id = ? ORDER BY sequence_no", [request_id]))
            sync_request_status(conn, request_id, updated_steps)
            write_audit_event(
                request=request,
                principal=request.state.principal,
                status_code=200,
                action="WORKFLOW_STEPS_REPLACED",
                detail={"project_id": project_id, "request_id": request_id, "step_count": len(payload.steps)},
                connection=conn,
            )
            conn.execute("COMMIT")
        except HTTPException:
            conn.execute("ROLLBACK")
            raise
        except Exception as exc:
            conn.execute("ROLLBACK")
            raise HTTPException(500, f"진행 단계를 저장하지 못했습니다: {exc}") from exc
        return rows(conn.execute("SELECT * FROM request_steps WHERE request_id = ? ORDER BY sequence_no", [request_id]))


@app.patch("/api/workflow-steps/{step_id}")
def update_workflow_step(step_id: str, payload: WorkflowStepUpdate, request: Request) -> dict[str, Any]:
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(400, "단계 이름은 두 글자 이상이어야 합니다.")
    with connect() as conn:
        existing = rows(conn.execute("SELECT * FROM request_steps WHERE id = ?", [step_id]))
        if not existing:
            planned_item = conn.execute(
                "SELECT request_id FROM request_work_items WHERE id = ?",
                [step_id],
            ).fetchone()
            if planned_item:
                require_resource_permission(request, WORKFLOW_EDIT, "work_item", step_id, conn=conn)
                raise HTTPException(
                    409,
                    detail={"code": "WORK_PLAN_IMMUTABLE", "request_id": planned_item[0]},
                )
            raise HTTPException(404, "작업 단계를 찾을 수 없습니다.")
        require_resource_permission(request, WORKFLOW_EDIT, "workflow_step", step_id, conn=conn)
        current = existing[0]
        if conn.execute("SELECT 1 FROM request_work_plans WHERE request_id = ?", [current["request_id"]]).fetchone():
            raise HTTPException(
                409,
                detail={"code": "WORK_PLAN_IMMUTABLE", "request_id": current["request_id"]},
            )
        request_row = conn.execute(
            "SELECT project_id FROM analysis_requests WHERE id=?",
            [current["request_id"]],
        ).fetchone()
        owner, owner_user_id = current["owner"], current.get("owner_user_id")
        if payload.owner_user_id is not None:
            assignee = resolve_project_assignee(conn, str(request_row[0]), payload.owner_user_id)
            owner, owner_user_id = assignee.display_name, assignee.user_id
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                """
                UPDATE request_steps
                SET name = ?, status = ?, owner = ?, owner_user_id = ?, progress = ?, is_optional = ?, note = ?
                WHERE id = ?
                """,
                [
                    name,
                    payload.status or current["status"],
                    owner,
                    owner_user_id,
                    payload.progress if payload.progress is not None else current["progress"],
                    payload.is_optional if payload.is_optional is not None else current["is_optional"],
                    payload.note.strip() if payload.note is not None else current["note"],
                    step_id,
                ],
            )
            write_audit_event(
                request=request,
                principal=request.state.principal,
                status_code=200,
                action="WORKFLOW_STEP_UPDATED",
                detail={"project_id": str(request_row[0]), "request_id": current["request_id"], "step_id": step_id, "owner_user_id": owner_user_id},
                connection=conn,
            )
            updated = rows(conn.execute("SELECT * FROM request_steps WHERE id = ?", [step_id]))
            sync_request_status(conn, current["request_id"])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    if not updated:
        raise HTTPException(404, "작업 단계를 찾을 수 없습니다.")
    return updated[0]


@app.get("/api/load-cases/{load_case_id}/variables")
def get_variables(load_case_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM load_cases WHERE id = ?", [load_case_id]).fetchone():
            raise HTTPException(404, "하중 경우를 찾을 수 없습니다.")
        return VariableCatalogRepository(conn).list_for_load_case(load_case_id)


def _catalog_error(exc: Exception) -> HTTPException:
    messages = {
        "VARIABLE_EXISTS": (409, "같은 변수 키가 이미 존재합니다."),
        "VARIABLE_NOT_FOUND": (404, "변수를 찾을 수 없습니다."),
        "LOAD_CASE_NOT_FOUND": (404, "하중 경우를 찾을 수 없습니다."),
        "INVALID_CATALOG_OPTIONS": (422, "데이터 유형에 허용되지 않은 위젯 또는 집계 방식입니다."),
        "NUMBER_THRESHOLD_REQUIRED": (422, "숫자 변수에는 판정 기준값이 필요합니다."),
    }
    status, message = messages.get(str(exc), (422, str(exc)))
    return HTTPException(status, message)


@app.post("/api/load-cases/{load_case_id}/variables", status_code=201)
def create_variable(load_case_id: str, payload: VariableCreate, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, PROJECT_VARIABLE_MANAGE, "load_case", load_case_id, conn=conn)
        try:
            return VariableCatalogRepository(conn).create(load_case_id, payload.model_dump())
        except (ValueError, LookupError) as exc:
            raise _catalog_error(exc) from exc


@app.put("/api/load-cases/{load_case_id}/variables/{variable_key}")
def update_variable(load_case_id: str, variable_key: str, payload: VariableUpdate, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, PROJECT_VARIABLE_MANAGE, "load_case", load_case_id, conn=conn)
        try:
            return VariableCatalogRepository(conn).update(load_case_id, variable_key, payload.model_dump())
        except (ValueError, LookupError) as exc:
            raise _catalog_error(exc) from exc


@app.delete("/api/load-cases/{load_case_id}/variables/{variable_key}")
def delete_variable(
    load_case_id: str,
    variable_key: str,
    request: Request,
    updated_by: str = Query(default="관리자", min_length=2, max_length=60),
) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, PROJECT_VARIABLE_MANAGE, "load_case", load_case_id, conn=conn)
        repository = VariableCatalogRepository(conn)
        references = repository.dashboard_references(load_case_id, variable_key)
        if references:
            raise HTTPException(409, detail={"message": "대시보드에서 사용 중인 변수는 삭제할 수 없습니다.", "dashboard_ids": references})
        try:
            repository.deactivate(load_case_id, variable_key, updated_by)
        except LookupError as exc:
            raise _catalog_error(exc) from exc
    return {"status": "deactivated", "variable_key": variable_key}


def _get_project_workspace_layout(
    project_id: str,
    layout_kind: Literal["portfolio", "workflow"],
    request: Request,
) -> dict[str, Any]:
    if layout_kind not in WORKSPACE_LAYOUT_KINDS:
        raise HTTPException(404, "지원하지 않는 레이아웃 종류입니다.")
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone():
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        require_permission(request, PROJECT_DATA_VIEW, project_id, conn=conn)
        stored = conn.execute(
            """
            SELECT project_id, layout_kind, version, definition_json, updated_by, updated_at
            FROM project_workspace_layouts WHERE project_id=? AND layout_kind=?
            """,
            [project_id, layout_kind],
        ).fetchone()
    if not stored:
        raise HTTPException(404, "저장된 레이아웃이 없습니다.")
    return {"project_id": stored[0], "layout_kind": stored[1], "version": stored[2], "definition": json_value(stored[3]), "updated_by": stored[4], "updated_at": stored[5]}


@app.get("/api/projects/{project_id}/workspace-layouts/{layout_kind}", response_model=WorkspaceLayoutResponse)
def get_project_workspace_layout(
    project_id: str,
    layout_kind: Literal["portfolio", "workflow"],
    request: Request,
) -> dict[str, Any]:
    return _get_project_workspace_layout(project_id, layout_kind, request)


@app.get("/api/workspace-layouts/{layout_kind}", response_model=WorkspaceLayoutResponse, deprecated=True)
def get_workspace_layout(
    layout_kind: Literal["portfolio", "workflow"],
    request: Request,
    project_id: str = Query(min_length=3, max_length=120),
) -> dict[str, Any]:
    return _get_project_workspace_layout(project_id, layout_kind, request)


def _save_project_workspace_layout(
    project_id: str,
    layout_kind: Literal["portfolio", "workflow"],
    payload: WorkspaceLayoutUpdate,
    request: Request,
) -> dict[str, Any]:
    definition = _validated_workspace_layout(layout_kind, payload.definition)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    encoded = json.dumps(definition, ensure_ascii=False)
    principal = request.state.principal
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            if not conn.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone():
                raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
            require_permission(request, PROJECT_LAYOUT_EDIT, project_id, conn=conn)
            existing = conn.execute(
                "SELECT version FROM project_workspace_layouts WHERE project_id=? AND layout_kind=?",
                [project_id, layout_kind],
            ).fetchone()
            if not existing:
                raise HTTPException(404, "저장된 레이아웃이 없습니다.")
            version = int(existing[0]) + 1
            conn.execute(
                """
                UPDATE project_workspace_layouts
                SET version=?, definition_json=?, updated_by=?, updated_at=?
                WHERE project_id=? AND layout_kind=?
                """,
                [version, encoded, principal.display_name, now, project_id, layout_kind],
            )
            conn.execute(
                """
                INSERT INTO project_workspace_layout_versions
                    (project_id, layout_kind, version, definition_json, created_by, created_at, is_valid)
                VALUES (?, ?, ?, ?, ?, ?, true)
                """,
                [project_id, layout_kind, version, encoded, principal.display_name, now],
            )
            write_audit_event(
                request=request,
                principal=principal,
                status_code=200,
                action="PROJECT_WORKSPACE_LAYOUT_UPDATED",
                detail={"project_id": project_id, "layout_kind": layout_kind, "version": version},
                connection=conn,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"project_id": project_id, "layout_kind": layout_kind, "version": version, "definition": definition, "updated_by": principal.display_name, "updated_at": now}


@app.put("/api/projects/{project_id}/workspace-layouts/{layout_kind}", response_model=WorkspaceLayoutResponse)
def save_project_workspace_layout(
    project_id: str,
    layout_kind: Literal["portfolio", "workflow"],
    payload: WorkspaceLayoutUpdate,
    request: Request,
) -> dict[str, Any]:
    return _save_project_workspace_layout(project_id, layout_kind, payload, request)


@app.put("/api/workspace-layouts/{layout_kind}", response_model=WorkspaceLayoutResponse, deprecated=True)
def save_workspace_layout(
    layout_kind: Literal["portfolio", "workflow"],
    payload: WorkspaceLayoutUpdate,
    request: Request,
    project_id: str = Query(min_length=3, max_length=120),
) -> dict[str, Any]:
    return _save_project_workspace_layout(project_id, layout_kind, payload, request)


@app.get("/api/projects/{project_id}/workspace-layouts/{layout_kind}/versions", response_model=list[WorkspaceLayoutVersionResponse])
def get_workspace_layout_versions(
    project_id: str,
    layout_kind: Literal["portfolio", "workflow"],
    request: Request,
) -> list[dict[str, Any]]:
    if layout_kind not in WORKSPACE_LAYOUT_KINDS:
        raise HTTPException(404, "지원하지 않는 레이아웃 종류입니다.")
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", [project_id]).fetchone():
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        require_permission(request, PROJECT_DATA_VIEW, project_id, conn=conn)
        return rows(conn.execute(
            """
            SELECT project_id, layout_kind, version, created_by, created_at, is_valid
            FROM project_workspace_layout_versions
            WHERE project_id=? AND layout_kind=? ORDER BY version DESC
            """,
            [project_id, layout_kind],
        ))


app.include_router(reports_router)


@app.post("/api/report-layouts", response_model=ReportLayoutSavedResponse, status_code=201)
def create_report_layout(payload: ReportLayoutPayload, request: Request) -> dict[str, Any]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    principal = request.state.principal
    actor_name = principal.display_name
    layout_id = f"report-layout-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    definition = _validated_report_layout({**payload.definition, "id": layout_id, "name": payload.name.strip(), "description": payload.description.strip(), "version": 1})
    encoded = json.dumps(definition, ensure_ascii=False)
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "INSERT INTO report_layouts VALUES (?, ?, ?, 1, ?, false, true, ?, ?, ?)",
                [layout_id, payload.name.strip(), payload.description.strip(), encoded, now, now, actor_name],
            )
            conn.execute(
                "INSERT INTO report_layout_versions VALUES (?, 1, ?, ?, ?, true)",
                [layout_id, encoded, actor_name, now],
            )
            write_audit_event(request=request, principal=principal, status_code=201, action="REPORT_LAYOUT_CREATED", detail={"layout_id": layout_id}, connection=conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": layout_id, "name": payload.name.strip(), "description": payload.description.strip(), "version": 1, "definition": definition, "is_system": False, "updated_at": now, "updated_by": actor_name}


@app.put("/api/report-layouts/{layout_id}", response_model=ReportLayoutSavedResponse)
def update_report_layout(layout_id: str, payload: ReportLayoutPayload, request: Request) -> dict[str, Any]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    principal = request.state.principal
    actor_name = principal.display_name
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        existing = conn.execute("SELECT version, is_system FROM report_layouts WHERE id=? AND is_active=true", [layout_id]).fetchone()
        if not existing:
            raise HTTPException(404, "보고서 레이아웃을 찾을 수 없습니다.")
        version = existing[0] + 1
        definition = _validated_report_layout({**payload.definition, "id": layout_id, "name": payload.name.strip(), "description": payload.description.strip(), "version": version})
        encoded = json.dumps(definition, ensure_ascii=False)
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "UPDATE report_layouts SET name=?, description=?, version=?, definition_json=?, updated_at=?, updated_by=? WHERE id=?",
                [payload.name.strip(), payload.description.strip(), version, encoded, now, actor_name, layout_id],
            )
            conn.execute(
                "INSERT INTO report_layout_versions VALUES (?, ?, ?, ?, ?, true)",
                [layout_id, version, encoded, actor_name, now],
            )
            write_audit_event(request=request, principal=principal, status_code=200, action="REPORT_LAYOUT_UPDATED", detail={"layout_id": layout_id, "version": version}, connection=conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": layout_id, "name": payload.name.strip(), "description": payload.description.strip(), "version": version, "definition": definition, "is_system": bool(existing[1]), "updated_at": now, "updated_by": actor_name}


@app.get("/api/report-layouts/{layout_id}/versions", response_model=list[ReportLayoutVersionSummaryResponse])
def get_report_layout_versions(layout_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute(
            "SELECT layout_id, version, created_by, created_at, is_valid FROM report_layout_versions WHERE layout_id=? ORDER BY version DESC",
            [layout_id],
        ))


@app.get("/api/report-layouts/{layout_id}/versions/{version}", response_model=ReportLayoutVersionDetailResponse)
def get_report_layout_version(layout_id: str, version: int) -> dict[str, Any]:
    with connect() as conn:
        stored = conn.execute(
            "SELECT definition_json, created_by, created_at FROM report_layout_versions WHERE layout_id=? AND version=? AND is_valid=true",
            [layout_id, version],
        ).fetchone()
    if not stored:
        raise HTTPException(404, "보고서 레이아웃 버전을 찾을 수 없습니다.")
    return {"layout_id": layout_id, "version": version, "definition": json_value(stored[0]), "created_by": stored[1], "created_at": stored[2]}


@app.delete("/api/report-layouts/{layout_id}", response_model=ReportLayoutDeactivationResponse)
def delete_report_layout(layout_id: str, request: Request) -> dict[str, str]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    with connect() as conn:
        existing = conn.execute("SELECT is_system FROM report_layouts WHERE id=? AND is_active=true", [layout_id]).fetchone()
        if not existing:
            raise HTTPException(404, "보고서 레이아웃을 찾을 수 없습니다.")
        if existing[0]:
            raise HTTPException(409, "기본 레이아웃은 삭제할 수 없습니다. 수정하면 새 버전으로 보존됩니다.")
        conn.execute("UPDATE report_layouts SET is_active=false, updated_at=? WHERE id=?", [datetime.now(timezone.utc).replace(tzinfo=None), layout_id])
    return {"status": "deactivated", "id": layout_id}


PPTX_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}
PPTX_TOKEN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
PPTX_SHAPE_TAG = re.compile(r"^(VAR|TEXT|CHART|IMAGE):\s*(.+)$", re.IGNORECASE)
REPORT_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "assets" / "report-templates"


def _safe_pptx_archive(data: bytes) -> zipfile.ZipFile:
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "PPTX 템플릿은 25MB 이하여야 합니다.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, ValueError) as exc:
        raise HTTPException(422, "올바른 PPTX 압축 구조가 아닙니다.") from exc
    infos = archive.infolist()
    if len(infos) > 2500 or sum(item.file_size for item in infos) > 80 * 1024 * 1024:
        archive.close()
        raise HTTPException(422, "PPTX 압축 해제 크기 또는 파일 개수가 허용 범위를 초과합니다.")
    names = {item.filename.replace("\\", "/") for item in infos}
    if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
        archive.close()
        raise HTTPException(422, "PowerPoint 프레젠테이션 필수 파일이 없습니다.")
    for name in names:
        parts = name.split("/")
        lowered = name.lower()
        if name.startswith("/") or ".." in parts or lowered.endswith("vbaproject.bin") or "/embeddings/" in lowered or "oleobject" in lowered:
            archive.close()
            raise HTTPException(422, "매크로, OLE 또는 안전하지 않은 경로가 포함된 PPTX는 사용할 수 없습니다.")
        if lowered.endswith(".rels"):
            relation_xml = archive.read(name).decode("utf-8", errors="ignore").lower()
            if 'targetmode="external"' in relation_xml:
                archive.close()
                raise HTTPException(422, "외부 링크 관계가 포함된 PPTX는 사용할 수 없습니다.")
    return archive


def _slide_number(name: str) -> int:
    match = re.search(r"slide(\d+)\.xml$", name)
    return int(match.group(1)) if match else 0


def _shape_placeholder(shape: ET.Element) -> tuple[str, str] | None:
    name_node = shape.find("./p:nvSpPr/p:cNvPr", PPTX_NS)
    shape_name = name_node.attrib.get("name", "") if name_node is not None else ""
    name_match = PPTX_SHAPE_TAG.match(shape_name)
    if name_match:
        prefix = name_match.group(1).lower()
        token = name_match.group(2).strip()
        kind = {"var": "variable", "text": "field", "chart": "chart", "image": "image"}[prefix]
        return kind, f"{kind}:{token}"
    text = "".join(node.text or "" for node in shape.findall(".//a:t", PPTX_NS))
    token_match = PPTX_TOKEN.search(text)
    if not token_match:
        return None
    token = token_match.group(1).strip()
    prefix, separator, value = token.partition(":")
    kind = prefix.lower() if separator and prefix.lower() in {"variable", "field", "text", "chart", "image"} else "text"
    return kind, token if separator else f"text:{token}"


def _inspect_pptx(data: bytes) -> dict[str, Any]:
    with _safe_pptx_archive(data) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        size = presentation.find("p:sldSz", PPTX_NS)
        slide_width = int(size.attrib.get("cx", "12192000")) if size is not None else 12192000
        slide_height = int(size.attrib.get("cy", "6858000")) if size is not None else 6858000
        slide_names = sorted((name for name in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)), key=_slide_number)
        placeholders: list[dict[str, Any]] = []
        for slide_index, slide_name in enumerate(slide_names, start=1):
            root = ET.fromstring(archive.read(slide_name))
            for shape_index, shape in enumerate(root.findall(".//p:sp", PPTX_NS), start=1):
                placeholder = _shape_placeholder(shape)
                if not placeholder:
                    continue
                name_node = shape.find("./p:nvSpPr/p:cNvPr", PPTX_NS)
                shape_name = name_node.attrib.get("name", f"Shape {shape_index}") if name_node is not None else f"Shape {shape_index}"
                transform = shape.find("./p:spPr/a:xfrm", PPTX_NS)
                offset = transform.find("a:off", PPTX_NS) if transform is not None else None
                extent = transform.find("a:ext", PPTX_NS) if transform is not None else None
                x = int(offset.attrib.get("x", "0")) if offset is not None else 0
                y = int(offset.attrib.get("y", "0")) if offset is not None else 0
                w = int(extent.attrib.get("cx", str(slide_width))) if extent is not None else slide_width
                h = int(extent.attrib.get("cy", str(slide_height))) if extent is not None else slide_height
                kind, token = placeholder
                placeholders.append({
                    "id": f"slide-{slide_index}-shape-{shape_index}", "slideIndex": slide_index, "shapeName": shape_name,
                    "token": token, "kind": kind, "x": x / slide_width, "y": y / slide_height, "w": w / slide_width, "h": h / slide_height,
                })
        return {"slideWidth": slide_width, "slideHeight": slide_height, "slideCount": len(slide_names), "placeholders": placeholders}


def _report_template_item(item: dict[str, Any]) -> dict[str, Any]:
    definition = json_value(item.pop("definition_json")) or {}
    item.pop("file_path", None)
    return {**item, "definition": definition}


@app.get("/api/report-templates")
def list_report_templates() -> list[dict[str, Any]]:
    with connect() as conn:
        items = rows(conn.execute("SELECT * FROM report_template_assets WHERE is_active=true ORDER BY updated_at DESC, name"))
    return [_report_template_item(item) for item in items]


@app.post("/api/report-templates", status_code=201)
def upload_report_template(payload: ReportTemplateUploadPayload, request: Request) -> dict[str, Any]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    principal = request.state.principal
    actor_name = principal.display_name
    if not payload.filename.lower().endswith(".pptx") or payload.filename.lower().endswith(".pptm"):
        raise HTTPException(422, ".pptx 템플릿만 업로드할 수 있습니다.")
    try:
        data = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "PPTX Base64 데이터가 올바르지 않습니다.") from exc
    inspected = _inspect_pptx(data)
    if not inspected["slideCount"]:
        raise HTTPException(422, "슬라이드가 없는 PPTX는 사용할 수 없습니다.")
    template_id = f"report-template-{uuid4().hex[:12]}"
    REPORT_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    relative_path = f"report-templates/{template_id}.pptx"
    target_path = REPORT_TEMPLATE_DIR / f"{template_id}.pptx"
    target_path.write_bytes(data)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    definition = {"slideWidth": inspected["slideWidth"], "slideHeight": inspected["slideHeight"], "placeholders": inspected["placeholders"]}
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "INSERT INTO report_template_assets VALUES (?, ?, ?, ?, ?, ?, true, ?, ?, ?)",
                [template_id, payload.name.strip(), Path(payload.filename).name, relative_path, inspected["slideCount"], json.dumps(definition, ensure_ascii=False), now, now, actor_name],
            )
            write_audit_event(request=request, principal=principal, status_code=201, action="REPORT_TEMPLATE_UPLOADED", detail={"template_id": template_id}, connection=conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            target_path.unlink(missing_ok=True)
            raise
    return {"id": template_id, "name": payload.name.strip(), "filename": Path(payload.filename).name, "slide_count": inspected["slideCount"], "definition": definition, "created_at": now, "updated_at": now, "updated_by": actor_name}


def _replacement_key(shape: ET.Element) -> str | None:
    placeholder = _shape_placeholder(shape)
    return placeholder[1] if placeholder else None


def _render_pptx_template(data: bytes, replacements: dict[str, str]) -> bytes:
    source = _safe_pptx_archive(data)
    output_buffer = io.BytesIO()
    with source, zipfile.ZipFile(output_buffer, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for item in source.infolist():
            content = source.read(item.filename)
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", item.filename):
                root = ET.fromstring(content)
                changed = False
                for shape in root.findall(".//p:sp", PPTX_NS):
                    key = _replacement_key(shape)
                    if not key:
                        continue
                    value = str(replacements.get(key, replacements.get(key.split(":", 1)[-1], "")))
                    text_nodes = shape.findall(".//a:t", PPTX_NS)
                    if not text_nodes:
                        continue
                    combined = "".join(node.text or "" for node in text_nodes)
                    token_matches = list(PPTX_TOKEN.finditer(combined))
                    if token_matches:
                        for match in reversed(token_matches):
                            raw = match.group(1).strip()
                            normalized = raw if ":" in raw else f"text:{raw}"
                            replacement = str(replacements.get(normalized, replacements.get(raw, "")))
                            combined = combined[:match.start()] + replacement + combined[match.end():]
                        text_nodes[0].text = combined
                    else:
                        text_nodes[0].text = value
                    for node in text_nodes[1:]:
                        node.text = ""
                    changed = True
                if changed:
                    content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            output.writestr(item, content)
    return output_buffer.getvalue()


@app.post("/api/report-templates/{template_id}/render")
def render_report_template(template_id: str, payload: ReportTemplateRenderPayload, request: Request) -> Response:
    require_permission(request, REPORT_EXPORT)
    with connect() as conn:
        stored = conn.execute("SELECT file_path FROM report_template_assets WHERE id=? AND is_active=true", [template_id]).fetchone()
    if not stored:
        raise HTTPException(404, "PPTX 템플릿을 찾을 수 없습니다.")
    source_path = Path(__file__).resolve().parents[1] / "assets" / stored[0]
    if not source_path.is_file():
        raise HTTPException(410, "PPTX 템플릿 파일이 없습니다.")
    rendered = _render_pptx_template(source_path.read_bytes(), payload.replacements)
    filename = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", Path(payload.filename).name)
    ascii_filename = re.sub(r"[^0-9A-Za-z._-]+", "_", filename) or "analysis-report.pptx"
    disposition = f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{quote(filename)}'
    return Response(content=rendered, media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation", headers={"Content-Disposition": disposition})


@app.delete("/api/report-templates/{template_id}")
def delete_report_template(template_id: str, request: Request) -> dict[str, str]:
    require_permission(request, SYSTEM_CATALOG_MANAGE)
    with connect() as conn:
        stored = conn.execute("SELECT file_path FROM report_template_assets WHERE id=? AND is_active=true", [template_id]).fetchone()
        if not stored:
            raise HTTPException(404, "PPTX 템플릿을 찾을 수 없습니다.")
        conn.execute("UPDATE report_template_assets SET is_active=false, updated_at=? WHERE id=?", [datetime.now(timezone.utc).replace(tzinfo=None), template_id])
    source_path = Path(__file__).resolve().parents[1] / "assets" / stored[0]
    if source_path.is_file() and source_path.parent.resolve() == REPORT_TEMPLATE_DIR.resolve():
        source_path.unlink()
    return {"status": "deactivated", "id": template_id}


SYSTEM_ANALYSIS_PAGE_IDS = {"dashboard-drop-default", "dashboard-chassis-default", "dashboard-run-comparison-default"}


def _analysis_page_meta(definition: dict[str, Any]) -> dict[str, Any] | None:
    page = definition.get("page")
    if not isinstance(page, dict) or page.get("kind") != "analysis_page":
        return None
    return page


def _analysis_page_summary(item: dict[str, Any], definition: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "project_id": item["project_id"],
        "request_id": item.get("request_id"),
        "load_case_id": item.get("load_case_id"),
        "name": item["name"],
        "description": item.get("description") or "",
        "version": item["version"],
        "updated_at": item["updated_at"],
        "page": _analysis_page_meta(definition),
    }


def _require_load_case_context(conn: Any, load_case_id: str) -> tuple[str, str]:
    context = conn.execute(
        """
        SELECT ar.project_id, lc.request_id
        FROM load_cases lc
        JOIN analysis_requests ar ON ar.id = lc.request_id
        WHERE lc.id = ?
        """,
        [load_case_id],
    ).fetchone()
    if not context:
        raise HTTPException(404, "하중 경우를 찾을 수 없습니다.")
    return context[0], context[1]


def _page_name_exists(conn: Any, load_case_id: str, name: str, exclude_id: str | None = None) -> bool:
    candidates = rows(
        conn.execute(
            """
            SELECT id, load_case_id, definition_json
            FROM dashboards
            WHERE load_case_id = ? OR id IN ('dashboard-drop-default', 'dashboard-chassis-default', 'dashboard-run-comparison-default')
            """,
            [load_case_id],
        )
    )
    normalized_name = name.strip().casefold()
    for item in candidates:
        if item["id"] == exclude_id:
            continue
        definition = json_value(item["definition_json"]) or {}
        page = _analysis_page_meta(definition)
        if not page or page.get("status") == "archived":
            continue
        if str(definition.get("name", "")).strip().casefold() == normalized_name:
            return True
    return False


def _write_dashboard_definition(
    conn: Any,
    dashboard_id: str,
    definition: dict[str, Any],
    current_version: int,
    created_by: str,
) -> tuple[int, datetime]:
    del current_version
    next_version = int(
        conn.execute(
            "SELECT COALESCE(max(version), 0) + 1 FROM dashboard_versions WHERE dashboard_id = ?",
            [dashboard_id],
        ).fetchone()[0]
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    encoded = json.dumps(definition, ensure_ascii=False)
    conn.execute(
        """
        UPDATE dashboards
        SET name = ?, description = ?, version = ?, definition_json = ?, updated_at = ?
        WHERE id = ?
        """,
        [definition["name"], definition.get("description", ""), next_version, encoded, now, dashboard_id],
    )
    conn.execute(
        "INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)",
        [dashboard_id, next_version, encoded, created_by, now, True],
    )
    return next_version, now


def _delete_analysis_page_records(conn: Any, dashboard_id: str) -> None:
    conn.execute("DELETE FROM dashboard_versions WHERE dashboard_id = ?", [dashboard_id])
    conn.execute("DELETE FROM dashboards WHERE id = ?", [dashboard_id])


def _list_analysis_pages(conn: Any, load_case_id: str, *, include_private: bool, include_archived: bool) -> list[dict[str, Any]]:
    _require_load_case_context(conn, load_case_id)
    stored = rows(
        conn.execute(
            """
            SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at
            FROM dashboards
            WHERE load_case_id = ? OR id IN ('dashboard-drop-default', 'dashboard-chassis-default', 'dashboard-run-comparison-default')
            """,
            [load_case_id],
        )
    )
    result: list[dict[str, Any]] = []
    for item in stored:
        definition = json_value(item.pop("definition_json")) or {}
        page = _analysis_page_meta(definition)
        if not page:
            continue
        is_system = item["id"] in SYSTEM_ANALYSIS_PAGE_IDS and page.get("is_system") is True
        is_current_custom = item.get("load_case_id") == load_case_id and page.get("analysis_key") == "custom" and page.get("is_system") is False
        if not (is_system or is_current_custom):
            continue
        status = page.get("status")
        if not include_private and not is_system and status != "published":
            continue
        if not include_archived and status == "archived":
            continue
        result.append(_analysis_page_summary(item, definition))
    return sorted(result, key=lambda item: (item["page"]["display_order"], item["name"].casefold(), item["id"]))


@app.get("/api/dashboard-pages", response_model=list[AnalysisPageSummary])
def list_public_dashboard_pages(load_case_id: str = Query(min_length=1, max_length=120)) -> list[dict[str, Any]]:
    with connect() as conn:
        return _list_analysis_pages(conn, load_case_id, include_private=False, include_archived=False)


@app.get("/api/admin/dashboard-pages", response_model=list[AnalysisPageSummary])
def list_admin_dashboard_pages(
    request: Request,
    load_case_id: str = Query(min_length=1, max_length=120),
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    with connect() as conn:
        project_id, _ = _require_load_case_context(conn, load_case_id)
        require_permission(request, DASHBOARD_EDIT, project_id, conn=conn)
        return _list_analysis_pages(conn, load_case_id, include_private=True, include_archived=include_archived)


@app.post("/api/admin/dashboard-pages", response_model=DashboardDefinition, status_code=201)
def create_dashboard_page(payload: AnalysisPageCreate, request: Request) -> dict[str, Any]:
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(422, "분석 페이지 이름은 두 글자 이상이어야 합니다.")
    dashboard_id = f"dashboard-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        project_id, request_id = _require_load_case_context(conn, payload.load_case_id)
        require_permission(request, DASHBOARD_EDIT, project_id, conn=conn)
        if _page_name_exists(conn, payload.load_case_id, name):
            raise HTTPException(409, "같은 하중 경우에 동일한 분석 페이지 이름이 이미 있습니다.")
        existing = rows(conn.execute("SELECT definition_json FROM dashboards WHERE load_case_id = ?", [payload.load_case_id]))
        custom_orders = []
        for item in existing:
            page = _analysis_page_meta(json_value(item["definition_json"]) or {})
            if page and page.get("analysis_key") == "custom" and page.get("is_system") is False:
                custom_orders.append(int(page.get("display_order", 99)))
        display_order = max([99, *custom_orders]) + 1
        definition = {
            "id": dashboard_id,
            "name": name,
            "description": payload.description.strip(),
            "widgets": [],
            "page": {
                "kind": "analysis_page",
                "analysis_key": "custom",
                "status": "draft",
                "display_order": display_order,
                "is_system": False,
            },
        }
        definition = DashboardDefinition.model_validate(definition).model_dump()
        encoded = json.dumps(definition, ensure_ascii=False)
        conn.execute(
            "INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [dashboard_id, project_id, request_id, payload.load_case_id, name, definition["description"], 1, encoded, now],
        )
        conn.execute("INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)", [dashboard_id, 1, encoded, "관리자", now, True])
    return definition


@app.patch("/api/admin/dashboard-pages/{dashboard_id}", response_model=DashboardDefinition)
def update_dashboard_page(dashboard_id: str, payload: AnalysisPageUpdate, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        stored_rows = rows(
            conn.execute(
                "SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at FROM dashboards WHERE id = ?",
                [dashboard_id],
            )
        )
        if not stored_rows:
            raise HTTPException(404, "분석 페이지를 찾을 수 없습니다.")
        item = stored_rows[0]
        definition = json_value(item.pop("definition_json")) or {}
        page = _analysis_page_meta(definition)
        if not page:
            raise HTTPException(404, "관리 가능한 분석 페이지가 아닙니다.")
        if dashboard_id in SYSTEM_ANALYSIS_PAGE_IDS or page.get("is_system") is True:
            raise HTTPException(409, "시스템 기본 분석 페이지의 생명주기는 변경할 수 없습니다.")

        name = payload.name.strip() if payload.name is not None else definition["name"]
        if len(name) < 2:
            raise HTTPException(422, "분석 페이지 이름은 두 글자 이상이어야 합니다.")
        target_status = payload.status or page["status"]
        if target_status == "published" and not definition.get("widgets"):
            raise HTTPException(422, "위젯이 없는 분석 페이지는 게시할 수 없습니다.")
        if target_status != "archived" and _page_name_exists(conn, item["load_case_id"], name, dashboard_id):
            raise HTTPException(409, "같은 하중 경우에 동일한 분석 페이지 이름이 이미 있습니다.")

        definition["name"] = name
        if payload.description is not None:
            definition["description"] = payload.description.strip()
        page["status"] = target_status
        definition["page"] = page
        definition = DashboardDefinition.model_validate(definition).model_dump()
        _write_dashboard_definition(conn, dashboard_id, definition, item["version"], "관리자")
    return definition


@app.delete("/api/admin/dashboard-pages/{dashboard_id}")
def delete_dashboard_page(
    dashboard_id: str,
    request: Request,
    load_case_id: str = Query(min_length=1, max_length=120),
) -> dict[str, str]:
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        stored = conn.execute("SELECT load_case_id, definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not stored:
            raise HTTPException(404, "분석 페이지를 찾을 수 없습니다.")
        definition = json_value(stored[1]) or {}
        page = _analysis_page_meta(definition)
        if (
            dashboard_id in SYSTEM_ANALYSIS_PAGE_IDS
            or not page
            or page.get("is_system") is not False
            or page.get("analysis_key") != "custom"
        ):
            raise HTTPException(409, "사용자 정의 분석 페이지만 영구 삭제할 수 있습니다.")
        if stored[0] != load_case_id:
            raise HTTPException(409, "분석 페이지가 요청한 하중 경우에 속하지 않습니다.")
        _require_load_case_context(conn, load_case_id)
        conn.execute("BEGIN TRANSACTION")
        try:
            _delete_analysis_page_records(conn, dashboard_id)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"status": "deleted", "id": dashboard_id, "load_case_id": load_case_id}


@app.put("/api/admin/dashboard-pages/order", response_model=list[AnalysisPageSummary])
def reorder_dashboard_pages(payload: AnalysisPageOrderUpdate, request: Request) -> list[dict[str, Any]]:
    if len(payload.page_ids) != len(set(payload.page_ids)):
        raise HTTPException(422, "분석 페이지 순서에 중복 ID가 있습니다.")
    with connect() as conn:
        project_id, _ = _require_load_case_context(conn, payload.load_case_id)
        require_permission(request, DASHBOARD_EDIT, project_id, conn=conn)
        stored = rows(
            conn.execute(
                "SELECT id, version, definition_json FROM dashboards WHERE load_case_id = ?",
                [payload.load_case_id],
            )
        )
        custom_pages: dict[str, dict[str, Any]] = {}
        for item in stored:
            definition = json_value(item.pop("definition_json")) or {}
            page = _analysis_page_meta(definition)
            if page and page.get("analysis_key") == "custom" and page.get("is_system") is False and page.get("status") != "archived":
                custom_pages[item["id"]] = {**item, "definition": definition}
        if set(payload.page_ids) != set(custom_pages):
            raise HTTPException(422, "현재 하중 경우의 보관되지 않은 사용자 분석 페이지를 모두 한 번씩 지정해야 합니다.")
        for offset, dashboard_id in enumerate(payload.page_ids):
            item = custom_pages[dashboard_id]
            item["definition"]["page"]["display_order"] = 100 + offset
            definition = DashboardDefinition.model_validate(item["definition"]).model_dump()
            _write_dashboard_definition(conn, dashboard_id, definition, item["version"], "관리자 순서 변경")
        return _list_analysis_pages(conn, payload.load_case_id, include_private=True, include_archived=False)


@app.get("/api/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: str, request: Request) -> dict[str, Any]:
    with connect() as conn:
        result = rows(conn.execute("SELECT * FROM dashboards WHERE id = ?", [dashboard_id]))
        if not result:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        item = result[0]
        definition = json_value(item.pop("definition_json"))
        page = _analysis_page_meta(definition)
        if page and page.get("status") in {"draft", "archived"}:
            require_permission(request, DASHBOARD_EDIT, item["project_id"], conn=conn)
        definition["version"] = item["version"]
        definition["updated_at"] = item["updated_at"]
        return definition


@app.get("/api/dashboards")
def list_dashboards(request: Request, project_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if project_id:
            stored = rows(conn.execute("SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at FROM dashboards WHERE project_id = ? ORDER BY updated_at DESC", [project_id]))
        else:
            stored = rows(conn.execute("SELECT id, project_id, request_id, load_case_id, name, description, version, definition_json, updated_at FROM dashboards ORDER BY updated_at DESC"))
        result = []
        for item in stored:
            definition = json_value(item.pop("definition_json")) or {}
            page = _analysis_page_meta(definition)
            if (
                page
                and page.get("status") in {"draft", "archived"}
                and not has_permission(request, DASHBOARD_EDIT, item["project_id"], conn=conn)
            ):
                continue
            result.append(item)
        return result


@app.put("/api/dashboards/{dashboard_id}")
def save_dashboard(dashboard_id: str, definition: DashboardDefinition, request: Request) -> dict[str, Any]:
    if dashboard_id != definition.id:
        raise HTTPException(400, "대시보드 ID가 일치하지 않습니다.")
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        existing = conn.execute("SELECT version, definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not existing:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        stored_definition = json_value(existing[1]) or {}
        stored_page = _analysis_page_meta(stored_definition)
        incoming = definition.model_dump()
        if stored_page:
            if (
                incoming.get("name") != stored_definition.get("name")
                or incoming.get("description", "") != stored_definition.get("description", "")
                or incoming.get("page") != stored_page
            ):
                raise HTTPException(409, "분석 페이지 이름·설명·상태·순서는 관리자 페이지 API에서 변경해야 합니다.")
        version, now = _write_dashboard_definition(conn, dashboard_id, incoming, existing[0], "대시보드 사용자")
    return {"status": "saved", "version": version, "updated_at": now}


@app.get("/api/dashboards/{dashboard_id}/versions")
def get_dashboard_versions(
    dashboard_id: str,
    request: Request,
    include_invalid: bool = False,
) -> list[dict[str, Any]]:
    with connect() as conn:
        current = conn.execute("SELECT definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not current:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        page = _analysis_page_meta(json_value(current[0]) or {})
        if page and page.get("status") in {"draft", "archived"}:
            require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        valid_filter = "" if include_invalid else " AND is_valid = true"
        return rows(
            conn.execute(
                f"SELECT dashboard_id, version, created_by, created_at, is_valid FROM dashboard_versions WHERE dashboard_id = ?{valid_filter} ORDER BY version DESC",
                [dashboard_id],
            )
        )


@app.get("/api/dashboards/{dashboard_id}/versions/{version}")
def get_dashboard_version(
    dashboard_id: str,
    version: int,
    request: Request,
    include_invalid: bool = False,
) -> dict[str, Any]:
    with connect() as conn:
        current = conn.execute("SELECT definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not current:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        page = _analysis_page_meta(json_value(current[0]) or {})
        if page and page.get("status") in {"draft", "archived"}:
            require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        valid_filter = "" if include_invalid else " AND is_valid = true"
        stored = conn.execute(
            f"SELECT definition_json, created_by, created_at, is_valid FROM dashboard_versions WHERE dashboard_id = ? AND version = ?{valid_filter}",
            [dashboard_id, version],
        ).fetchone()
        if not stored:
            raise HTTPException(404, "대시보드 버전을 찾을 수 없습니다.")
    return {
        "dashboard_id": dashboard_id,
        "version": version,
        "definition": json_value(stored[0]) or {},
        "created_by": stored[1],
        "created_at": stored[2],
        "is_valid": stored[3],
    }


@app.delete("/api/dashboards/{dashboard_id}/versions/{version}")
def delete_dashboard_version(dashboard_id: str, version: int, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        current = conn.execute("SELECT version, definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not current:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        current_version = int(current[0])
        definition = json_value(current[1]) or {}
        page = _analysis_page_meta(definition)
        if version == 1 and (dashboard_id in SYSTEM_ANALYSIS_PAGE_IDS or (page and page.get("is_system") is True)):
            raise HTTPException(409, "시스템 대시보드의 최초 기준 버전은 삭제할 수 없습니다.")

        stored = conn.execute(
            "SELECT is_valid FROM dashboard_versions WHERE dashboard_id = ? AND version = ?",
            [dashboard_id, version],
        ).fetchone()
        if not stored or stored[0] is not True:
            raise HTTPException(404, "삭제할 수 있는 유효한 대시보드 버전을 찾을 수 없습니다.")
        if version == current_version:
            raise HTTPException(409, "현재 사용 중인 live 버전은 삭제할 수 없습니다.")

        valid_history_count = int(
            conn.execute(
                "SELECT count(*) FROM dashboard_versions WHERE dashboard_id = ? AND is_valid = true AND version <> ?",
                [dashboard_id, current_version],
            ).fetchone()[0]
        )
        if valid_history_count <= 1:
            raise HTTPException(409, "현재 버전 외에 최소 1개의 유효한 과거 버전을 유지해야 합니다.")
        conn.execute(
            "UPDATE dashboard_versions SET is_valid = false WHERE dashboard_id = ? AND version = ? AND is_valid = true",
            [dashboard_id, version],
        )
    return {"status": "invalidated", "dashboard_id": dashboard_id, "version": version}


@app.post("/api/dashboards/{dashboard_id}/clone", status_code=201)
def clone_dashboard(dashboard_id: str, payload: DashboardClone, request: Request) -> dict[str, Any]:
    clone_id = f"dashboard-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    principal = request.state.principal
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        source = conn.execute("SELECT project_id, request_id, load_case_id, definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not source:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        definition = json_value(source[3])
        definition.pop("page", None)
        definition.update({"id": clone_id, "name": payload.name.strip(), "description": payload.description.strip()})
        encoded = json.dumps(definition, ensure_ascii=False)
        conn.execute("INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [clone_id, source[0], source[1], source[2], definition["name"], definition["description"], 1, encoded, now])
        conn.execute("INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)", [clone_id, 1, encoded, principal.user_id, now, True])
    return {"id": clone_id, "version": 1, "status": "cloned"}


@app.post("/api/dashboards/{dashboard_id}/restore/{version}")
def restore_dashboard(dashboard_id: str, version: int, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_resource_permission(request, DASHBOARD_EDIT, "dashboard", dashboard_id, conn=conn)
        stored = conn.execute("SELECT definition_json FROM dashboard_versions WHERE dashboard_id = ? AND version = ? AND is_valid = true", [dashboard_id, version]).fetchone()
        current = conn.execute("SELECT version, definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not stored or not current:
            raise HTTPException(404, "복구 가능한 정상 버전을 찾을 수 없습니다.")
        definition = json_value(stored[0])
        current_definition = json_value(current[1]) or {}
        current_page = _analysis_page_meta(current_definition)
        if current_page:
            if current_page.get("status") == "published" and not definition.get("widgets"):
                raise HTTPException(422, "게시된 분석 페이지를 빈 위젯 버전으로 복구할 수 없습니다.")
            definition.update(
                {
                    "id": current_definition["id"],
                    "name": current_definition["name"],
                    "description": current_definition.get("description", ""),
                    "page": current_page,
                }
            )
        definition = DashboardDefinition.model_validate(definition).model_dump()
        next_version, _ = _write_dashboard_definition(conn, dashboard_id, definition, current[0], "복구 작업")
    return {"status": "restored", "version": next_version, "restored_from": version}


@app.post("/api/dashboard-commands/preview")
def preview_dashboard_command(
    payload: NaturalLanguageCommand,
    request: Request,
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    require_permission(request, DASHBOARD_EDIT, project_id)
    text = payload.command.strip()
    compact = text.replace(" ", "").lower()
    widget: dict[str, Any] | None = None
    message = ""

    if "응력" in compact and ("시간" in compact or "시계열" in compact) and "기준선" in compact and ("추가" in compact or "넣" in compact):
        return {"recognized": True, "message": "기존 응력-시간 위젯에 기준선을 표시합니다.", "proposal": {"action": "update_widgets", "updates": [{"widget_type": "time_series", "settings": {"showThreshold": True}}]}}
    if ("판정" in compact or "패스" in compact or "실패" in compact) and ("오른쪽" in compact or "우측" in compact) and ("이동" in compact or "옮" in compact):
        return {"recognized": True, "message": "패스/실패 판정 카드를 맨 위 오른쪽으로 이동합니다.", "proposal": {"action": "update_widgets", "updates": [{"widget_type": "verdict", "x": 9, "y": 0}]}}
    if "컨투어" in compact and "의견" in compact and ("나란히" in compact or "옆" in compact):
        return {"recognized": True, "message": "컨투어 이미지와 수행자 의견을 같은 행에 배치합니다.", "proposal": {"action": "update_widgets", "updates": [{"widget_type": "contour", "x": 4, "y": 18, "w": 4}, {"widget_type": "note", "x": 8, "y": 18, "w": 4}]}}
    if "응력" in compact and ("시간" in compact or "시계열" in compact) and ("추가" in compact or "만들" in compact):
        widget = {
            "id": f"time-series-{int(datetime.now().timestamp())}",
            "type": "time_series",
            "title": "Open Cell 엣지 응력-시간",
            "x": 0,
            "y": 20,
            "w": 8,
            "h": 5,
            "settings": {"showThreshold": True},
        }
        message = "상하좌우 엣지 응력 시계열과 기준선을 표시하는 위젯을 추가합니다."
    elif ("최대응력" in compact or "상하좌우" in compact) and ("추가" in compact or "막대" in compact):
        widget = {
            "id": f"edge-bar-{int(datetime.now().timestamp())}",
            "type": "edge_bar",
            "title": "상하좌우 엣지 최대 응력",
            "x": 0,
            "y": 20,
            "w": 6,
            "h": 4,
            "settings": {"failColor": "#ff5d73", "showThreshold": True},
        }
        message = "엣지별 최대 응력과 기준값을 비교하는 막대그래프를 추가합니다."
    elif "판정" in compact and ("추가" in compact or "카드" in compact):
        widget = {
            "id": f"verdict-{int(datetime.now().timestamp())}",
            "type": "verdict",
            "title": "패스/실패 판정",
            "x": 0,
            "y": 20,
            "w": 3,
            "h": 2,
            "settings": {},
        }
        message = "기준값에 따른 전체 판정 카드를 추가합니다."
    else:
        return {
            "recognized": False,
            "message": "요청을 안전한 변경 명세로 변환하지 못했습니다. ‘응력-시간 그래프 추가’, ‘상하좌우 최대 응력 막대그래프 추가’, ‘판정 카드 추가’처럼 요청해 주세요.",
            "proposal": None,
        }

    return {"recognized": True, "message": message, "proposal": {"action": "add_widget", "widget": widget}}
