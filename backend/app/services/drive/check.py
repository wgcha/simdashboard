"""Administrator "드라이브 점검" (plan decision D7, contract §8 C1/C9).

Runs a fixed, READ-ONLY sequence against one test folder below the SPDM root
(stat → list_dir → stat of one small file → download_to → health) and reports
each step's result and latency.  User decision 2026-10-07: the check only
reads; it never calls a drive write method (upload_new, mkdirs, copy_within,
...), so nothing on the drive is created, changed or deleted.  The local
staging copy of the downloaded file is removed.  C2/C7 (no-overwrite upload
and copy conflicts) are not verified here; they stay open for an explicit
write acceptance test (D3) in a dedicated test space.
"""
from __future__ import annotations

import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .gateway import drive_error_code, priority, safe_error_message

DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024
LIST_MAX_ENTRIES = 50_000

_run_lock = threading.Lock()


class CheckAlreadyRunning(RuntimeError):
    pass


@dataclass
class StepResult:
    step: str
    label: str
    ok: bool
    code: str | None = None
    latency_ms: float | None = None
    observations: list[str] = field(default_factory=list)
    skipped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step, "label": self.label, "ok": self.ok, "skipped": self.skipped, "code": self.code,
                "latency_ms": None if self.latency_ms is None else round(self.latency_ms, 1),
                "observations": self.observations}


def _kind(entry: Any) -> str:
    return str(getattr(getattr(entry, "kind", None), "value", getattr(entry, "kind", "")))


def _timed(step: StepResult, call: Callable[[], Any]) -> tuple[bool, Any]:
    started = time.perf_counter()
    try:
        value = call()
    except Exception as error:  # adapter DriveError or a client failure; message is sanitized
        step.latency_ms = (time.perf_counter() - started) * 1000
        step.code = drive_error_code(error) or "INTERNAL"
        step.observations.append(safe_error_message(error))
        return False, error
    step.latency_ms = (time.perf_counter() - started) * 1000
    return True, value


def _discard_local(path: Path) -> None:
    """Remove a local staging folder (server disk only, never the drive)."""
    shutil.rmtree(path, ignore_errors=True)


def run_drive_check(gateway: Any, *, spdm_root: str, test_folder: str, staging_dir: Path,
                    now: Callable[[], datetime] | None = None) -> dict[str, Any]:
    if not _run_lock.acquire(blocking=False):
        raise CheckAlreadyRunning("드라이브 점검이 이미 실행 중입니다.")
    try:
        return _run(gateway, spdm_root=spdm_root, test_folder=test_folder, staging_dir=staging_dir,
                    now=now or (lambda: datetime.now(timezone.utc)))
    finally:
        _run_lock.release()


