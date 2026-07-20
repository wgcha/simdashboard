from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .database import connect, initialize_database, json_value, rows


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


class AnalysisRequestCreate(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    owner: str = Field(min_length=2, max_length=60)
    due_in_days: int = Field(default=7, ge=1, le=365)
    overall_note: str = Field(default="", max_length=500)


class LoadCaseCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    analysis_type: Literal["DROP", "SIDE_CLAMP"]
    parameters: dict[str, Any] = Field(default_factory=dict)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/projects")
def get_projects() -> list[dict[str, Any]]:
    with connect() as conn:
        return rows(conn.execute("SELECT * FROM projects ORDER BY created_at DESC"))


@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectCreate) -> dict[str, Any]:
    project_id = f"project-{uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
            [project_id, payload.name.strip(), payload.product_name.strip(), payload.description.strip(), now],
        )
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
        result = rows(
            conn.execute(
                """
                SELECT DISTINCT sr.variable_key AS id, sr.display_name, 'NUMBER' AS data_type,
                       sr.unit, true AS filterable, 'scalar_results' AS source
                FROM scalar_results sr
                JOIN analysis_runs run ON run.id = sr.analysis_run_id
                WHERE run.load_case_id = ?
                UNION ALL
                SELECT DISTINCT ts.variable_key AS id, ts.display_name, 'TIME_SERIES' AS data_type,
                       ts.value_unit AS unit, true AS filterable, 'time_series_results' AS source
                FROM time_series_results ts
                JOIN analysis_runs run ON run.id = ts.analysis_run_id
                WHERE run.load_case_id = ?
                """,
                [load_case_id, load_case_id],
            )
        )
    for item in result:
        item["allowed_widgets"] = ["kpi", "edge_bar", "result_table"] if item["data_type"] == "NUMBER" else ["time_series"]
    return result


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
    return {"status": "saved", "version": version, "updated_at": now}


@app.post("/api/dashboard-commands/preview")
def preview_dashboard_command(payload: NaturalLanguageCommand) -> dict[str, Any]:
    text = payload.command.strip()
    compact = text.replace(" ", "").lower()
    widget: dict[str, Any] | None = None
    message = ""

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


from .schemas.result_import import ImportRequest
from .schemas.result_response import ImportJobResponse
from .services.result_import_service import ResultImportService
from pathlib import Path
import os

IMPORT_ROOT = os.environ.get("SIMDASH_IMPORT_ROOT", str(Path(__file__).resolve().parents[2] / "data" / "import"))

def get_import_service():
    return ResultImportService(IMPORT_ROOT)

@app.post("/api/result-imports/scan")
def scan_imports():
    service = get_import_service()
    return {"manifests": service.scan()}

@app.post("/api/result-imports/import")
def import_result(payload: ImportRequest):
    service = get_import_service()
    return service.import_result(payload.manifest_path)

@app.get("/api/result-imports")
def get_import_jobs():
    with connect() as conn:
        return rows(conn.execute("SELECT * FROM result_import_jobs ORDER BY imported_at DESC"))

@app.get("/api/result-imports/{job_id}")
def get_import_job(job_id: str):
    with connect() as conn:
        result = rows(conn.execute("SELECT * FROM result_import_jobs WHERE id = ?", [job_id]))
        if not result:
            raise HTTPException(404, "수집 이력을 찾을 수 없습니다.")
        return result[0]

@app.get("/api/analysis-runs/{run_id}/results")
def get_run_results(run_id: str):
    with connect() as conn:
        scalars = rows(conn.execute("SELECT * FROM scalar_results WHERE analysis_run_id = ?", [run_id]))
        timeseries = rows(conn.execute("SELECT * FROM time_series_results WHERE analysis_run_id = ?", [run_id]))
        run_data = rows(conn.execute("SELECT overall_verdict, result_import_status FROM analysis_runs WHERE id = ?", [run_id]))
        verdict = run_data[0]["overall_verdict"] if run_data else "NO_DATA"
        status = run_data[0]["result_import_status"] if run_data else "NONE"
        return {
            "scalar_results": scalars,
            "time_series_results": timeseries,
            "overall_verdict": verdict,
            "status": status
        }

@app.get("/api/load-cases/{load_case_id}/analysis-runs")
def get_load_case_runs(load_case_id: str):
    with connect() as conn:
        return rows(conn.execute("SELECT * FROM analysis_runs WHERE load_case_id = ? ORDER BY run_no DESC", [load_case_id]))
