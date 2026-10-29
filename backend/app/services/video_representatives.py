"""Representative video per (Case, Scene), the project keyword rule and cheap playability hints (issue #55).

A Scene folder can hold more than one video.  The screens show one *representative* video per (Case, Scene):

1. the user's explicit choice (``case_scene_video_choices``), while that file still exists in the Scene;
2. otherwise the first video whose file name contains a keyword of the project's ordered rule
   (``project_video_rules``), keywords tried in their order, videos in natural file-name order;
3. otherwise the first video by file name (natural, case-insensitive order).

The identity is ``(case_id, scene_key)``: ``case_id`` is the stable ``dashboard_cases.id`` and ``scene_key`` is
``<run id>|<run option id>|<Scene folder name, case-folded>``.  Capture and asset ids change with every
re-capture, so neither is used.  The chosen file is stored by its Case-relative path.

Playability is only a hint and stays cheap: a stored ``.mp4`` is read at most once per asset (the first and the
last 128 KiB) to find the sample-entry fourcc of its video track (``avc1`` plays in browsers; ``mp4v`` /
``hvc1`` / ``hev1`` do not).  ``.webm`` is assumed playable.  Files with a container the capture never takes
(``.avi`` ...) are listed by the capture itself (``scene["unplayable_videos"]``).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable

from ..database_connection import ConnectionLike

MAX_KEYWORDS = 20
MAX_KEYWORD_LENGTH = 64
# Containers the capture does not store (the browser cannot play most of them); listed, never downloaded.
UNPLAYABLE_SUFFIXES = frozenset({".avi", ".mov", ".mkv", ".wmv", ".flv", ".m4v", ".mpg", ".mpeg"})
PROBE_BYTES = 128 * 1024
PROBE_CACHE_SIZE = 4096
PLAYABLE_FOURCC = frozenset({b"avc1", b"avc3", b"vp08", b"vp09", b"av01"})
KNOWN_VIDEO_FOURCC = PLAYABLE_FOURCC | frozenset({b"hvc1", b"hev1", b"mp4v", b"mjpa", b"mjpb", b"jpeg", b"s263", b"h263",
                                                  b"dvc ", b"dvcp", b"apch", b"apcn", b"apcs", b"ap4h", b"vc-1"})


class VideoRepresentativeError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- identities and ordering

def natural_key(name: str) -> tuple:
    """Case-insensitive natural order: ``a2`` < ``a10``."""
    parts = re.split(r"(\d+)", str(name).casefold())
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in parts if part != "")


def file_name(relative_path: str) -> str:
    return str(relative_path or "").replace("\\", "/").rsplit("/", 1)[-1]


def scene_key(run: dict[str, Any], scene: dict[str, Any]) -> str:
    """Re-capture-stable Scene identity (Run id, Run option id and the Scene folder name)."""
    name = str(scene.get("label") or scene.get("source_name") or scene.get("id") or "")
    return f"{run.get('id')}|{run.get('run_option_id') or run.get('mode') or ''}|{name.casefold()}"


def choice_id(case_id: str, key: str) -> str:
    return "vrc-" + hashlib.sha256(f"{case_id}\n{key}".encode()).hexdigest()[:40]


def clean_keywords(raw: Any) -> list[str]:
    """Ordered, de-duplicated (case-insensitive) keywords from a list or a comma/newline separated text."""
    if raw is None:
        return []
    items = re.split(r"[,\n;]", raw) if isinstance(raw, str) else list(raw)
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            raise VideoRepresentativeError("VIDEO_RULE_INVALID", "키워드는 문자열이어야 합니다.")
        word = " ".join(item.split())
        if not word:
            continue
        if len(word) > MAX_KEYWORD_LENGTH or any(ord(ch) < 0x20 for ch in word) or "/" in word or "\\" in word:
            raise VideoRepresentativeError("VIDEO_RULE_INVALID", f"키워드가 올바르지 않습니다(최대 {MAX_KEYWORD_LENGTH}자, 경로 문자 불가): {word[:20]}")
        if word.casefold() in seen:
            continue
        seen.add(word.casefold())
        result.append(word)
    if len(result) > MAX_KEYWORDS:
        raise VideoRepresentativeError("VIDEO_RULE_INVALID", f"키워드는 최대 {MAX_KEYWORDS}개입니다.")
    return result


def pick_representative(videos: list[dict[str, Any]], keywords: Iterable[str] = (), chosen_path: str | None = None
                        ) -> tuple[dict[str, Any] | None, str, bool]:
    """``(video, source, choice_missing)``; ``source`` is ``USER``, ``RULE`` or ``DEFAULT``.

    ``videos`` carry ``relative_path`` (the file name is taken from it).  ``choice_missing`` is true when a user
    choice exists but its file is no longer in the Scene (the default is used instead).
    """
    if not videos:
        return None, "DEFAULT", bool(chosen_path)
    ordered = sorted(videos, key=lambda video: (natural_key(file_name(video["relative_path"])), str(video["relative_path"])))
    missing = False
    if chosen_path:
        wanted = str(chosen_path).replace("\\", "/")
        for video in ordered:
            if str(video["relative_path"]).replace("\\", "/") == wanted:
                return video, "USER", False
        missing = True
    for keyword in keywords:
        needle = keyword.casefold()
        for video in ordered:
            if needle in file_name(video["relative_path"]).casefold():
                return video, "RULE", missing
    return ordered[0], "DEFAULT", missing


# ---------------------------------------------------------------- persistence

def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return str(value) if value else None


def load_rule(conn: ConnectionLike, project_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT keywords_json,updated_by,updated_at FROM project_video_rules WHERE project_id=?",
                       [project_id]).fetchone()
    if not row:
        return {"project_id": project_id, "keywords": [], "updated_by": None, "updated_at": None}
    try:
        keywords = [str(item) for item in json.loads(row[0]) if str(item).strip()]
    except (TypeError, ValueError):
        keywords = []
    return {"project_id": project_id, "keywords": keywords, "updated_by": row[1], "updated_at": _iso(row[2])}


def save_rule(conn: ConnectionLike, project_id: str, keywords: list[str], actor: str) -> dict[str, Any]:
    """Replaces the project's rule (an empty list removes the row).  The caller owns the transaction."""
    conn.execute("DELETE FROM project_video_rules WHERE project_id=?", [project_id])
    if keywords:
        conn.execute("INSERT INTO project_video_rules(project_id,keywords_json,updated_by,updated_at) VALUES(?,?,?,?)",
                     [project_id, json.dumps(keywords, ensure_ascii=False), actor, _now()])
    return load_rule(conn, project_id)


