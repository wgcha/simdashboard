from __future__ import annotations

from typing import Literal

from .models import AnalysisRun, AnalysisRunSummary, RunEvidence


def summarize_run(
    run: AnalysisRun,
    evidence: RunEvidence,
    catalog_units: dict[str, str | None],
    *,
    is_latest: bool,
) -> AnalysisRunSummary:
    overall_verdict: Literal["PASS", "FAIL", "NO_DATA"] = (
        "FAIL"
        if evidence["failed_scalar_verdicts"]
        else "PASS"
        if evidence["scalar_verdicts"]
        else "NO_DATA"
    )
    result_keys = evidence["result_keys"]
    warned = (
        not evidence["source_exists"]
        or bool(result_keys - set(catalog_units))
        or bool(set(catalog_units) - result_keys)
        or any(
            catalog_units.get(variable_key)
            and catalog_units[variable_key] != "-"
            and actual_unit
            and catalog_units[variable_key] != actual_unit
            for variable_key, actual_unit in evidence["result_units"]
        )
        or not evidence["validation_verdicts"]
    )
    failed = run["status"] != "COMPLETED" or "FAIL" in evidence["validation_verdicts"]
    trust_status: Literal["TRUSTED", "WARN", "FAIL"] = (
        "FAIL" if failed else "WARN" if warned else "TRUSTED"
    )
    return {
        **run,
        "overall_verdict": overall_verdict,
        "scalar_count": evidence["scalar_count"],
        "series_count": evidence["series_count"],
        "trust_status": trust_status,
        "is_latest": is_latest,
    }
