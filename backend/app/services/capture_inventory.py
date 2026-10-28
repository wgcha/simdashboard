"""File inventory and size guard of one Case capture attempt (issue #55).

A capture of a big DISTRIBUTION Case once ended in a PostgreSQL ``ProgramLimitExceeded`` (jsonb element
total above 268435455 bytes): every csv value becomes one observation object in ``payload_json``.  This
module measures the result *before* the INSERT and describes what the attempt took and skipped, so that

* ``dashboard_capture.create_capture`` can refuse an oversized capture with ``DASHBOARD_CAPTURE_TOO_LARGE``
  (a clear code and message instead of a database error),
* one bounded WARNING line per attempt reaches the server log (only WARNING+ is kept there), and
* the same dict is kept with the capture job (in memory, ``capture_jobs.record_inventory``) for the
  「결과 받기 상태」 panel.

The inventory never contains file contents or secrets: only relative paths, counts and sizes.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

MIB = 1024 * 1024
# PostgreSQL refuses a jsonb whose elements total 268435455 bytes; stay well below it.
DEFAULT_MAX_PAYLOAD_BYTES = 128 * MIB   # jsonb is larger than its JSON text; 224 MB JSON already failed at the 256 MB jsonb limit
ENV_MAX_PAYLOAD = "SIMDASH_CAPTURE_MAX_PAYLOAD_BYTES"
# Raw files of one capture (stored as BYTEA assets); same value as the long-standing walk limit.
DEFAULT_MAX_ASSET_TOTAL_BYTES = 256 * MIB
ENV_MAX_ASSETS = "SIMDASH_CAPTURE_MAX_ASSET_TOTAL_BYTES"
TOP_N = 5
MAX_SKIPPED_PATHS = 20
MAX_LOG_CHARS = 1800
TOO_LARGE_CODE = "DASHBOARD_CAPTURE_TOO_LARGE"

REASON_LABELS = {
    "UNSUPPORTED_EXTENSION": "지원하지 않는 파일 형식",
    "ASSET_TOO_LARGE": "파일 하나가 너무 큼(32 MiB 초과)",
    "NAME_NOT_LOCAL_SAFE": "이 서버에서 쓸 수 없는 파일 이름",
    "EXCLUDED_DIRECTORY": "제외 폴더(cad·report·final 등)",
    "FORMAT_OR_PATTERN_EXCLUDED": "수집 대상 규칙에서 제외",
}


def _limit(env: str, default: int) -> int:
    raw = (os.getenv(env) or "").strip()
    try:
        value = int(float(raw)) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


def max_payload_bytes() -> int:
    return _limit(ENV_MAX_PAYLOAD, DEFAULT_MAX_PAYLOAD_BYTES)


def max_asset_total_bytes() -> int:
    return _limit(ENV_MAX_ASSETS, DEFAULT_MAX_ASSET_TOTAL_BYTES)


def human(size: int | float | None) -> str:
    value = float(size or 0)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def _extension(path: str) -> str:
    return PurePosixPath(path).suffix.casefold() or "(없음)"


def _safe(path: str) -> str:
    return "".join(ch if ord(ch) >= 0x20 and ch != "\x7f" else "?" for ch in str(path))[:300]


def start(case_path: str, files: list[tuple[str, bytes, str]], skipped: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Taken files per extension and skipped files per reason (before anything is parsed)."""
    taken: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "bytes": 0})
    largest_media: list[tuple[int, str]] = []
    for path, data, media_type in files:
        entry = taken[_extension(path)]
        entry["count"] += 1
        entry["bytes"] += len(data)
        if str(media_type).startswith(("video/", "image/")):
            largest_media.append((len(data), path))
    reasons: dict[str, dict[str, Any]] = {}
    for item in skipped or []:
        reason = str(item.get("reason") or "UNKNOWN")
        entry = reasons.setdefault(reason, {"reason": reason, "label": REASON_LABELS.get(reason, reason), "count": 0,
                                            "bytes": 0, "extensions": {}, "examples": []})
        entry["count"] += 1
        entry["bytes"] += int(item.get("size") or 0)
        path = str(item.get("path") or "")
        extension = _extension(path) if PurePosixPath(path).suffix else ""
        if extension:
            entry["extensions"][extension] = entry["extensions"].get(extension, 0) + 1
        if len(entry["examples"]) < 3:
            entry["examples"].append(_safe(path))
    largest_media.sort(reverse=True)
    return {
        "case_path": _safe(case_path),
        "taken": [{"extension": ext, **values} for ext, values in sorted(taken.items())],
        "taken_total": {"count": sum(v["count"] for v in taken.values()), "bytes": sum(v["bytes"] for v in taken.values())},
        "skipped": sorted(reasons.values(), key=lambda value: (-value["count"], value["reason"])),
        "skipped_total": {"count": sum(v["count"] for v in reasons.values()), "bytes": sum(v["bytes"] for v in reasons.values())},
        "top_media": [{"path": _safe(path), "bytes": size} for size, path in largest_media[:TOP_N]],
        "observation_count": None,
        "payload_bytes": None,
        "manifest_bytes": None,
        "summary_rows": None,
        "summary_bytes": None,
        "summary_max_bytes": None,
        "top_sources": [],
        "limits": {"payload_bytes": max_payload_bytes(), "asset_total_bytes": max_asset_total_bytes()},
        "outcome": "RUNNING",
    }


