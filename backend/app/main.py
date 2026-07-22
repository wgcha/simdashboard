from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .database import connect, initialize_database, json_value, rows
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
    data_type: Literal["NUMBER", "TIME_SERIES"]
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


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/projects")
def get_projects() -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute("SELECT * FROM projects ORDER BY created_at DESC"))


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
        {"type": "time_series", "label": "시계열 그래프", "category": "차트", "allowed_data_types": ["TIME_SERIES"], "default_size": [8, 5]},
        {"type": "scatter", "label": "산점도", "category": "차트", "allowed_data_types": ["NUMBER", "TIME_SERIES"], "default_size": [6, 4]},
        {"type": "result_table", "label": "데이터 테이블", "category": "표", "allowed_data_types": ["NUMBER", "TIME_SERIES", "TEXT"], "default_size": [12, 4]},
        {"type": "contour", "label": "컨투어 이미지", "category": "미디어", "allowed_data_types": ["IMAGE"], "default_size": [4, 3]},
        {"type": "video", "label": "영상 플레이어", "category": "미디어", "allowed_data_types": ["VIDEO"], "default_size": [6, 4]},
        {"type": "model3d", "label": "경량 3D 뷰어", "category": "미디어", "allowed_data_types": ["MODEL_3D"], "default_size": [6, 5]},
        {"type": "note", "label": "수행자 의견", "category": "텍스트", "allowed_data_types": ["TEXT"], "default_size": [4, 3]},
        {"type": "workflow", "label": "작업 흐름", "category": "프로세스", "allowed_data_types": ["STATUS"], "default_size": [12, 5]},
    ]


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
