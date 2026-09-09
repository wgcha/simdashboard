from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request

from ..access_policy import PROJECT_DATA_VIEW, require_assigned_work_item, require_resource_permission
from ..config import security_settings
from ..database_connection import ConnectionLike, rows
from ..security import AuthenticationError, Principal, _load_principal
from ..schemas.managed_local_execution import DeviceAuthorize, DevicePair, DeviceRunEvent, LocalRun, ManagedContext


PAIRING_TTL = timedelta(minutes=2)
SESSION_TTL = timedelta(minutes=10)
MAX_EVENTS_BODY_BYTES = 1_048_576


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _token() -> str:
    return secrets.token_urlsafe(32)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("저장된 관리형 실행 기록 형식이 올바르지 않습니다.")


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def require_managed_user_auth() -> None:
    if security_settings().auth_mode == "disabled":
        raise _error(503, "MANAGED_EXECUTION_AUTH_REQUIRED", "관리형 로컬 실행에는 password 또는 OIDC 인증 설정이 필요합니다.")


def bearer_token(request: Request, *, purpose: str) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _error(401, "DEVICE_AUTHENTICATION_REQUIRED", f"{purpose} Bearer 인증이 필요합니다.")
    return token


def device_response(row: tuple[Any, ...]) -> dict[str, Any]:
    return {"id": str(row[0]), "device_id": str(row[1]), "host_name": str(row[2]), "user_id": str(row[3]), "created_at": row[4], "revoked_at": row[5]}


def create_pairing(conn: ConnectionLike, principal: Principal, device_id: str) -> dict[str, Any]:
    require_managed_user_auth()
    now = utcnow()
    conn.execute("DELETE FROM managed_device_pairing_tokens WHERE expires_at<=?", [now])
    token = _token()
    expires_at = now + PAIRING_TTL
    conn.execute(
        """INSERT INTO managed_device_pairing_tokens
           (token_hash, user_id, device_id, expires_at, consumed_at, created_at)
           VALUES (?, ?, ?, ?, NULL, ?)""",
        [_digest(token), principal.user_id, device_id, expires_at, now],
    )
    return {"pairing_token": token, "expires_at": expires_at}


def list_devices(conn: ConnectionLike, principal: Principal) -> list[dict[str, Any]]:
    require_managed_user_auth()
    return [device_response(row) for row in conn.execute(
        """SELECT id, device_id, host_name, user_id, created_at, revoked_at
           FROM managed_device_bindings WHERE user_id=? ORDER BY created_at DESC""", [principal.user_id]
    ).fetchall()]


def create_session(conn: ConnectionLike, principal: Principal, binding_id: str) -> dict[str, Any]:
    require_managed_user_auth()
    conn.execute("DELETE FROM managed_device_sessions WHERE expires_at<=?", [utcnow()])
    binding = conn.execute(
        "SELECT id, user_id, revoked_at FROM managed_device_bindings WHERE id=?", [binding_id]
    ).fetchone()
    if not binding:
        raise _error(404, "DEVICE_NOT_FOUND", "연결된 PC를 찾을 수 없습니다.")
    if binding[1] != principal.user_id:
        raise _error(403, "DEVICE_NOT_OWNED", "본인의 연결된 PC만 사용할 수 있습니다.")
    if binding[2] is not None:
        raise _error(409, "DEVICE_REVOKED", "해제된 PC 연결에는 세션을 만들 수 없습니다.")
    now = utcnow()
    random_part = _token()
    expires_at = now + SESSION_TTL
    conn.execute(
        "INSERT INTO managed_device_sessions (token_hash, binding_id, user_id, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
        [_digest(random_part), binding_id, principal.user_id, expires_at, now],
    )
    return {"token": f"{binding_id}.{random_part}", "expires_at": expires_at, "binding_id": binding_id, "user_id": principal.user_id}


