"""Admin-managed, reusable vocabulary definitions for explicit UI selection."""
from __future__ import annotations

import json
import re
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ..database_connection import connect, rows
from ..domains.semantic_vocabulary.normalization import normalize_term
from ..modules.access_control import SYSTEM_CATALOG_MANAGE, require_permission
from ..security import write_audit_event
from .semantic_body_limit import SemanticBodyLimitRoute


router = APIRouter(prefix="/api/semantic-vocabulary", tags=["semantic-vocabulary"], route_class=SemanticBodyLimitRoute)
TargetKind = Literal["FOLDER_ROLE", "PROJECT", "REQUEST", "LOAD_CASE", "RESULT_ITEM"]
FolderRole = {"PROJECT", "REQUEST", "LOAD_CASE", "INPUT", "RESULTS"}
_KEY = re.compile(r"^[a-z][a-z0-9_]{1,127}$")
_ENTRY_LIMIT = 2000


class VocabularyBody(BaseModel):
    key: str = Field(min_length=2, max_length=128)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)
    target_kind: TargetKind
    target_id: str = Field(min_length=1, max_length=200)
    scope_project_id: str | None = Field(default=None, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=64)
    enabled: bool = True

    @field_validator("key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("key must be a string")
        if not _KEY.fullmatch(value):
            raise ValueError("key must be lowercase ASCII snake_case")
        return value

    @field_validator("label", "target_id", "scope_project_id", mode="before")
    @classmethod
    def stripped(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("aliases")
    @classmethod
    def valid_aliases(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            if not isinstance(value, str):
                raise ValueError("aliases must be strings")
            value = value.strip()
            if not value or len(value) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError("each alias must be 1 to 200 characters")
            cleaned.append(value)
        if len({normalize_term(value) for value in cleaned}) != len(cleaned):
            raise ValueError("aliases must be distinct after normalization")
        return cleaned

    @field_validator("label", "description", "target_id", "scope_project_id")
    @classmethod
    def database_text_has_no_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("NUL is not allowed in database text")
        return value


class VocabularyUpdate(VocabularyBody):
    expected_revision: int = Field(ge=1)


class ResolveBody(BaseModel):
    terms: list[str] = Field(min_length=1, max_length=64)
    target_kinds: list[TargetKind] | None = Field(default=None, max_length=5)
    scope_project_id: str | None = Field(default=None, max_length=200)

    @field_validator("terms")
    @classmethod
    def valid_terms(cls, values: list[str]) -> list[str]:
        if any(
            not isinstance(value, str)
            or not value.strip()
            or any(unicodedata.category(char) == "Cc" for char in value)
            or len(normalize_term(value)) > 200
            for value in values
        ):
            raise ValueError("terms must be non-empty strings up to 200 characters")
        return [value.strip() for value in values]

    @field_validator("scope_project_id")
    @classmethod
    def scope_has_no_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("NUL is not allowed in database text")
        return value


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@contextmanager
def _transaction(conn: Any):
    conn.execute("BEGIN TRANSACTION")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _target_context(conn: Any, kind: str, target_id: str, *, allow_missing: bool = False) -> tuple[str, dict[str, str | None], bool]:
    missing_context = {"project_id": None, "request_id": None, "load_case_id": None}
    def missing() -> tuple[str, dict[str, str | None], bool]:
        if allow_missing:
            return target_id, missing_context, False
        raise HTTPException(422, {"code": "SEMANTIC_VOCABULARY_TARGET_NOT_FOUND"})
    if kind == "FOLDER_ROLE":
        if target_id not in FolderRole:
            raise HTTPException(422, {"code": "SEMANTIC_VOCABULARY_FOLDER_ROLE_INVALID"})
        return target_id, missing_context, True
    if kind == "PROJECT":
        row = conn.execute("SELECT id, name FROM projects WHERE id=?", [target_id]).fetchone()
        if not row:
            return missing()
        return str(row[1]), {"project_id": str(row[0]), "request_id": None, "load_case_id": None}, True
    if kind == "REQUEST":
        row = conn.execute("SELECT id, project_id, title FROM analysis_requests WHERE id=?", [target_id]).fetchone()
        if not row:
            return missing()
        return str(row[2]), {"project_id": str(row[1]), "request_id": str(row[0]), "load_case_id": None}, True
    if kind == "LOAD_CASE":
        row = conn.execute("SELECT lc.id, ar.project_id, ar.id, lc.name FROM load_cases lc JOIN analysis_requests ar ON ar.id=lc.request_id WHERE lc.id=?", [target_id]).fetchone()
        if not row:
            return missing()
        return str(row[3]), {"project_id": str(row[1]), "request_id": str(row[2]), "load_case_id": str(row[0])}, True
    row = conn.execute("SELECT i.id, i.key, v.definition_json FROM semantic_result_items i JOIN semantic_result_item_versions v ON v.item_id=i.id AND v.version=i.latest_version WHERE i.id=?", [target_id]).fetchone()
    if not row:
        return missing()
    definition = _json(row[2])
    return str(definition.get("label") or row[1]), missing_context, True


def _validate_scope(conn: Any, scope_project_id: str | None, context: dict[str, str | None]) -> None:
    if scope_project_id is None:
        return
    if conn.execute("SELECT 1 FROM projects WHERE id=?", [scope_project_id]).fetchone() is None:
        raise HTTPException(422, {"code": "SEMANTIC_VOCABULARY_SCOPE_NOT_FOUND"})
    target_project = context["project_id"]
    if target_project is not None and target_project != scope_project_id:
        raise HTTPException(422, {"code": "SEMANTIC_VOCABULARY_SCOPE_TARGET_MISMATCH"})


def _terms(body: VocabularyBody) -> list[str]:
    values = [body.key, body.label, *body.aliases]
    if any(any(ord(char) < 32 or ord(char) == 127 for char in value) for value in values):
        raise HTTPException(422, {"code": "SEMANTIC_VOCABULARY_TERM_INVALID"})
    normalized = [normalize_term(value) for value in values]
    if any(not value or len(value) > 200 for value in normalized):
        raise HTTPException(422, {"code": "SEMANTIC_VOCABULARY_TERM_INVALID"})
    return list(dict.fromkeys(normalized))


def _record(conn: Any, entry_id: str) -> dict[str, Any] | None:
    records = rows(conn.execute("SELECT * FROM semantic_vocabulary_entries WHERE id=?", [entry_id]))
    if not records:
        return None
    entry = records[0]
    entry["aliases"] = _json(entry.pop("aliases_json"))
    label, context, available = _target_context(conn, entry["target_kind"], entry["target_id"], allow_missing=True)
    entry["target_label"] = label
    entry["target_available"] = available
    entry.update({key: value for key, value in context.items() if value is not None})
    return entry


def _insert_terms(conn: Any, entry_id: str, body: VocabularyBody) -> None:
    terms = _terms(body)
    if not body.enabled:
        return
    scope_key = body.scope_project_id or ""
    for term in terms:
        try:
            conn.execute("INSERT INTO semantic_vocabulary_terms(entry_id, scope_key, target_kind, normalized_term) VALUES (?, ?, ?, ?)", [entry_id, scope_key, body.target_kind, term])
        except Exception as error:
            # Both adapters surface a unique violation differently.  The only
            # failing statement here is the normalized uniqueness invariant.
            message = str(error).casefold()
            if "duplicate" in message or "unique" in message or "primary key" in message:
                raise HTTPException(409, {"code": "SEMANTIC_VOCABULARY_TERM_CONFLICT", "term": term}) from error
            raise


@router.get("")
def list_entries(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        return {"entries": [entry for row in rows(conn.execute("SELECT id FROM semantic_vocabulary_entries ORDER BY scope_project_id NULLS FIRST, target_kind, key")) if (entry := _record(conn, str(row["id"]))) is not None]}


@router.post("", status_code=201)
def create_entry(payload: VocabularyBody, request: Request) -> dict[str, Any]:
    with connect() as conn, _transaction(conn):
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        if getattr(conn, "backend", "duckdb") == "postgresql":
            conn.execute("LOCK TABLE semantic_vocabulary_entries, semantic_vocabulary_terms IN SHARE ROW EXCLUSIVE MODE")
        if int(conn.execute("SELECT count(*) FROM semantic_vocabulary_entries").fetchone()[0]) >= _ENTRY_LIMIT:
            raise HTTPException(409, {"code": "SEMANTIC_VOCABULARY_LIMIT_EXCEEDED", "limit": _ENTRY_LIMIT})
        label, context, _ = _target_context(conn, payload.target_kind, payload.target_id)
        _validate_scope(conn, payload.scope_project_id, context)
        entry_id, now, actor = f"semantic-vocabulary-{uuid4().hex[:12]}", _now(), request.state.principal.user_id
        conn.execute("INSERT INTO semantic_vocabulary_entries(id, key, label, description, target_kind, target_id, scope_project_id, aliases_json, revision, enabled, created_at, updated_at, created_by, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)", [entry_id, payload.key, payload.label, payload.description, payload.target_kind, payload.target_id, payload.scope_project_id, json.dumps(payload.aliases, ensure_ascii=False), payload.enabled, now, now, actor, actor])
        _insert_terms(conn, entry_id, payload)
        write_audit_event(request=request, principal=request.state.principal, status_code=201, action="SEMANTIC_VOCABULARY_CREATED", detail={"entry_id": entry_id, "target_kind": payload.target_kind, "target_id": payload.target_id}, connection=conn)
        return _record(conn, entry_id) or {"target_label": label}


@router.put("/{entry_id}")
def update_entry(entry_id: str, payload: VocabularyUpdate, request: Request) -> dict[str, Any]:
    with connect() as conn, _transaction(conn):
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        if getattr(conn, "backend", "duckdb") == "postgresql":
            # Serialize all term rewrites in the same table order as create.
            # It avoids deadlocks when two entries exchange aliases at once.
            conn.execute("LOCK TABLE semantic_vocabulary_entries, semantic_vocabulary_terms IN SHARE ROW EXCLUSIVE MODE")
        lock = " FOR UPDATE" if getattr(conn, "backend", "duckdb") == "postgresql" else ""
        current = conn.execute(f"SELECT key, target_kind, target_id, scope_project_id, revision FROM semantic_vocabulary_entries WHERE id=?{lock}", [entry_id]).fetchone()
        if not current:
            raise HTTPException(404, {"code": "SEMANTIC_VOCABULARY_NOT_FOUND"})
        if (str(current[0]), str(current[1]), str(current[2]), current[3]) != (payload.key, payload.target_kind, payload.target_id, payload.scope_project_id):
            raise HTTPException(422, {"code": "SEMANTIC_VOCABULARY_MEANING_IMMUTABLE"})
        if int(current[4]) != payload.expected_revision:
            raise HTTPException(409, {"code": "SEMANTIC_VOCABULARY_REVISION_CONFLICT", "current_revision": int(current[4])})
        _, context, available = _target_context(conn, payload.target_kind, payload.target_id, allow_missing=True)
        if payload.enabled and not available:
            raise HTTPException(422, {"code": "SEMANTIC_VOCABULARY_TARGET_NOT_FOUND"})
        _validate_scope(conn, payload.scope_project_id, context if available else {"project_id": None, "request_id": None, "load_case_id": None})
        new_revision = payload.expected_revision + 1
        conn.execute("DELETE FROM semantic_vocabulary_terms WHERE entry_id=?", [entry_id])
        conn.execute("UPDATE semantic_vocabulary_entries SET label=?, description=?, aliases_json=?, enabled=?, revision=?, updated_at=?, updated_by=? WHERE id=? AND revision=?", [payload.label, payload.description, json.dumps(payload.aliases, ensure_ascii=False), payload.enabled, new_revision, _now(), request.state.principal.user_id, entry_id, payload.expected_revision])
        _insert_terms(conn, entry_id, payload)
        write_audit_event(request=request, principal=request.state.principal, status_code=200, action="SEMANTIC_VOCABULARY_UPDATED", detail={"entry_id": entry_id, "revision": new_revision, "enabled": payload.enabled}, connection=conn)
        return _record(conn, entry_id) or {}


@router.post("/resolve")
def resolve_terms(payload: ResolveBody, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        if payload.scope_project_id is not None and conn.execute("SELECT 1 FROM projects WHERE id=?", [payload.scope_project_id]).fetchone() is None:
            raise HTTPException(422, {"code": "SEMANTIC_VOCABULARY_SCOPE_NOT_FOUND"})
        kinds = payload.target_kinds or list(TargetKind.__args__)
        matches = []
        for term in payload.terms:
            candidates: list[dict[str, Any]] = []
            normalized = normalize_term(term)
            for kind in kinds:
                scope_key = payload.scope_project_id or ""
                found = rows(conn.execute("SELECT t.entry_id FROM semantic_vocabulary_terms t JOIN semantic_vocabulary_entries e ON e.id=t.entry_id WHERE t.scope_key=? AND t.target_kind=? AND t.normalized_term=? AND e.enabled=true", [scope_key, kind, normalized]))
                if not found and payload.scope_project_id is not None:
                    found = rows(conn.execute("SELECT t.entry_id FROM semantic_vocabulary_terms t JOIN semantic_vocabulary_entries e ON e.id=t.entry_id WHERE t.scope_key='' AND t.target_kind=? AND t.normalized_term=? AND e.enabled=true", [kind, normalized]))
                for row in found:
                    try:
                        entry = _record(conn, str(row["entry_id"]))
                    except HTTPException:
                        entry = None
                    if entry is not None and entry["enabled"] and entry["target_available"] and (payload.scope_project_id is None or entry.get("project_id") in (None, payload.scope_project_id)):
                        candidates.append(entry)
            status = "UNMAPPED" if not candidates else "MATCHED" if len(candidates) == 1 else "AMBIGUOUS"
            matches.append({"term": term, "status": status, "candidates": candidates})
        return {"matches": matches}