def load_choices(conn: ConnectionLike, case_id: str) -> dict[str, dict[str, Any]]:
    found = conn.execute("SELECT scene_key,relative_path,chosen_by,chosen_at FROM case_scene_video_choices WHERE case_id=?",
                         [case_id]).fetchall()
    return {str(row[0]): {"relative_path": str(row[1]), "chosen_by": row[2], "chosen_at": _iso(row[3])} for row in found}


def save_choice(conn: ConnectionLike, case_id: str, key: str, relative_path: str, actor: str) -> None:
    conn.execute("DELETE FROM case_scene_video_choices WHERE case_id=? AND scene_key=?", [case_id, key])
    conn.execute("INSERT INTO case_scene_video_choices(id,case_id,scene_key,relative_path,chosen_by,chosen_at) VALUES(?,?,?,?,?,?)",
                 [choice_id(case_id, key), case_id, key, relative_path, actor, _now()])


def clear_choice(conn: ConnectionLike, case_id: str, key: str) -> bool:
    existed = conn.execute("SELECT 1 FROM case_scene_video_choices WHERE case_id=? AND scene_key=?", [case_id, key]).fetchone()
    conn.execute("DELETE FROM case_scene_video_choices WHERE case_id=? AND scene_key=?", [case_id, key])
    return bool(existed)


def user_names(conn: ConnectionLike, user_ids: Iterable[str]) -> dict[str, str]:
    """Display names for ``chosen_by`` / ``updated_by`` (callers fall back to the id when the account is gone)."""
    ids = sorted({str(value) for value in user_ids if value})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    try:
        rows = conn.execute(f"SELECT id,display_name FROM users WHERE id IN ({marks})", ids).fetchall()
    except Exception:  # noqa: BLE001 - a name is informational only
        return {}
    return {str(row[0]): str(row[1]) for row in rows if row[1]}


