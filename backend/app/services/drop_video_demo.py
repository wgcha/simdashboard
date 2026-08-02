from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal


Verdict = Literal["PASS", "FAIL"]

DROP_VIDEO_SOURCE_DIR = Path(__file__).resolve().parents[3] / "video_example"
DEMO_DROP_VIDEO_LOAD_CASE_IDS = frozenset({"loadcase-drop-bottom-001"})
OPEN_CELL_THRESHOLD_MPA = 75.0
CHASSIS_REAR_THRESHOLD_MM = 5.0


@dataclass(frozen=True)
class DemoDropVideoScene:
    video_id: str
    filename: str
    scene_name: str
    sort_order: int
    open_cell_critical_mpa: float
    chassis_rear_critical_mm: float


# These values are deterministic synthetic demo fixtures. They are deliberately
# independent from the seeded overview/run results and must not be presented as
# solver output.
DROP_VIDEO_DEMO_SCENES: tuple[DemoDropVideoScene, ...] = (
    DemoDropVideoScene("drop-analysis", "tv_drop_analysis_simulation.mp4", "기준 낙하 해석", 1, 68.2, 4.2),
    DemoDropVideoScene("drop-scene-01", "tv_drop_simulation_variant_01.mp4", "낙하 비교 Scene 01", 2, 76.8, 4.4),
    DemoDropVideoScene("drop-scene-02", "tv_drop_simulation_variant_02.mp4", "낙하 비교 Scene 02", 3, 70.1, 5.3),
    DemoDropVideoScene("drop-scene-03", "tv_drop_simulation_variant_03.mp4", "낙하 비교 Scene 03", 4, 82.5, 5.7),
    DemoDropVideoScene("drop-scene-04", "tv_drop_simulation_variant_04.mp4", "낙하 비교 Scene 04", 5, 62.4, 3.8),
    DemoDropVideoScene("drop-scene-05", "tv_drop_simulation_variant_05.mp4", "낙하 비교 Scene 05", 6, 73.9, 4.9),
    DemoDropVideoScene("drop-scene-06", "tv_drop_simulation_variant_06.mp4", "낙하 비교 Scene 06", 7, 75.0, 5.0),
    DemoDropVideoScene("drop-scene-07", "tv_drop_simulation_variant_07.mp4", "낙하 비교 Scene 07", 8, 79.1, 4.1),
    DemoDropVideoScene("drop-scene-08", "tv_drop_simulation_variant_08.mp4", "낙하 비교 Scene 08", 9, 66.7, 4.6),
    DemoDropVideoScene("drop-scene-09", "tv_drop_simulation_variant_09.mp4", "낙하 비교 Scene 09", 10, 71.8, 5.2),
    DemoDropVideoScene("drop-scene-10", "tv_drop_simulation_variant_10.mp4", "낙하 비교 Scene 10", 11, 84.3, 6.1),
    DemoDropVideoScene("drop-scene-11", "tv_drop_simulation_variant_11.mp4", "낙하 비교 Scene 11", 12, 69.5, 4.3),
    DemoDropVideoScene("drop-scene-12", "tv_drop_simulation_variant_12.mp4", "낙하 비교 Scene 12", 13, 74.6, 4.8),
    DemoDropVideoScene("drop-scene-13", "tv_drop_simulation_variant_13.mp4", "낙하 비교 Scene 13", 14, 77.2, 4.7),
    DemoDropVideoScene("drop-scene-14", "tv_drop_simulation_variant_14.mp4", "낙하 비교 Scene 14", 15, 64.9, 5.5),
    DemoDropVideoScene("drop-scene-15", "tv_drop_simulation_variant_15.mp4", "낙하 비교 Scene 15", 16, 72.3, 3.9),
    DemoDropVideoScene("drop-scene-16", "tv_drop_simulation_variant_16.mp4", "낙하 비교 Scene 16", 17, 80.6, 5.1),
    DemoDropVideoScene("drop-scene-17", "tv_drop_simulation_variant_17.mp4", "낙하 비교 Scene 17", 18, 67.4, 4.0),
    DemoDropVideoScene("drop-scene-18", "tv_drop_simulation_variant_18.mp4", "낙하 비교 Scene 18", 19, 73.1, 4.5),
    DemoDropVideoScene("drop-scene-19", "tv_drop_simulation_variant_19.mp4", "낙하 비교 Scene 19", 20, 78.0, 5.4),
)

DROP_VIDEO_DEMO_BY_ID = {scene.video_id: scene for scene in DROP_VIDEO_DEMO_SCENES}


@dataclass(frozen=True)
class Mp4Probe:
    codec: str | None
    fast_start: bool | None


