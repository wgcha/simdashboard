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

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .database import connect, initialize_database, json_value, rows
from .folder_import import FolderImportError, scan_folder
from .media_policy import validate_media_metadata
from .result_import import CSV_TEMPLATE, JSON_TEMPLATE, ResultFormatError, parse_result_file
from .repositories.portfolio import PortfolioRepository
from .repositories.variable_catalog import VariableCatalogRepository


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Analysis Canvas API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/assets", StaticFiles(directory=str(__import__("pathlib").Path(__file__).resolve().parents[1] / "assets")), name="assets")


class Widget(BaseModel):
    id: str
    type: str
    title: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=2, le=12)
    h: int = Field(ge=2, le=12)
    settings: dict[str, Any] = Field(default_factory=dict)


class DashboardDefinition(BaseModel):
    id: str
    name: str
    description: str = ""
    widgets: list[Widget]


class DashboardClone(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    created_by: str = Field(default="대시보드 사용자", min_length=2, max_length=60)


class NaturalLanguageCommand(BaseModel):
    command: str = Field(min_length=2, max_length=500)


class WorkflowStepUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class QualityThresholdUpdate(BaseModel):
    threshold_double: float = Field(gt=0, le=1000)
    updated_by: str = Field(default="관리자", min_length=2, max_length=40)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    product_name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    manufacturer: str = Field(default="", max_length=120)
    display_size_inch: float | None = Field(default=None, gt=0, le=200)


class AnalysisRequestCreate(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    owner: str = Field(min_length=2, max_length=60)
    due_in_days: int = Field(default=7, ge=1, le=365)
    overall_note: str = Field(default="", max_length=500)


class LoadCaseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    analysis_type: Literal["DROP", "SIDE_CLAMP"]
    parameters: dict[str, Any] = Field(default_factory=dict)


class ResultImportPayload(BaseModel):
    filename: str = Field(min_length=5, max_length=240)
    content: str = Field(min_length=1, max_length=5_000_000)
    author: str = Field(default="해석 담당자", min_length=2, max_length=60)
    validate_only: bool = False


class VariableCreate(BaseModel):
    variable_key: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str = Field(min_length=2, max_length=120)
    data_type: Literal["NUMBER", "TIME_SERIES", "FLOAT", "INTEGER", "TEXT", "CURVE", "IMAGE", "VIDEO", "MODEL_3D", "VERDICT", "STATUS", "BOOLEAN"]
    unit: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=500)
    filterable: bool = True
    threshold: float | None = None
    allowed_widgets: list[str] = Field(default_factory=list, max_length=12)
    allowed_aggregations: list[str] = Field(default_factory=list, max_length=8)
    result_group: Literal["OPEN_CELL", "CHASSIS_REAR", "CUSTOM"] = "CUSTOM"
    updated_by: str = Field(default="관리자", min_length=2, max_length=60)


class VariableUpdate(BaseModel):
    display_name: str = Field(min_length=2, max_length=120)
    unit: str = Field(min_length=1, max_length=30)
    description: str = Field(default="", max_length=500)
    filterable: bool = True
    threshold: float | None = None
    allowed_widgets: list[str] = Field(default_factory=list, max_length=12)
    allowed_aggregations: list[str] = Field(default_factory=list, max_length=8)
    result_group: Literal["OPEN_CELL", "CHASSIS_REAR", "CUSTOM"] = "CUSTOM"
    updated_by: str = Field(default="관리자", min_length=2, max_length=60)

class ImportSchemaPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    definition: dict[str, Any]
    updated_by: str = Field(default="관리자", min_length=2, max_length=60)


class ReportLayoutPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=500)
    definition: dict[str, Any]
    updated_by: str = Field(default="보고서 편집자", min_length=2, max_length=60)


class ReportTemplateUploadPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    filename: str = Field(min_length=6, max_length=240)
    content_base64: str = Field(min_length=8, max_length=36_000_000)
    updated_by: str = Field(default="보고서 편집자", min_length=2, max_length=60)


class ReportTemplateRenderPayload(BaseModel):
    replacements: dict[str, str] = Field(default_factory=dict)
    filename: str = Field(default="analysis-report.pptx", min_length=6, max_length=240)


def _validated_report_layout(definition: dict[str, Any]) -> dict[str, Any]:
    if definition.get("coverVariant") not in {"balanced", "executive", "evidence"}:
        raise HTTPException(422, "지원하지 않는 표지 형식입니다.")
    sections = definition.get("sectionOrder")
    if not isinstance(sections, list) or len(sections) != 3 or set(sections) != {"series", "scalar", "media"}:
        raise HTTPException(422, "근거 페이지 순서는 series, scalar, media를 한 번씩 포함해야 합니다.")
    accent = str(definition.get("accentColor") or "")
    if len(accent) != 6 or any(character not in "0123456789abcdefABCDEF" for character in accent):
        raise HTTPException(422, "강조색은 6자리 HEX 색상이어야 합니다.")
    placements = definition.get("variablePlacements")
    if not isinstance(placements, list):
        raise HTTPException(422, "variablePlacements 배열이 필요합니다.")
    for placement in placements:
        if not isinstance(placement, dict) or not placement.get("variableKey") or placement.get("presentation") not in {"chart", "table", "both"}:
            raise HTTPException(422, "변수 배치에는 variableKey와 올바른 presentation이 필요합니다.")
    slides = definition.get("slides")
    if slides is not None:
        if not isinstance(slides, list) or not slides or len(slides) > 32:
            raise HTTPException(422, "slides는 1개 이상 32개 이하의 배열이어야 합니다.")
        slide_ids: set[str] = set()
        for slide in slides:
            if not isinstance(slide, dict) or not slide.get("id") or slide.get("kind") not in {"cover", "series", "scalar", "media", "custom"}:
                raise HTTPException(422, "각 슬라이드에는 고유 id와 올바른 kind가 필요합니다.")
            if slide["id"] in slide_ids:
                raise HTTPException(422, "슬라이드 id는 중복될 수 없습니다.")
            slide_ids.add(slide["id"])
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
    return {"status": "ok"}