# ---------------------------------------------------------------- playability hints

_probe_lock = threading.Lock()
_probe_cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def fourcc_of(data: bytes) -> bytes | None:
    """The first video sample-entry fourcc found in the ``stsd`` boxes of ``data`` (None when unknown)."""
    start = 0
    while True:
        index = data.find(b"stsd", start)
        if index < 0:
            return None
        start = index + 4
        entry = data[index + 16:index + 20]   # 'stsd' + version/flags + entry count + entry size, then the fourcc
        if entry in KNOWN_VIDEO_FOURCC:
            return entry


def codec_hint(data: bytes) -> dict[str, Any]:
    entry = fourcc_of(data)
    if entry is None:
        return {"codec": None, "playable": None}
    return {"codec": entry.decode("latin-1").strip(), "playable": entry in PLAYABLE_FOURCC}


def _remember(asset_id: str, value: dict[str, Any]) -> None:
    with _probe_lock:
        _probe_cache[asset_id] = value
        _probe_cache.move_to_end(asset_id)
        while len(_probe_cache) > PROBE_CACHE_SIZE:
            _probe_cache.popitem(last=False)


def reset_probe_cache() -> None:
    with _probe_lock:
        _probe_cache.clear()


def probe_assets(conn: ConnectionLike, assets: dict[str, str]) -> dict[str, dict[str, Any]]:
    """``{asset_id: {size_bytes, codec, playable}}`` for ``assets`` (asset id -> file name); bounded and cached.

    The size is one ``octet_length`` query for all ids.  The codec of an ``.mp4`` is read once per asset from
    its first and last ``PROBE_BYTES`` (a ``moov`` box can sit at either end); other containers get no codec.
    """
    if not assets:
        return {}
    ids = sorted(assets)
    marks = ",".join("?" for _ in ids)
    sizes = {str(row[0]): int(row[1] or 0) for row in conn.execute(
        f"SELECT id,octet_length(content) FROM dashboard_assets WHERE id IN ({marks})", ids).fetchall()}
    result: dict[str, dict[str, Any]] = {}
    postgres = getattr(conn, "backend", "duckdb") == "postgresql"
    for asset_id in ids:
        size = sizes.get(asset_id)
        if size is None:
            continue
        if PurePosixPath(assets[asset_id]).suffix.casefold() != ".mp4":
            result[asset_id] = {"size_bytes": size, "codec": None, "playable": None}
            continue
        with _probe_lock:
            cached = _probe_cache.get(asset_id)
            if cached is not None:
                _probe_cache.move_to_end(asset_id)
        if cached is None:
            cached = _read_hint(conn, asset_id, size, postgres)
            _remember(asset_id, cached)
        result[asset_id] = {"size_bytes": size, **cached}
    return result


def _read_hint(conn: ConnectionLike, asset_id: str, size: int, postgres: bool) -> dict[str, Any]:
    try:
        if size <= 2 * PROBE_BYTES:
            row = conn.execute("SELECT content FROM dashboard_assets WHERE id=?", [asset_id]).fetchone()
            data = bytes(row[0]) if row and row[0] is not None else b""
        elif postgres:
            head = conn.execute("SELECT substring(content from 1 for ?) FROM dashboard_assets WHERE id=?",
                                [PROBE_BYTES, asset_id]).fetchone()
            tail = conn.execute("SELECT substring(content from ? for ?) FROM dashboard_assets WHERE id=?",
                                [size - PROBE_BYTES + 1, PROBE_BYTES, asset_id]).fetchone()
            data = bytes(head[0] or b"") + bytes(tail[0] or b"")
        else:   # DuckDB has no substr() for BLOB; slicing is the equivalent
            head = conn.execute("SELECT content[1:?] FROM dashboard_assets WHERE id=?", [PROBE_BYTES, asset_id]).fetchone()
            tail = conn.execute("SELECT content[?:?] FROM dashboard_assets WHERE id=?",
                                [size - PROBE_BYTES + 1, size, asset_id]).fetchone()
            data = bytes(head[0] or b"") + bytes(tail[0] or b"")
        return codec_hint(data)
    except Exception:  # noqa: BLE001 - a hint must never fail the list
        return {"codec": None, "playable": None}