def revoke_device(conn: ConnectionLike, principal: Principal, binding_id: str) -> None:
    require_managed_user_auth()
    row = conn.execute("SELECT user_id, revoked_at FROM managed_device_bindings WHERE id=?", [binding_id]).fetchone()
    if not row:
        raise _error(404, "DEVICE_NOT_FOUND", "연결된 PC를 찾을 수 없습니다.")
    if row[0] != principal.user_id:
        raise _error(403, "DEVICE_NOT_OWNED", "본인의 연결된 PC만 해제할 수 있습니다.")
    if row[1] is None:
        conn.execute("UPDATE managed_device_bindings SET revoked_at=? WHERE id=?", [utcnow(), binding_id])


def pairing_preview(conn: ConnectionLike, pairing_token: str, device_id: str) -> dict[str, str]:
    row = conn.execute(
        """SELECT token.user_id, users.display_name, users.account_status, users.is_active
           FROM managed_device_pairing_tokens token JOIN users ON users.id=token.user_id
           WHERE token.token_hash=? AND token.device_id=? AND token.consumed_at IS NULL AND token.expires_at>?""",
        [_digest(pairing_token), device_id, utcnow()],
    ).fetchone()
    if not row or row[2] != "ACTIVE" or not row[3]:
        raise _error(401, "PAIRING_TOKEN_INVALID", "연결 요청이 없거나 만료되었습니다.")
    return {"user_id": str(row[0]), "display_name": str(row[1])}


def pair_device(conn: ConnectionLike, pairing_token: str, payload: DevicePair) -> dict[str, Any]:
    now = utcnow()
    # The conditional update consumes one-use requests atomically on PostgreSQL
    # and under DuckDB's serialized development connection.
    row = conn.execute(
        """UPDATE managed_device_pairing_tokens SET consumed_at=?
           WHERE token_hash=? AND device_id=? AND consumed_at IS NULL AND expires_at>?
           RETURNING user_id""",
        [now, _digest(pairing_token), payload.device_id, now],
    ).fetchone()
    if not row:
        raise _error(401, "PAIRING_TOKEN_INVALID", "연결 요청이 없거나 만료되었습니다.")
    user_id = str(row[0])
    locking_suffix = " FOR UPDATE" if conn.backend == "postgresql" else ""
    # Serializing through the account row prevents two valid pairing requests
    # for the same account/device from leaving two active bindings.
    user = conn.execute("SELECT account_status, is_active FROM users WHERE id=?" + locking_suffix, [user_id]).fetchone()
    if not user or user[0] != "ACTIVE" or not user[1]:
        raise _error(403, "ACCOUNT_NOT_ACTIVE", "활성 계정만 PC를 연결할 수 있습니다.")
    conn.execute(
        "UPDATE managed_device_bindings SET revoked_at=? WHERE user_id=? AND device_id=? AND revoked_at IS NULL",
        [now, user_id, payload.device_id],
    )
    binding_id = f"binding-{uuid4().hex}"
    conn.execute(
        """INSERT INTO managed_device_bindings
           (id, device_id, host_name, user_id, secret_hash, created_at, revoked_at)
           VALUES (?, ?, ?, ?, ?, ?, NULL)""",
        [binding_id, payload.device_id, payload.host_name, user_id, _digest(payload.device_secret), now],
    )
    return {"id": binding_id, "device_id": payload.device_id, "host_name": payload.host_name, "user_id": user_id, "created_at": now, "revoked_at": None}


def pair_device_atomic(conn: ConnectionLike, pairing_token: str, payload: DevicePair) -> dict[str, Any]:
    conn.execute("BEGIN TRANSACTION")
    try:
        result = pair_device(conn, pairing_token, payload)
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _binding_for_secret(conn: ConnectionLike, binding_id: str, device_secret: str) -> tuple[Any, ...]:
    row = conn.execute(
        "SELECT id, user_id, secret_hash, revoked_at FROM managed_device_bindings WHERE id=?", [binding_id]
    ).fetchone()
    if not row or not hmac.compare_digest(str(row[2]), _digest(device_secret)):
        raise _error(401, "DEVICE_SECRET_INVALID", "PC 연결 인증이 올바르지 않습니다.")
    if row[3] is not None:
        raise _error(403, "DEVICE_REVOKED", "해제된 PC 연결입니다.")
    return row


