"""Depth-schema editor reads on the SCX drive (issue #55 VoC: "깊이 스키마 불러오기" never finished).

On the real drive the editor's example folders (``POST …/depth-schema/samples``) walked the tree inside a read
session *with* a DB connection: every unread level was one miss round of up to 600 live listings (8 rounds),
no time bound, and the answer still lacked the folders of the last rounds.  The reads now run without a
connection and stop at a budget (``partial``).  In-memory fake adapter only.
"""
from __future__ import annotations

import time
import uuid

import pytest

from app.services import environment_folder_profiles as profiles
from tests.test_depth_schema import _restore_default_depth_schema  # noqa: F401  (autouse)
from tests.test_drive_reads import CSV, ENV, scx  # noqa: F401  (fixture)
from tests.test_folder_auto_discovery import _container_schema
from tests.test_new_scene_registration import admin_client  # noqa: F401  (fixture)

pytestmark = pytest.mark.duckdb_integration
SAMPLES = ENV + "/depth-schema/samples"
CHECK = ENV + "/depth-schema/check"
CONTAINER = "SPDM (Admin)"
UPPER = {"levels": [{"level": 1, "role": "CONTAINER"}, {"level": 2, "role": "PROJECT"}, {"level": 3, "role": "REQUEST"}]}


def _wide_tree(scx, projects: int, requests: int, keyword: str = "유통 환경") -> str:
    tag = uuid.uuid4().hex[:6]
    for p in range(projects):
        for r in range(requests):
            scx.add(f"{CONTAINER}/PROJ-{tag}-{p:03d}/WR-{p:03d}{r:02d}_{keyword}/Working/Case/Drop/Opt/Scene/{CSV}")
    return tag


def _list_calls(scx) -> int:
    return sum(1 for op, _rel in scx.drive.calls if op == "list_dir")


def test_distribution_samples_of_a_wide_tree_finish_complete_in_one_pass(scx):
    _container_schema()
    _wide_tree(scx, 10, 6)
    before = _list_calls(scx)
    body = scx.post(SAMPLES, {"segment": "DISTRIBUTION"})
    assert body["partial"] is False and "issues" not in body, body.get("issues")
    assert body["requests_sampled"] == 20
    assert [item["level"] for item in body["levels"]] == [1, 2, 3, 4, 5]
    assert body["levels"][0]["samples"] == [{"name": "Working", "count": 20}]
    assert body["run_option_names"] == [{"name": "Scene", "count": 60, "request_count": 60}]
    # root + container + 10 projects + (60 requests x 5 levels) + the 20 sampled requests: one pass, no re-walk rounds
    assert _list_calls(scx) - before <= 500, _list_calls(scx) - before


def test_usage_samples_stop_listing_projects_once_enough_requests_are_known(scx):
    _container_schema()
    _wide_tree(scx, 30, 6, keyword="사용 환경")
    before = _list_calls(scx)
    body = scx.post(SAMPLES, {"segment": "USAGE"})
    assert body["requests_sampled"] == 20 and body["partial"] is False
    # 20 requests = 4 projects of 6: the other 26 projects of the drive are never listed
    projects = [rel for op, rel in scx.drive.calls if op == "list_dir" and rel.rsplit("/", 1)[-1].startswith("PROJ-")]
    assert len(projects) <= 4, len(projects)


def test_upper_samples_probe_only_a_few_requests(scx, monkeypatch):
    _container_schema()
    _wide_tree(scx, 20, 6)
    before = _list_calls(scx)
    body = scx.post(SAMPLES, {"segment": "UPPER"})
    assert [item["level"] for item in body["levels"]] == [1, 2, 3, 4]
    assert body["levels"][2]["folder_count"] == 120 and body["levels"][3]["truncated"] is True
    assert _list_calls(scx) - before <= 1 + 1 + 20 + profiles.UPPER_PROBE_REQUESTS


def test_samples_stop_at_the_listing_budget_and_say_partial(scx, monkeypatch):
    _container_schema()
    _wide_tree(scx, 30, 6)
    monkeypatch.setattr(profiles, "SAMPLE_BUDGET_LISTINGS", 12)
    before = _list_calls(scx)
    response = scx.client.post(SAMPLES, json={"segment": "DISTRIBUTION"})
    assert response.status_code == 200, response.text
    assert response.json()["partial"] is True
    assert _list_calls(scx) - before <= 12


def test_samples_stop_at_the_time_budget_on_a_slow_drive(scx, monkeypatch):
    _container_schema()
    _wide_tree(scx, 30, 6)
    scx.drive.call_delay_s["list_dir"] = 0.05
    monkeypatch.setattr(profiles, "SAMPLE_BUDGET_SECONDS", 0.5)
    started = time.monotonic()
    body = scx.post(SAMPLES, {"segment": "DISTRIBUTION"})
    assert body["partial"] is True
    assert time.monotonic() - started < 5


def test_check_is_bounded_too_and_never_reads_with_a_connection(scx, monkeypatch):
    _container_schema()
    _wide_tree(scx, 10, 4)
    with profiles_connection() as conn:
        current = profiles.get_depth_schema(conn)
    environments = {env: {"environment_keyword": item["environment_keyword"], "lower": item["lower"],
                          "usage_sources": item.get("usage_sources")} for env, item in current["environments"].items()}
    full = scx.post(CHECK, {"upper": UPPER, "environments": environments})
    assert full["partial"] is False and full["requests_checked"] == 40
    monkeypatch.setattr(profiles, "CHECK_BUDGET_LISTINGS", 15)
    cut = scx.post(CHECK, {"upper": UPPER, "environments": environments})
    assert cut["partial"] is True


def profiles_connection():
    from app.database_connection import connect
    return connect()