def _observation_lists(value: Any, found: list[list[Any]], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if key == "observations" and isinstance(nested, list):
                found.append(nested)
            elif isinstance(nested, (dict, list)):
                _observation_lists(nested, found, depth + 1)
    elif isinstance(value, list):
        for nested in value:
            if isinstance(nested, (dict, list)):
                _observation_lists(nested, found, depth + 1)


def measure_observations(payload: Any) -> tuple[int, list[dict[str, Any]]]:
    """Observation count and the heaviest csv sources (count and an estimate of their serialized bytes)."""
    lists: list[list[Any]] = []
    _observation_lists(payload, lists)
    per_source: dict[str, list[Any]] = defaultdict(lambda: [0, 0, 0])   # count, sampled bytes, sampled n
    total = 0
    for observations in lists:
        for observation in observations:
            total += 1
            source = _safe(str(observation.get("source_path") or "(알 수 없음)")) if isinstance(observation, dict) else "(알 수 없음)"
            entry = per_source[source]
            entry[0] += 1
            if entry[2] < 20:   # estimate from a sample: serializing every observation costs as much as the payload
                entry[1] += len(json.dumps(observation, ensure_ascii=False, separators=(",", ":"), default=str))
                entry[2] += 1
    ranked = []
    for source, (count, sampled_bytes, sampled) in per_source.items():
        estimate = int(sampled_bytes / sampled * count) if sampled else 0
        ranked.append({"path": source, "observations": count, "approx_bytes": estimate})
    ranked.sort(key=lambda value: (-value["approx_bytes"], -value["observations"], value["path"]))
    return total, ranked[:TOP_N]


def finish(inventory: dict[str, Any], *, observation_count: int, top_sources: list[dict[str, Any]],
           payload_bytes: int, manifest_bytes: int, summary_bytes: int = 0, summary_max_bytes: int = 0,
           summary_rows: int = 0) -> dict[str, Any]:
    # Issue #55, stage 2: per-Scene summary rows (dashboard_scene_summaries) of a DISTRIBUTION capture.
    inventory.update(summary_rows=summary_rows, summary_bytes=summary_bytes, summary_max_bytes=summary_max_bytes)
    inventory.update(observation_count=observation_count, top_sources=top_sources, payload_bytes=payload_bytes,
                     manifest_bytes=manifest_bytes)
    return inventory


def too_large_message(inventory: dict[str, Any], what: str) -> str:
    """Korean message of DASHBOARD_CAPTURE_TOO_LARGE with the sizes and the top contributors."""
    limits = inventory["limits"]
    lines = []
    if what == "payload":
        lines.append(f"결과 데이터가 너무 커서 저장하지 않았습니다. 저장할 데이터 {human(inventory['payload_bytes'])}"
                     f"(한도 {human(limits['payload_bytes'])}), 읽은 값 {inventory['observation_count']:,}개.")
        top = inventory.get("top_sources") or []
        if top:
            lines.append("가장 큰 파일: " + ", ".join(
                f"{item['path']}({item['observations']:,}개, 약 {human(item['approx_bytes'])})" for item in top[:3]))
    else:
        lines.append(f"받을 원본 파일 합계 {human(inventory['taken_total']['bytes'])}가 한도 "
                     f"{human(limits['asset_total_bytes'])}를 넘어 저장하지 않았습니다.")
        top = inventory.get("top_media") or []
        if top:
            lines.append("가장 큰 파일: " + ", ".join(f"{item['path']}({human(item['bytes'])})" for item in top[:3]))
    lines.append("Case를 나누거나 큰 결과 파일을 줄인 뒤 [다시 시도]하세요. 자동으로 다시 시도하지 않습니다.")
    return " ".join(lines)


def log_line(inventory: dict[str, Any]) -> str:
    """One bounded summary line (paths, counts, sizes only)."""
    taken = ",".join(f"{item['extension']}={item['count']}/{item['bytes']}B" for item in inventory["taken"]) or "-"
    skipped = ",".join(f"{item['reason']}={item['count']}/{item['bytes']}B"
                       + (f"[{','.join(f'{k}:{v}' for k, v in sorted(item['extensions'].items()))}]" if item["extensions"] else "")
                       for item in inventory["skipped"]) or "-"
    sources = ";".join(f"{item['path']}={item['observations']}obs/~{item['approx_bytes']}B"
                       for item in inventory["top_sources"]) or "-"
    media = ";".join(f"{item['path']}={item['bytes']}B" for item in inventory["top_media"]) or "-"
    line = (f"Capture inventory case={inventory['case_path']} outcome={inventory['outcome']} "
            f"taken[{taken}] skipped[{skipped}] observations={inventory['observation_count']} "
            f"payload_bytes={inventory['payload_bytes']} manifest_bytes={inventory['manifest_bytes']} "
            f"summaries={inventory.get('summary_rows')}/{inventory.get('summary_bytes')}B"
            f"(max {inventory.get('summary_max_bytes')}B) "
            f"top_csv[{sources}] top_media[{media}]")
    return line if len(line) <= MAX_LOG_CHARS else line[:MAX_LOG_CHARS - 3] + "..."
