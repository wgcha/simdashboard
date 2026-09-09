from __future__ import annotations

import os
import platform
import socket
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .executor import RunExecutor, prepare_command, validate_executable
from .discovery import discover_programs
from .models import BatchCreate, CompleteRun, PickRequest, PickResult, Program, ProgramCandidate, RetryRun, Run, RunContext, RunCreate, SavedBatch
from .picker import pick_paths
from .storage import LocalRunnerStorage
from .managed import (
    BindingRuntime, CentralClient, CentralError, CentralUnavailable, ConfirmPairing,
    HttpCentralClient, ManagedConfig, ManagedStorage, native_pair_confirmation,
)


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
SHELL_SUFFIXES = {".bat", ".cmd", ".ps1"}


def _default_database() -> Path:
    return Path(os.environ.get("LOCAL_RUNNER_DATA_DIR", Path.home() / ".simulation-workbench" / "local-runner")) / "local-runner.sqlite3"


def _is_loopback(host: str) -> bool:
    return host.split(":", 1)[0].lower() in LOOPBACK_HOSTS


def _load_connection_code(data_dir: Path, supplied_token: str | None) -> str:
    if supplied_token:
        return supplied_token
    config_path = data_dir / "config.json"
    if config_path.exists():
        try:
            token = json.loads(config_path.read_text(encoding="utf-8")).get("connection_code")
            if isinstance(token, str) and token:
                return token
        except (OSError, json.JSONDecodeError):
            pass
    token = secrets.token_urlsafe(32)
    config_path.write_text(json.dumps({"connection_code": token}, indent=2), encoding="utf-8")
    return token


def create_app(
    *, data_dir: str | Path | None = None, token: str | None = None,
    allowed_origins: list[str] | None = None, host_id: str | None = None,
    executor: RunExecutor | None = None, server_url: str | None = None,
    confirm_pairing: ConfirmPairing | None = None, central_client: CentralClient | None = None,
) -> FastAPI:
    """Create standalone mode only when no managed server URL is configured."""
    if server_url:
        if token is not None:
            raise ValueError("관리 모드에서는 연결 코드를 사용할 수 없습니다.")
        return _create_managed_app(
            data_dir=data_dir, server_url=server_url, allowed_origins=allowed_origins,
            host_id=host_id, executor=executor, confirm_pairing=confirm_pairing,
            central_client=central_client,
        )
    return _create_standalone_app(data_dir=data_dir, token=token, allowed_origins=allowed_origins, host_id=host_id, executor=executor)