@app.get("/api/projects")
def get_projects() -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute("SELECT * FROM projects ORDER BY created_at DESC"))


@app.get("/api/import-schemas")
def list_import_schemas() -> list[dict[str, Any]]:
    with connect() as conn:
        items = rows(conn.execute("SELECT * FROM import_schemas WHERE is_active=true ORDER BY updated_at DESC"))
    for item in items:
        item["definition"] = json_value(item.pop("definition_json"))
    return items


@app.post("/api/import-schemas", status_code=201)
def create_import_schema(payload: ImportSchemaPayload) -> dict[str, Any]:
    if not isinstance(payload.definition.get("mappings"), list):
        raise HTTPException(422, "스키마 정의에는 mappings 배열이 필요합니다.")
    schema_id, now = f"import-schema-{uuid4().hex[:12]}", datetime.now(timezone.utc).replace(tzinfo=None)
    definition = {**payload.definition, "schema_id": payload.definition.get("schema_id") or schema_id, "version": 1}
    encoded = json.dumps(definition, ensure_ascii=False)
    with connect() as conn:
        conn.execute("INSERT INTO import_schemas VALUES (?, ?, ?, ?, true, ?, ?, ?)", [schema_id, payload.name.strip(), payload.description.strip(), encoded, now, now, payload.updated_by.strip()])
        conn.execute("INSERT INTO import_schema_versions VALUES (?, 1, ?, ?, ?)", [schema_id, encoded, now, payload.updated_by.strip()])
    return {"id": schema_id, "name": payload.name.strip(), "description": payload.description.strip(), "definition": definition, "created_at": now, "updated_at": now, "updated_by": payload.updated_by.strip()}


@app.put("/api/import-schemas/{schema_id}")
def update_import_schema(schema_id: str, payload: ImportSchemaPayload) -> dict[str, Any]:
    if not isinstance(payload.definition.get("mappings"), list):
        raise HTTPException(422, "스키마 정의에는 mappings 배열이 필요합니다.")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        existing = conn.execute("SELECT definition_json, created_at FROM import_schemas WHERE id=? AND is_active=true", [schema_id]).fetchone()
        if not existing:
            raise HTTPException(404, "폴더 스키마를 찾을 수 없습니다.")
        previous = json_value(existing[0]) or {}
        version = int(previous.get("version") or 1) + 1
        definition = {**payload.definition, "schema_id": previous.get("schema_id") or schema_id, "version": version}
        encoded = json.dumps(definition, ensure_ascii=False)
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute("UPDATE import_schemas SET name=?, description=?, definition_json=?, updated_at=?, updated_by=? WHERE id=?", [payload.name.strip(), payload.description.strip(), encoded, now, payload.updated_by.strip(), schema_id])
            conn.execute("INSERT INTO import_schema_versions VALUES (?, ?, ?, ?, ?)", [schema_id, version, encoded, now, payload.updated_by.strip()])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": schema_id, "name": payload.name.strip(), "description": payload.description.strip(), "definition": definition, "created_at": existing[1], "updated_at": now, "updated_by": payload.updated_by.strip()}


@app.delete("/api/import-schemas/{schema_id}")
def delete_import_schema(schema_id: str) -> dict[str, str]:
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


@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectCreate) -> dict[str, Any]:
    project_id = f"project-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
            [project_id, payload.name.strip(), payload.product_name.strip(), payload.description.strip(), now],
        )
        product_rows = [
            ("MODEL", "제품 모델명", payload.product_name.strip(), {"source": "project_registration"}),
            ("MANUFACTURER", "제조사", payload.manufacturer.strip(), {"source": "project_registration"}),
            ("SPEC", "화면 크기", f"{payload.display_size_inch:g} inch" if payload.display_size_inch else "", {"diagonal_inch": payload.display_size_inch}),
        ]
        for category, label, value, metadata in product_rows:
            if value:
                conn.execute("INSERT INTO product_information VALUES (?, ?, ?, ?, ?, ?, ?)", [f"product-{uuid4().hex[:12]}", project_id, category, label, value, None, json.dumps(metadata, ensure_ascii=False)])
    return {"id": project_id, **payload.model_dump(), "created_at": now}


