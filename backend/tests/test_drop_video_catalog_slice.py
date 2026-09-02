from __future__ import annotations

import ast
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

import app.main as main_module
from app.adapters.filesystem import drop_videos as drop_video_filesystem
from app.adapters.http.routers import drop_videos as drop_videos_router
from app.adapters.persistence.drop_videos import SQLDropVideoRepository
from app.application.drop_videos import queries as drop_video_queries
from app.application.drop_videos.queries import list_drop_videos
from app.database import json_value
from app.domains.drop_videos.errors import LoadCaseNotFoundError
from app.domains.drop_videos import policies as drop_video_policies
from app.main import app
from app.schemas.api import DropVideoPageResponse
from app.services.drop_video_demo import (
    DROP_VIDEO_DEMO_SCENES,
    build_demo_evaluation,
    summarize_demo_evaluations,
)


LOAD_CASE_ID = "loadcase-drop-bottom-001"
CONTEXT = (LOAD_CASE_ID, "하부 낙하", "DROP", "request-drop-001", "낙하 해석 요청")


class FakeRepository:
    def __init__(
        self,
        events: list[str],
        *,
        context: tuple[Any, ...] | None = CONTEXT,
        stored: list[dict[str, Any]] | None = None,
    ) -> None:
        self.events = events
        self.context = context
        self.stored = stored or []

    def load_case_context(self, load_case_id: str) -> tuple[Any, ...] | None:
        self.events.append(f"context:{load_case_id}")
        return self.context

    def authorize(self, callback: Any) -> None:
        self.events.append("authorize")
        callback(self)

    def list_drop_videos(self, load_case_id: str) -> list[dict[str, Any]]:
        self.events.append(f"list:{load_case_id}")
        return self.stored


def _provider(repository: FakeRepository, events: list[str]):
    @contextmanager
    def provide() -> Iterator[FakeRepository]:
        events.append("open")
        try:
            yield repository
        finally:
            events.append("close")

    return provide


def _stored(video_id: str, sort_order: int, **values: Any) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "scene_name": values.pop("scene_name", video_id),
        "file_size": values.pop("file_size", 10),
        "mime_type": values.pop("mime_type", "video/mp4"),
        "sort_order": sort_order,
        "metadata_json": values.pop("metadata_json", "{}"),
        **values,
    }


def _query(
    repository: FakeRepository,
    events: list[str],
    *,
    load_case_id: str = LOAD_CASE_ID,
    page: int = 1,
    page_size: int = 20,
    authorize: Any | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "storage_mode": lambda: "database-only",
        "demo_by_id": {},
        "json_value": json_value,
        "build_evaluation": build_demo_evaluation,
        "summarize": summarize_demo_evaluations,
    }
    policy.update(overrides)
    example_source = policy.pop("example_source", lambda _load_case_id: [])

    def allow(connection: object) -> None:
        assert connection is repository
        events.append("authorize-callback")

    return list_drop_videos(
        load_case_id,
        page,
        page_size,
        authorize or allow,
        _provider(repository, events),
        example_source,
        **policy,
    )