def _read_box_header(handle: Any, offset: int, file_size: int) -> tuple[str, int, int] | None:
    if offset + 8 > file_size:
        return None
    handle.seek(offset)
    header = handle.read(16)
    if len(header) < 8:
        return None
    box_size = int.from_bytes(header[:4], "big")
    box_type = header[4:8].decode("latin-1")
    header_size = 8
    if box_size == 1:
        if len(header) < 16:
            return None
        box_size = int.from_bytes(header[8:16], "big")
        header_size = 16
    elif box_size == 0:
        box_size = file_size - offset
    if box_size < header_size or offset + box_size > file_size:
        return None
    return box_type, box_size, header_size


@lru_cache(maxsize=128)
def _probe_mp4_cached(path_text: str, file_size: int, modified_ns: int) -> Mp4Probe:
    del modified_ns  # The cache key invalidates replaced files even when the size is unchanged.
    path = Path(path_text)
    moov_offset: int | None = None
    mdat_offset: int | None = None
    moov_payload = b""
    try:
        with path.open("rb") as handle:
            offset = 0
            while offset < file_size:
                header = _read_box_header(handle, offset, file_size)
                if header is None:
                    break
                box_type, box_size, header_size = header
                if box_type == "moov":
                    moov_offset = offset
                    # Demo files have a compact fast-start moov. Bound the read so a
                    # malformed or unexpectedly large upload cannot consume memory.
                    if box_size <= 32 * 1024 * 1024:
                        handle.seek(offset + header_size)
                        moov_payload = handle.read(box_size - header_size)
                elif box_type == "mdat" and mdat_offset is None:
                    mdat_offset = offset
                offset += box_size
    except OSError:
        return Mp4Probe(None, None)

    codec = None
    if b"avc1" in moov_payload or b"avc3" in moov_payload:
        codec = "h264"
    elif b"mp4v" in moov_payload:
        codec = "mpeg4-part2"
    fast_start = None if moov_offset is None or mdat_offset is None else moov_offset < mdat_offset
    return Mp4Probe(codec, fast_start)


def probe_mp4(path: Path) -> Mp4Probe:
    try:
        stat = path.stat()
    except OSError:
        return Mp4Probe(None, None)
    return _probe_mp4_cached(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def _metric_values(critical: float, scene_number: int, keys: tuple[str, ...]) -> dict[str, float]:
    factors = (0.78, 0.84, 0.89, 0.93, 0.81, 0.87)
    values = {key: round(critical * factors[index], 2) for index, key in enumerate(keys)}
    values[keys[(scene_number - 1) % len(keys)]] = critical
    return values


def build_demo_evaluation(scene: DemoDropVideoScene) -> dict[str, Any]:
    open_cell_verdict: Verdict = "FAIL" if scene.open_cell_critical_mpa > OPEN_CELL_THRESHOLD_MPA else "PASS"
    chassis_verdict: Verdict = "FAIL" if scene.chassis_rear_critical_mm >= CHASSIS_REAR_THRESHOLD_MM else "PASS"
    overall_verdict: Verdict = "PASS" if open_cell_verdict == chassis_verdict == "PASS" else "FAIL"
    return {
        "overall_verdict": overall_verdict,
        "open_cell": {
            "critical_value": scene.open_cell_critical_mpa,
            "threshold": OPEN_CELL_THRESHOLD_MPA,
            "unit": "MPa",
            "verdict": open_cell_verdict,
            "metrics": _metric_values(scene.open_cell_critical_mpa, scene.sort_order, ("top_edge", "bottom_edge", "left_edge", "right_edge")),
        },
        "chassis_rear": {
            "critical_value": scene.chassis_rear_critical_mm,
            "threshold": CHASSIS_REAR_THRESHOLD_MM,
            "unit": "mm",
            "verdict": chassis_verdict,
            "metrics": _metric_values(scene.chassis_rear_critical_mm, scene.sort_order, ("top_gap", "bottom_gap", "top_left_corner", "top_right_corner", "bottom_left_corner", "bottom_right_corner")),
        },
    }


def summarize_demo_evaluations(videos: list[dict[str, Any]]) -> dict[str, Any]:
    evaluations = [item["evaluation"] for item in videos]
    return {
        "total_scenes": len(evaluations),
        "pass_count": sum(item["overall_verdict"] == "PASS" for item in evaluations),
        "fail_count": sum(item["overall_verdict"] == "FAIL" for item in evaluations),
        "open_cell": {
            "pass_count": sum(item["open_cell"]["verdict"] == "PASS" for item in evaluations),
            "fail_count": sum(item["open_cell"]["verdict"] == "FAIL" for item in evaluations),
            "threshold": OPEN_CELL_THRESHOLD_MPA,
            "unit": "MPa",
        },
        "chassis_rear": {
            "pass_count": sum(item["chassis_rear"]["verdict"] == "PASS" for item in evaluations),
            "fail_count": sum(item["chassis_rear"]["verdict"] == "FAIL" for item in evaluations),
            "threshold": CHASSIS_REAR_THRESHOLD_MM,
            "unit": "mm",
        },
    }