@app.get("/api/projects/{project_id}/requests")
def get_requests(project_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(
            conn.execute(
                "SELECT * FROM analysis_requests WHERE project_id = ? ORDER BY requested_at DESC",
                [project_id],
            )
        )


@app.post("/api/projects/{project_id}/requests", status_code=201)
def create_request(project_id: str, payload: AnalysisRequestCreate) -> dict[str, Any]:
    request_id = f"request-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    due_at = now + timedelta(days=payload.due_in_days)
    step_names = ["의뢰 접수", "요구사항 검토", "모델 준비", "해석 전처리 모델링", "해석 실행", "후처리 작업", "결과 검토", "Validation", "승인", "완료"]
    with connect() as conn:
        if conn.execute("SELECT id FROM projects WHERE id = ?", [project_id]).fetchone() is None:
            raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "INSERT INTO analysis_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [request_id, project_id, payload.title.strip(), "READY", payload.owner.strip(), now, due_at, payload.overall_note.strip()],
            )
            for index, name in enumerate(step_names, start=1):
                planned_start = now + timedelta(hours=(index - 1) * 16)
                conn.execute(
                    """
                    INSERT INTO request_steps
                        (id, request_id, sequence_no, name, status, owner, planned_start, planned_end,
                         actual_start, actual_end, progress, is_optional, blocked_reason, note)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        f"step-{uuid4().hex[:12]}", request_id, index, name, "WAITING", payload.owner.strip(),
                        planned_start, planned_start + timedelta(hours=12), None, None, 0, name == "Validation", None, None,
                    ],
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": request_id, "project_id": project_id, "title": payload.title.strip(), "status": "READY", "owner": payload.owner.strip(), "requested_at": now, "due_at": due_at, "overall_note": payload.overall_note.strip()}


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


@app.post("/api/requests/{request_id}/load-cases", status_code=201)
def create_load_case(request_id: str, payload: LoadCaseCreate) -> dict[str, Any]:
    load_case_id = f"loadcase-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        if conn.execute("SELECT id FROM analysis_requests WHERE id = ?", [request_id]).fetchone() is None:
            raise HTTPException(404, "해석 의뢰를 찾을 수 없습니다.")
        conn.execute(
            "INSERT INTO load_cases VALUES (?, ?, ?, ?, ?, ?, ?)",
            [load_case_id, request_id, payload.name.strip(), payload.analysis_type, "READY", json.dumps(payload.parameters, ensure_ascii=False), now],
        )
    return {"id": load_case_id, "request_id": request_id, "name": payload.name.strip(), "analysis_type": payload.analysis_type, "status": "READY", "parameters": payload.parameters, "created_at": now}


@app.get("/api/result-import/template/{file_format}")
def get_result_import_template(file_format: Literal["csv", "json", "radioss-csv"]) -> Response:
    if file_format == "csv":
        return Response(CSV_TEMPLATE, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="analysis-result-template.csv"'})
    if file_format == "radioss-csv":
        sample_path = Path(__file__).resolve().parents[2] / "examples" / "radioss" / "radioss_tv_result_example.csv"
        if not sample_path.exists():
            raise HTTPException(404, "Radioss 예제 파일을 찾을 수 없습니다.")
        return Response(sample_path.read_bytes(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="radioss-tv-result-example.csv"'})
    content = json.dumps(JSON_TEMPLATE, ensure_ascii=False, indent=2)
    return Response(content, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="analysis-result-template.json"'})


@app.post("/api/load-cases/{load_case_id}/results/import")
def import_analysis_results(load_case_id: str, payload: ResultImportPayload) -> dict[str, Any]:
    with connect() as conn:
        context = conn.execute(
            """
            SELECT lc.id, lc.request_id, ar.project_id
            FROM load_cases lc
            JOIN analysis_requests ar ON ar.id = lc.request_id
            WHERE lc.id = ?
            """,
            [load_case_id],
        ).fetchone()
        if context is None:
            raise HTTPException(404, "하중 경우를 찾을 수 없습니다.")
        threshold_row = conn.execute(
            "SELECT threshold_double FROM quality_thresholds WHERE project_id = ? AND criterion_key = 'chassis_rear_permanent_deformation_mm'",
            [context[2]],
        ).fetchone()
        chassis_threshold = float(threshold_row[0]) if threshold_row else 5.0
        catalog = {
            item["id"]: item
            for item in VariableCatalogRepository(conn).list_for_load_case(load_case_id)
        }
        try:
            parsed = parse_result_file(payload.filename, payload.content, chassis_threshold, catalog)
        except ResultFormatError as exc:
            raise HTTPException(422, str(exc)) from exc
        if payload.validate_only:
            return {"status": "VALID", "filename": payload.filename, **parsed["summary"], "results": parsed["scalars"], "warnings": parsed["warnings"]}

        run_id = f"run-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        next_run_no = conn.execute("SELECT coalesce(max(run_no), 0) + 1 FROM analysis_runs WHERE load_case_id = ?", [load_case_id]).fetchone()[0]
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, load_case_id, None, next_run_no, parsed["solver"], "COMPLETED", now, now],
            )
            for item in parsed["scalars"]:
                conn.execute(
                    "INSERT INTO scalar_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [f"scalar-{uuid4().hex[:12]}", run_id, item["variable_key"], item["display_name"], item["value"], None, None, item["unit"], item["threshold"], item["verdict"]],
                )
            for item in parsed["time_series"]:
                conn.execute(
                    "INSERT INTO time_series_results VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [run_id, item["variable_key"], item["display_name"], item["time"], item["value"], item["time_unit"], item["value_unit"]],
                )
            for item in parsed["locations"]:
                conn.execute(
                    "INSERT INTO result_locations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [run_id, item["variable_key"], item["entity_type"], item["entity_id"], item["x"], item["y"], item["z"], item["time"], item["time_unit"], item["method"]],
                )
            if parsed["note"]:
                conn.execute(
                    "INSERT INTO qualitative_notes VALUES (?, ?, ?, ?, ?)",
                    [f"note-{uuid4().hex[:12]}", run_id, payload.author.strip(), parsed["note"], now],
                )
            conn.execute("UPDATE load_cases SET status = 'COMPLETED' WHERE id = ?", [load_case_id])
            conn.execute("UPDATE analysis_requests SET status = 'IN_PROGRESS' WHERE id = ?", [context[1]])
            conn.execute("UPDATE request_steps SET status = 'COMPLETED', progress = 100, actual_end = ? WHERE request_id = ? AND name IN ('해석 실행', '후처리 작업')", [now, context[1]])
            conn.execute("UPDATE request_steps SET status = 'IN_PROGRESS', progress = greatest(progress, 20), actual_start = coalesce(actual_start, ?) WHERE request_id = ? AND name = '결과 검토'", [now, context[1]])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"status": "IMPORTED", "run_id": run_id, "run_no": next_run_no, "filename": payload.filename, **parsed["summary"], "results": parsed["scalars"], "warnings": parsed["warnings"]}


@app.post("/api/load-cases/{load_case_id}/folder-import/example")
def import_typed_result_example(load_case_id: str) -> dict[str, Any]:
    """Register the checked-in typed folder example through the same importer used by future uploads."""
    example_root = Path(__file__).resolve().parents[2] / "examples" / "typed-results" / "tv-drop-chassis"
    try:
        parsed = scan_folder(example_root)
    except FolderImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    job_id, run_id = f"folder-job-{uuid4().hex[:12]}", f"run-{uuid4().hex[:12]}"
    with connect() as conn:
        context = conn.execute(
            """SELECT lc.id, lc.request_id, ar.project_id, lc.analysis_type
               FROM load_cases lc JOIN analysis_requests ar ON ar.id=lc.request_id WHERE lc.id=?""",
            [load_case_id],
        ).fetchone()
        if not context:
            raise HTTPException(404, "하중경우를 찾을 수 없습니다.")
        next_run_no = conn.execute("SELECT coalesce(max(run_no), 0) + 1 FROM analysis_runs WHERE load_case_id=?", [load_case_id]).fetchone()[0]
        catalog = VariableCatalogRepository(conn)
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                "INSERT INTO folder_import_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [job_id, load_case_id, run_id, parsed["schema_id"], parsed["schema_version"], str(example_root), "RUNNING", None, now],
            )
            for item in parsed["scalars"]:
                if not catalog.get(load_case_id, item["variable_key"], include_inactive=True):
                    catalog.create(load_case_id, {
                        "variable_key": item["variable_key"], "display_name": item["display_name"], "data_type": item["data_type"],
                        "unit": item["unit"] or "-", "description": f"폴더 적재: {item['source_file']}", "threshold": item["threshold"],
                        "result_group": item["result_group"], "updated_by": "폴더 가져오기",
                    })
            for item in parsed["curves"]:
                if not catalog.get(load_case_id, item["variable_key"], include_inactive=True):
                    catalog.create(load_case_id, {
                        "variable_key": item["variable_key"], "display_name": item["display_name"], "data_type": "CURVE",
                        "unit": item["y_unit"] or "-", "description": f"폴더 커브: {item['source_file']}", "threshold": None,
                        "result_group": item["result_group"], "updated_by": "폴더 가져오기",
                    })
            for item in parsed["media"]:
                if not catalog.get(load_case_id, item["variable_key"], include_inactive=True):
                    catalog.create(load_case_id, {
                        "variable_key": item["variable_key"], "display_name": item["display_name"], "data_type": item["asset_type"],
                        "unit": "-", "description": f"폴더 미디어: {item['source_file']}", "threshold": None,
                        "result_group": item["result_group"], "updated_by": "폴더 가져오기",
                    })
            conn.execute("INSERT INTO analysis_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [run_id, load_case_id, None, next_run_no, parsed["solver"], "COMPLETED", now, now])
            for item in parsed["scalars"]:
                value_double = item["value"] if item["data_type"] == "FLOAT" else None
                value_integer = item["value"] if item["data_type"] == "INTEGER" else None
                value_text = str(item["value"]) if item["data_type"] not in {"FLOAT", "INTEGER"} else None
                threshold = float(item["threshold"]) if item["threshold"] is not None else None
                verdict = ("FAIL" if float(item["value"]) >= threshold else "PASS") if threshold is not None and item["data_type"] in {"FLOAT", "INTEGER"} else (str(item["value"]) if item["data_type"] == "VERDICT" else None)
                conn.execute("INSERT INTO scalar_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [f"scalar-{uuid4().hex[:12]}", run_id, item["variable_key"], item["display_name"], value_double, value_integer, value_text, item["unit"], threshold, verdict])
            for item in parsed["curves"]:
                curve_id = f"curve-{uuid4().hex[:12]}"
                conn.execute("INSERT INTO curve_results VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [curve_id, run_id, item["variable_key"], item["display_name"], item["series_key"], item["x_label"], item["x_unit"], item["y_label"], item["y_unit"], len(item["points"]), item["source_file"], item["source_checksum"], now])
                for index, point in enumerate(item["points"]):
                    conn.execute("INSERT INTO curve_points VALUES (?, ?, ?, ?)", [curve_id, index, point["x"], point["y"]])
                    conn.execute("INSERT INTO time_series_results VALUES (?, ?, ?, ?, ?, ?, ?)", [run_id, item["variable_key"], item["display_name"], point["x"], point["y"], item["x_unit"], item["y_unit"]])
            asset_root = Path(__file__).resolve().parents[1] / "assets" / "imports" / run_id
            asset_root.mkdir(parents=True, exist_ok=True)
            for item in parsed["media"]:
                validate_media_metadata("CONTOUR_IMAGE" if item["asset_type"] == "IMAGE" else item["asset_type"], item["path"].name, item["path"].stat().st_size)
                destination = asset_root / item["path"].name
                shutil.copy2(item["path"], destination)
                relative_path = f"imports/{run_id}/{destination.name}"
                conn.execute("INSERT INTO media_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [f"media-{uuid4().hex[:12]}", run_id, item["asset_type"], item["display_name"], relative_path, item["mime_type"], destination.stat().st_size, item["source_checksum"], json.dumps({"variable_key": item["variable_key"], "source_file": item["source_file"]})])
            if parsed["note"]:
                conn.execute("INSERT INTO qualitative_notes VALUES (?, ?, ?, ?, ?)", [f"note-{uuid4().hex[:12]}", run_id, "폴더 가져오기", parsed["note"], now])
            summary = {"scalar_count": len(parsed["scalars"]), "curve_count": len(parsed["curves"]), "media_count": len(parsed["media"])}
            conn.execute("UPDATE folder_import_jobs SET status='COMPLETED', summary_json=?, analysis_run_id=? WHERE id=?", [json.dumps(summary, ensure_ascii=False), run_id, job_id])
            conn.execute("UPDATE load_cases SET status='COMPLETED' WHERE id=?", [load_case_id])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"status": "IMPORTED", "job_id": job_id, "run_id": run_id, "run_no": next_run_no, "schema_id": parsed["schema_id"], "summary": summary}


@app.get("/api/assets/{asset_id}")
def get_result_asset(asset_id: str) -> FileResponse:
    with connect() as conn:
        item = conn.execute("SELECT file_path, mime_type FROM media_assets WHERE id=?", [asset_id]).fetchone()
    if not item:
        raise HTTPException(404, "결과 미디어를 찾을 수 없습니다.")
    path = (Path(__file__).resolve().parents[1] / "assets" / item[0]).resolve()
    assets_root = (Path(__file__).resolve().parents[1] / "assets").resolve()
    if assets_root not in path.parents or not path.is_file():
        raise HTTPException(404, "결과 미디어 파일을 찾을 수 없습니다.")
    return FileResponse(path, media_type=item[1])


@app.get("/api/load-cases/{load_case_id}/overview")
def get_load_case_overview(load_case_id: str) -> dict[str, Any]:
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
        product_information = rows(
            conn.execute(
                "SELECT category, name, value_text, file_path, metadata_json FROM product_information WHERE project_id = ? ORDER BY category, name",
                [load_case["project_id"]],
            )
        )
        run = conn.execute(
            "SELECT id FROM analysis_runs WHERE load_case_id = ? ORDER BY run_no DESC LIMIT 1",
            [load_case_id],
        ).fetchone()
        if run is None:
            load_case["parameters"] = json_value(load_case.pop("parameters_json"))
            for item in product_information:
                item["metadata"] = json_value(item.pop("metadata_json"))
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
                "SELECT * FROM scalar_results WHERE analysis_run_id = ? ORDER BY display_name",
                [run_id],
            )
        )
        time_series = rows(
            conn.execute(
                "SELECT * FROM time_series_results WHERE analysis_run_id = ? ORDER BY time_value, variable_key",
                [run_id],
            )
        )
        curves = rows(conn.execute("SELECT * FROM curve_results WHERE analysis_run_id = ? ORDER BY display_name, series_key", [run_id]))
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
    for item in template:
        item["input"] = json_value(item.pop("input_json"))
        item["generated_model"] = json_value(item.pop("generated_model_json"))
    for item in product_information:
        item["metadata"] = json_value(item.pop("metadata_json"))
    open_cell_results = [item for item in scalar_results if not item["variable_key"].startswith("chassis_rear_")]
    chassis_results = [item for item in scalar_results if item["variable_key"].startswith("chassis_rear_")]
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


@app.get("/api/projects/{project_id}/quality-thresholds")
def get_quality_thresholds(project_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(
            conn.execute(
                "SELECT * FROM quality_thresholds WHERE project_id = ? ORDER BY analysis_key, criterion_key",
                [project_id],
            )
        )


@app.put("/api/quality-thresholds/{criterion_key}")
def update_quality_threshold(criterion_key: str, payload: QualityThresholdUpdate) -> dict[str, Any]:
    with connect() as conn:
        criterion = conn.execute(
            "SELECT project_id, unit FROM quality_thresholds WHERE criterion_key = ?",
            [criterion_key],
        ).fetchone()
        if not criterion:
            raise HTTPException(404, "품질 판정 기준을 찾을 수 없습니다.")
        project_id, unit = criterion
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute("BEGIN TRANSACTION")
        try:
            conn.execute(
                """
                UPDATE quality_thresholds
                SET threshold_double = ?, updated_by = ?, updated_at = ?
                WHERE criterion_key = ?
                """,
                [payload.threshold_double, payload.updated_by, now, criterion_key],
            )
            if criterion_key == "chassis_rear_permanent_deformation_mm":
                conn.execute(
                    """
                    UPDATE scalar_results
                    SET threshold_double = ?,
                        verdict = CASE WHEN value_double >= ? THEN 'FAIL' ELSE 'PASS' END
                    WHERE variable_key LIKE 'chassis_rear_%_permanent_deformation'
                      AND analysis_run_id IN (
                          SELECT run.id
                          FROM analysis_runs run
                          JOIN load_cases lc ON lc.id = run.load_case_id
                          JOIN analysis_requests ar ON ar.id = lc.request_id
                          WHERE ar.project_id = ?
                      )
                    """,
                    [payload.threshold_double, payload.threshold_double, project_id],
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {
        "criterion_key": criterion_key,
        "threshold_double": payload.threshold_double,
        "unit": unit,
        "updated_by": payload.updated_by,
        "updated_at": now,
    }


@app.get("/api/requests/{request_id}/workflow")
def get_workflow(request_id: str) -> dict[str, Any]:
    with connect() as conn:
        request_data = rows(conn.execute("SELECT * FROM analysis_requests WHERE id = ?", [request_id]))
        if not request_data:
            raise HTTPException(404, "해석 의뢰를 찾을 수 없습니다.")
        steps = rows(conn.execute("SELECT * FROM request_steps WHERE request_id = ? ORDER BY sequence_no", [request_id]))
    progress = round(sum(step["progress"] for step in steps) / len(steps)) if steps else 0
    return {
        "request": request_data[0],
        "steps": steps,
        "progress": progress,
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
                GROUP BY ar.id, ar.project_id, ar.title, ar.status, ar.owner, ar.requested_at,
                         ar.due_at, ar.overall_note, p.name, p.product_name
                ORDER BY ar.requested_at DESC
                """
            )
        )
        result = []
        for request in requests:
            steps = rows(
                conn.execute(
                    "SELECT * FROM request_steps WHERE request_id = ? ORDER BY sequence_no",
                    [request["id"]],
                )
            )
            progress = round(sum(step["progress"] for step in steps) / len(steps)) if steps else 0
            result.append({"request": request, "steps": steps, "progress": progress})
    return result