def _authenticate_session(conn: ConnectionLike, binding_id: str, user_id: str, session_token: str) -> Principal:
    prefix, separator, random_part = session_token.partition(".")
    if not separator or prefix != binding_id or not random_part:
        raise _error(401, "DEVICE_SESSION_INVALID", "PC 세션이 올바르지 않습니다.")
    row = conn.execute(
        """SELECT 1 FROM managed_device_sessions WHERE token_hash=? AND binding_id=? AND user_id=? AND expires_at>?""",
        [_digest(random_part), binding_id, user_id, utcnow()],
    ).fetchone()
    if not row:
        raise _error(401, "DEVICE_SESSION_INVALID", "PC 세션이 없거나 만료되었습니다.")
    try:
        principal = _load_principal(user_id)
    except AuthenticationError as exc:
        raise _error(403, "ACCOUNT_NOT_ACTIVE", str(exc)) from exc
    if principal.account_status != "ACTIVE":
        raise _error(403, "ACCOUNT_NOT_ACTIVE", "활성 계정만 관리형 PC를 사용할 수 있습니다.")
    return principal


def _server_context(conn: ConnectionLike, request: Request, principal: Principal, submitted: ManagedContext, *, require_in_progress: bool) -> ManagedContext:
    item = conn.execute(
        "SELECT request_id, display_name, status FROM request_work_items WHERE id=?", [submitted.work_item_id]
    ).fetchone()
    if not item or item[0] != submitted.request_id:
        raise _error(404, "WORK_ITEM_REQUEST_MISMATCH", "작업과 의뢰의 연결을 찾을 수 없습니다.")
    request.state.principal = principal
    require_assigned_work_item(request, submitted.work_item_id, conn=conn)
    if require_in_progress and item[2] != "IN_PROGRESS":
        raise _error(409, "WORK_ITEM_NOT_IN_PROGRESS", "진행 중인 현재 작업만 실행할 수 있습니다.")
    if require_in_progress:
        current = conn.execute(
            """SELECT id FROM request_work_items WHERE request_id=? AND status IN ('IN_PROGRESS', 'READY')
               ORDER BY CASE WHEN status='IN_PROGRESS' THEN 0 ELSE 1 END, sequence_no LIMIT 1""",
            [submitted.request_id],
        ).fetchone()
        if not current or current[0] != submitted.work_item_id:
            raise _error(409, "WORK_ITEM_NOT_CURRENT", "현재 순서의 작업만 실행할 수 있습니다.")
    return ManagedContext(request_id=submitted.request_id, work_item_id=submitted.work_item_id, task_name=str(item[1]), actor=principal.display_name)


