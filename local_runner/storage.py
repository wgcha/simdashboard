from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .models import Program, ProgramCandidate, Run, RunContext, RunCreate, RunItemInput, RunMode, RunStatus, SavedBatch


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalRunnerStorage:
    """SQLite-backed local catalogue and immutable run history."""

    def __init__(self, database_path: str | Path, host_id: str) -> None:
        self.database_path = Path(database_path)
        self.host_id = host_id
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS programs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
                    keywords_json TEXT NOT NULL, executable_path TEXT NOT NULL,
                    arguments_json TEXT NOT NULL, host_id TEXT NOT NULL, available INTEGER NOT NULL,
                    UNIQUE(host_id, executable_path)
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, batch_id TEXT, mode TEXT NOT NULL, status TEXT NOT NULL,
                    program_name TEXT NOT NULL, program_version TEXT NOT NULL, input_path TEXT NOT NULL,
                    working_directory TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
                    completed_at TEXT, exit_code INTEGER, note TEXT, error TEXT,
                    context_json TEXT NOT NULL, program_snapshot_json TEXT NOT NULL,
                    source_run_id TEXT,
                    grant_id TEXT
                );
                CREATE INDEX IF NOT EXISTS runs_context_idx ON runs(created_at DESC);
                CREATE TABLE IF NOT EXISTS idempotency_requests (
                    key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, response_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, program_id TEXT NOT NULL,
                    items_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
            if "source_run_id" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN source_run_id TEXT")
            if "grant_id" not in columns:
                conn.execute("ALTER TABLE runs ADD COLUMN grant_id TEXT")
            conn.execute(
                "UPDATE runs SET status=?, completed_at=?, error=COALESCE(error, ?) WHERE status IN (?, ?)",
                (RunStatus.INTERRUPTED.value, utc_now(), "도우미 재시작으로 실행 상태를 확인할 수 없습니다.", RunStatus.QUEUED.value, RunStatus.RUNNING.value),
            )

    @staticmethod
    def _program(row: sqlite3.Row) -> Program:
        return Program(id=row["id"], name=row["name"], version=row["version"], keywords=json.loads(row["keywords_json"]), executable_path=row["executable_path"], arguments=json.loads(row["arguments_json"]), host_id=row["host_id"], available=bool(row["available"]) and Path(row["executable_path"]).is_file())

    @staticmethod
    def _run(row: sqlite3.Row) -> Run:
        return Run(id=row["id"], source_run_id=row["source_run_id"], batch_id=row["batch_id"], mode=row["mode"], status=row["status"], program_name=row["program_name"], program_version=row["program_version"], input_path=row["input_path"], working_directory=row["working_directory"], created_at=row["created_at"], started_at=row["started_at"], completed_at=row["completed_at"], exit_code=row["exit_code"], note=row["note"], error=row["error"], context=json.loads(row["context_json"]), program_snapshot=json.loads(row["program_snapshot_json"]))

    def list_programs(self, query: str | None = None) -> list[Program]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM programs WHERE host_id=? ORDER BY name COLLATE NOCASE, version COLLATE NOCASE", (self.host_id,)).fetchall()
        programs = [self._program(row) for row in rows]
        if not query:
            return programs
        terms = " ".join(query.casefold().split()).split()
        return [p for p in programs if all(term in " ".join([p.name, p.version, *p.keywords]).casefold() for term in terms)]

    def get_program(self, program_id: str) -> Program | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM programs WHERE id=? AND host_id=?", (program_id, self.host_id)).fetchone()
        return self._program(row) if row else None

    def register_program(self, candidate: ProgramCandidate) -> Program:
        path = str(Path(candidate.executable_path).expanduser().resolve())
        with self._connection() as conn:
            existing = conn.execute("SELECT * FROM programs WHERE host_id=? AND executable_path=?", (self.host_id, path)).fetchone()
            if existing:
                return self._program(existing)
            item = Program(id=f"program-{uuid4().hex}", host_id=self.host_id, available=Path(path).is_file(), **candidate.model_dump(exclude={"executable_path"}), executable_path=path)
            conn.execute("INSERT INTO programs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (item.id, item.name, item.version, json.dumps(item.keywords), item.executable_path, json.dumps(item.arguments), item.host_id, item.available))
        return item

    def delete_program(self, program_id: str) -> bool:
        with self._connection() as conn:
            result = conn.execute("DELETE FROM programs WHERE id=? AND host_id=?", (program_id, self.host_id))
        return result.rowcount > 0

    def update_program(self, program_id: str, candidate: ProgramCandidate) -> Program | None:
        path = str(Path(candidate.executable_path).expanduser().resolve())
        with self._connection() as conn:
            existing = conn.execute("SELECT id FROM programs WHERE host_id=? AND executable_path=? AND id<>?", (self.host_id, path, program_id)).fetchone()
            if existing:
                raise ValueError("같은 실행 파일이 이미 등록되어 있습니다.")
            result = conn.execute("UPDATE programs SET name=?, version=?, keywords_json=?, executable_path=?, arguments_json=?, available=? WHERE id=? AND host_id=?", (candidate.name, candidate.version, json.dumps(candidate.keywords), path, json.dumps(candidate.arguments), Path(path).is_file(), program_id, self.host_id))
            if result.rowcount == 0:
                return None
            row = conn.execute("SELECT * FROM programs WHERE id=?", (program_id,)).fetchone()
        return self._program(row)

    @staticmethod
    def _request_fingerprint(payload: RunCreate) -> str:
        serialized = payload.model_dump(mode="json")
        return hashlib.sha256(json.dumps(serialized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def replay_runs(self, payload: RunCreate) -> list[Run] | None:
        """Return an existing idempotent response before any mutable validation."""
        fingerprint = self._request_fingerprint(payload)
        with self._connection() as conn:
            previous = conn.execute("SELECT payload_hash, response_json FROM idempotency_requests WHERE key=?", (payload.idempotency_key,)).fetchone()
            if not previous:
                return None
            if previous["payload_hash"] != fingerprint:
                raise ValueError("같은 idempotency key에 다른 요청을 사용할 수 없습니다.")
            stored = json.loads(previous["response_json"])
            run_ids = stored if isinstance(stored, list) and all(isinstance(value, str) for value in stored) else [value["id"] for value in stored]
            rows = conn.execute(f"SELECT * FROM runs WHERE id IN ({','.join('?' for _ in run_ids)})", run_ids).fetchall()
        by_id = {row["id"]: self._run(row) for row in rows}
        return [by_id[run_id] for run_id in run_ids]

    @staticmethod
    def _retry_fingerprint(source_run_id: str) -> str:
        return hashlib.sha256(json.dumps({"retry_run_id": source_run_id}, sort_keys=True).encode()).hexdigest()

    def replay_retry(self, source_run_id: str, idempotency_key: str) -> list[Run] | None:
        fingerprint = self._retry_fingerprint(source_run_id)
        with self._connection() as conn:
            previous = conn.execute("SELECT payload_hash, response_json FROM idempotency_requests WHERE key=?", (idempotency_key,)).fetchone()
            if not previous:
                return None
            if previous["payload_hash"] != fingerprint:
                raise ValueError("같은 idempotency key에 다른 요청을 사용할 수 없습니다.")
            run_ids = json.loads(previous["response_json"])
            rows = conn.execute(f"SELECT * FROM runs WHERE id IN ({','.join('?' for _ in run_ids)})", run_ids).fetchall()
        by_id = {row["id"]: self._run(row) for row in rows}
        return [by_id[run_id] for run_id in run_ids]

    def _persist_initial_runs(self, conn: sqlite3.Connection, runs: list[Run], grant_id: str | None) -> None:
        """Extension point kept inside the initial run/idempotency transaction."""
        if grant_id:
            conn.execute(
                f"UPDATE runs SET grant_id=? WHERE id IN ({','.join('?' for _ in runs)})",
                [grant_id, *(run.id for run in runs)],
            )

    def create_runs(self, payload: RunCreate, *, grant_id: str | None = None) -> tuple[list[Run], bool]:
        fingerprint = self._request_fingerprint(payload)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT payload_hash, response_json FROM idempotency_requests WHERE key=?", (payload.idempotency_key,)).fetchone()
            if previous:
                if previous["payload_hash"] != fingerprint:
                    raise ValueError("같은 idempotency key에 다른 요청을 사용할 수 없습니다.")
                stored = json.loads(previous["response_json"])
                run_ids = stored if isinstance(stored, list) and all(isinstance(value, str) for value in stored) else [value["id"] for value in stored]
                rows = conn.execute(f"SELECT * FROM runs WHERE id IN ({','.join('?' for _ in run_ids)})", run_ids).fetchall()
                by_id = {row["id"]: self._run(row) for row in rows}
                return [by_id[run_id] for run_id in run_ids], True
            active_count = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN (?, ?)",
                (RunStatus.QUEUED.value, RunStatus.RUNNING.value),
            ).fetchone()[0]
            if active_count + len(payload.items) > 100:
                raise ValueError("실행 대기열은 최대 100개입니다.")
            if payload.mode is RunMode.DIRECT:
                matching = conn.execute(
                    "SELECT program_snapshot_json, input_path FROM runs WHERE mode=? AND status IN (?, ?, ?)",
                    (RunMode.DIRECT.value, RunStatus.QUEUED.value, RunStatus.RUNNING.value, RunStatus.AWAITING_COMPLETION.value),
                ).fetchall()
                if any(
                    json.loads(row["program_snapshot_json"]).get("id") == payload.program_id
                    and row["input_path"] == payload.items[0].input_path
                    for row in matching
                ):
                    raise ValueError("같은 프로그램과 입력의 직접 실행이 이미 진행 중입니다.")
            program_row = conn.execute("SELECT * FROM programs WHERE id=? AND host_id=?", (payload.program_id, self.host_id)).fetchone()
            if not program_row:
                raise LookupError("등록된 프로그램을 찾을 수 없습니다.")
            program = self._program(program_row)
            batch_id = f"batch-run-{uuid4().hex}" if payload.mode is RunMode.BATCH else None
            now = utc_now()
            snapshot = program.model_dump(mode="json")
            runs: list[Run] = []
            for item in payload.items:
                run = Run(id=f"run-{uuid4().hex}", batch_id=batch_id, mode=payload.mode, status=RunStatus.QUEUED, program_name=program.name, program_version=program.version, input_path=item.input_path, working_directory=item.working_directory, created_at=now, started_at=None, completed_at=None, exit_code=None, note=None, error=None, context=payload.context.model_dump(mode="json"), program_snapshot=snapshot)
                conn.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (run.id, run.batch_id, run.mode.value, run.status.value, run.program_name, run.program_version, run.input_path, run.working_directory, run.created_at, None, None, None, None, None, json.dumps(run.context), json.dumps(run.program_snapshot), None, None))
                runs.append(run)
            self._persist_initial_runs(conn, runs, grant_id)
            conn.execute("INSERT INTO idempotency_requests VALUES (?, ?, ?)", (payload.idempotency_key, fingerprint, json.dumps([run.id for run in runs])))
        return runs, False

    def retry_run(self, source_run_id: str, idempotency_key: str, *, grant_id: str | None = None, context: dict[str, Any] | None = None) -> tuple[list[Run], bool]:
        """Create a fresh run from an immutable failed-run snapshot."""
        fingerprint = self._retry_fingerprint(source_run_id)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT payload_hash, response_json FROM idempotency_requests WHERE key=?", (idempotency_key,)).fetchone()
            if previous:
                if previous["payload_hash"] != fingerprint:
                    raise ValueError("같은 idempotency key에 다른 요청을 사용할 수 없습니다.")
                run_ids = json.loads(previous["response_json"])
                rows = conn.execute(f"SELECT * FROM runs WHERE id IN ({','.join('?' for _ in run_ids)})", run_ids).fetchall()
                by_id = {row["id"]: self._run(row) for row in rows}
                return [by_id[run_id] for run_id in run_ids], True
            source_row = conn.execute("SELECT * FROM runs WHERE id=?", (source_run_id,)).fetchone()
            if not source_row:
                raise LookupError("재실행할 기록을 찾을 수 없습니다.")
            source = self._run(source_row)
            if source.status not in {RunStatus.FAILED, RunStatus.INTERRUPTED}:
                raise ValueError("실패 또는 중단된 실행만 재실행할 수 있습니다.")
            if source.program_snapshot.get("host_id") != self.host_id:
                raise ValueError("다른 PC에서 생성된 실행 기록은 이 도우미에서 재실행할 수 없습니다.")
            active_count = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN (?, ?)",
                (RunStatus.QUEUED.value, RunStatus.RUNNING.value),
            ).fetchone()[0]
            if active_count >= 100:
                raise ValueError("실행 대기열은 최대 100개입니다.")
            if source.mode is RunMode.DIRECT:
                matching = conn.execute(
                    "SELECT program_snapshot_json, input_path FROM runs WHERE mode=? AND status IN (?, ?, ?)",
                    (RunMode.DIRECT.value, RunStatus.QUEUED.value, RunStatus.RUNNING.value, RunStatus.AWAITING_COMPLETION.value),
                ).fetchall()
                if any(
                    json.loads(row["program_snapshot_json"]).get("id") == source.program_snapshot.get("id")
                    and row["input_path"] == source.input_path
                    for row in matching
                ):
                    raise ValueError("같은 프로그램과 입력의 직접 실행이 이미 진행 중입니다.")
            now = utc_now()
            run = Run(
                id=f"run-{uuid4().hex}", source_run_id=source.id,
                batch_id=f"batch-run-{uuid4().hex}" if source.mode is RunMode.BATCH else None,
                mode=source.mode, status=RunStatus.QUEUED,
                program_name=source.program_name, program_version=source.program_version,
                input_path=source.input_path, working_directory=source.working_directory,
                created_at=now, started_at=None, completed_at=None, exit_code=None,
                note=None, error=None, context=context if context is not None else source.context, program_snapshot=source.program_snapshot,
            )
            conn.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (run.id, run.batch_id, run.mode.value, run.status.value, run.program_name, run.program_version, run.input_path, run.working_directory, run.created_at, None, None, None, None, None, json.dumps(run.context), json.dumps(run.program_snapshot), run.source_run_id, None))
            self._persist_initial_runs(conn, [run], grant_id)
            conn.execute("INSERT INTO idempotency_requests VALUES (?, ?, ?)", (idempotency_key, fingerprint, json.dumps([run.id])))
        return [run], False

    def get_run(self, run_id: str) -> Run | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run(row) if row else None

    def set_grant(self, run_ids: list[str], grant_id: str) -> None:
        """Persist the central execution grant before an executor is started."""
        if not run_ids or not grant_id:
            raise ValueError("실행 승인 정보를 저장할 수 없습니다.")
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT id FROM runs WHERE id IN ({','.join('?' for _ in run_ids)})", run_ids
            ).fetchall()
            if {row["id"] for row in rows} != set(run_ids):
                raise LookupError("실행 기록을 찾을 수 없습니다.")
            conn.execute(
                f"UPDATE runs SET grant_id=? WHERE id IN ({','.join('?' for _ in run_ids)})",
                [grant_id, *run_ids],
            )

    def get_grant(self, run_id: str) -> str | None:
        with self._connection() as conn:
            row = conn.execute("SELECT grant_id FROM runs WHERE id=?", (run_id,)).fetchone()
        return str(row["grant_id"]) if row and row["grant_id"] else None

    def list_runs(self, request_id: str | None = None, work_item_id: str | None = None) -> list[Run]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        result = [self._run(row) for row in rows]
        return [run for run in result if (request_id is None or run.context.get("request_id") == request_id) and (work_item_id is None or run.context.get("work_item_id") == work_item_id)]

    def transition_run(self, run_id: str, status: RunStatus, *, exit_code: int | None = None, error: str | None = None) -> Run:
        now = utc_now()
        with self._connection() as conn:
            current = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not current:
                raise LookupError("실행 기록을 찾을 수 없습니다.")
            started_at = now if status is RunStatus.RUNNING and current["started_at"] is None else current["started_at"]
            completed_at = now if status in {RunStatus.AWAITING_COMPLETION, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.COMPLETED, RunStatus.INTERRUPTED} else None
            conn.execute("UPDATE runs SET status=?, started_at=?, completed_at=?, exit_code=?, error=? WHERE id=?", (status.value, started_at, completed_at, exit_code, error, run_id))
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run(row)

    def mark_started(self, run_id: str) -> Run:
        """Mark a persisted run as started; called by RunExecutor immediately before Popen."""
        return self.transition_run(run_id, RunStatus.RUNNING)

    def mark_finished(
        self,
        run_id: str,
        *,
        status: RunStatus | str,
        exit_code: int | None = None,
        error: str | None = None,
    ) -> Run:
        """Persist a terminal executor result without exposing database details to it."""
        final_status = RunStatus(status)
        if final_status not in {RunStatus.AWAITING_COMPLETION, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.INTERRUPTED}:
            raise ValueError("실행기는 종료 상태만 기록할 수 있습니다.")
        return self.transition_run(run_id, final_status, exit_code=exit_code, error=error)

    def complete_direct_run(self, run_id: str, note: str) -> Run:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise LookupError("실행 기록을 찾을 수 없습니다.")
            if row["mode"] != RunMode.DIRECT.value or row["status"] != RunStatus.AWAITING_COMPLETION.value:
                raise ValueError("직접 실행의 완료 대기 상태에서만 완료할 수 있습니다.")
            conn.execute("UPDATE runs SET status=?, note=?, completed_at=? WHERE id=?", (RunStatus.COMPLETED.value, note, utc_now(), run_id))
            updated = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run(updated)

    def save_batch(self, name: str, program_id: str, items: list[RunItemInput]) -> SavedBatch:
        if not self.get_program(program_id):
            raise LookupError("등록된 프로그램을 찾을 수 없습니다.")
        batch = SavedBatch(id=f"batch-{uuid4().hex}", name=name, program_id=program_id, items=items)
        with self._connection() as conn:
            conn.execute("INSERT INTO batches VALUES (?, ?, ?, ?, ?)", (batch.id, batch.name, batch.program_id, json.dumps([item.model_dump() for item in items]), utc_now()))
        return batch

    def list_batches(self) -> list[SavedBatch]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM batches ORDER BY created_at DESC").fetchall()
        return [SavedBatch(id=row["id"], name=row["name"], program_id=row["program_id"], items=json.loads(row["items_json"])) for row in rows]
