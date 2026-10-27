"""Issue #55 (2026-10-27): Cases whose load-case folders carry a prefix (``completed_Drop``) were refused."""
from __future__ import annotations

import pytest

from app.services import dashboard_capture as capture


@pytest.mark.parametrize("name,kinds,ok", [
    ("Drop", capture._DISTRIBUTION_DIRS, True),
    ("completed_Drop", capture._DISTRIBUTION_DIRS, True),
    ("completed_ Drop", capture._DISTRIBUTION_DIRS, True),
    ("Clamping", capture._DISTRIBUTION_DIRS, True),
    ("Dropout", capture._DISTRIBUTION_DIRS, False),
    ("CAD", capture._DISTRIBUTION_DIRS, False),
    ("completed_Slope_Angle_360", capture._USAGE_DIRS, True),
    ("completed_Horizontal_Force_Angle", capture._USAGE_DIRS, True),
    ("completed_Settle", capture._USAGE_DIRS, True),
    ("Slope_Angle_3600", capture._USAGE_DIRS, False),
    ("completed_Drop", capture._USAGE_DIRS, False),
])
def test_load_case_names(name, kinds, ok):
    assert capture._is_load_case_name(name, kinds) is ok


def test_case_with_prefixed_load_case_folder_is_a_valid_capture_root(tmp_path):
    case = tmp_path / "PROJ" / "WR" / "Working" / "Package_Model_SetCase1_CushionCase2"
    (case / "completed_Drop" / "run" / "INDIVIDUAL" / "DAMP-2_Face_Drop_Scene02").mkdir(parents=True)
    rel = "PROJ/WR/Working/Package_Model_SetCase1_CushionCase2"
    assert capture._validate_case_root(tmp_path, rel, "DISTRIBUTION")
    with pytest.raises(capture.DashboardCaptureError):
        capture._validate_case_root(tmp_path, rel, "USAGE")


def test_case_invalid_failures_are_queued_again_on_start():
    from app.services import capture_jobs

    seen = {}

    class Conn:
        def execute(self, sql, params):
            seen["sql"], seen["params"] = sql, params
            return type("C", (), {"rowcount": 3})()

    assert capture_jobs.retry_fixed_failures(Conn()) == 3
    assert "status='FAILED'" in seen["sql"] and "SET status='PENDING',error_code=NULL" in seen["sql"]
    assert "DASHBOARD_CASE_INVALID" in seen["params"] and "DASHBOARD_SOURCE_MISSING" not in seen["params"]
    assert "DELETED" not in seen["params"]
