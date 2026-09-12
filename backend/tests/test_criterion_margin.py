import pytest

from app.domains.analysis_insights.criterion_margin import criterion_margin
from app.folder_import import FolderImportError
from app.services.run_criteria_metadata import validate_result_criteria

pytestmark = pytest.mark.unit


def margin(value, spec):
    return criterion_margin(
        {"analysis_run_id": "run", "variable_key": "metric", "value_double": value, "unit": "mm", "threshold_double": 999, "verdict": "PASS"},
        {"id": "run", "metadata_json": {"result_criteria": {"metric": spec}}},
    )


@pytest.mark.parametrize("operator,bounds,value,distance,meets", [
    ("LT", {"upper": 0}, 0, 0, False),
    ("LTE", {"upper": 0}, 0, 0, True),
    ("GT", {"lower": -2}, -2, 0, False),
    ("GTE", {"lower": -2}, -2, 0, True),
    ("LTE", {"upper": -2}, -5, 3, True),
    ("GT", {"lower": -2}, -3, -1, False),
    ("BETWEEN", {"lower": -2, "upper": 3}, -3, -1, False),
    ("BETWEEN", {"lower": -2, "upper": 3}, 4, -1, False),
    ("BETWEEN", {"lower": 0, "upper": 0}, 0, 0, True),
])
def test_recorded_boundary_semantics(operator, bounds, value, distance, meets):
    result = margin(value, {"operator": operator, "unit": "mm", **bounds})
    assert result["status"] == "AVAILABLE"
    assert result["value"] == distance
    assert result["meets_criterion"] is meets


@pytest.mark.parametrize("spec", [
    {"operator": [], "upper": 1, "unit": "mm"},
    {"operator": {}, "upper": 1, "unit": "mm"},
    {"operator": "LTE", "upper": 10 ** 400, "unit": "mm"},
    {"operator": "LTE", "upper": float("inf"), "unit": "mm"},
    {"operator": "LTE", "upper": True, "unit": "mm"},
    {"operator": "LTE", "upper": 1, "lower": 0, "unit": "mm"},
    {"operator": "LTE", "upper": 1, "unit": " "},
    {"operator": "BETWEEN", "lower": 2, "upper": 1, "unit": "mm"},
])
def test_malformed_records_fail_closed_at_import_and_query(spec):
    with pytest.raises(FolderImportError) as error:
        validate_result_criteria({"metric": spec})
    assert error.value.code == "RESULT_CRITERIA_INVALID"
    assert margin(0, spec)["status"] == "UNKNOWN"


def test_overflow_and_non_numeric_results_are_not_json_numbers():
    assert margin(-1e308, {"operator": "LTE", "upper": 1e308, "unit": "mm"})["status"] == "UNKNOWN"
    for value in (None, True, float("nan"), 10 ** 400):
        assert margin(value, {"operator": "LTE", "upper": 1, "unit": "mm"})["reason"] == "NON_NUMERIC_VALUE"


def test_wrong_run_source_is_not_applied():
    result = criterion_margin(
        {"analysis_run_id": "one", "variable_key": "metric", "value_double": 0, "unit": "mm"},
        {"id": "two", "metadata_json": {"result_criteria": {"metric": {"operator": "LTE", "upper": 1, "unit": "mm"}}}},
    )
    assert result["status"] == "UNKNOWN"