@pytest.mark.contract
def test_catalog_route_openapi_and_global_order_are_exact() -> None:
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    catalog = next(route for route in routes if route.path == "/api/load-cases/{load_case_id}/drop-videos")
    assert catalog.methods == {"GET"}
    assert catalog.endpoint.__module__ == drop_videos_router.__name__
    assert catalog.endpoint.__name__ == "get_drop_videos"
    assert (catalog.operation_id or catalog.unique_id) == (
        "get_drop_videos_api_load_cases__load_case_id__drop_videos_get"
    )
    assert catalog.response_model is DropVideoPageResponse

    operation = app.openapi()["paths"]["/api/load-cases/{load_case_id}/drop-videos"]["get"]
    assert operation["operationId"] == "get_drop_videos_api_load_cases__load_case_id__drop_videos_get"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DropVideoPageResponse"
    }
    parameters = {item["name"]: item for item in operation["parameters"]}
    assert parameters["load_case_id"]["required"] is True
    assert parameters["page"]["required"] is False
    assert parameters["page"]["schema"]["default"] == 1
    assert parameters["page"]["schema"]["minimum"] == 1
    assert parameters["page_size"]["required"] is False
    assert parameters["page_size"]["schema"] == {
        "type": "integer", "minimum": 1, "maximum": 20, "default": 20, "title": "Page Size",
    }

    index = routes.index(catalog)
    neighbors = routes[index - 1:index + 5]
    assert [
        (route.path, tuple(sorted(route.methods or ())), route.endpoint.__module__, route.endpoint.__name__)
        for route in neighbors
    ] == [
        ("/api/requests/{request_id}/load-cases", ("GET",), "app.adapters.http.routers.request_load_cases", "get_load_cases"),
        ("/api/load-cases/{load_case_id}/drop-videos", ("GET",), drop_videos_router.__name__, "get_drop_videos"),
        ("/api/drop-videos/{video_id}/content", ("HEAD",), "app.main", "get_drop_video_content"),
        ("/api/drop-videos/{video_id}/content", ("GET",), "app.main", "get_drop_video_content"),
        ("/api/drop-videos/{video_id}/download", ("HEAD",), "app.main", "download_drop_video"),
        ("/api/drop-videos/{video_id}/download", ("GET",), "app.main", "download_drop_video"),
    ]
    assert [route.operation_id or route.unique_id for route in neighbors[2:]] == [
        "get_drop_video_content_api_drop_videos__video_id__content_head",
        "get_drop_video_content",
        "download_drop_video_api_drop_videos__video_id__download_head",
        "download_drop_video",
    ]