def authorize_device(conn: ConnectionLike, request: Request, device_secret: str, payload: DeviceAuthorize) -> dict[str, Any]:
    binding = _binding_for_secret(conn, payload.binding_id, device_secret)
    principal = _authenticate_session(conn, payload.binding_id, str(binding[1]), payload.session_token)
    if payload.action in {"catalog", "history"}:
        if payload.context is not None:
            raise _error(422, "CONTEXT_NOT_ALLOWED", "카탈로그와 이력 인증에는 작업 context를 포함할 수 없습니다.")
        return {"user_id": principal.user_id, "display_name": principal.display_name, "context": None, "grant_id": None}
    if payload.context is None:
        raise _error(422, "CONTEXT_REQUIRED", "이 작업에는 request/work-item context가 필요합니다.")
    context = _server_context(conn, request, principal, payload.context, require_in_progress=payload.action in {"execute", "retry"})
    if payload.action == "complete":
        return {"user_id": principal.user_id, "display_name": principal.display_name, "context": context, "grant_id": None}
    grant_id = f"grant-{uuid4().hex}"
    conn.execute(
        """INSERT INTO managed_device_grants
           (id, binding_id, user_id, action, request_id, work_item_id, task_name, actor, issued_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [grant_id, payload.binding_id, principal.user_id, payload.action, context.request_id, context.work_item_id, context.task_name, context.actor, utcnow()],
    )
    return {"user_id": principal.user_id, "display_name": principal.display_name, "context": context, "grant_id": grant_id}


def _immutable_run_hash(run: LocalRun) -> str:
    immutable = run.model_dump(include={"source_run_id", "batch_id", "mode", "program_name", "program_version", "input_path", "working_directory", "created_at", "context", "program_snapshot"}, mode="json")
    return _digest(_json(immutable))


def _event_hash(event: DeviceRunEvent) -> str:
    return _digest(_json(event.model_dump(mode="json")))


_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "QUEUED": frozenset({"QUEUED", "RUNNING", "AWAITING_COMPLETION", "SUCCEEDED", "FAILED", "INTERRUPTED"}),
    "RUNNING": frozenset({"RUNNING", "AWAITING_COMPLETION", "SUCCEEDED", "FAILED", "INTERRUPTED"}),
    "AWAITING_COMPLETION": frozenset({"AWAITING_COMPLETION", "COMPLETED", "FAILED", "INTERRUPTED"}),
    "SUCCEEDED": frozenset({"SUCCEEDED"}),
    "FAILED": frozenset({"FAILED"}),
    "COMPLETED": frozenset({"COMPLETED"}),
    "INTERRUPTED": frozenset({"INTERRUPTED"}),
}


def _validate_status_transition(previous_run: dict[str, Any], submitted: LocalRun) -> None:
    previous_status = str(previous_run.get("status", ""))
    if submitted.status not in _STATUS_TRANSITIONS.get(previous_status, frozenset()):
        raise _error(409, "RUN_STATUS_REGRESSION", "완료되었거나 더 진행된 실행 상태를 되돌릴 수 없습니다.")


def _validate_event_grant(conn: ConnectionLike, binding_id: str, event: DeviceRunEvent) -> tuple[Any, ...]:
    grant = conn.execute(
        """SELECT id, binding_id, user_id, request_id, work_item_id, task_name, actor
           FROM managed_device_grants WHERE id=?""", [event.grant_id]
    ).fetchone()
    if not grant or grant[1] != binding_id:
        raise _error(403, "GRANT_INVALID", "실행 승인을 찾을 수 없습니다.")
    context = event.run.context
    if (context.request_id, context.work_item_id, context.task_name, context.actor) != (grant[3], grant[4], grant[5], grant[6]):
        raise _error(403, "GRANT_CONTEXT_MISMATCH", "실행 승인과 보고 context가 일치하지 않습니다.")
    return grant


def accept_events(conn: ConnectionLike, binding_id: str, device_secret: str, events: list[DeviceRunEvent], body_bytes: int) -> list[dict[str, Any]]:
    if body_bytes > MAX_EVENTS_BODY_BYTES:
        raise _error(413, "DEVICE_EVENTS_TOO_LARGE", "PC 실행 보고 크기가 제한을 초과했습니다.")
    _binding_for_secret(conn, binding_id, device_secret)
    sequences_by_run: dict[str, list[int]] = {}
    for event in events:
        sequences_by_run.setdefault(event.run.id, []).append(event.sequence)
    if any(values != sorted(values) or len(values) != len(set(values)) for values in sequences_by_run.values()):
        raise _error(409, "EVENT_SEQUENCE_NOT_MONOTONIC", "같은 실행의 보고 순서번호는 요청 안에서 오름차순이어야 합니다.")
    accepted: list[dict[str, Any]] = []
    highest_by_run: dict[str, int] = {}
    now = utcnow()
    for event in events:
        digest = _event_hash(event)
        previous = conn.execute(
            "SELECT event_hash FROM managed_device_event_sequences WHERE binding_id=? AND run_id=? AND sequence=?", [binding_id, event.run.id, event.sequence]
        ).fetchone()
        if previous:
            if not hmac.compare_digest(str(previous[0]), digest):
                raise _error(409, "EVENT_SEQUENCE_REUSED", "이미 사용한 보고 순서번호입니다.")
            accepted.append({"run_id": event.run.id, "sequence": event.sequence})
            continue
        if event.run.id not in highest_by_run:
            maximum = conn.execute(
                "SELECT max(sequence) FROM managed_device_event_sequences WHERE binding_id=? AND run_id=?", [binding_id, event.run.id]
            ).fetchone()[0]
            highest_by_run[event.run.id] = int(maximum) if maximum is not None else -1
        if event.sequence <= highest_by_run[event.run.id]:
            raise _error(409, "EVENT_SEQUENCE_NOT_MONOTONIC", "이전보다 큰 보고 순서번호가 필요합니다.")
        grant = _validate_event_grant(conn, binding_id, event)
        immutable_hash = _immutable_run_hash(event.run)
        current = conn.execute(
            "SELECT binding_id, grant_id, immutable_hash, run_json FROM managed_local_runs WHERE id=?", [event.run.id]
        ).fetchone()
        if current:
            if current[0] != binding_id or current[1] != event.grant_id:
                raise _error(409, "RUN_ID_CONFLICT", "다른 PC 또는 승인으로 생성된 실행 ID입니다.")
            if not hmac.compare_digest(str(current[2]), immutable_hash):
                raise _error(409, "RUN_IMMUTABLE_FIELDS_CHANGED", "실행 설정은 같은 실행 ID에서 변경할 수 없습니다.")
            _validate_status_transition(_json_object(current[3]), event.run)
            conn.execute(
                "UPDATE managed_local_runs SET run_json=?, last_sequence=?, synced_at=? WHERE id=?",
                [_json(event.run.model_dump(mode="json")), event.sequence, now, event.run.id],
            )
        else:
            conn.execute(
                """INSERT INTO managed_local_runs
                   (id, binding_id, grant_id, actor_user_id, request_id, work_item_id, task_name,
                    run_json, immutable_hash, last_sequence, created_at, synced_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [event.run.id, binding_id, event.grant_id, grant[2], grant[3], grant[4], grant[5],
                 _json(event.run.model_dump(mode="json")), immutable_hash, event.sequence, now, now],
            )
        conn.execute(
            "INSERT INTO managed_device_event_sequences (binding_id, run_id, sequence, event_hash, accepted_at) VALUES (?, ?, ?, ?, ?)",
            [binding_id, event.run.id, event.sequence, digest, now],
        )
        highest_by_run[event.run.id] = event.sequence
        accepted.append({"run_id": event.run.id, "sequence": event.sequence})
    return accepted


def accept_events_atomic(conn: ConnectionLike, binding_id: str, device_secret: str, events: list[DeviceRunEvent], body_bytes: int) -> list[dict[str, Any]]:
    conn.execute("BEGIN TRANSACTION")
    try:
        result = accept_events(conn, binding_id, device_secret, events, body_bytes)
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise


def list_central_runs(conn: ConnectionLike, request: Request, request_id: str, work_item_id: str) -> list[dict[str, Any]]:
    relation = conn.execute("SELECT 1 FROM request_work_items WHERE id=? AND request_id=?", [work_item_id, request_id]).fetchone()
    if not relation:
        raise _error(404, "WORK_ITEM_REQUEST_MISMATCH", "작업과 의뢰의 연결을 찾을 수 없습니다.")
    require_resource_permission(request, PROJECT_DATA_VIEW, "request", request_id, conn=conn)
    result: list[dict[str, Any]] = []
    for row in rows(conn.execute(
        """SELECT run.run_json, run.binding_id, binding.device_id, binding.host_name, run.actor_user_id, run.synced_at
           FROM managed_local_runs run JOIN managed_device_bindings binding ON binding.id=run.binding_id
           WHERE run.request_id=? AND run.work_item_id=? ORDER BY run.synced_at DESC, run.id DESC""",
        [request_id, work_item_id],
    )):
        run = _json_object(row["run_json"])
        run.update({key: row[key] for key in ("binding_id", "device_id", "host_name", "actor_user_id", "synced_at")})
        result.append(run)
    return result