def _run(gateway: Any, *, spdm_root: str, test_folder: str, staging_dir: Path,
         now: Callable[[], datetime]) -> dict[str, Any]:
    started_at = now()
    folder = f"{spdm_root}/{test_folder}"
    interactive = priority("INTERACTIVE")
    steps: list[StepResult] = []
    local_dir = staging_dir / f"drive-check-{uuid.uuid4().hex}"

    def add(step: str, label: str) -> StepResult:
        result = StepResult(step=step, label=label, ok=False)
        steps.append(result)
        return result

    def skip(step: str, label: str, reason: str) -> None:
        steps.append(StepResult(step=step, label=label, ok=False, skipped=True, observations=[reason]))

    try:
        # 1. stat: the folder must exist and be a directory.
        s = add("stat_folder", "시험 폴더 조회(stat)")
        ok, entry = _timed(s, lambda: gateway.stat(folder, priority=interactive))
        if ok and entry is None:
            ok, s.code = False, "NOT_FOUND"
            s.observations.append("시험 폴더가 드라이브에 없습니다.")
        elif ok and _kind(entry) != "dir":
            ok, s.code = False, "INVALID_PATH"
            s.observations.append("시험 경로가 폴더가 아닙니다.")
        elif ok:
            s.observations.append(f"item_id={'있음' if getattr(entry, 'item_id', None) else '없음'}")
        s.ok = ok
        if not ok:
            return _report(started_at, now(), test_folder, folder, steps)

        # 2. list_dir: count, sha1/modified_at coverage (C1, C9).
        s = add("list_dir", "목록 조회(list_dir)")
        ok, entries = _timed(s, lambda: gateway.list_dir(folder, max_entries=LIST_MAX_ENTRIES, priority=interactive))
        files: list[Any] = []
        if ok:
            entries = list(entries)
            files = [item for item in entries if _kind(item) == "file"]
            dirs = sum(1 for item in entries if _kind(item) == "dir")
            with_sha1 = sum(1 for item in files if getattr(item, "sha1", None))
            with_mtime = sum(1 for item in entries if getattr(item, "modified_at", None) is not None)
            unsafe = sum(1 for item in entries if not getattr(item, "local_safe", True))
            s.observations += [
                f"항목 {len(entries)}개(파일 {len(files)}, 폴더 {dirs})",
                f"목록의 sha1: 파일 {len(files)}개 중 {with_sha1}개 (C9)",
                f"목록의 modified_at: {len(entries)}개 중 {with_mtime}개 (C1)",
            ]
            if unsafe:
                s.observations.append(f"Windows에서 쓸 수 없는 이름 {unsafe}개(local_safe=False)")
        s.ok = ok

        small = next((item for item in files if getattr(item, "local_safe", True)
                      and getattr(item, "size", None) is not None and int(item.size) <= DOWNLOAD_MAX_BYTES), None)

        # 3. stat of one file: does the single lookup carry sha1/modified_at?
        if small is not None:
            s = add("stat_file", "파일 조회(stat)")
            ok, info = _timed(s, lambda: gateway.stat(small.rel_path, priority=interactive))
            if ok and info is not None:
                s.observations += [f"{small.name}: sha1={'있음' if getattr(info, 'sha1', None) else '없음'}, "
                                   f"modified_at={'있음' if getattr(info, 'modified_at', None) else '없음'}",
                                   f"version_token={str(getattr(info, 'version_token', ''))[:12]}…"]
            elif ok:
                ok, s.code = False, "NOT_FOUND"
            s.ok = ok

            # 4. download_to the server staging folder, then remove the local copy.
            s = add("download_to", "작은 파일 다운로드(download_to)")
            local_dir.mkdir(parents=True, exist_ok=True)
            ok, result = _timed(s, lambda: gateway.download_to(small.rel_path, local_dir, max_bytes=DOWNLOAD_MAX_BYTES,
                                                               priority=interactive))
            if ok:
                s.observations += [f"{small.name}: {getattr(result, 'size', '?')} bytes",
                                   f"sha256={str(getattr(result, 'sha256', ''))[:16]}…"]
            s.ok = ok
            _discard_local(local_dir)
        else:
            skip("stat_file", "파일 조회(stat)", f"{DOWNLOAD_MAX_BYTES // (1024 * 1024)} MiB 이하 파일이 없습니다.")
            skip("download_to", "작은 파일 다운로드(download_to)", "다운로드할 작은 파일이 없습니다.")

        # 5. health snapshot (no SDK call).
        s = add("health", "어댑터 상태(health)")
        ok, health = _timed(s, gateway.health)
        if ok:
            state = getattr(getattr(health, "state", None), "value", getattr(health, "state", None))
            s.observations.append(f"state={state}, queue_depth={getattr(health, 'queue_depth', '?')}")
        s.ok = ok
        return _report(started_at, now(), test_folder, folder, steps)
    finally:
        _discard_local(local_dir)


def _report(started_at: datetime, finished_at: datetime, test_folder: str, folder: str,
            steps: list[StepResult]) -> dict[str, Any]:
    ran = [step for step in steps if not step.skipped]
    return {
        "test_folder": test_folder,
        "drive_path": folder,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "ok": bool(ran) and all(step.ok for step in ran),
        "steps": [step.as_dict() for step in steps],
        "note": "조회 전용 점검입니다. 드라이브에는 아무것도 만들거나 바꾸거나 지우지 않습니다(다운로드한 서버 사본은 삭제).",
    }
