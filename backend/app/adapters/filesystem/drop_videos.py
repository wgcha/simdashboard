from __future__ import annotations

from typing import Any

from ...services.drop_video_demo import DROP_VIDEO_DEMO_SCENES, DROP_VIDEO_SOURCE_DIR, build_demo_evaluation, probe_mp4


def example_drop_videos(load_case_id: str, demo_load_case_ids: frozenset[str]) -> list[dict[str, Any]]:
    if load_case_id not in demo_load_case_ids:
        return []
    videos: list[dict[str, Any]] = []
    for scene in DROP_VIDEO_DEMO_SCENES:
        path = DROP_VIDEO_SOURCE_DIR / scene.filename
        if not path.is_file():
            continue
        media = probe_mp4(path)
        videos.append({
            "video_id": scene.video_id, "scene_id": scene.video_id, "scene_name": scene.scene_name,
            "video_url": f"/api/drop-videos/{scene.video_id}/content",
            "download_url": f"/api/drop-videos/{scene.video_id}/download", "thumbnail_url": None,
            "duration": None, "file_size": path.stat().st_size, "format": "mp4",
            "codec": media.codec, "fast_start": media.fast_start, "sort_order": scene.sort_order,
            "drop_direction": None, "drop_condition": None, "analysis_version": None,
            "evaluation": build_demo_evaluation(scene),
        })
    return videos
