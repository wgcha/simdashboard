"""Persist unresolved semantic refresh files and require explicit confirmation."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..adapters.persistence.result_ingestion import SQLResultIngestionQuery
from ..database_connection import connect, rows
from ..domains.semantic_mapping.engine import SemanticValidationError, preview_recipe, resolve_widgets
from ..modules.access_control import RESULT_IMPORT, require_resource_permission
from ..security import write_audit_event
from ..services import spdm_storage
from ..services.semantic_mapping import persist_semantic_import_in_transaction
from .semantic_body_limit import SemanticBodyLimitRoute

router = APIRouter(prefix="/api/semantic-mapping", tags=["semantic-mapping"], route_class=SemanticBodyLimitRoute)
ReviewState = Literal["OPEN", "SELECTED", "READY", "STALE", "IMPORTED", "SKIPPED"]


class RevalidateBody(BaseModel):
    expected_revision: int = Field(ge=1)
    recipe_id: str | None = Field(default=None, min_length=1, max_length=160)
    recipe_version: int | None = Field(default=None, ge=1)


class ConfirmBody(BaseModel):
    expected_revision: int = Field(ge=1)


class ReopenBody(BaseModel):
    expected_revision: int = Field(ge=1)


class ReviewCandidate(BaseModel):
    recipe_id: str
    recipe_version: int


class ReviewItemResponse(BaseModel):
    id: str; binding_id: str; binding_revision: int; load_case_id: str; relative_path: str
    source_sha256: str | None = None; source_size: int | None = None; scan_status: str; review_state: str
    candidates: list[ReviewCandidate] = Field(default_factory=list); selected_recipe_id: str | None = None
    selected_recipe_version: int | None = None; template_id: str | None = None; template_version: int | None = None
    validated_sha256: str | None = None; revision: int; confirmed_analysis_run_id: str | None = None
    previous_confirmed_analysis_run_id: str | None = None; error: dict[str, Any] | None = None


class ReviewPageResponse(BaseModel):
    items: list[ReviewItemResponse]
    next_cursor: str | None = None


class ReviewEventResponse(BaseModel):
    id: str
    review_item_id: str
    old_state: str | None = None
    new_state: str
    revision: int
    prior_run_id: str | None = None
    current_run_id: str | None = None
    detail: dict[str, Any]
    occurred_at: str
    actor: str


class ReviewHistoryResponse(BaseModel):
    events: list[ReviewEventResponse]
    truncated: bool = False


class ReviewRevalidateResponse(ReviewItemResponse):
    widgets: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class ReviewConfirmResponse(BaseModel):
    item: ReviewItemResponse
    status: Literal["IMPORTED", "SKIPPED"]
    run_id: str | None = None
    widgets: list[dict[str, Any]] = Field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _record(conn: Any, item_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if lock and getattr(conn, "backend", "duckdb") == "postgresql" else ""
    values = rows(conn.execute("SELECT * FROM semantic_import_review_items WHERE id=?" + suffix, [item_id]))
    if not values:
        return None
    item = values[0]
    for key in ("candidates_json", "validation_summary_json", "error_json"):
        item[key.removesuffix("_json")] = _json(item.pop(key)) if item.get(key) is not None else None
    for key, value in tuple(item.items()):
        if isinstance(value, datetime): item[key] = value.isoformat()
    return item


@contextmanager
def _transaction(conn: Any):
    conn.execute("BEGIN TRANSACTION")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _binding(conn: Any, binding_id: str) -> dict[str, Any]:
    records = rows(conn.execute("SELECT * FROM semantic_folder_bindings WHERE id=?", [binding_id]))
    if not records:
        raise HTTPException(404, {"code": "SEMANTIC_BINDING_NOT_FOUND"})
    return records[0]


def _locked_binding(conn: Any, binding_id: str) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if getattr(conn, "backend", "duckdb") == "postgresql" else ""
    records = rows(conn.execute("SELECT * FROM semantic_folder_bindings WHERE id=?" + suffix, [binding_id]))
    return records[0] if records else None


def _authorize(request: Request, conn: Any, binding: dict[str, Any]) -> None:
    if not binding.get("load_case_id"):
        raise HTTPException(422, {"code": "SEMANTIC_BINDING_LOAD_CASE_REQUIRED"})
    require_resource_permission(request, RESULT_IMPORT, "load_case", str(binding["load_case_id"]), conn=conn)


def _item_target_ids(conn: Any, item: dict[str, Any]) -> set[str]:
    """Return every target whose Run links may be exposed by this item."""
    targets = {str(item["load_case_id"])}
    for run_id in (item.get("previous_confirmed_analysis_run_id"), item.get("confirmed_analysis_run_id")):
        if not run_id:
            continue
        row = conn.execute("SELECT load_case_id FROM analysis_runs WHERE id=?", [run_id]).fetchone()
        if row is None:
            # A dangling reference is not safe to show through a reconnected
            # binding.  The normal FK prevents new rows from reaching here.
            raise HTTPException(409, {"code": "SEMANTIC_REVIEW_RUN_TARGET_STALE"})
        targets.add(str(row[0]))
    return targets


def _authorize_item(request: Request, conn: Any, binding: dict[str, Any], item: dict[str, Any]) -> None:
    """A reconnect cannot grant access to a review item's older target."""
    _authorize(request, conn, binding)
    for load_case_id in _item_target_ids(conn, item):
        require_resource_permission(request, RESULT_IMPORT, "load_case", load_case_id, conn=conn)


