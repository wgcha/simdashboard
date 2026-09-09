"""Managed-mode state and central-server client for the loopback helper.

This module deliberately keeps browser credentials out of the local database.
Only a short lived central session reaches the loopback API; the durable secret
is used exclusively by this process when it talks back to the fixed server.
"""

from __future__ import annotations

import json
import hashlib
import os
import secrets
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request as UrlRequest, urlopen
from uuid import uuid4

from .models import Run, RunStatus
from .storage import LocalRunnerStorage, utc_now


API_PREFIX = "/api/local-execution"


class CentralError(RuntimeError):
    def __init__(self, status: int, detail: Any = None) -> None:
        self.status = status
        self.detail = detail
        super().__init__("Central local-execution authorization failed")


class CentralUnavailable(RuntimeError):
    pass


class CentralClient(Protocol):
    def post(self, path: str, bearer: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class HttpCentralClient:
    """Small dependency-free JSON client with no redirect following."""

    def __init__(self, server_url: str) -> None:
        parsed = urlsplit(server_url)
        if parsed.query or parsed.fragment or parsed.username or parsed.password or parsed.path not in {"", "/"} or not parsed.hostname:
            raise ValueError("서버 URL 형식이 올바르지 않습니다.")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("중앙 서버는 HTTPS여야 합니다(개발용 loopback HTTP 제외).")
        self.base_url = server_url.rstrip("/")

    def post(self, path: str, bearer: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Keep the byte-budget calculation in server.sync exact for Korean and
        # other non-ASCII program metadata carried in event snapshots.
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = UrlRequest(
            self.base_url + API_PREFIX + path,
            data=body,
            headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            # urllib's default redirect handler is intentionally bypassed. A
            # device must never be redirected to a browser-selected authority.
            opener = __import__("urllib.request", fromlist=["build_opener", "HTTPRedirectHandler"]).build_opener(_NoRedirect())
            with opener.open(request, timeout=8) as response:
                raw = response.read(1_048_577)
                if len(raw) > 1_048_576:
                    raise CentralUnavailable("central response exceeded limit")
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read(65_537).decode("utf-8")).get("detail")
            except Exception:
                detail = None
            raise CentralError(exc.code, detail) from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise CentralUnavailable("central server is unavailable") from exc
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CentralUnavailable("central server returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise CentralUnavailable("central server returned invalid JSON")
        return result


class _NoRedirect(__import__("urllib.request", fromlist=["HTTPRedirectHandler"]).HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class ManagedConfig:
    def __init__(self, data_dir: Path, server_url: str) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "managed-config.json"
        self.server_url = HttpCentralClient(server_url).base_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()
        if not self.path.exists():
            self.save()

    def _load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("관리 모드 설정을 읽을 수 없습니다. 새 데이터 디렉터리를 사용하세요.") from exc
            if not isinstance(value, dict) or not isinstance(value.get("device_id"), str) or not isinstance(value.get("bindings", {}), dict):
                raise ValueError("관리 모드 설정이 올바르지 않습니다. 새 데이터 디렉터리를 사용하세요.")
            if value.get("server_url") != self.server_url:
                raise ValueError("이 데이터 디렉터리는 다른 중앙 서버에 연결되어 있습니다. 별도 데이터 디렉터리를 사용하세요.")
            value.setdefault("bindings", {})
            return value
        # A changed central authority is a separate local installation.  Do
        # not import the old standalone sqlite database or its code token.
        return {"server_url": self.server_url, "device_id": str(uuid4()), "bindings": {}}

    def save(self) -> None:
        with self._lock:
            _atomic_json(self.path, self._data)

    @property
    def device_id(self) -> str:
        return self._data["device_id"]

    def binding(self, binding_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._data["bindings"].get(binding_id)
            return dict(item) if isinstance(item, dict) else None

    def bindings(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._data["bindings"].items() if isinstance(value, dict)}

    def add_binding(self, binding_id: str, user_id: str, device_secret: str) -> None:
        with self._lock:
            for existing_id, existing in self._data["bindings"].items():
                if existing_id != binding_id and isinstance(existing, dict) and existing.get("user_id") == user_id:
                    existing["sync_disabled"] = "이 계정의 새 PC 연결로 인해 이전 연결 동기화가 중지되었습니다."
            self._data["bindings"][binding_id] = {"user_id": user_id, "device_secret": device_secret}
            self.save()

    def disable_sync(self, binding_id: str, reason: str) -> None:
        with self._lock:
            item = self._data["bindings"].get(binding_id)
            if isinstance(item, dict):
                item["sync_disabled"] = reason
                self.save()


class ManagedStorage(LocalRunnerStorage):
    """One binding's isolated SQLite store plus a durable central event outbox."""

    def __init__(self, database_path: Path, host_id: str, binding_id: str) -> None:
        self.binding_id = binding_id
        super().__init__(database_path, host_id)

    def initialize(self) -> None:
        super().initialize()
        with self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS run_events (
                    run_id TEXT NOT NULL, sequence INTEGER NOT NULL, grant_id TEXT NOT NULL,
                    run_json TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (run_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS run_events_pending_idx ON run_events(sent, run_id, sequence);
            """)
        self.reconcile_snapshots()

    @staticmethod
    def _request_fingerprint(payload: Any) -> str:
        """Idempotency follows requested work, not server-corrected display fields."""
        value = payload.model_dump(mode="json")
        context = value.get("context")
        if isinstance(context, dict):
            context.pop("actor", None)
            context.pop("task_name", None)
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _persist_initial_runs(self, conn: sqlite3.Connection, runs: list[Run], grant_id: str | None) -> None:
        """Atomically store the grant, first snapshot, and idempotency response.

        The base class calls this while its BEGIN IMMEDIATE transaction is
        still open. A fault here rolls back the run records as well, preventing
        an idempotency replay from stranding an ungranted queued run.
        """
        if not grant_id:
            raise ValueError("중앙 실행 승인이 필요합니다.")
        for run in runs:
            conn.execute("UPDATE runs SET grant_id=? WHERE id=?", (grant_id, run.id))
            conn.execute(
                "INSERT INTO run_events(run_id, sequence, grant_id, run_json) VALUES (?, 1, ?, ?)",
                (run.id, grant_id, json.dumps(run.model_dump(mode="json"), separators=(",", ":"))),
            )

    def reconcile_snapshots(self) -> None:
        """Queue the latest state of every granted run after a process restart.

        A crash after a sqlite state transition cannot make a completed run
        disappear from central history: if its exact snapshot was not already
        queued, this adds one durable, later sequence number.
        """
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM runs WHERE grant_id IS NOT NULL").fetchall()
        for row in rows:
            run = self._run(row)
            encoded = json.dumps(run.model_dump(mode="json"), separators=(",", ":"))
            with self._connection() as conn:
                present = conn.execute(
                    "SELECT 1 FROM run_events WHERE run_id=? AND run_json=? LIMIT 1", (run.id, encoded)
                ).fetchone()
            if not present:
                self._enqueue(run, self.get_grant(run.id))

    def enqueue_runs(self, runs: list[Run], grant_id: str) -> None:
        self.set_grant([run.id for run in runs], grant_id)
        for run in runs:
            current = self.get_run(run.id)
            if current:
                self._enqueue(current, grant_id)

    def _enqueue(self, run: Run, grant_id: str | None = None) -> None:
        grant = grant_id or self.get_grant(run.id)
        if not grant:
            return
        serialized = json.dumps(run.model_dump(mode="json"), separators=(",", ":"))
        with self._connection() as conn:
            next_sequence = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id=?", (run.id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO run_events(run_id, sequence, grant_id, run_json) VALUES (?, ?, ?, ?)",
                (run.id, next_sequence, grant, serialized),
            )

    def transition_run(self, run_id: str, status: RunStatus, **kwargs: Any) -> Run:
        """Persist a state transition and its outbox snapshot in one commit."""
        now = utc_now()
        exit_code, error = kwargs.get("exit_code"), kwargs.get("error")
        with self._connection() as conn:
            current = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not current:
                raise LookupError("실행 기록을 찾을 수 없습니다.")
            started_at = now if status is RunStatus.RUNNING and current["started_at"] is None else current["started_at"]
            completed_at = now if status in {RunStatus.AWAITING_COMPLETION, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.COMPLETED, RunStatus.INTERRUPTED} else None
            conn.execute("UPDATE runs SET status=?, started_at=?, completed_at=?, exit_code=?, error=? WHERE id=?", (status.value, started_at, completed_at, exit_code, error, run_id))
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            run = self._run(row)
            grant = row["grant_id"]
            if grant:
                sequence = conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id=?", (run.id,)).fetchone()[0]
                conn.execute("INSERT INTO run_events(run_id, sequence, grant_id, run_json) VALUES (?, ?, ?, ?)", (run.id, sequence, grant, json.dumps(run.model_dump(mode="json"), separators=(",", ":"))))
        return run

    def complete_direct_run(self, run_id: str, note: str) -> Run:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise LookupError("실행 기록을 찾을 수 없습니다.")
            if row["mode"] != "DIRECT" or row["status"] != RunStatus.AWAITING_COMPLETION.value:
                raise ValueError("직접 실행의 완료 대기 상태에서만 완료할 수 있습니다.")
            conn.execute("UPDATE runs SET status=?, note=?, completed_at=? WHERE id=?", (RunStatus.COMPLETED.value, note, utc_now(), run_id))
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            run = self._run(row)
            if row["grant_id"]:
                sequence = conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id=?", (run.id,)).fetchone()[0]
                conn.execute("INSERT INTO run_events(run_id, sequence, grant_id, run_json) VALUES (?, ?, ?, ?)", (run.id, sequence, row["grant_id"], json.dumps(run.model_dump(mode="json"), separators=(",", ":"))))
        return run

    def pending_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT run_id, sequence, grant_id, run_json FROM run_events WHERE sent=0 ORDER BY run_id, sequence LIMIT ?", (limit,)
            ).fetchall()
        return [{"run_id": row["run_id"], "sequence": row["sequence"], "grant_id": row["grant_id"], "run": json.loads(row["run_json"])} for row in rows]

    def update_run_context(self, run_ids: list[str], context: dict[str, Any]) -> list[Run]:
        with self._connection() as conn:
            conn.execute(f"UPDATE runs SET context_json=? WHERE id IN ({','.join('?' for _ in run_ids)})", [json.dumps(context), *run_ids])
            rows = conn.execute(f"SELECT * FROM runs WHERE id IN ({','.join('?' for _ in run_ids)})", run_ids).fetchall()
        by_id = {row["id"]: self._run(row) for row in rows}
        return [by_id[run_id] for run_id in run_ids]

    def mark_events_sent(self, accepted: list[dict[str, Any]]) -> None:
        with self._connection() as conn:
            for item in accepted:
                run_id, sequence = item.get("run_id"), item.get("sequence")
                if isinstance(run_id, str) and isinstance(sequence, int):
                    conn.execute("UPDATE run_events SET sent=1 WHERE run_id=? AND sequence=?", (run_id, sequence))

    def pending_count(self) -> int:
        with self._connection() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM run_events WHERE sent=0").fetchone()[0])


class BindingRuntime:
    def __init__(self, binding_id: str, metadata: dict[str, Any], store: ManagedStorage, executor: Any) -> None:
        self.binding_id, self.user_id = binding_id, str(metadata["user_id"])
        self.secret = str(metadata["device_secret"])
        self.store, self.executor = store, executor
        self.sync_disabled = str(metadata["sync_disabled"]) if metadata.get("sync_disabled") else None
        self.sync_error: str | None = self.sync_disabled
        self.sync_attempts = 0
        self.next_sync_at = 0.0
        self.sync_max_bytes = 900 * 1024
        self.lock = threading.RLock()


ConfirmPairing = Callable[[dict[str, str]], bool]


def _native_pair_prompt(details: dict[str, str], queue: Any) -> None:
    try:
        import tkinter
        from tkinter import messagebox
        root = tkinter.Tk(); root.withdraw(); root.attributes("-topmost", True)
        approved = messagebox.askyesno("Simulation Workbench PC 연결", f"서버: {details['server_url']}\n계정: {details['display_name']}\n이 PC: {details['host_name']}\n\n이 계정으로 이 PC를 연결할까요?", parent=root)
        queue.put(bool(approved)); root.destroy()
    except Exception:
        queue.put(False)


def native_pair_confirmation(details: dict[str, str]) -> bool:
    """Run an explicit native confirmation in a short-lived tkinter process."""
    import multiprocessing

    queue: Any = multiprocessing.Queue(1)

    process = multiprocessing.Process(target=_native_pair_prompt, args=(details, queue), daemon=True)
    process.start(); process.join(120)
    if process.is_alive():
        process.terminate(); process.join(2)
        return False
    try:
        return bool(queue.get_nowait())
    except Exception:
        return False