@pytest.mark.contract
def test_main_relinquishes_catalog_and_architecture_ceiling_is_monotonic() -> None:
    main_path = Path(main_module.__file__)
    source = main_path.read_text(encoding="utf-8")
    assert "def get_drop_videos(" not in source
    assert "SELECT lc.id, lc.name, lc.analysis_type, ar.id, ar.title" not in source
    request_include = source.index("app.include_router(request_load_cases_router)")
    catalog_include = source.index("app.include_router(drop_videos_router)")
    content_route = source.index('@app.get("/api/drop-videos/{video_id}/content"')
    assert request_include < catalog_include < content_route

    tree = ast.parse(source)
    actual = sum(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        for node in ast.walk(tree)
    )
    baseline = json.loads(
        (main_path.parents[1] / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8")
    )
    assert actual <= 59
    assert baseline["execute_call_ceilings"]["app/main.py"] == actual
    for module in (drop_videos_router, drop_video_queries, drop_video_policies, drop_video_filesystem):
        assert Path(module.__file__).read_text(encoding="utf-8").count(".execute(") == 0


@pytest.mark.unit
def test_query_uses_one_repository_in_context_authorize_list_order_then_closes() -> None:
    events: list[str] = []
    repository = FakeRepository(events)

    def storage_mode() -> str:
        events.append("storage-mode")
        return "database-only"

    response = _query(
        repository,
        events,
        storage_mode=storage_mode,
        example_source=lambda _load_case_id: (_ for _ in ()).throw(
            AssertionError("database-only mode must not read the example source")
        ),
    )
    assert response["videos"] == []
    assert events == [
        "open", f"context:{LOAD_CASE_ID}", "authorize", "authorize-callback",
        f"list:{LOAD_CASE_ID}", "close", "storage-mode",
    ]


@pytest.mark.unit
def test_authorization_and_missing_load_case_fail_closed_before_demo_policy() -> None:
    denied_events: list[str] = []
    denied = FakeRepository(denied_events, stored=[_stored("must-not-read", 1)])

    def deny(_connection: object) -> None:
        denied_events.append("deny")
        raise PermissionError("denied")

    with pytest.raises(PermissionError, match="denied"):
        _query(
            denied,
            denied_events,
            authorize=deny,
            storage_mode=lambda: denied_events.append("storage-mode") or "dual-read",
            example_source=lambda _load_case_id: denied_events.append("example-source") or [],
            summarize=lambda _videos: denied_events.append("summary"),
        )
    assert denied_events == ["open", f"context:{LOAD_CASE_ID}", "authorize", "deny", "close"]

    missing_events: list[str] = []
    missing = FakeRepository(missing_events, context=None)
    with pytest.raises(LoadCaseNotFoundError, match="하중 경우를 찾을 수 없습니다."):
        _query(
            missing,
            missing_events,
            storage_mode=lambda: missing_events.append("storage-mode") or "dual-read",
            example_source=lambda _load_case_id: missing_events.append("example-source") or [],
            summarize=lambda _videos: missing_events.append("summary"),
        )
    assert missing_events == [
        "open", f"context:{LOAD_CASE_ID}", "authorize", "authorize-callback",
        f"list:{LOAD_CASE_ID}", "close",
    ]


@pytest.mark.unit
def test_demo_files_probe_and_projection_run_only_after_repository_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    repository = FakeRepository(events)
    scene = SimpleNamespace(video_id="demo-a", filename="demo-a.mp4", scene_name="Demo A", sort_order=2)

    class TrackingPath:
        def is_file(self) -> bool:
            events.append("source:is-file")
            return True

        def stat(self) -> Any:
            events.append("source:stat")
            return SimpleNamespace(st_size=33)

    class TrackingRoot:
        def __truediv__(self, filename: str) -> TrackingPath:
            events.append(f"source:{filename}")
            return TrackingPath()

    evaluation = {
        "overall_verdict": "PASS",
        "open_cell": {"critical_value": 1, "threshold": 75, "unit": "MPa", "verdict": "PASS", "metrics": {}},
        "chassis_rear": {"critical_value": 1, "threshold": 5, "unit": "mm", "verdict": "PASS", "metrics": {}},
    }

    def probe(path: Any) -> Any:
        assert isinstance(path, TrackingPath)
        events.append("probe")
        return SimpleNamespace(codec="h264", fast_start=True)

    def evaluate(item: Any) -> dict[str, Any]:
        assert item is scene
        events.append("evaluate")
        return evaluation

    def summarize(videos: list[dict[str, Any]]) -> dict[str, Any]:
        assert [item["video_id"] for item in videos] == ["demo-a"]
        events.append("summarize")
        return summarize_demo_evaluations(videos)

    source_root = TrackingRoot()

    def storage_mode() -> str:
        events.append("storage-mode")
        return "dual-read"

    def example_source(load_case_id: str) -> list[dict[str, Any]]:
        events.append("example-source")
        return drop_video_filesystem.example_drop_videos(load_case_id, frozenset({LOAD_CASE_ID}))

    monkeypatch.setattr(drop_video_filesystem, "DROP_VIDEO_DEMO_SCENES", (scene,))
    monkeypatch.setattr(drop_video_filesystem, "DROP_VIDEO_SOURCE_DIR", source_root)
    monkeypatch.setattr(drop_video_filesystem, "probe_mp4", probe)
    monkeypatch.setattr(drop_video_filesystem, "build_demo_evaluation", evaluate)

    response = _query(
        repository,
        events,
        storage_mode=storage_mode,
        example_source=example_source,
        summarize=summarize,
    )
    assert response["source"] == "EXAMPLE_ADAPTER"
    assert response["videos"][0] == {
        "video_id": "demo-a", "scene_id": "demo-a", "scene_name": "Demo A",
        "video_url": "/api/drop-videos/demo-a/content", "download_url": "/api/drop-videos/demo-a/download",
        "thumbnail_url": None, "duration": None, "file_size": 33, "format": "mp4", "codec": "h264",
        "fast_start": True, "sort_order": 2, "drop_direction": None, "drop_condition": None,
        "analysis_version": None, "evaluation": evaluation,
    }
    close_index = events.index("close")
    assert events[close_index + 1:] == [
        "storage-mode", "example-source", "source:demo-a.mp4", "source:is-file",
        "probe", "source:stat", "evaluate", "summarize",
    ]


@pytest.mark.unit
def test_stored_mapping_default_evaluation_and_no_demo_mixing_are_exact() -> None:
    events: list[str] = []
    scene = DROP_VIDEO_DEMO_SCENES[0]
    stored = [
        _stored(
            scene.video_id,
            2,
            scene_name="Known",
            file_size="12",
            metadata_json=json.dumps({
                "codec": "h264", "fast_start": True, "drop_direction": "BOTTOM",
                "drop_condition": "1.2m", "analysis_version": "v2",
            }),
        ),
        _stored("custom", 1, scene_name="Custom", file_size=7, mime_type="video/quicktime", metadata_json=None),
    ]
    repository = FakeRepository(events, stored=stored)

    class MustNotIterate:
        def __iter__(self) -> Any:
            raise AssertionError("stored results must not mix demo rows")

    def storage_mode() -> str:
        events.append("storage-mode")
        return "dual-read"

    def decode(value: Any) -> Any:
        events.append("decode")
        return json_value(value)

    def evaluate(item: Any) -> dict[str, Any]:
        events.append("evaluate")
        return build_demo_evaluation(item)

    def summarize(videos: list[dict[str, Any]]) -> dict[str, Any]:
        events.append("summarize")
        return summarize_demo_evaluations(videos)

    response = _query(
        repository,
        events,
        storage_mode=storage_mode,
        example_source=lambda _load_case_id: list(MustNotIterate()),
        demo_by_id={scene.video_id: scene},
        json_value=decode,
        build_evaluation=evaluate,
        summarize=summarize,
    )
    assert response["source"] == "DATABASE"
    assert [item["video_id"] for item in response["videos"]] == ["custom", scene.video_id]
    custom, known = response["videos"]
    assert custom == {
        "video_id": "custom", "scene_id": "custom", "scene_name": "Custom",
        "video_url": "/api/drop-videos/custom/content", "download_url": "/api/drop-videos/custom/download",
        "thumbnail_url": None, "duration": None, "file_size": 7, "format": "webm", "codec": None,
        "fast_start": None, "sort_order": 1, "drop_direction": None, "drop_condition": None,
        "analysis_version": None,
        "evaluation": {
            "overall_verdict": "PASS",
            "open_cell": {"critical_value": 0, "threshold": 75, "unit": "MPa", "verdict": "PASS", "metrics": {}},
            "chassis_rear": {"critical_value": 0, "threshold": 5, "unit": "mm", "verdict": "PASS", "metrics": {}},
        },
    }
    assert known == {
        "video_id": scene.video_id, "scene_id": scene.video_id, "scene_name": "Known",
        "video_url": f"/api/drop-videos/{scene.video_id}/content",
        "download_url": f"/api/drop-videos/{scene.video_id}/download", "thumbnail_url": None,
        "duration": None, "file_size": 12, "format": "mp4", "codec": "h264", "fast_start": True,
        "sort_order": 2, "drop_direction": "BOTTOM", "drop_condition": "1.2m",
        "analysis_version": "v2", "evaluation": build_demo_evaluation(scene),
    }
    close_index = events.index("close")
    assert events[close_index + 1:] == ["storage-mode", "decode", "evaluate", "decode", "summarize"]


@pytest.mark.unit
def test_sort_whole_summary_pagination_out_of_range_and_empty_contracts() -> None:
    stored = [_stored(video_id, order) for video_id, order in [("c", 2), ("b", 1), ("a", 1), ("e", 4), ("d", 3)]]
    summarized: list[list[str]] = []

    def summarize(videos: list[dict[str, Any]]) -> dict[str, Any]:
        summarized.append([item["video_id"] for item in videos])
        return summarize_demo_evaluations(videos)

    events: list[str] = []
    page = _query(FakeRepository(events, stored=stored), events, page=2, page_size=2, summarize=summarize)
    assert [item["video_id"] for item in page["videos"]] == ["c", "d"]
    assert page["summary"]["total_scenes"] == 5
    assert page["pagination"] == {
        "page": 2, "page_size": 2, "total_items": 5, "total_pages": 3,
        "has_previous": True, "has_next": True,
    }
    assert summarized == [["a", "b", "c", "d", "e"]]

    events = []
    outside = _query(FakeRepository(events, stored=stored), events, page=9, page_size=2, summarize=summarize)
    assert outside["videos"] == []
    assert outside["summary"]["total_scenes"] == 5
    assert outside["pagination"] == {
        "page": 9, "page_size": 2, "total_items": 5, "total_pages": 3,
        "has_previous": True, "has_next": False,
    }
    assert summarized[-1] == ["a", "b", "c", "d", "e"]

    events = []
    empty = _query(FakeRepository(events), events, page=2, page_size=20)
    assert empty["source"] == "DATABASE"
    assert empty["videos"] == []
    assert empty["summary"] == {
        "total_scenes": 0, "pass_count": 0, "fail_count": 0,
        "open_cell": {"pass_count": 0, "fail_count": 0, "threshold": 75.0, "unit": "MPa"},
        "chassis_rear": {"pass_count": 0, "fail_count": 0, "threshold": 5.0, "unit": "mm"},
    }
    assert empty["pagination"] == {
        "page": 2, "page_size": 20, "total_items": 0, "total_pages": 0,
        "has_previous": False, "has_next": False,
    }


@pytest.mark.unit
def test_dual_read_non_demo_empty_catalog_uses_example_source_without_rows() -> None:
    events: list[str] = []
    response = _query(
        FakeRepository(events, context=("other", "Other", "DROP", "request", "Request")),
        events,
        load_case_id="other",
        storage_mode=lambda: "dual-read",
        example_source=lambda _load_case_id: [],
    )
    assert response["source"] == "EXAMPLE_ADAPTER"
    assert response["videos"] == []
    assert response["pagination"]["total_items"] == 0


@pytest.mark.contract
def test_seeded_and_demo_http_contract_missing_404_and_storage_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app) as client:
        seeded = client.get(
            f"/api/load-cases/{LOAD_CASE_ID}/drop-videos",
            params={"page": 1, "page_size": 7},
        )
        assert seeded.status_code == 200
        assert seeded.json()["source"] == "DATABASE"
        assert seeded.json()["summary"] == {
            "total_scenes": 20, "pass_count": 9, "fail_count": 11,
            "open_cell": {"pass_count": 13, "fail_count": 7, "threshold": 75.0, "unit": "MPa"},
            "chassis_rear": {"pass_count": 12, "fail_count": 8, "threshold": 5.0, "unit": "mm"},
        }

        missing = client.get("/api/load-cases/missing/drop-videos")
        assert missing.status_code == 404
        assert missing.json() == {"detail": "요청한 리소스를 찾을 수 없습니다."}

        monkeypatch.setattr(SQLDropVideoRepository, "list_drop_videos", lambda _self, _load_case_id: [])
        monkeypatch.setattr(drop_videos_router.app_config, "media_storage_mode", lambda: "dual-read")
        demo = client.get(
            f"/api/load-cases/{LOAD_CASE_ID}/drop-videos",
            params={"page": 1, "page_size": 7},
        )
        assert demo.status_code == 200
        assert demo.json() == {**seeded.json(), "source": "EXAMPLE_ADAPTER"}

        monkeypatch.setattr(drop_videos_router.app_config, "media_storage_mode", lambda: "database-only")
        database_only = client.get(f"/api/load-cases/{LOAD_CASE_ID}/drop-videos")
        assert database_only.status_code == 200
        assert database_only.json()["source"] == "DATABASE"
        assert database_only.json()["videos"] == []
        assert database_only.json()["pagination"]["total_items"] == 0


@pytest.mark.contract
@pytest.mark.parametrize(
    ("params", "invalid_name"),
    [({"page": 0}, "page"), ({"page_size": 0}, "page_size"), ({"page_size": 21}, "page_size")],
)
def test_http_pagination_bounds_remain_422(params: dict[str, int], invalid_name: str) -> None:
    with TestClient(app) as client:
        response = client.get(f"/api/load-cases/{LOAD_CASE_ID}/drop-videos", params=params)
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == invalid_name
