from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _load_registration_module() -> Any:
    path = Path(__file__).resolve().parents[2] / "example" / "register_example.py"
    spec = importlib.util.spec_from_file_location("simdashboard_register_example", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_register_example_posts_a_complete_project_to_result_flow() -> None:
    module = _load_registration_module()
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((path, payload))
        if path == "/api/projects":
            return {"id": "project-example"}
        if path == "/api/projects/project-example/requests":
            return {"id": "request-example"}
        if path == "/api/requests/request-example/load-cases":
            return {"id": "loadcase-example"}
        if path == "/api/load-cases/loadcase-example/results/import":
            return {
                "run_id": "run-example", "source_format": "RADioss CSV", "node_count": 2,
                "element_count": 1, "scalar_count": 1, "time_series_count": 1,
                "fail_count": 0, "overall_verdict": "PASS",
            }
        raise AssertionError(f"unexpected POST {path}")

    csv_path = Path(__file__).resolve().parents[2] / "example" / "radioss_tv_result_example.csv"
    result = module.register_example(post_request=fake_post, csv_path=csv_path)

    assert [path for path, _ in calls] == [
        "/api/projects",
        "/api/projects/project-example/requests",
        "/api/requests/request-example/load-cases",
        "/api/load-cases/loadcase-example/results/import",
    ]
    request_payload = calls[1][1]
    assert request_payload["owner_user_id"] == "local-admin"
    assert request_payload["source_type"] == "EXTERNAL_SYSTEM"
    assert request_payload["source_reference"] == "example/radioss_tv_result_example.csv"
    assert request_payload["request_type_id"] == "design-reliability-validation"
    assert request_payload["request_type_version"] == 1
    assert calls[2][1]["analysis_type"] == "DROP"
    assert calls[3][1]["filename"] == csv_path.name
    assert calls[3][1]["content"]
    assert result == {
        "project_id": "project-example", "request_id": "request-example", "load_case_id": "loadcase-example",
        "run_id": "run-example", "source_format": "RADioss CSV", "node_count": 2,
        "element_count": 1, "scalar_count": 1, "time_series_count": 1,
        "fail_count": 0, "overall_verdict": "PASS",
    }