@app.patch("/api/workflow-steps/{step_id}")
def update_workflow_step(step_id: str, payload: WorkflowStepUpdate) -> dict[str, Any]:
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(400, "단계 이름은 두 글자 이상이어야 합니다.")
    with connect() as conn:
        existing = conn.execute("SELECT id FROM request_steps WHERE id = ?", [step_id]).fetchone()
        if not existing:
            raise HTTPException(404, "작업 단계를 찾을 수 없습니다.")
        conn.execute("UPDATE request_steps SET name = ? WHERE id = ?", [name, step_id])
    return {"id": step_id, "name": name, "status": "saved"}


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
def create_variable(load_case_id: str, payload: VariableCreate) -> dict[str, Any]:
    with connect() as conn:
        try:
            return VariableCatalogRepository(conn).create(load_case_id, payload.model_dump())
        except (ValueError, LookupError) as exc:
            raise _catalog_error(exc) from exc


@app.put("/api/load-cases/{load_case_id}/variables/{variable_key}")
def update_variable(load_case_id: str, variable_key: str, payload: VariableUpdate) -> dict[str, Any]:
    with connect() as conn:
        try:
            return VariableCatalogRepository(conn).update(load_case_id, variable_key, payload.model_dump())
        except (ValueError, LookupError) as exc:
            raise _catalog_error(exc) from exc