def _require_item_binding_target(binding: dict[str, Any], item: dict[str, Any]) -> None:
    if str(binding.get("load_case_id")) != str(item.get("load_case_id")):
        raise HTTPException(409, {"code": "SEMANTIC_REVIEW_TARGET_CHANGED"})


def _source(conn: Any, binding: dict[str, Any], relative_path: str) -> bytes:
    if not relative_path or "/" in relative_path or "\\" in relative_path or relative_path in {".", ".."}:
        raise HTTPException(422, {"code": "SEMANTIC_REVIEW_PATH_INVALID"})
    try:
        root = spdm_storage.storage_root(conn)
        if root.root is None: raise HTTPException(409, {"code": "SPDM_ROOT_UNSET"})
        directory = root.root.joinpath(*str(binding["relative_path"]).split("/"))
        spdm_storage._assert_safe_existing(directory, root.root)
        path = directory / relative_path
        spdm_storage._assert_safe_existing(path, root.root)
        if not path.is_file() or spdm_storage._is_reparse(path): raise HTTPException(422, {"code": "SEMANTIC_REVIEW_SOURCE_MISSING"})
        return spdm_storage.read_stable_bytes(path, max_bytes=5 * 1024 * 1024)[0]
    except spdm_storage.SpdmStorageError as error:
        raise HTTPException(422, {"code": error.code}) from error