def _create_standalone_app(*, data_dir: str | Path | None = None, token: str | None = None, allowed_origins: list[str] | None = None, host_id: str | None = None, executor: RunExecutor | None = None) -> FastAPI:
    """Create a loopback API. Production configuration must supply a connection code."""
    directory = Path(data_dir) if data_dir is not None else _default_database().parent
    directory.mkdir(parents=True, exist_ok=True)
    secret = _load_connection_code(directory, token or os.environ.get("LOCAL_RUNNER_CONNECTION_CODE"))
    origins = allowed_origins or ["http://127.0.0.1:5173", "http://localhost:5173"]
    local_host_id = host_id or os.environ.get("LOCAL_RUNNER_HOST_ID", socket.gethostname())
    store = LocalRunnerStorage(directory / "local-runner.sqlite3", local_host_id)
    runner = executor or RunExecutor(store)
    app = FastAPI(title="Local Program Runner API", version="1.0.0")
    app.state.storage = store
    app.state.executor = runner
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def require_local_client(request: Request, call_next):
        raw_host = request.headers.get("host", "").lower()
        host = "[::1]" if raw_host.startswith("[::1]") else raw_host.split(":", 1)[0]
        if host not in {"127.0.0.1", "localhost", "[::1]"}:
            return JSONResponse(status_code=status.HTTP_421_MISDIRECTED_REQUEST, content={"detail": "로컬 주소로만 접근할 수 있습니다."})
        if request.method == "OPTIONS":
            if request.headers.get("origin") not in origins:
                return JSONResponse(status_code=403, content={"detail": "허용되지 않은 origin입니다."})
            return await call_next(request)
        request_origin = request.headers.get("origin")
        # Browsers always supply Origin for this cross-origin bearer request;
        # native loopback clients may not. Treat any supplied Origin, including
        # the literal browser value "null", as an exact allow-list match.
        if request_origin is not None and request_origin not in origins:
            return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": "허용되지 않은 origin입니다."})
        if request.headers.get("authorization") != f"Bearer {secret}":
            return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "연결 코드가 올바르지 않습니다."})
        try:
            content_length = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "잘못된 Content-Length 헤더입니다."})
        if content_length < 0:
            return JSONResponse(status_code=400, content={"detail": "잘못된 Content-Length 헤더입니다."})
        if request.method in {"POST", "PUT"}:
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                return JSONResponse(status_code=415, content={"detail": "application/json 본문이 필요합니다."})
            if content_length > 65_536 or len(await request.body()) > 65_536:
                return JSONResponse(status_code=413, content={"detail": "요청 본문이 너무 큽니다."})
        if request.url.path.startswith("/v1/"):
            known = {
                "/v1/health": set(), "/v1/programs": {"q"}, "/v1/runs": {"request_id", "work_item_id"},
                "/v1/discover": set(), "/v1/pick": set(), "/v1/batches": set(),
            }
            allowed = known.get(request.url.path, set())
            if set(request.query_params) - allowed:
                return JSONResponse(status_code=422, content={"detail": "지원하지 않는 query parameter입니다."})
        return await call_next(request)

    def validate_program(candidate: ProgramCandidate) -> None:
        try:
            validate_executable(candidate.executable_path)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if any("{" in value.replace("{input}", "") or "}" in value.replace("{input}", "") for value in candidate.arguments):
            raise HTTPException(422, "인자에는 {input} 토큰만 사용할 수 있습니다.")

    @app.get("/v1/health")
    def health() -> dict[str, str]:
        return {"host_id": local_host_id, "host_name": socket.gethostname(), "platform": platform.platform()}

    @app.get("/v1/programs", response_model=list[Program])
    def list_programs(q: str | None = None) -> list[Program]:
        return store.list_programs(q)

    @app.post("/v1/programs", response_model=Program, status_code=status.HTTP_201_CREATED)
    def register_program(payload: ProgramCandidate) -> Program:
        validate_program(payload)
        return store.register_program(payload)

    @app.put("/v1/programs/{program_id}", response_model=Program)
    def update_program(program_id: str, payload: ProgramCandidate) -> Program:
        validate_program(payload)
        try:
            updated = store.update_program(program_id, payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not updated:
            raise HTTPException(404, "등록된 프로그램을 찾을 수 없습니다.")
        return updated

    @app.delete("/v1/programs/{program_id}")
    def delete_program(program_id: str) -> dict[str, bool]:
        if not store.delete_program(program_id):
            raise HTTPException(404, "등록된 프로그램을 찾을 수 없습니다.")
        return {"ok": True}

    @app.post("/v1/discover", response_model=list[ProgramCandidate])
    def discover() -> list[ProgramCandidate]:
        return [ProgramCandidate.model_validate(candidate) for candidate in discover_programs()]

    @app.post("/v1/pick", response_model=PickResult)
    def pick(payload: PickRequest) -> PickResult:
        try:
            return PickResult(paths=pick_paths(payload.kind))
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc

    @app.post("/v1/runs", response_model=list[Run])
    def create_runs(payload: RunCreate) -> list[Run]:
        try:
            replay = store.replay_runs(payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if replay is not None:
            return replay
        if payload.mode.value == "DIRECT" and len(payload.items) != 1:
            raise HTTPException(422, "직접 실행은 한 항목만 실행할 수 있습니다.")
        program = store.get_program(payload.program_id)
        if not program:
            raise HTTPException(404, "등록된 프로그램을 찾을 수 없습니다.")
        executable = Path(program.executable_path)
        if not program.available or not executable.is_file() or executable.suffix.lower() in SHELL_SUFFIXES:
            raise HTTPException(422, "실행할 수 있는 등록 프로그램이 아닙니다.")
        for item in payload.items:
            try:
                prepare_command(program.model_dump(mode="json"), item.input_path, item.working_directory, allow_empty_input=payload.mode.value == "DIRECT")
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if payload.mode.value == "BATCH" and not item.input_path:
                raise HTTPException(422, "일괄 실행에는 입력 파일이 필요합니다.")
            if item.input_path and not Path(item.input_path).is_file():
                raise HTTPException(422, "입력 파일이 존재해야 합니다.")
            if item.working_directory and not Path(item.working_directory).is_dir():
                raise HTTPException(422, "작업 폴더가 존재해야 합니다.")
        try:
            runs, reused = store.create_runs(payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not reused:
            program_mapping = runs[0].program_snapshot
            if payload.mode.value == "BATCH":
                runner.start_batch([run.model_dump(mode="json") for run in runs], program_mapping)
            else:
                runner.start_direct(runs[0].model_dump(mode="json"), program_mapping)
        return runs

    @app.get("/v1/runs", response_model=list[Run])
    def list_runs(request_id: str | None = None, work_item_id: str | None = None) -> list[Run]:
        return store.list_runs(request_id, work_item_id)

    @app.post("/v1/runs/{run_id}/complete", response_model=Run)
    def complete_run(run_id: str, payload: CompleteRun) -> Run:
        try:
            return store.complete_direct_run(run_id, payload.note)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/v1/runs/{run_id}/retry", response_model=list[Run])
    def retry_run(run_id: str, payload: RetryRun) -> list[Run]:
        try:
            replay = store.replay_retry(run_id, payload.idempotency_key)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if replay is not None:
            return replay
        source = store.get_run(run_id)
        if not source:
            raise HTTPException(404, "재실행할 기록을 찾을 수 없습니다.")
        if source.status.value not in {"FAILED", "INTERRUPTED"}:
            raise HTTPException(409, "실패 또는 중단된 실행만 재실행할 수 있습니다.")
        if source.program_snapshot.get("host_id") != local_host_id:
            raise HTTPException(409, "다른 PC에서 생성된 실행 기록은 이 도우미에서 재실행할 수 없습니다.")
        try:
            prepare_command(
                source.program_snapshot,
                source.input_path,
                source.working_directory,
                allow_empty_input=source.mode.value == "DIRECT",
            )
        except ValueError as exc:
            raise HTTPException(422, f"저장된 실행 설정을 재실행할 수 없습니다: {exc}") from exc
        try:
            runs, reused = store.retry_run(run_id, payload.idempotency_key)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not reused:
            snapshot = runs[0].program_snapshot
            if runs[0].mode.value == "BATCH":
                runner.start_batch([run.model_dump(mode="json") for run in runs], snapshot)
            else:
                runner.start_direct(runs[0].model_dump(mode="json"), snapshot)
        return runs

    @app.get("/v1/batches", response_model=list[SavedBatch])
    def list_batches() -> list[SavedBatch]:
        return store.list_batches()

    @app.post("/v1/batches", response_model=SavedBatch, status_code=status.HTTP_201_CREATED)
    def save_batch(payload: BatchCreate) -> SavedBatch:
        for item in payload.items:
            if not item.input_path:
                raise HTTPException(422, "일괄 목록에는 입력 파일이 필요합니다.")
        try:
            return store.save_batch(payload.name, payload.program_id, payload.items)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    return app


def _create_managed_app(
    *, data_dir: str | Path | None, server_url: str, allowed_origins: list[str] | None,
    host_id: str | None, executor: RunExecutor | None, confirm_pairing: ConfirmPairing | None,
    central_client: CentralClient | None,
) -> FastAPI:
    """Create the account-bound loopback API.

    This intentionally does not open or migrate ``local-runner.sqlite3`` from
    standalone mode. Each central binding is assigned its own database below
    ``bindings/<binding-id>/``.
    """
    directory = Path(data_dir) if data_dir is not None else _default_database().parent
    directory.mkdir(parents=True, exist_ok=True)
    origins = allowed_origins or ["http://127.0.0.1:5173", "http://localhost:5173"]
    config = ManagedConfig(directory, server_url)
    # The device UUID survives a Windows computer-name change.  ``host_id``
    # remains a test seam; host_name is display-only in managed mode.
    local_host_id = host_id or config.device_id
    client = central_client or HttpCentralClient(server_url)
    runtimes: dict[str, BindingRuntime] = {}
    runtime_lock = threading.RLock()
    stop_sync = threading.Event()
    app = FastAPI(title="Managed Local Program Runner API", version="1.0.0")
    app.state.managed = True
    app.state.config = config
    app.state.runtimes = runtimes

    def runtime_for(binding_id: str) -> BindingRuntime | None:
        with runtime_lock:
            existing = runtimes.get(binding_id)
            if existing:
                return existing
            metadata = config.binding(binding_id)
            if not metadata or not metadata.get("user_id") or not metadata.get("device_secret"):
                return None
            store = ManagedStorage(directory / "bindings" / binding_id / "local-runner.sqlite3", local_host_id, binding_id)
            runner: Any = executor if executor is not None and not runtimes else RunExecutor(store)
            value = BindingRuntime(binding_id, metadata, store, runner)
            runtimes[binding_id] = value
            return value

    # Recover every already paired account once.  The cache prevents request
    # handlers from reopening a store (and falsely interrupting live runs).
    for existing_binding in config.bindings():
        runtime_for(existing_binding)

    def central_error(exc: Exception) -> HTTPException:
        if isinstance(exc, CentralError):
            code = exc.status if exc.status in {401, 403, 409, 422} else 503
            return HTTPException(code, "중앙 서버에서 이 요청을 승인하지 않았습니다.")
        return HTTPException(503, "중앙 서버에 연결할 수 없어 새 작업을 처리할 수 없습니다.")

    def bearer(request: Request) -> tuple[str, BindingRuntime]:
        value = request.headers.get("authorization", "")
        if not value.startswith("Bearer "):
            raise HTTPException(401, "중앙 연결 세션이 필요합니다.")
        session = value[7:].strip()
        binding_id, separator, _ = session.partition(".")
        if not separator or not binding_id:
            raise HTTPException(401, "중앙 연결 세션이 올바르지 않습니다.")
        runtime = runtime_for(binding_id)
        if not runtime:
            raise HTTPException(401, "이 PC에 연결된 계정을 찾을 수 없습니다.")
        return session, runtime

    def authorize(request: Request, action: str, context: dict[str, Any] | None = None) -> tuple[BindingRuntime, dict[str, Any]]:
        session, runtime = bearer(request)
        try:
            result = client.post("/device/authorize", runtime.secret, {
                "binding_id": runtime.binding_id, "session_token": session, "action": action,
                **({"context": context} if context is not None else {}),
            })
        except (CentralError, CentralUnavailable) as exc:
            raise central_error(exc) from exc
        if result.get("user_id") != runtime.user_id:
            raise HTTPException(401, "중앙 연결 세션이 올바르지 않습니다.")
        return runtime, result

    def sync(runtime: BindingRuntime) -> None:
        if runtime.sync_disabled:
            return
        if time.monotonic() < runtime.next_sync_at:
            return
        def backoff(message: str) -> None:
            runtime.sync_attempts += 1
            delay = min(300, 2 ** min(runtime.sync_attempts, 8))
            runtime.next_sync_at = time.monotonic() + delay
            runtime.sync_error = message

        # Device events have a bounded 1 MiB central body limit.  Use a lower
        # 900 KiB envelope budget and measure the actual UTF-8 JSON, rather
        # than assuming that 100 valid local events also fit together.
        for _ in range(100):
            events = runtime.store.pending_events(100)
            if not events:
                runtime.sync_error = None
                runtime.sync_attempts = 0
                runtime.next_sync_at = 0.0
                return
            wire_events = [{key: value for key, value in event.items() if key != "run_id"} for event in events]
            batch: list[dict[str, Any]] = []
            for event in wire_events:
                candidate = [*batch, event]
                size = len(json.dumps({"binding_id": runtime.binding_id, "events": candidate}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                if batch and size > runtime.sync_max_bytes:
                    break
                batch = candidate
            payload = {"binding_id": runtime.binding_id, "events": batch}
            payload_size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            try:
                response = client.post("/device/events", runtime.secret, payload)
                accepted = response.get("accepted")
                if not isinstance(accepted, list) or not accepted:
                    raise CentralUnavailable("central events response was invalid")
                runtime.store.mark_events_sent(accepted)
                runtime.sync_error = None
                runtime.sync_attempts = 0
                runtime.next_sync_at = 0.0
            except CentralError as exc:
                if exc.status in {401, 403}:
                    runtime.sync_disabled = "중앙 서버에서 이 PC 연결을 해제하여 보류 중인 이력을 보관한 채 동기화를 중지했습니다."
                    runtime.sync_error = runtime.sync_disabled
                    config.disable_sync(runtime.binding_id, runtime.sync_disabled)
                    return
                if exc.status == 413 and len(batch) > 1:
                    runtime.sync_max_bytes = max(64 * 1024, min(runtime.sync_max_bytes // 2, payload_size // 2))
                    continue
                backoff("중앙 이력 동기화 요청이 너무 크거나 충돌했습니다. 보류 중인 이력은 유지하며 더 작은 묶음으로 다시 시도합니다.")
                return
            except CentralUnavailable:
                backoff("중앙 서버에 연결할 수 없습니다. 보류 중인 이력은 유지하며 나중에 다시 시도합니다.")
                return

    def sync_loop() -> None:
        while not stop_sync.wait(2):
            with runtime_lock:
                values = list(runtimes.values())
            for runtime in values:
                with runtime.lock:
                    sync(runtime)

    @app.on_event("startup")
    def start_sync() -> None:
        thread = threading.Thread(target=sync_loop, daemon=True, name="local-runner-sync")
        app.state.sync_thread = thread
        thread.start()

    @app.on_event("shutdown")
    def end_sync() -> None:
        stop_sync.set()

    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def require_local_client(request: Request, call_next: Callable[..., Any]):
        raw_host = request.headers.get("host", "").lower()
        host = "[::1]" if raw_host.startswith("[::1]") else raw_host.split(":", 1)[0]
        if host not in {"127.0.0.1", "localhost", "[::1]"}:
            return JSONResponse(status_code=421, content={"detail": "로컬 주소로만 접근할 수 있습니다."})
        origin = request.headers.get("origin")
        if origin is not None and origin not in origins:
            return JSONResponse(status_code=403, content={"detail": "허용되지 않은 origin입니다."})
        if request.method == "OPTIONS":
            return await call_next(request)
        try:
            content_length = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "잘못된 Content-Length 헤더입니다."})
        if content_length < 0:
            return JSONResponse(status_code=400, content={"detail": "잘못된 Content-Length 헤더입니다."})
        if request.method in {"POST", "PUT"} or content_length > 0:
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                return JSONResponse(status_code=415, content={"detail": "application/json 본문이 필요합니다."})
            if content_length > 65_536 or len(await request.body()) > 65_536:
                return JSONResponse(status_code=413, content={"detail": "요청 본문이 너무 큽니다."})
        known = {"/v1/identity": set(), "/v1/pair": set(), "/v1/health": set(), "/v1/programs": {"q"}, "/v1/runs": {"request_id", "work_item_id"}, "/v1/discover": set(), "/v1/pick": set(), "/v1/batches": set()}
        if request.url.path.startswith("/v1/") and set(request.query_params) - known.get(request.url.path, set()):
            return JSONResponse(status_code=422, content={"detail": "지원하지 않는 query parameter입니다."})
        return await call_next(request)

    def validate_program(candidate: ProgramCandidate) -> None:
        try:
            validate_executable(candidate.executable_path)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if any("{" in value.replace("{input}", "") or "}" in value.replace("{input}", "") for value in candidate.arguments):
            raise HTTPException(422, "인자에는 {input} 토큰만 사용할 수 있습니다.")

    @app.get("/v1/identity")
    def identity() -> dict[str, Any]:
        return {"device_id": config.device_id, "host_name": socket.gethostname(), "managed": True}

    @app.post("/v1/pair")
    def pair(payload: dict[str, str]) -> dict[str, str]:
        pairing_token = payload.get("pairing_token") if set(payload) == {"pairing_token"} else None
        if not isinstance(pairing_token, str) or not pairing_token or len(pairing_token) > 4096:
            raise HTTPException(422, "연결 요청이 올바르지 않습니다.")
        try:
            preview = client.post("/device/pair-preview", pairing_token, {"device_id": config.device_id})
        except (CentralError, CentralUnavailable) as exc:
            raise central_error(exc) from exc
        user_id, display_name = preview.get("user_id"), preview.get("display_name")
        if not isinstance(user_id, str) or not isinstance(display_name, str):
            raise HTTPException(503, "중앙 서버의 연결 미리보기가 올바르지 않습니다.")
        confirmation = confirm_pairing or native_pair_confirmation
        if not confirmation({"server_url": config.server_url, "user_id": user_id, "display_name": display_name, "host_name": socket.gethostname()}):
            raise HTTPException(409, "PC 연결이 취소되었습니다.")
        device_secret = secrets.token_urlsafe(32)
        try:
            device = client.post("/device/pair", pairing_token, {"device_id": config.device_id, "host_name": socket.gethostname(), "device_secret": device_secret})
        except (CentralError, CentralUnavailable) as exc:
            raise central_error(exc) from exc
        binding_id = device.get("id") or device.get("binding_id")
        stored_user = device.get("user_id")
        if not isinstance(binding_id, str) or not isinstance(stored_user, str) or stored_user != user_id:
            raise HTTPException(503, "중앙 서버의 연결 응답이 올바르지 않습니다.")
        config.add_binding(binding_id, stored_user, device_secret)
        with runtime_lock:
            for existing_id, existing_runtime in runtimes.items():
                if existing_id != binding_id and existing_runtime.user_id == stored_user:
                    existing_runtime.sync_disabled = "이 계정의 새 PC 연결로 인해 이전 연결 동기화가 중지되었습니다."
                    existing_runtime.sync_error = existing_runtime.sync_disabled
        runtime_for(binding_id)
        return {"binding_id": binding_id, "user_id": stored_user}

    @app.get("/v1/health")
    def health(request: Request) -> dict[str, Any]:
        runtime, _ = authorize(request, "catalog")
        return {"host_id": local_host_id, "host_name": socket.gethostname(), "platform": platform.platform(), "managed": True, "binding_id": runtime.binding_id, "user_id": runtime.user_id, "sync_pending": runtime.store.pending_count(), "sync_error": runtime.sync_error}

    @app.get("/v1/programs", response_model=list[Program])
    def list_programs(request: Request, q: str | None = None) -> list[Program]:
        runtime, _ = authorize(request, "catalog")
        return runtime.store.list_programs(q)

    @app.post("/v1/programs", response_model=Program, status_code=201)
    def register_program(request: Request, payload: ProgramCandidate) -> Program:
        runtime, _ = authorize(request, "catalog")
        validate_program(payload); return runtime.store.register_program(payload)

    @app.put("/v1/programs/{program_id}", response_model=Program)
    def update_program(request: Request, program_id: str, payload: ProgramCandidate) -> Program:
        runtime, _ = authorize(request, "catalog")
        validate_program(payload)
        try: value = runtime.store.update_program(program_id, payload)
        except ValueError as exc: raise HTTPException(409, str(exc)) from exc
        if not value: raise HTTPException(404, "등록된 프로그램을 찾을 수 없습니다.")
        return value

    @app.delete("/v1/programs/{program_id}")
    def delete_program(request: Request, program_id: str) -> dict[str, bool]:
        runtime, _ = authorize(request, "catalog")
        if not runtime.store.delete_program(program_id): raise HTTPException(404, "등록된 프로그램을 찾을 수 없습니다.")
        return {"ok": True}

    @app.post("/v1/discover", response_model=list[ProgramCandidate])
    def discover(request: Request) -> list[ProgramCandidate]:
        authorize(request, "catalog"); return [ProgramCandidate.model_validate(value) for value in discover_programs()]

    @app.post("/v1/pick", response_model=PickResult)
    def pick(request: Request, payload: PickRequest) -> PickResult:
        authorize(request, "catalog")
        try: return PickResult(paths=pick_paths(payload.kind))
        except RuntimeError as exc: raise HTTPException(500, str(exc)) from exc

    def validate_run(runtime: BindingRuntime, payload: RunCreate) -> Program:
        if payload.mode.value == "DIRECT" and len(payload.items) != 1: raise HTTPException(422, "직접 실행은 한 항목만 실행할 수 있습니다.")
        program = runtime.store.get_program(payload.program_id)
        if not program: raise HTTPException(404, "등록된 프로그램을 찾을 수 없습니다.")
        executable = Path(program.executable_path)
        if not program.available or not executable.is_file() or executable.suffix.lower() in SHELL_SUFFIXES: raise HTTPException(422, "실행할 수 있는 등록 프로그램이 아닙니다.")
        for item in payload.items:
            try: prepare_command(program.model_dump(mode="json"), item.input_path, item.working_directory, allow_empty_input=payload.mode.value == "DIRECT")
            except ValueError as exc: raise HTTPException(422, str(exc)) from exc
            if payload.mode.value == "BATCH" and not item.input_path: raise HTTPException(422, "일괄 실행에는 입력 파일이 필요합니다.")
        return program

    @app.post("/v1/runs", response_model=list[Run])
    def create_runs(request: Request, payload: RunCreate) -> list[Run]:
        runtime, _ = authorize(request, "catalog")
        try: replay = runtime.store.replay_runs(payload)
        except ValueError as exc: raise HTTPException(409, str(exc)) from exc
        if replay is not None: return replay
        validate_run(runtime, payload)
        context = payload.context.model_dump(mode="json")
        _, approval = authorize(request, "execute", context)
        authoritative = approval.get("context")
        grant_id = approval.get("grant_id")
        if not isinstance(authoritative, dict) or not isinstance(grant_id, str): raise HTTPException(503, "중앙 서버의 실행 승인이 올바르지 않습니다.")
        try:
            payload = payload.model_copy(update={"context": RunContext.model_validate(authoritative)})
        except ValueError as exc:
            raise HTTPException(503, "중앙 서버의 실행 승인이 올바르지 않습니다.") from exc
        try: runs, reused = runtime.store.create_runs(payload, grant_id=grant_id)
        except (ValueError, LookupError) as exc: raise HTTPException(409, str(exc)) from exc
        if reused: return runs
        if payload.mode.value == "BATCH": runtime.executor.start_batch([item.model_dump(mode="json") for item in runs], runs[0].program_snapshot)
        else: runtime.executor.start_direct(runs[0].model_dump(mode="json"), runs[0].program_snapshot)
        sync(runtime)
        return runs

    @app.get("/v1/runs", response_model=list[Run])
    def list_runs(request: Request, request_id: str | None = None, work_item_id: str | None = None) -> list[Run]:
        runtime, _ = authorize(request, "history")
        return runtime.store.list_runs(request_id, work_item_id)

    @app.post("/v1/runs/{run_id}/complete", response_model=Run)
    def complete_run(request: Request, run_id: str, payload: CompleteRun) -> Run:
        runtime, _ = authorize(request, "catalog")
        source = runtime.store.get_run(run_id)
        if not source: raise HTTPException(404, "실행 기록을 찾을 수 없습니다.")
        authorize(request, "complete", source.context)
        try: run = runtime.store.complete_direct_run(run_id, payload.note)
        except ValueError as exc: raise HTTPException(409, str(exc)) from exc
        sync(runtime); return run

    @app.post("/v1/runs/{run_id}/retry", response_model=list[Run])
    def retry_run(request: Request, run_id: str, payload: RetryRun) -> list[Run]:
        runtime, _ = authorize(request, "catalog")
        try: replay = runtime.store.replay_retry(run_id, payload.idempotency_key)
        except ValueError as exc: raise HTTPException(409, str(exc)) from exc
        if replay is not None: return replay
        source = runtime.store.get_run(run_id)
        if not source: raise HTTPException(404, "재실행할 기록을 찾을 수 없습니다.")
        if source.status.value not in {"FAILED", "INTERRUPTED"}:
            raise HTTPException(409, "실패 또는 중단된 실행만 재실행할 수 있습니다.")
        _, approval = authorize(request, "retry", source.context)
        grant_id = approval.get("grant_id")
        authoritative = approval.get("context")
        if not isinstance(grant_id, str) or not isinstance(authoritative, dict): raise HTTPException(503, "중앙 서버의 재실행 승인이 올바르지 않습니다.")
        if source.program_snapshot.get("host_id") != local_host_id:
            raise HTTPException(409, "다른 PC에서 생성된 실행 기록은 이 도우미에서 재실행할 수 없습니다.")
        try:
            prepare_command(source.program_snapshot, source.input_path, source.working_directory, allow_empty_input=source.mode.value == "DIRECT")
        except ValueError as exc:
            raise HTTPException(422, f"저장된 실행 설정을 재실행할 수 없습니다: {exc}") from exc
        try:
            authoritative_context = RunContext.model_validate(authoritative).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(503, "중앙 서버의 재실행 승인이 올바르지 않습니다.") from exc
        try: runs, reused = runtime.store.retry_run(run_id, payload.idempotency_key, grant_id=grant_id, context=authoritative_context)
        except (ValueError, LookupError) as exc: raise HTTPException(409, str(exc)) from exc
        if reused: return runs
        if runs[0].mode.value == "BATCH": runtime.executor.start_batch([item.model_dump(mode="json") for item in runs], runs[0].program_snapshot)
        else: runtime.executor.start_direct(runs[0].model_dump(mode="json"), runs[0].program_snapshot)
        sync(runtime); return runs

    @app.get("/v1/batches", response_model=list[SavedBatch])
    def list_batches(request: Request) -> list[SavedBatch]:
        runtime, _ = authorize(request, "catalog"); return runtime.store.list_batches()

    @app.post("/v1/batches", response_model=SavedBatch, status_code=201)
    def save_batch(request: Request, payload: BatchCreate) -> SavedBatch:
        runtime, _ = authorize(request, "catalog")
        try: return runtime.store.save_batch(payload.name, payload.program_id, payload.items)
        except LookupError as exc: raise HTTPException(404, str(exc)) from exc

    return app