@app.delete("/api/load-cases/{load_case_id}/variables/{variable_key}")
def delete_variable(load_case_id: str, variable_key: str, updated_by: str = Query(default="관리자", min_length=2, max_length=60)) -> dict[str, Any]:
    with connect() as conn:
        repository = VariableCatalogRepository(conn)
        references = repository.dashboard_references(load_case_id, variable_key)
        if references:
            raise HTTPException(409, detail={"message": "대시보드에서 사용 중인 변수는 삭제할 수 없습니다.", "dashboard_ids": references})
        try:
            repository.deactivate(load_case_id, variable_key, updated_by)
        except LookupError as exc:
            raise _catalog_error(exc) from exc
    return {"status": "deactivated", "variable_key": variable_key}


@app.get("/api/automation-templates")
def get_automation_templates(project_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        sql = """
            SELECT te.*, lc.name AS load_case_name, lc.analysis_type,
                   ar.id AS request_id, ar.title AS request_title,
                   p.id AS project_id, p.name AS project_name
            FROM template_executions te
            JOIN load_cases lc ON lc.id = te.load_case_id
            JOIN analysis_requests ar ON ar.id = lc.request_id
            JOIN projects p ON p.id = ar.project_id
        """
        params: list[Any] = []
        if project_id:
            sql += " WHERE p.id = ?"
            params.append(project_id)
        sql += " ORDER BY te.executed_at DESC"
        result = rows(conn.execute(sql, params))
    for item in result:
        item["input"] = json_value(item.pop("input_json"))
        item["generated_model"] = json_value(item.pop("generated_model_json"))
    return result


@app.get("/api/widget-catalog")
def get_widget_catalog() -> list[dict[str, Any]]:
    return [
        {"type": "kpi", "label": "KPI 카드", "category": "요약", "allowed_data_types": ["NUMBER", "VERDICT"], "default_size": [3, 2]},
        {"type": "verdict", "label": "패스/실패 판정", "category": "요약", "allowed_data_types": ["VERDICT", "NUMBER"], "default_size": [3, 2]},
        {"type": "gauge", "label": "임계값 게이지", "category": "차트", "allowed_data_types": ["NUMBER"], "default_size": [4, 3]},
        {"type": "edge_bar", "label": "막대그래프", "category": "차트", "allowed_data_types": ["NUMBER"], "default_size": [6, 4]},
        {"type": "time_series", "label": "시계열 그래프", "category": "차트", "allowed_data_types": ["TIME_SERIES", "CURVE"], "default_size": [8, 5]},
        {"type": "scatter", "label": "산점도", "category": "차트", "allowed_data_types": ["NUMBER", "FLOAT", "INTEGER", "TIME_SERIES", "CURVE"], "default_size": [6, 4]},
        {"type": "result_table", "label": "데이터 테이블", "category": "표", "allowed_data_types": ["NUMBER", "FLOAT", "INTEGER", "TEXT", "TIME_SERIES", "CURVE", "IMAGE", "VIDEO", "MODEL_3D"], "default_size": [12, 4]},
        {"type": "contour", "label": "컨투어 이미지", "category": "미디어", "allowed_data_types": ["IMAGE"], "default_size": [4, 3]},
        {"type": "video", "label": "영상 플레이어", "category": "미디어", "allowed_data_types": ["VIDEO"], "default_size": [6, 4]},
        {"type": "model3d", "label": "경량 3D 뷰어", "category": "미디어", "allowed_data_types": ["MODEL_3D"], "default_size": [6, 5]},
        {"type": "note", "label": "수행자 의견", "category": "텍스트", "allowed_data_types": ["TEXT"], "default_size": [4, 3]},
        {"type": "workflow", "label": "작업 흐름", "category": "프로세스", "allowed_data_types": ["STATUS"], "default_size": [12, 5]},
    ]


def _report_layout_item(item: dict[str, Any]) -> dict[str, Any]:
    definition = json_value(item.pop("definition_json")) or {}
    definition.update({
        "id": item["id"],
        "name": item["name"],
        "description": item.get("description") or "",
        "version": item["version"],
    })
    return {
        **item,
        "definition": definition,
    }


@app.get("/api/report-layouts")
def list_report_layouts() -> list[dict[str, Any]]:
    with connect() as conn:
        items = rows(conn.execute(
            "SELECT * FROM report_layouts WHERE is_active=true ORDER BY is_system DESC, updated_at DESC, name"
        ))
    return [_report_layout_item(item) for item in items]


@app.post("/api/report-layouts", status_code=201)
def create_report_layout(payload: ReportLayoutPayload) -> dict[str, Any]:
    layout_id = f"report-layout-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    definition = _validated_report_layout({**payload.definition, "id": layout_id, "name": payload.name.strip(), "description": payload.description.strip(), "version": 1})
    encoded = json.dumps(definition, ensure_ascii=False)
    with connect() as conn:
        conn.execute(
            "INSERT INTO report_layouts VALUES (?, ?, ?, 1, ?, false, true, ?, ?, ?)",
            [layout_id, payload.name.strip(), payload.description.strip(), encoded, now, now, payload.updated_by.strip()],
        )
        conn.execute(
            "INSERT INTO report_layout_versions VALUES (?, 1, ?, ?, ?, true)",
            [layout_id, encoded, payload.updated_by.strip(), now],
        )
    return {"id": layout_id, "name": payload.name.strip(), "description": payload.description.strip(), "version": 1, "definition": definition, "is_system": False, "updated_at": now, "updated_by": payload.updated_by.strip()}


@app.put("/api/report-layouts/{layout_id}")
def update_report_layout(layout_id: str, payload: ReportLayoutPayload) -> dict[str, Any]:
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
                [payload.name.strip(), payload.description.strip(), version, encoded, now, payload.updated_by.strip(), layout_id],
            )
            conn.execute(
                "INSERT INTO report_layout_versions VALUES (?, ?, ?, ?, ?, true)",
                [layout_id, version, encoded, payload.updated_by.strip(), now],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return {"id": layout_id, "name": payload.name.strip(), "description": payload.description.strip(), "version": version, "definition": definition, "is_system": bool(existing[1]), "updated_at": now, "updated_by": payload.updated_by.strip()}


@app.get("/api/report-layouts/{layout_id}/versions")
def get_report_layout_versions(layout_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute(
            "SELECT layout_id, version, created_by, created_at, is_valid FROM report_layout_versions WHERE layout_id=? ORDER BY version DESC",
            [layout_id],
        ))


@app.get("/api/report-layouts/{layout_id}/versions/{version}")
def get_report_layout_version(layout_id: str, version: int) -> dict[str, Any]:
    with connect() as conn:
        stored = conn.execute(
            "SELECT definition_json, created_by, created_at FROM report_layout_versions WHERE layout_id=? AND version=? AND is_valid=true",
            [layout_id, version],
        ).fetchone()
    if not stored:
        raise HTTPException(404, "보고서 레이아웃 버전을 찾을 수 없습니다.")
    return {"layout_id": layout_id, "version": version, "definition": json_value(stored[0]), "created_by": stored[1], "created_at": stored[2]}


@app.delete("/api/report-layouts/{layout_id}")
def delete_report_layout(layout_id: str) -> dict[str, str]:
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
def upload_report_template(payload: ReportTemplateUploadPayload) -> dict[str, Any]:
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
        conn.execute(
            "INSERT INTO report_template_assets VALUES (?, ?, ?, ?, ?, ?, true, ?, ?, ?)",
            [template_id, payload.name.strip(), Path(payload.filename).name, relative_path, inspected["slideCount"], json.dumps(definition, ensure_ascii=False), now, now, payload.updated_by.strip()],
        )
    return {"id": template_id, "name": payload.name.strip(), "filename": Path(payload.filename).name, "slide_count": inspected["slideCount"], "definition": definition, "created_at": now, "updated_at": now, "updated_by": payload.updated_by.strip()}


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
def render_report_template(template_id: str, payload: ReportTemplateRenderPayload) -> Response:
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
def delete_report_template(template_id: str) -> dict[str, str]:
    with connect() as conn:
        stored = conn.execute("SELECT file_path FROM report_template_assets WHERE id=? AND is_active=true", [template_id]).fetchone()
        if not stored:
            raise HTTPException(404, "PPTX 템플릿을 찾을 수 없습니다.")
        conn.execute("UPDATE report_template_assets SET is_active=false, updated_at=? WHERE id=?", [datetime.now(timezone.utc).replace(tzinfo=None), template_id])
    source_path = Path(__file__).resolve().parents[1] / "assets" / stored[0]
    if source_path.is_file() and source_path.parent.resolve() == REPORT_TEMPLATE_DIR.resolve():
        source_path.unlink()
    return {"status": "deactivated", "id": template_id}


@app.get("/api/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: str) -> dict[str, Any]:
    with connect() as conn:
        result = rows(conn.execute("SELECT * FROM dashboards WHERE id = ?", [dashboard_id]))
    if not result:
        raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
    item = result[0]
    definition = json_value(item.pop("definition_json"))
    definition["version"] = item["version"]
    definition["updated_at"] = item["updated_at"]
    return definition


@app.get("/api/dashboards")
def list_dashboards(project_id: str | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if project_id:
            return rows(conn.execute("SELECT id, project_id, request_id, load_case_id, name, description, version, updated_at FROM dashboards WHERE project_id = ? ORDER BY updated_at DESC", [project_id]))
        return rows(conn.execute("SELECT id, project_id, request_id, load_case_id, name, description, version, updated_at FROM dashboards ORDER BY updated_at DESC"))


@app.put("/api/dashboards/{dashboard_id}")
def save_dashboard(dashboard_id: str, definition: DashboardDefinition) -> dict[str, Any]:
    if dashboard_id != definition.id:
        raise HTTPException(400, "대시보드 ID가 일치하지 않습니다.")
    with connect() as conn:
        existing = conn.execute("SELECT version FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not existing:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        version = existing[0] + 1
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        conn.execute(
            """
            UPDATE dashboards
            SET name = ?, description = ?, version = ?, definition_json = ?, updated_at = ?
            WHERE id = ?
            """,
            [definition.name, definition.description, version, json.dumps(definition.model_dump(), ensure_ascii=False), now, dashboard_id],
        )
        conn.execute(
            "INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)",
            [dashboard_id, version, json.dumps(definition.model_dump(), ensure_ascii=False), "대시보드 사용자", now, True],
        )
    return {"status": "saved", "version": version, "updated_at": now}


@app.get("/api/dashboards/{dashboard_id}/versions")
def get_dashboard_versions(dashboard_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute("SELECT dashboard_id, version, created_by, created_at, is_valid FROM dashboard_versions WHERE dashboard_id = ? ORDER BY version DESC", [dashboard_id]))


@app.post("/api/dashboards/{dashboard_id}/clone", status_code=201)
def clone_dashboard(dashboard_id: str, payload: DashboardClone) -> dict[str, Any]:
    clone_id = f"dashboard-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        source = conn.execute("SELECT project_id, request_id, load_case_id, definition_json FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not source:
            raise HTTPException(404, "대시보드를 찾을 수 없습니다.")
        definition = json_value(source[3])
        definition.update({"id": clone_id, "name": payload.name.strip(), "description": payload.description.strip()})
        encoded = json.dumps(definition, ensure_ascii=False)
        conn.execute("INSERT INTO dashboards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [clone_id, source[0], source[1], source[2], definition["name"], definition["description"], 1, encoded, now])
        conn.execute("INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)", [clone_id, 1, encoded, payload.created_by.strip(), now, True])
    return {"id": clone_id, "version": 1, "status": "cloned"}


@app.post("/api/dashboards/{dashboard_id}/restore/{version}")
def restore_dashboard(dashboard_id: str, version: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        stored = conn.execute("SELECT definition_json FROM dashboard_versions WHERE dashboard_id = ? AND version = ? AND is_valid = true", [dashboard_id, version]).fetchone()
        current = conn.execute("SELECT version FROM dashboards WHERE id = ?", [dashboard_id]).fetchone()
        if not stored or not current:
            raise HTTPException(404, "복구 가능한 정상 버전을 찾을 수 없습니다.")
        definition = json_value(stored[0])
        next_version = current[0] + 1
        encoded = json.dumps(definition, ensure_ascii=False)
        conn.execute("UPDATE dashboards SET name = ?, description = ?, version = ?, definition_json = ?, updated_at = ? WHERE id = ?", [definition["name"], definition.get("description", ""), next_version, encoded, now, dashboard_id])
        conn.execute("INSERT INTO dashboard_versions VALUES (?, ?, ?, ?, ?, ?)", [dashboard_id, next_version, encoded, "복구 작업", now, True])
    return {"status": "restored", "version": next_version, "restored_from": version}


@app.post("/api/dashboard-commands/preview")
def preview_dashboard_command(payload: NaturalLanguageCommand) -> dict[str, Any]:
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