def _exact_recipe(conn: Any, recipe_id: str, version: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row = conn.execute("SELECT definition_json, item_snapshot_json FROM semantic_recipe_versions WHERE recipe_id=? AND version=?", [recipe_id, version]).fetchone()
    if not row:
        raise HTTPException(409, {"code": "SEMANTIC_REVIEW_VERSION_STALE"})
    return _json(row[0]), _json(row[1])


def _exact_template(conn: Any, template_id: str | None, version: int | None, fallback: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if template_id is None or version is None:
        return {"widgets": []}, fallback
    row = conn.execute("SELECT definition_json, item_snapshot_json FROM semantic_template_versions WHERE template_id=? AND version=?", [template_id, version]).fetchone()
    if not row:
        raise HTTPException(409, {"code": "SEMANTIC_REVIEW_VERSION_STALE"})
    return _json(row[0]), _json(row[1])


def _stale(conn: Any, item: dict[str, Any], code: str, actor: str) -> dict[str, Any]:
    """Expire a locked review item and append its state event atomically."""
    if item["review_state"] == "STALE" and item.get("error") == {"code": code}:
        return item
    changed = conn.execute(
        "UPDATE semantic_import_review_items SET review_state='STALE', error_json=?, revision=revision+1, updated_at=?, updated_by=? "
        "WHERE id=? AND revision=? RETURNING revision",
        [json.dumps({"code": code}), _now(), actor, item["id"], item["revision"]],
    ).fetchone()
    if not changed:
        raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT"})
    revision = int(changed[0])
    _event(conn, str(item["id"]), str(item["review_state"]), "STALE", revision, actor, {"code": code}, item.get("previous_confirmed_analysis_run_id"), item.get("confirmed_analysis_run_id"))
    return _record(conn, str(item["id"])) or item


def _stale_and_raise(conn: Any, item_id: str, expected_revision: int, code: str, actor: str) -> None:
    """Serialize expiry with a concurrent revalidation/confirmation command."""
    with _transaction(conn):
        current = _record(conn, item_id, lock=True)
        if not current:
            raise HTTPException(404, {"code": "SEMANTIC_REVIEW_NOT_FOUND"})
        if int(current["revision"]) != expected_revision:
            raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT", "current_revision": current["revision"]})
        stale = _stale(conn, current, code, actor)
    raise HTTPException(409, {"code": code, "item": stale})


def _event(conn: Any, item_id: str, old: str | None, new: str, revision: int, actor: str, detail: dict[str, Any], prior: str | None = None, current: str | None = None) -> None:
    conn.execute("INSERT INTO semantic_import_review_events VALUES (?,?,?,?,?,?,?,?,?,?)", [f"semantic-review-event-{uuid4().hex[:12]}", item_id, old, new, revision, prior, current, json.dumps(detail, ensure_ascii=False), _now(), actor])


def record_unresolved_refresh(
    conn: Any, binding: dict[str, Any], *, relative_path: str, scan_status: str,
    candidates: list[dict[str, Any]], source_sha256: str | None, source_size: int | None,
    error: dict[str, Any] | None, actor: str,
) -> dict[str, Any]:
    """Store one refresh decision without letting an older scan replace a newer one.

    The binding row is the serialization key for refreshes.  Lock it before
    looking up the item, then verify that the caller's scanned binding revision
    and load-case target are still current.  This keeps a reconnecting binding
    from accepting review data read from its previous folder.
    """
    now = _now()
    encoded_candidates = json.dumps(candidates, ensure_ascii=False)
    encoded_error = json.dumps(error, ensure_ascii=False) if error else None
    with _transaction(conn):
        current_binding = _locked_binding(conn, str(binding["id"]))
        if not current_binding:
            raise HTTPException(404, {"code": "SEMANTIC_BINDING_NOT_FOUND"})
        if (
            int(current_binding["revision"]) != int(binding["revision"])
            or str(current_binding.get("load_case_id") or "") != str(binding.get("load_case_id") or "")
        ):
            raise HTTPException(409, {"code": "SEMANTIC_REVIEW_BINDING_STALE"})
        existing = conn.execute(
            "SELECT id FROM semantic_import_review_items WHERE binding_id=? AND load_case_id=? AND relative_path=?" +
            (" FOR UPDATE" if getattr(conn, "backend", "duckdb") == "postgresql" else ""),
            [binding["id"], binding["load_case_id"], relative_path],
        ).fetchone()
        if not existing:
            ident = f"semantic-review-{uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO semantic_import_review_items(id,binding_id,binding_revision,load_case_id,relative_path,source_sha256,source_size,scan_status,review_state,candidates_json,error_json,revision,created_at,updated_at,created_by,updated_by) VALUES (?,?,?,?,?,?,?,?,? ,?,?,1,?,?,?,?)",
                [ident, binding["id"], binding["revision"], binding["load_case_id"], relative_path, source_sha256, source_size, scan_status, "OPEN", encoded_candidates, encoded_error, now, now, actor, actor],
            )
            _event(conn, ident, None, "OPEN", 1, actor, {"scan_status": scan_status, "reason": "CREATED"})
            return _record(conn, ident) or {}
        previous = _record(conn, str(existing[0]), lock=True)
        assert previous is not None
        same_binding_target = (
            int(previous["binding_revision"]) == int(binding["revision"])
            and str(previous.get("load_case_id")) == str(binding.get("load_case_id"))
        )
        if same_binding_target and source_sha256 is None and previous["review_state"] in {"IMPORTED", "SKIPPED"}:
            # A transient lock/share violation does not start a new cycle for
            # an already completed source.  Reopen is explicit.
            return previous
        # A completed review is keyed by the immutable source and binding
        # target.  New candidates, an active-definition change, or a later
        # parser diagnostic must not reopen that completed import: a changed
        # source/binding scan is the explicit start of the next cycle.
        terminal_identity = (
            previous.get("source_sha256") == source_sha256
            and previous.get("source_size") == source_size
            and int(previous["binding_revision"]) == int(binding["revision"])
            and str(previous.get("load_case_id")) == str(binding.get("load_case_id"))
        )
        if terminal_identity and previous["review_state"] in {"IMPORTED", "SKIPPED"}:
            return previous
        unchanged = (
            previous.get("source_sha256") == source_sha256
            and previous.get("source_size") == source_size
            and int(previous["binding_revision"]) == int(binding["revision"])
            and str(previous.get("load_case_id")) == str(binding.get("load_case_id"))
            and previous.get("scan_status") == scan_status
            and previous.get("candidates") == candidates
            and previous.get("error") == error
        )
        if unchanged:
            return previous
        prior_run = previous.get("confirmed_analysis_run_id") or previous.get("previous_confirmed_analysis_run_id")
        changed = conn.execute(
            "UPDATE semantic_import_review_items SET binding_revision=?, source_sha256=?, source_size=?, scan_status=?, review_state='OPEN', candidates_json=?, selected_recipe_id=NULL, selected_recipe_version=NULL, template_id=NULL, template_version=NULL, validated_sha256=NULL, validation_summary_json=NULL, error_json=?, previous_confirmed_analysis_run_id=?, confirmed_analysis_run_id=NULL, revision=revision+1, updated_at=?, updated_by=? WHERE id=? AND revision=? RETURNING revision",
            [binding["revision"], source_sha256, source_size, scan_status, encoded_candidates, encoded_error, prior_run, now, actor, previous["id"], previous["revision"]],
        ).fetchone()
        if not changed:
            raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT"})
        _event(
            conn, str(previous["id"]), str(previous["review_state"]), "OPEN", int(changed[0]), actor,
            {"scan_status": scan_status, "reason": "SOURCE_OR_SCAN_CHANGED"}, prior_run,
        )
        return _record(conn, str(previous["id"])) or {}


@router.get("/bindings/{binding_id}/review-items", response_model=ReviewPageResponse)
def list_review_items(binding_id: str, request: Request, state: ReviewState | None = None, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
    if limit < 1 or limit > 100:
        raise HTTPException(422, {"code": "SEMANTIC_REVIEW_PAGE_INVALID"})
    with connect() as conn:
        binding = _binding(conn, binding_id); _authorize(request, conn, binding)
        query, args = "SELECT id FROM semantic_import_review_items WHERE binding_id=? AND load_case_id=?", [binding_id, binding["load_case_id"]]
        if state:
            query += " AND review_state=?"; args.append(state)
        query += " AND id>?"; args.append(cursor or "")
        query += " ORDER BY id LIMIT ?"
        listed = []
        scan_cursor = cursor or ""
        while True:
            batch = rows(conn.execute(query, [*args, 101]))
            if not batch:
                break
            for row in batch:
                scan_cursor = str(row["id"])
                entry = _record(conn, scan_cursor)
                if entry is None:
                    continue
                try:
                    _authorize_item(request, conn, binding, entry)
                except HTTPException as error:
                    if error.status_code in {401, 403}:
                        continue
                    raise
                listed.append(entry)
                if len(listed) > limit:
                    page = listed[:limit]
                    return {"items": page, "next_cursor": page[-1]["id"]}
            if len(batch) < 101:
                break
            # Advance only over rows that have already been denied; an
            # authorized overflow returns the last visible item as cursor.
            args[-1] = scan_cursor
        return {"items": listed, "next_cursor": None}


@router.get("/review-items/{item_id}/history", response_model=ReviewHistoryResponse)
def history(item_id: str, request: Request, limit: int = 100) -> dict[str, Any]:
    if limit < 1 or limit > 100:
        raise HTTPException(422, {"code": "SEMANTIC_REVIEW_PAGE_INVALID"})
    with connect() as conn:
        item = _record(conn, item_id)
        if not item: raise HTTPException(404, {"code": "SEMANTIC_REVIEW_NOT_FOUND"})
        _authorize_item(request, conn, _binding(conn, str(item["binding_id"])), item)
        events = rows(conn.execute("SELECT * FROM semantic_import_review_events WHERE review_item_id=? ORDER BY occurred_at DESC, id DESC LIMIT ?", [item_id, limit + 1]))
        truncated = len(events) > limit
        events = events[:limit]
        for event in events:
            event["detail"] = _json(event.pop("detail_json"))
            if isinstance(event.get("occurred_at"), datetime): event["occurred_at"] = event["occurred_at"].isoformat()
        return {"events": events, "truncated": truncated}


@router.post("/review-items/{item_id}/revalidate", response_model=ReviewRevalidateResponse)
def revalidate(item_id: str, payload: RevalidateBody, request: Request) -> dict[str, Any]:
    with connect() as conn:
        item = _record(conn, item_id)
        if not item: raise HTTPException(404, {"code": "SEMANTIC_REVIEW_NOT_FOUND"})
        binding = _binding(conn, str(item["binding_id"])); _authorize_item(request, conn, binding, item); _require_item_binding_target(binding, item)
        if int(item["revision"]) != payload.expected_revision: raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT", "current_revision": item["revision"]})
        if item["review_state"] in {"IMPORTED", "SKIPPED"}:
            raise HTTPException(409, {"code": "SEMANTIC_REVIEW_TERMINAL", "item": item})
        if int(binding["revision"]) != int(item["binding_revision"]):
            _stale_and_raise(conn, item_id, payload.expected_revision, "SEMANTIC_REVIEW_BINDING_STALE", request.state.principal.user_id)
        try:
            content = _source(conn, binding, str(item["relative_path"]))
        except HTTPException as error:
            if error.status_code in {409, 422}:
                _stale_and_raise(conn, item_id, payload.expected_revision, "SEMANTIC_REVIEW_SOURCE_STALE", request.state.principal.user_id)
            raise
        digest = hashlib.sha256(content).hexdigest()
        if digest != item.get("source_sha256"):
            _stale_and_raise(conn, item_id, payload.expected_revision, "SEMANTIC_REVIEW_SOURCE_STALE", request.state.principal.user_id)
        candidates = _json(item["candidates"]) if item.get("candidates") is not None else []
        recipe_id, version = payload.recipe_id or item.get("selected_recipe_id"), payload.recipe_version or item.get("selected_recipe_version")
        if (payload.recipe_id is None) != (payload.recipe_version is None):
            raise HTTPException(422, {"code": "SEMANTIC_REVIEW_RECIPE_REQUIRED"})
        configured = _json(binding["recipe_ids_json"])
        if payload.recipe_id is not None:
            active = conn.execute("SELECT active_version FROM semantic_recipes WHERE id=?", [payload.recipe_id]).fetchone()
            if payload.recipe_id not in configured or not active or int(active[0] or 0) != payload.recipe_version:
                raise HTTPException(422, {"code": "SEMANTIC_REVIEW_RECIPE_NOT_CONFIGURED"})
        if not recipe_id or version is None:
            raise HTTPException(422, {"code": "SEMANTIC_REVIEW_RECIPE_REQUIRED"})
        recipe, items = _exact_recipe(conn, str(recipe_id), int(version))
        template_id = binding.get("template_id")
        template_version = None
        if template_id:
            active = conn.execute("SELECT active_version FROM semantic_templates WHERE id=?", [template_id]).fetchone()
            if not active or active[0] is None: raise HTTPException(409, {"code": "SEMANTIC_REVIEW_TEMPLATE_STALE"})
            template_version = int(active[0])
        template, template_items = _exact_template(conn, str(template_id) if template_id else None, template_version, items)
        try:
            parsed = preview_recipe(recipe, items, str(item["relative_path"]), content)
            widgets = resolve_widgets(template, template_items, parsed) if template_id else []
        except SemanticValidationError as error:
            raise HTTPException(422, {"code": error.code, "message": str(error)}) from error
        if any(widget.get("status") != "READY" for widget in widgets): raise HTTPException(422, {"code": "SEMANTIC_WIDGET_INPUT_INVALID", "widgets": widgets})
        stale: dict[str, Any] | None = None
        updated: dict[str, Any] | None = None
        with _transaction(conn):
            locked_binding = _locked_binding(conn, str(binding["id"]))
            current = _record(conn, item_id, lock=True)
            if current is None:
                raise HTTPException(404, {"code": "SEMANTIC_REVIEW_NOT_FOUND"})
            if int(current["revision"]) != payload.expected_revision:
                raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT", "current_revision": current["revision"]})
            if not locked_binding or int(locked_binding["revision"]) != int(item["binding_revision"]) or str(locked_binding.get("load_case_id")) != str(item.get("load_case_id")):
                stale = _stale(conn, current, "SEMANTIC_REVIEW_BINDING_STALE", request.state.principal.user_id)
            elif current["review_state"] in {"IMPORTED", "SKIPPED"}:
                raise HTTPException(409, {"code": "SEMANTIC_REVIEW_TERMINAL", "item": current})
            else:
                changed = conn.execute("UPDATE semantic_import_review_items SET review_state='READY', selected_recipe_id=?, selected_recipe_version=?, template_id=?, template_version=?, validated_sha256=?, validation_summary_json=?, error_json=NULL, revision=revision+1, updated_at=?, updated_by=?, validated_at=?, validated_by=? WHERE id=? AND revision=? RETURNING revision", [recipe_id, version, template_id, template_version, digest, json.dumps(parsed["summary"], ensure_ascii=False), _now(), request.state.principal.user_id, _now(), request.state.principal.user_id, item_id, payload.expected_revision]).fetchone()
                if not changed:
                    raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT"})
                _event(conn, item_id, str(current["review_state"]), "READY", int(changed[0]), request.state.principal.user_id, {"recipe_id": recipe_id, "recipe_version": version})
                write_audit_event(request=request, principal=request.state.principal, status_code=200, action="SEMANTIC_REVIEW_REVALIDATED", detail={"review_item_id": item_id, "recipe_id": recipe_id, "recipe_version": version}, connection=conn)
                updated = _record(conn, item_id)
        if stale is not None:
            raise HTTPException(409, {"code": "SEMANTIC_REVIEW_BINDING_STALE", "item": stale})
        updated = updated or {}; updated["widgets"] = widgets; updated["summary"] = parsed["summary"]; return updated


@router.post("/review-items/{item_id}/confirm", response_model=ReviewConfirmResponse)
def confirm(item_id: str, payload: ConfirmBody, request: Request) -> dict[str, Any]:
    with connect() as conn:
        item = _record(conn, item_id)
        if not item: raise HTTPException(404, {"code": "SEMANTIC_REVIEW_NOT_FOUND"})
        binding = _binding(conn, str(item["binding_id"])); _authorize_item(request, conn, binding, item); _require_item_binding_target(binding, item)
        if int(item["revision"]) != payload.expected_revision:
            raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT", "current_revision": item["revision"]})
        if item["review_state"] in {"IMPORTED", "SKIPPED"}:
            return {"item": item, "status": item["review_state"], "run_id": item.get("confirmed_analysis_run_id"), "widgets": []}
        try:
            content = _source(conn, binding, str(item["relative_path"]))
        except HTTPException as error:
            if error.status_code in {409, 422}:
                _stale_and_raise(conn, item_id, payload.expected_revision, "SEMANTIC_REVIEW_CONFIRM_STALE", request.state.principal.user_id)
            raise
        digest = hashlib.sha256(content).hexdigest()
        stale: dict[str, Any] | None = None
        result: dict[str, Any] | None = None
        with _transaction(conn):
            binding = _locked_binding(conn, str(binding["id"]))
            if binding is None:
                raise HTTPException(404, {"code": "SEMANTIC_BINDING_NOT_FOUND"})
            current = _record(conn, item_id, lock=True)
            if not current: raise HTTPException(404, {"code": "SEMANTIC_REVIEW_NOT_FOUND"})
            if int(current["revision"]) != payload.expected_revision:
                raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT", "current_revision": current["revision"]})
            if current["review_state"] in {"IMPORTED", "SKIPPED"}:
                result = {"item": current, "status": current["review_state"], "run_id": current.get("confirmed_analysis_run_id"), "widgets": []}
            elif current["review_state"] != "READY" or current.get("validated_sha256") != digest or current.get("source_sha256") != digest or int(binding["revision"]) != int(current["binding_revision"]) or str(binding.get("load_case_id")) != str(current.get("load_case_id")):
                stale = _stale(conn, current, "SEMANTIC_REVIEW_CONFIRM_STALE", request.state.principal.user_id)
            else:
                recipe, recipe_items = _exact_recipe(conn, str(current["selected_recipe_id"]), int(current["selected_recipe_version"]))
                template, template_items = _exact_template(conn, current.get("template_id"), current.get("template_version"), recipe_items)
                parsed = preview_recipe(recipe, recipe_items, str(current["relative_path"]), content)
                widgets = resolve_widgets(template, template_items, parsed) if current.get("template_id") else []
                if any(widget.get("status") != "READY" for widget in widgets): raise HTTPException(422, {"code": "SEMANTIC_WIDGET_INPUT_INVALID", "widgets": widgets})
                target = SQLResultIngestionQuery(conn).get_result_ingestion_target(str(current["load_case_id"]))
                if target is None: raise HTTPException(404, {"code": "LOAD_CASE_NOT_FOUND"})
                command = {"project_id": target.project_id, "request_id": target.request_id, "load_case_id": current["load_case_id"], "source_type": "SEMANTIC_RECIPE", "source_name": f"semantic/{current['selected_recipe_id']}/{current['selected_recipe_version']}/{current['relative_path']}", "source_checksum": digest, "source_run_id": f"semantic:{current['selected_recipe_id']}:{current['selected_recipe_version']}:{digest}", "conflict_policy": "SKIP", "parser_version": "semantic-mapping-v1", "parsed": parsed, "actor": request.state.principal.display_name, "metadata": {"recipe_id": current["selected_recipe_id"], "recipe_version": current["selected_recipe_version"], "template_id": current.get("template_id"), "template_version": current.get("template_version"), "observations": parsed.get("observations", []), "source_filename": current["relative_path"]}}
                outcome, run_id = persist_semantic_import_in_transaction(conn, command, recipe_id=str(current["selected_recipe_id"]), recipe_version=int(current["selected_recipe_version"]), template_id=current.get("template_id"), template_version=current.get("template_version"), filename=str(current["relative_path"]), source_bytes=content, authorize=lambda c, tx: require_resource_permission(request, RESULT_IMPORT, "load_case", c["load_case_id"], conn=tx), now=_now)
                state = "SKIPPED" if outcome["status"] == "SKIPPED" else "IMPORTED"
                changed = conn.execute("UPDATE semantic_import_review_items SET review_state=?, confirmed_analysis_run_id=?, confirmed_at=?, confirmed_by=?, revision=revision+1, updated_at=?, updated_by=? WHERE id=? AND revision=? RETURNING revision", [state, run_id, _now(), request.state.principal.user_id, _now(), request.state.principal.user_id, item_id, current["revision"]]).fetchone()
                if not changed:
                    raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT"})
                _event(conn, item_id, "READY", state, int(changed[0]), request.state.principal.user_id, {"outcome": outcome["status"]}, current.get("previous_confirmed_analysis_run_id"), run_id)
                write_audit_event(request=request, principal=request.state.principal, status_code=200, action="SEMANTIC_REVIEW_CONFIRMED", detail={"review_item_id": item_id, "run_id": run_id, "status": state, "previous_confirmed_run_id": current.get("previous_confirmed_analysis_run_id")}, connection=conn)
                result = {"item": _record(conn, item_id), "status": state, "run_id": run_id, "widgets": widgets}
        if stale is not None:
            raise HTTPException(409, {"code": "SEMANTIC_REVIEW_CONFIRM_STALE", "item": stale})
        assert result is not None
        return result


@router.post("/review-items/{item_id}/reopen", response_model=ReviewItemResponse)
def reopen(item_id: str, payload: ReopenBody, request: Request) -> dict[str, Any]:
    """Begin an explicit new review cycle while retaining the prior Run link."""
    with connect() as conn:
        item = _record(conn, item_id)
        if not item:
            raise HTTPException(404, {"code": "SEMANTIC_REVIEW_NOT_FOUND"})
        binding = _binding(conn, str(item["binding_id"]))
        _authorize_item(request, conn, binding, item)
        _require_item_binding_target(binding, item)
        if int(item["revision"]) != payload.expected_revision:
            raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT", "current_revision": item["revision"]})
        if item["review_state"] not in {"IMPORTED", "SKIPPED"}:
            raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REOPEN_TERMINAL_REQUIRED"})
        try:
            content = _source(conn, binding, str(item["relative_path"]))
        except HTTPException as error:
            if error.status_code in {409, 422}:
                raise HTTPException(409, {"code": "SEMANTIC_REVIEW_SOURCE_STALE"}) from error
            raise
        digest = hashlib.sha256(content).hexdigest()
        with _transaction(conn):
            locked_binding = _locked_binding(conn, str(binding["id"]))
            current = _record(conn, item_id, lock=True)
            if not locked_binding or not current:
                raise HTTPException(404, {"code": "SEMANTIC_REVIEW_NOT_FOUND"})
            if int(current["revision"]) != payload.expected_revision:
                raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT", "current_revision": current["revision"]})
            if current["review_state"] not in {"IMPORTED", "SKIPPED"}:
                raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REOPEN_TERMINAL_REQUIRED"})
            if int(locked_binding["revision"]) != int(current["binding_revision"]) or str(locked_binding.get("load_case_id")) != str(current.get("load_case_id")):
                raise HTTPException(409, {"code": "SEMANTIC_REVIEW_BINDING_STALE"})
            prior_run = current.get("confirmed_analysis_run_id") or current.get("previous_confirmed_analysis_run_id")
            changed = conn.execute(
                "UPDATE semantic_import_review_items SET source_sha256=?, source_size=?, scan_status='PENDING', review_state='OPEN', selected_recipe_id=NULL, selected_recipe_version=NULL, template_id=NULL, template_version=NULL, validated_sha256=NULL, validation_summary_json=NULL, error_json=NULL, previous_confirmed_analysis_run_id=?, confirmed_analysis_run_id=NULL, revision=revision+1, updated_at=?, updated_by=? WHERE id=? AND revision=? RETURNING revision",
                [digest, len(content), prior_run, _now(), request.state.principal.user_id, item_id, current["revision"]],
            ).fetchone()
            if not changed:
                raise HTTPException(409, {"code": "SEMANTIC_REVIEW_REVISION_CONFLICT"})
            _event(conn, item_id, str(current["review_state"]), "OPEN", int(changed[0]), request.state.principal.user_id, {"reason": "EXPLICIT_REOPEN"}, prior_run)
            write_audit_event(request=request, principal=request.state.principal, status_code=200, action="SEMANTIC_REVIEW_REOPENED", detail={"review_item_id": item_id, "prior_run_id": prior_run}, connection=conn)
            updated = _record(conn, item_id)
        return updated or {}
