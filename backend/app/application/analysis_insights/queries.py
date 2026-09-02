from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...domains.analysis_insights.errors import (
    AnalysisRunNotFoundError,
    ComparisonRunsNotFoundError,
    MatchingRunComparisonError,
)
from ...domains.analysis_insights.policies import json_value
from ...domains.analysis_insights.ports import AnalysisInsightsRepositoryProvider


def compare_analysis_runs(
    load_case_id: str,
    baseline_run_id: str,
    target_run_id: str,
    variable_key: str | None,
    repository_provider: AnalysisInsightsRepositoryProvider,
) -> dict[str, Any]:
    if baseline_run_id == target_run_id:
        raise MatchingRunComparisonError()
    with repository_provider() as repository:
        run_rows = repository.comparison_runs(load_case_id, baseline_run_id, target_run_id)
        if len(run_rows) != 2:
            raise ComparisonRunsNotFoundError()
        run_map = {item["id"]: item for item in run_rows}
        scalar_rows = repository.comparison_scalars(baseline_run_id, target_run_id)
        scalars = {
            run_id: {item["variable_key"]: item for item in scalar_rows if item["analysis_run_id"] == run_id}
            for run_id in (baseline_run_id, target_run_id)
        }
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
            comparable = bool(
                baseline
                and target
                and baseline_value is not None
                and target_value is not None
                and baseline.get("unit") == target.get("unit")
            )
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
            comparison.append(
                {
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
                }
            )

        series_rows = repository.comparison_series(baseline_run_id, target_run_id)
        available_series = [
            {"variable_key": item["variable_key"], "display_name": item["display_name"], "unit": item["value_unit"]}
            for item in series_rows
        ]
        selected_key = (
            variable_key
            if any(item["variable_key"] == variable_key for item in available_series)
            else available_series[0]["variable_key"]
            if available_series
            else None
        )
        series_payload = None
        if selected_key:
            points = repository.comparison_points(baseline_run_id, target_run_id, selected_key)
            merged: dict[float, dict[str, Any]] = {}
            for point in points:
                item = merged.setdefault(
                    float(point["time_value"]),
                    {
                        "time_value": point["time_value"],
                        "time_unit": point["time_unit"],
                        "baseline_value": None,
                        "target_value": None,
                    },
                )
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


def get_analysis_run_trust(
    run_id: str,
    repository_provider: AnalysisInsightsRepositoryProvider,
    expected_load_case_id: str | None = None,
) -> dict[str, Any]:
    with repository_provider() as repository:
        run = repository.trust_run(run_id)
        if run is None or (expected_load_case_id and run["load_case_id"] != expected_load_case_id):
            raise AnalysisRunNotFoundError()
        latest_run_id = repository.latest_run_id(run["load_case_id"])
        metadata = repository.run_metadata(run_id)
        if metadata:
            metadata["metadata"] = json_value(metadata.pop("metadata_json"))
        import_job = repository.import_job(run_id)
        if import_job:
            import_job["summary"] = json_value(import_job.pop("summary_json"))

        counts = repository.result_counts(run_id)
        result_keys = repository.result_keys(run_id)
        catalog = repository.active_catalog(run["load_case_id"])
        unmapped = sorted(result_keys - set(catalog))
        missing = sorted(set(catalog) - result_keys)

        unit_mismatches: list[dict[str, str]] = []
        for variable_key, actual_unit in repository.result_unit_rows(run_id):
            expected_unit = catalog.get(variable_key, {}).get("unit")
            if expected_unit and expected_unit != "-" and actual_unit and expected_unit != actual_unit:
                mismatch = {"variable_key": variable_key, "expected": expected_unit, "actual": actual_unit}
                if mismatch not in unit_mismatches:
                    unit_mismatches.append(mismatch)

        validations = repository.validations(run_id)
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
            "is_latest": bool(latest_run_id == run_id),
            "age_days": age_days,
            "metadata": metadata,
            "import_job": import_job,
            "counts": counts,
            "coverage": {"result_variables": len(result_keys), "catalog_variables": len(catalog), "unmapped": unmapped, "missing": missing},
            "unit_mismatches": unit_mismatches,
            "validations": validations,
            "checks": checks,
        }
