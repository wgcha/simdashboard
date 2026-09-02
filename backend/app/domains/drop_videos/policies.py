from __future__ import annotations

from typing import Any, Callable

from .errors import LoadCaseNotFoundError


def build_page(
    context: tuple[Any, ...] | None,
    stored_videos: list[dict[str, Any]],
    example_videos: list[dict[str, Any]],
    page: int,
    page_size: int,
    *,
    database_only: bool,
    json_value: Callable[[Any], Any],
    build_evaluation: Callable[[Any], dict[str, Any]],
    demo_by_id: dict[str, Any],
    summarize: Callable[[list[dict[str, Any]]], dict[str, Any]],
) -> dict[str, Any]:
    if context is None:
        raise LoadCaseNotFoundError()
    videos = list(example_videos)
    if stored_videos:
        videos = []
        for item in stored_videos:
            scene = demo_by_id.get(item["video_id"])
            metadata = json_value(item.get("metadata_json")) or {}
            videos.append(
                {
                    "video_id": item["video_id"],
                    "scene_id": item["video_id"],
                    "scene_name": item["scene_name"],
                    "video_url": f"/api/drop-videos/{item['video_id']}/content",
                    "download_url": f"/api/drop-videos/{item['video_id']}/download",
                    "thumbnail_url": None,
                    "duration": None,
                    "file_size": int(item["file_size"]),
                    "format": "mp4" if str(item["mime_type"]) == "video/mp4" else "webm",
                    "codec": metadata.get("codec"),
                    "fast_start": metadata.get("fast_start"),
                    "sort_order": int(item["sort_order"]),
                    "drop_direction": metadata.get("drop_direction"),
                    "drop_condition": metadata.get("drop_condition"),
                    "analysis_version": metadata.get("analysis_version"),
                    "evaluation": build_evaluation(scene)
                    if scene
                    else {
                        "overall_verdict": "PASS",
                        "open_cell": {"critical_value": 0, "threshold": 75, "unit": "MPa", "verdict": "PASS", "metrics": {}},
                        "chassis_rear": {"critical_value": 0, "threshold": 5, "unit": "mm", "verdict": "PASS", "metrics": {}},
                    },
                }
            )
    videos.sort(key=lambda item: (item["sort_order"], item["scene_id"]))
    total_items = len(videos)
    total_pages = (total_items + page_size - 1) // page_size if total_items else 0
    start = (page - 1) * page_size
    return {
        "load_case": {
            "load_case_id": context[0],
            "load_case_name": context[1],
            "analysis_type": context[2],
            "request_id": context[3],
            "request_name": context[4],
        },
        "source": "DATABASE" if stored_videos or database_only else "EXAMPLE_ADAPTER",
        "demo_only": True,
        "evaluation_source": "SYNTHETIC_DEMO",
        "contract_version": 1,
        "summary": summarize(videos),
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_items": total_items,
            "total_pages": total_pages,
            "has_previous": page > 1 and total_pages > 0,
            "has_next": page < total_pages,
        },
        "videos": videos[start : start + page_size] if start < total_items else [],
    }
