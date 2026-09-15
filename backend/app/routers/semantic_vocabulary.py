"""Admin-managed, reusable vocabulary definitions for explicit UI selection."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ..database_connection import connect
from ..domains.semantic_vocabulary.normalization import normalize_term
from ..modules.access_control import SYSTEM_CATALOG_MANAGE, require_permission
from ..repositories import semantic_vocabulary as vocabulary_repository
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
    result = vocabulary_repository.target_context(conn, kind, target_id)
    if result is None:
        return missing()
    label, context = result
    return label, context, True


def _validate_scope(conn: Any, scope_project_id: str | None, context: dict[str, str | None]) -> None:
    if scope_project_id is None:
        return
    if not vocabulary_repository.scope_exists(conn, scope_project_id):
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
    entry = vocabulary_repository.entry_record(conn, entry_id)
    if entry is None:
        return None
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
    try:
        vocabulary_repository.insert_terms(conn, entry_id=entry_id, target_kind=body.target_kind, scope_project_id=body.scope_project_id, enabled=body.enabled, terms=terms)
    except vocabulary_repository.TermConflictError as error:
        raise HTTPException(409, {"code": "SEMANTIC_VOCABULARY_TERM_CONFLICT", "term": error.term}) from error


@router.get("")
def list_entries(request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        return {"entries": [entry for entry_id in vocabulary_repository.entry_ids(conn) if (entry := _record(conn, entry_id)) is not None]}


@router.post("", status_code=201)
def create_entry(payload: VocabularyBody, request: Request) -> dict[str, Any]:
    with connect() as conn, vocabulary_repository.vocabulary_transaction(conn):
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        vocabulary_repository.lock_vocabulary_tables(conn)
        if vocabulary_repository.entry_count(conn) >= _ENTRY_LIMIT:
            raise HTTPException(409, {"code": "SEMANTIC_VOCABULARY_LIMIT_EXCEEDED", "limit": _ENTRY_LIMIT})
        label, context, _ = _target_context(conn, payload.target_kind, payload.target_id)
        _validate_scope(conn, payload.scope_project_id, context)
        entry_id, now, actor = f"semantic-vocabulary-{uuid4().hex[:12]}", _now(), request.state.principal.user_id
        vocabulary_repository.create_entry(conn, entry_id=entry_id, body=payload, actor=actor, now=now)
        _insert_terms(conn, entry_id, payload)
        write_audit_event(request=request, principal=request.state.principal, status_code=201, action="SEMANTIC_VOCABULARY_CREATED", detail={"entry_id": entry_id, "target_kind": payload.target_kind, "target_id": payload.target_id}, connection=conn)
        return _record(conn, entry_id) or {"target_label": label}


@router.put("/{entry_id}")
def update_entry(entry_id: str, payload: VocabularyUpdate, request: Request) -> dict[str, Any]:
    with connect() as conn, vocabulary_repository.vocabulary_transaction(conn):
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        vocabulary_repository.lock_vocabulary_tables(conn)
        current = vocabulary_repository.current_entry(conn, entry_id)
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
        vocabulary_repository.rewrite_entry(conn, entry_id=entry_id, body=payload, revision=new_revision, actor=request.state.principal.user_id, now=_now())
        _insert_terms(conn, entry_id, payload)
        write_audit_event(request=request, principal=request.state.principal, status_code=200, action="SEMANTIC_VOCABULARY_UPDATED", detail={"entry_id": entry_id, "revision": new_revision, "enabled": payload.enabled}, connection=conn)
        return _record(conn, entry_id) or {}


@router.post("/resolve")
def resolve_terms(payload: ResolveBody, request: Request) -> dict[str, Any]:
    with connect() as conn:
        require_permission(request, SYSTEM_CATALOG_MANAGE, conn=conn)
        if payload.scope_project_id is not None and not vocabulary_repository.scope_exists(conn, payload.scope_project_id):
            raise HTTPException(422, {"code": "SEMANTIC_VOCABULARY_SCOPE_NOT_FOUND"})
        kinds = payload.target_kinds or list(TargetKind.__args__)
        matches = []
        for term in payload.terms:
            candidates: list[dict[str, Any]] = []
            normalized = normalize_term(term)
            for kind in kinds:
                scope_key = payload.scope_project_id or ""
                found = vocabulary_repository.resolve_term_ids_for_normalized(conn, scope_key=scope_key, target_kind=kind, normalized_term=normalized)
                if not found and payload.scope_project_id is not None:
                    found = vocabulary_repository.resolve_term_ids_for_normalized(conn, scope_key="", target_kind=kind, normalized_term=normalized)
                for found_id in found:
                    try:
                        entry = _record(conn, found_id)
                    except HTTPException:
                        entry = None
                    if entry is not None and entry["enabled"] and entry["target_available"] and (payload.scope_project_id is None or entry.get("project_id") in (None, payload.scope_project_id)):
                        candidates.append(entry)
            status = "UNMAPPED" if not candidates else "MATCHED" if len(candidates) == 1 else "AMBIGUOUS"
            matches.append({"term": term, "status": status, "candidates": candidates})
        return {"matches": matches}
