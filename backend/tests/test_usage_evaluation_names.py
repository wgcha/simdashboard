"""Issue #55 (2026-10-28): usage evaluation folders carry a prefix (completed_Settle …)."""
from __future__ import annotations

import pytest

from app.domains.dashboard.parser import scene_sequence_label, usage_evaluation_name
from app.services import usage_source_review


@pytest.mark.parametrize("name,expected", [
    ("Settle", "Settle"), ("completed_Settle", "Settle"), ("completed_Wobble", "Wobble"),
    ("completed_Horizontal_Force_Angle", "Horizontal_Force_Angle"),
    ("completed_Slope_Angle", "Slope_Angle"), ("completed_Slope_Angle_360", "Slope_Angle_360"),
    ("COMPLETED_slope_angle_360", "Slope_Angle_360"), ("completed_ Settle", "Settle"),
    ("Slope_Angle_3600", None), ("Settlement", None), ("1_Face", None),
])
def test_usage_evaluation_name(name, expected):
    assert usage_evaluation_name(name) == expected


def test_files_under_prefixed_evaluation_folders_are_found():
    base = "Working/Assy_Motion_75R9J_Set0907_Stand0907_Inner"
    path = f"{base}/completed_Slope_Angle_360/results/cond_slope_angle_front_360_result.csv"
    assert usage_source_review.evaluation_for_path(path) == "Slope_Angle_360"
    assert usage_source_review.evaluation_for_path(f"{base}/completed_Settle/x_settle_result.csv") == "Settle"


def test_evaluation_folders_are_labelled_by_evaluation_not_first_letters():
    assert scene_sequence_label("completed_Settle") == "Settle"
    assert scene_sequence_label("completed_Slope_Angle_360") == "Slope_Angle_360"
    assert scene_sequence_label("DAMP-13_Corner_Drop") == "13"
