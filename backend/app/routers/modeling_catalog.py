from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..database import json_value
from ..database_connection import connect, rows


router = APIRouter(prefix="/api", tags=["modeling-catalog"])


@router.get("/automation-templates")
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


@router.get("/widget-catalog")
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
        {"type": "video_grid", "label": "낙하 영상 비교", "category": "미디어", "allowed_data_types": ["VIDEO"], "default_size": [12, 10]},
        {"type": "model3d", "label": "경량 3D 뷰어", "category": "미디어", "allowed_data_types": ["MODEL_3D"], "default_size": [6, 5]},
        {"type": "note", "label": "수행자 의견", "category": "텍스트", "allowed_data_types": ["TEXT"], "default_size": [4, 3]},
        {"type": "workflow", "label": "작업 흐름", "category": "프로세스", "allowed_data_types": ["STATUS"], "default_size": [12, 5]},
    ]
