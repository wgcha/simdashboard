"""Register the reproducible Radioss example through the public API."""
from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any


BASE = "http://127.0.0.1:8000"


def post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(BASE + path, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def register_example(
    *,
    post_request: Callable[[str, dict[str, Any]], dict[str, Any]] = post,
    csv_path: Path | None = None,
) -> dict[str, Any]:
    """Register the complete project → request → load case → result flow."""
    project = post_request(
        "/api/projects",
        {
            "name": "Radioss CSV 등록 검토",
            "product_name": "ORION-65-OLED Example",
            "manufacturer": "NeoView Display",
            "display_size_inch": 65,
            "description": "합성 Radioss CSV 대리 등록 및 검산",
        },
    )
    request = post_request(
        f"/api/projects/{project['id']}/requests",
        {
            "title": "TV 포장 낙하 예제 등록 검토",
            "owner_user_id": "local-admin",
            "owner": "예제 검토자",
            "due_in_days": 7,
            "overall_note": "합성 데이터 등록 흐름 검증",
            "source_type": "EXTERNAL_SYSTEM",
            "source_reference": "example/radioss_tv_result_example.csv",
            "requested_by": "로컬 관리자",
            "assigned_by": "로컬 관리자",
            "request_type_id": "design-reliability-validation",
            "request_type_version": 1,
        },
    )
    load_case = post_request(
        f"/api/requests/{request['id']}/load-cases",
        {
            "name": "Bottom Face 450 mm Radioss Example",
            "analysis_type": "DROP",
            "parameters": {
                "drop_height_mm": 450,
                "direction": "BOTTOM",
                "source": "example/radioss_tv_result_example.csv",
            },
        },
    )
    result_file = csv_path or Path(__file__).with_name("radioss_tv_result_example.csv")
    result = post_request(
        f"/api/load-cases/{load_case['id']}/results/import",
        {
            "filename": result_file.name,
            "content": result_file.read_text(encoding="utf-8-sig"),
            "author": "예제 검토자",
            "validate_only": False,
        },
    )
    return {
        "project_id": project["id"],
        "request_id": request["id"],
        "load_case_id": load_case["id"],
        **{
            key: result[key]
            for key in (
                "run_id", "source_format", "node_count", "element_count", "scalar_count",
                "time_series_count", "fail_count", "overall_verdict",
            )
        },
    }


if __name__ == "__main__":
    print(json.dumps(register_example(), ensure_ascii=False, indent=2))
